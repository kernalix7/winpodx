# SPDX-License-Identifier: MIT
# =====================================================================
# winpodx guest agent (Phase 2) -- /health + bearer-auth /exec
# =====================================================================
#
# Purpose
# -------
# HTTP server inside the Windows guest. Phase 1 shipped the bare
# /health readiness probe. Phase 2 adds bearer-auth and a POST /exec
# endpoint that runs base64-encoded PowerShell snippets -- replacing
# FreeRDP RemoteApp PowerShell calls for non-sensitive host -> guest
# traffic so the host can stop emitting visible PS-window flashes for
# every registry tweak / discovery roundtrip.
#
# Invariants
# ----------
#   * Bind: ``http://+:8765/`` (all interfaces inside the Windows VM).
#     QEMU's user-mode NAT forwards from the container to the VM's
#     slirp interface (10.0.2.15:8765, NOT 127.0.0.1:8765 -- slirp
#     hostfwd targets the VM's main NIC, not loopback). Binding only
#     to 127.0.0.1 inside Windows would mean slirp's forwarded packets
#     hit a closed port -- kernalix7 saw "Connection reset by peer" on
#     2026-04-30 from exactly this. Binding to ``+`` covers all
#     interfaces. The agent is still externally unreachable: compose's
#     ``127.0.0.1:8765:8765/tcp`` mapping is loopback-only on the host,
#     and QEMU slirp is private to the container.
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
#     payload -- sensitive payloads (registry keys, credentials touched
#     by self-heal) must not survive in the log.
# =====================================================================

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:AgentVersion = '0.2.3'
$script:StartedAt    = (Get-Date).ToUniversalTime().ToString('o')
$script:OemDir       = 'C:\OEM'
$script:TokenPath    = 'C:\OEM\agent_token.txt'
$script:LogPath      = 'C:\OEM\agent.log'
$script:RunsDir      = 'C:\OEM\agent-runs'
$script:Prefix       = 'http://+:8765/'
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
# Returns $true / $false -- never throws, never logs the supplied value.
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

# SHA256 hex of arbitrary bytes -- used to log a fingerprint of /exec
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
        # Spawn the child via [Diagnostics.Process] + ProcessStartInfo with
        # CreateNoWindow=$true. Start-Process -NoNewWindow re-opens a console
        # for the child when the parent (agent.ps1) was launched with
        # -WindowStyle Hidden -- kernalix7 saw flashing PS windows on every
        # /exec call on 2026-04-30. CreateNoWindow + UseShellExecute=$false
        # is the canonical "run windowless and capture stdio" combination.
        $proc = $null
        try {
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName               = 'powershell.exe'
            $psi.Arguments              = '-NoProfile -ExecutionPolicy Bypass -File "' + $tempFile + '"'
            $psi.UseShellExecute        = $false
            $psi.CreateNoWindow         = $true
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError  = $true
            $proc = New-Object System.Diagnostics.Process
            $proc.StartInfo = $psi
            [void]$proc.Start()
            # Drain stdio asynchronously so a child writing >64KB of output
            # doesn't deadlock against the OS pipe buffer while we're still
            # blocked in WaitForExit (Microsoft's documented gotcha).
            $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
            $stderrTask = $proc.StandardError.ReadToEndAsync()
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
            # Start-Process -PassThru can leave ExitCode as $null even after
            # WaitForExit() returns true (Windows quirk: handle isn't kept open
            # for fast-exiting children unless the StartInfo enables it). The
            # process did terminate cleanly, so treat null as 0 -- and never
            # emit a non-int rc, since the host AgentClient parses it as int.
            $exitCode = $proc.ExitCode
            if ($null -eq $exitCode) { $rc = 0 } else { $rc = [int]$exitCode }
        }
        $stdoutText = ''
        $stderrText = ''
        # Pull from the async ReadToEndAsync tasks queued at spawn time.
        # On timeout the streams may already be closed by Kill(); guard each.
        try { if ($stdoutTask) { $stdoutText = $stdoutTask.GetAwaiter().GetResult() } } catch { }
        try { if ($stderrTask) { $stderrText = $stderrTask.GetAwaiter().GetResult() } } catch { }
        if ($timedOut -and -not $stderrText) { $stderrText = 'timeout' }
        return @{
            rc       = $rc
            stdout   = $stdoutText
            stderr   = $stderrText
            hash     = $hash
            timedOut = $timedOut
        }
    } finally {
        if ($tempFile -and (Test-Path $tempFile)) {
            try { Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue } catch { }
        }
    }
}

# Wait for the token before binding. /health is no-auth, but every
# other endpoint compares against $script:Token, so binding before the
# token lands would race the first auth check on a real /exec call.
$script:Token = Wait-Token

