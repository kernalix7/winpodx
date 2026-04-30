# =====================================================================
# winpodx guest agent (Phase 2) — /health + bearer-auth /exec
# =====================================================================
#
# Purpose
# -------
# HTTP server inside the Windows guest. Phase 1 shipped the bare
# /health readiness probe. Phase 2 adds bearer-auth and a POST /exec
# endpoint that runs base64-encoded PowerShell snippets — replacing
# FreeRDP RemoteApp PowerShell calls for non-sensitive host -> guest
# traffic so the host can stop emitting visible PS-window flashes for
# every registry tweak / discovery roundtrip.
#
# Invariants
# ----------
#   * Bind: 127.0.0.1:8765 ONLY (loopback inside the Windows VM).
#     dockur's user-mode QEMU NAT forwards this to the Linux container's
#     loopback 8765, and compose's `127.0.0.1:8765:8765/tcp` forwards
#     that to the host's loopback. Reachable ONLY from the host that
#     owns the pod — see docs/AGENT_V2_DESIGN.md "Architecture".
#   * /health takes NO authentication. It is the readiness signal; the
#     host may probe it before the token has even been delivered.
#   * Every other endpoint requires `Authorization: Bearer <token>`.
#     Mismatch returns 401 with JSON {"error":"unauthorized"}. The
#     compare is constant-time so timing leaks can't recover the token.
#   * Wait-Token loop polls C:\OEM\agent_token.txt with bounded backoff,
#     never throws. Anti-goal #6 in AGENT_V2_DESIGN: throwing kills the
#     process and HKCU\Run does not auto-restart.
#   * The token is never logged or echoed back. /exec script content
#     lands in C:\OEM\agent.log only as a SHA256 hash, never the raw
#     payload — sensitive payloads (registry keys, credentials touched
#     by self-heal) must not survive in the log.
# =====================================================================

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:AgentVersion = '0.2.2-rev2'
$script:StartedAt    = (Get-Date).ToUniversalTime().ToString('o')
$script:OemDir       = 'C:\OEM'
$script:TokenPath    = 'C:\OEM\agent_token.txt'
$script:LogPath      = 'C:\OEM\agent.log'
$script:RunsDir      = 'C:\OEM\agent-runs'
$script:Prefix       = 'http://127.0.0.1:8765/'
$script:ExecDefaultTimeoutSec = 60
$script:ExecMaxTimeoutSec     = 300

if (-not (Test-Path $script:RunsDir)) {
    try { New-Item -ItemType Directory -Path $script:RunsDir -Force | Out-Null } catch { }
}

function Read-Token {
    if (-not (Test-Path $script:TokenPath)) { return $null }
    try {
        $t = (Get-Content -Path $script:TokenPath -TotalCount 1 -ErrorAction Stop).Trim()
    } catch { return $null }
    if (-not $t) { return $null }
    return $t
}

# Poll for the token file with backoff capped at 30s. The token is
# delivered via the OEM bind mount (config/oem/agent_token.txt staged
# at setup time -> /oem in the container -> C:\OEM\ inside Windows by
# dockur's first-boot copy), but we cannot assume any particular order
# between HKCU\Run firing and the OEM stage completing. We never throw
# here: throwing would kill the process and HKCU\Run does not respawn.
function Wait-Token {
    $delay = 2
    while ($true) {
        $t = Read-Token
        if ($t) { return $t }
        Start-Sleep -Seconds $delay
        if ($delay -lt 30) { $delay = [Math]::Min(30, $delay * 2) }
    }
}

# Constant-time string compare. Both inputs are ASCII hex on the happy
# path; we still walk the full length to avoid leaking string length
# via early-return timing. Returns $false on null / length mismatch
# without short-circuiting on content.
function Compare-Constant([string]$a, [string]$b) {
    if ($null -eq $a -or $null -eq $b) { return $false }
    if ($a.Length -ne $b.Length) { return $false }
    $diff = 0
    for ($i = 0; $i -lt $a.Length; $i++) {
        $diff = $diff -bor ([int][char]$a[$i] -bxor [int][char]$b[$i])
    }
    return ($diff -eq 0)
}

# Read the Authorization header off an HttpListener request, strip the
# "Bearer " prefix, and constant-time compare against $script:Token.
# Returns $true / $false — never throws, never logs the supplied value.
function Test-Auth($req) {
    $h = $req.Headers['Authorization']
    if (-not $h) { return $false }
    if (-not $h.StartsWith('Bearer ')) { return $false }
    return (Compare-Constant -a $h.Substring(7) -b $script:Token)
}

function Read-Body($req) {
    if (-not $req.HasEntityBody) { return '' }
    $sr = [IO.StreamReader]::new($req.InputStream, $req.ContentEncoding)
    try { return $sr.ReadToEnd() } finally { $sr.Dispose() }
}

# SHA256 hex of arbitrary bytes — used to log a fingerprint of /exec
# script payloads without spilling the script itself into agent.log.
function Get-BytesHash([byte[]]$bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($bytes)
    } finally {
        $sha.Dispose()
    }
    return ([BitConverter]::ToString($hash) -replace '-','').ToLowerInvariant()
}

function Write-Log([string]$method, [string]$path, [string]$auth, [int]$code, [int]$ms, [string]$extra) {
    $ts = (Get-Date).ToUniversalTime().ToString('o')
    $line = "$ts $method $path auth=$auth code=$code ${ms}ms"
    if ($extra) { $line = "$line $extra" }
    try { Add-Content -Path $script:LogPath -Value $line -ErrorAction SilentlyContinue } catch { }
}

function Send-Json($resp, [int]$code, $obj) {
    $resp.StatusCode = $code
    $resp.ContentType = 'application/json; charset=utf-8'
    $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -Compress -Depth 6 $obj))
    $resp.ContentLength64 = $bytes.Length
    $resp.OutputStream.Write($bytes, 0, $bytes.Length)
    $resp.OutputStream.Close()
}

# Run a base64-encoded PowerShell snippet via Start-Process, with a
# server-side timeout enforced by WaitForExit. Returns a hashtable with
# rc / stdout / stderr / hash, or { error = '...' } on spawn failure.
# We deliberately use -File against a temp .ps1 (not -EncodedCommand)
# because Start-Process does not support -EncodedCommand cleanly with
# stdout/stderr redirection; the temp file is cleaned up after the run.
function Invoke-ExecScript([string]$scriptB64, [int]$timeoutSec) {
    $tempBase  = Join-Path $script:RunsDir ([Guid]::NewGuid().ToString('N'))
    $tempFile  = "$tempBase.ps1"
    $stdoutFile = "$tempBase.out"
    $stderrFile = "$tempBase.err"
    try {
        try {
            $bytes = [Convert]::FromBase64String($scriptB64)
        } catch {
            return @{ error = 'bad_base64'; detail = $_.Exception.Message }
        }
        $hash = Get-BytesHash $bytes
        try {
            [IO.File]::WriteAllBytes($tempFile, $bytes)
        } catch {
            return @{ error = 'temp_write_failed'; detail = $_.Exception.Message; hash = $hash }
        }
        $proc = $null
        try {
            $proc = Start-Process -FilePath 'powershell.exe' `
                -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$tempFile `
                -PassThru -NoNewWindow `
                -RedirectStandardOutput $stdoutFile `
                -RedirectStandardError  $stderrFile
        } catch {
            return @{ error = 'spawn_failed'; detail = $_.Exception.Message; hash = $hash }
        }
        $rc = 0
        $timedOut = $false
        if (-not $proc.WaitForExit([int]([Math]::Min($timeoutSec, $script:ExecMaxTimeoutSec)) * 1000)) {
            $timedOut = $true
            try { $proc.Kill() } catch { }
            try { [void]$proc.WaitForExit(2000) } catch { }
            $rc = 124
        } else {
            $rc = $proc.ExitCode
        }
        $stdoutText = ''
        $stderrText = ''
        if (Test-Path $stdoutFile) {
            try { $stdoutText = [IO.File]::ReadAllText($stdoutFile, [Text.Encoding]::UTF8) } catch { }
        }
        if (Test-Path $stderrFile) {
            try { $stderrText = [IO.File]::ReadAllText($stderrFile, [Text.Encoding]::UTF8) } catch { }
        }
        if ($timedOut -and -not $stderrText) { $stderrText = 'timeout' }
        return @{
            rc       = $rc
            stdout   = $stdoutText
            stderr   = $stderrText
            hash     = $hash
            timedOut = $timedOut
        }
    } finally {
        foreach ($f in @($tempFile, $stdoutFile, $stderrFile)) {
            if ($f -and (Test-Path $f)) {
                try { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue } catch { }
            }
        }
    }
}