# Bind with a bounded retry loop. install.bat (#269 fix) reserves the
# urlacl for the World SID (S-1-1-0 / sddl WD) just before spawning the
# agent, but the agent's first spawn can race that reservation landing
# in HTTP.sys, and an autologon-retry session can re-spawn before the
# OS finished applying the ACL. A few short retries absorb that race
# without masking a genuine persistent conflict (which still ends in a
# FATAL + the full urlacl state dumped to agent.log so the real owner
# of the conflicting reservation is visible).
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($script:Prefix)
$bindAttempts = 5
$bound = $false
for ($i = 1; $i -le $bindAttempts; $i++) {
    try {
        $listener.Start()
        $bound = $true
        break
    } catch {
        $msg = $_.Exception.Message
        try {
            Add-Content -Path $script:LogPath -Value (
                "$((Get-Date).ToUniversalTime().ToString('o')) WARN " +
                "HttpListener.Start() attempt $i/$bindAttempts failed: $msg"
            ) -ErrorAction SilentlyContinue
        } catch { }
        if ($i -lt $bindAttempts) {
            Start-Sleep -Seconds 3
            # Re-create the listener -- a failed Start() can leave the
            # instance in a state that rejects a second Start().
            $listener = [System.Net.HttpListener]::new()
            $listener.Prefixes.Add($script:Prefix)
        }
    }
}
if (-not $bound) {
    # Persistent failure. Dump the actual urlacl reservation state so the
    # next debugging round sees WHICH SID owns the conflicting prefix --
    # the agent runs as a non-admin User and cannot re-register the ACL
    # itself (needs admin), so the fix has to land in install.bat.
    $aclState = ''
    try {
        $aclState = (& netsh http show urlacl url=$($script:Prefix) 2>&1 | Out-String).Trim()
    } catch { }
    try {
        Add-Content -Path $script:LogPath -Value (
            "$((Get-Date).ToUniversalTime().ToString('o')) FATAL " +
            "HttpListener.Start() failed after $bindAttempts attempts on $($script:Prefix)." +
            "  hint: install.bat should have reserved this via " +
            "'netsh http add urlacl url=$($script:Prefix) sddl=D:(A;;GX;;;WD)' as admin." +
            "  current urlacl state: $aclState"
        ) -ErrorAction SilentlyContinue
    } catch { }
    throw "HttpListener.Start() failed after $bindAttempts attempts on $($script:Prefix)"
}

# --- exec worker pool (#751) -----------------------------------------
# The accept loop below is single-threaded; before 0.2.3 a long /exec
# (WaitForExit up to 300s) blocked it entirely, so /health went
# unanswered, the host's 5s HEALTH_TIMEOUT expired, and dispatch
# declared "agent unavailable" on an agent that was merely busy --
# reported as the agent "repeatedly dying" in #751. Run each /exec in a
# background runspace instead: the worker owns the HttpListenerResponse
# (responding from another thread is supported) and the main loop keeps
# serving /health. Pool max 4 bounds concurrent guest PowerShell spawns;
# excess execs queue inside the pool, which still never blocks /health.
$iss = [initialsessionstate]::CreateDefault()
foreach ($fnName in @('Get-BytesHash', 'Write-Log', 'Send-Json', 'Invoke-ExecScript')) {
    $fnBody = (Get-Content -Path ("function:" + $fnName)).ToString()
    $iss.Commands.Add((New-Object System.Management.Automation.Runspaces.SessionStateFunctionEntry($fnName, $fnBody)))
}
# The functions above read these as $script:<name>; at a runspace's top
# level script scope IS global scope, so plain global entries satisfy them.
foreach ($varName in @('LogPath', 'RunsDir', 'ExecMaxTimeoutSec')) {
    $iss.Variables.Add((New-Object System.Management.Automation.Runspaces.SessionStateVariableEntry(
        $varName, (Get-Variable -Name $varName -Scope Script -ValueOnly), $null)))
}
$script:ExecPool = [runspacefactory]::CreateRunspacePool(1, 4, $iss, $Host)
$script:ExecPool.Open()
$script:ExecWorkers = [System.Collections.ArrayList]::new()

# Worker body: run the script, send the response, write the log line.
# Owns the response object end-to-end so the main loop never touches it
# again after handoff.
$script:ExecWorkerBody = {
    param($resp, $scriptB64, $timeoutSec)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $code = 500
    $extraLog = ''
    try {
        $result = Invoke-ExecScript -scriptB64 $scriptB64 -timeoutSec $timeoutSec
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
    } catch {
        try { Send-Json $resp 500 @{ error = 'internal_error' } } catch { }
        $code = 500
    }
    $sw.Stop()
    Write-Log 'POST' '/exec' 'ok' $code ([int]$sw.ElapsedMilliseconds) $extraLog
}

try {
    while ($listener.IsListening) {
        $ctx = $null
        try { $ctx = $listener.GetContext() } catch { continue }
        # Reap finished exec workers (EndInvoke + Dispose). Runs on every
        # request; the host polls /health continuously, so completed
        # workers never linger long.
        if ($script:ExecWorkers.Count -gt 0) {
            $done = @($script:ExecWorkers | Where-Object { $_.handle.IsCompleted })
            foreach ($w in $done) {
                try { [void]$w.ps.EndInvoke($w.handle) } catch { }
                try { $w.ps.Dispose() } catch { }
                $script:ExecWorkers.Remove($w)
            }
        }
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
                    # Hand off to a pool runspace (#751): the worker sends
                    # the response and writes the /exec log line; this loop
                    # goes straight back to GetContext so /health stays
                    # answerable during long execs. code -1 = skip the
                    # main-loop Write-Log below (worker owns it).
                    $ps = [powershell]::Create()
                    $ps.RunspacePool = $script:ExecPool
                    [void]$ps.AddScript($script:ExecWorkerBody).AddArgument($resp).AddArgument([string]$parsed.script).AddArgument($timeout)
                    [void]$script:ExecWorkers.Add(@{ ps = $ps; handle = $ps.BeginInvoke() })
                    $code = -1
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
        if ($code -ne -1) {
            Write-Log $method $path $authTag $code ([int]$sw.ElapsedMilliseconds) $extraLog
        }
    }
} finally {
    try { $listener.Stop() } catch { }
    try { $listener.Close() } catch { }
    try { $script:ExecPool.Close() } catch { }
    try { $script:ExecPool.Dispose() } catch { }
}