# Wait for the token before binding. /health is no-auth, but every
# other endpoint compares against $script:Token, so binding before the
# token lands would race the first auth check on a real /exec call.
$script:Token = Wait-Token

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($script:Prefix)
$listener.Start()

try {
    while ($listener.IsListening) {
        $ctx = $null
        try { $ctx = $listener.GetContext() } catch { continue }
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $req = $ctx.Request
        $resp = $ctx.Response
        $method = $req.HttpMethod
        $path = $req.Url.AbsolutePath
        $code = 500
        $authTag = 'none'
        $extraLog = ''
        try {
            if ($method -eq 'GET' -and $path -eq '/health') {
                $payload = @{
                    version    = $script:AgentVersion
                    ok         = $true
                    started_at = $script:StartedAt
                }
                Send-Json $resp 200 $payload
                $code = 200
            } elseif (-not (Test-Auth $req)) {
                Send-Json $resp 401 @{ error = 'unauthorized' }
                $code = 401
                $authTag = 'fail'
            } elseif ($method -eq 'POST' -and $path -eq '/exec') {
                $authTag = 'ok'
                $body = Read-Body $req
                $parsed = $null
                try { $parsed = $body | ConvertFrom-Json -ErrorAction Stop } catch { $parsed = $null }
                if ($null -eq $parsed -or -not $parsed.PSObject.Properties['script']) {
                    Send-Json $resp 400 @{ error = 'bad_request'; detail = 'missing script field' }
                    $code = 400
                } else {
                    $timeout = $script:ExecDefaultTimeoutSec
                    if ($parsed.PSObject.Properties['timeout_sec']) {
                        try { $timeout = [int]$parsed.timeout_sec } catch { $timeout = $script:ExecDefaultTimeoutSec }
                        if ($timeout -le 0) { $timeout = $script:ExecDefaultTimeoutSec }
                        if ($timeout -gt $script:ExecMaxTimeoutSec) { $timeout = $script:ExecMaxTimeoutSec }
                    }
                    $result = Invoke-ExecScript -scriptB64 ([string]$parsed.script) -timeoutSec $timeout
                    if ($result.ContainsKey('error')) {
                        Send-Json $resp 500 @{ error = 'exec_failed'; detail = $result.error }
                        $code = 500
                        if ($result.ContainsKey('hash')) { $extraLog = "hash=$($result.hash)" }
                    } else {
                        Send-Json $resp 200 @{
                            rc     = $result.rc
                            stdout = $result.stdout
                            stderr = $result.stderr
                        }
                        $code = 200
                        $extraLog = "hash=$($result.hash) rc=$($result.rc) timeout=$($result.timedOut)"
                    }
                }
            } else {
                $authTag = 'ok'
                Send-Json $resp 404 @{ error = 'not_found' }
                $code = 404
            }
        } catch {
            try {
                Send-Json $resp 500 @{ error = 'internal_error' }
                $code = 500
            } catch { }
        }
        $sw.Stop()
        Write-Log $method $path $authTag $code ([int]$sw.ElapsedMilliseconds) $extraLog
    }
} finally {
    try { $listener.Stop() } catch { }
    try { $listener.Close() } catch { }
}
