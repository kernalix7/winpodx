# =====================================================================
# winpodx guest agent — long-running HTTP server inside the Windows VM
# =====================================================================
#
# Purpose
# -------
# Replaces the per-call FreeRDP RemoteApp PowerShell channel for non-secret
# host -> guest traffic (registry applies, app discovery, log streaming).
# A single FreeRDP RemoteApp PowerShell call costs ~5-10s + a visible
# PowerShell window flash; an HTTP round-trip against this agent costs
# ~30-80ms with no UI artefacts.
#
# Threat model
# ------------
#   * Bind: 127.0.0.1:8765 ONLY (loopback inside the Windows VM). dockur's
#     user-mode QEMU NAT publishes it on the host's 127.0.0.1:8765, so the
#     listener is never reachable from anything other than the host that
#     owns the pod.
#   * Auth: bearer token read once at startup from C:\OEM\agent_token.txt
#     (single-line, hex). Every endpoint except /health requires
#     `Authorization: Bearer <token>`. Constant-time string compare.
#   * Transport: HTTP. We deliberately do NOT use HTTPS — there is no
#     external attacker on the loopback path, and a self-signed cert
#     would force the host to either pin or skip-verify, neither of
#     which adds real security here.
#   * Out of scope: password rotation / sync-password — those keep using
#     FreeRDP RemoteApp because they touch credentials we don't want
#     traversing this channel even on loopback.
#
# Endpoints
# ---------
#   GET  /health             (no auth)   liveness probe; tiny JSON
#   POST /exec               (auth)      run a base64-encoded ps1, 60s cap
#   GET  /events             (auth, SSE) tail the agent's internal queue
#   POST /apply/<step>       (auth, SSE) max_sessions|rdp_timeouts|oem|multi_session
#   POST /discover           (auth, SSE) run discover_apps.ps1
#
# Assumed environment (provided by install.bat, Task #31)
# -------------------------------------------------------
#   * C:\OEM\agent_token.txt    exists, single-line hex token
#   * C:\OEM\agent-runs\        writable directory
#   * C:\OEM\agent.log          appendable
#   * C:\OEM\discover_apps.ps1  the discovery script (host-streamed
#                               fallback: C:\Users\Public\winpodx\
#                               discover_apps.ps1)
#   * ExecutionPolicy           Bypass for the scheduled task user
# =====================================================================

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:AgentVersion = '0.2.2'
$script:StartedAt    = (Get-Date).ToUniversalTime()
$script:OemDir       = 'C:\OEM'
$script:TokenPath    = Join-Path $script:OemDir 'agent_token.txt'
$script:LogPath      = Join-Path $script:OemDir 'agent.log'
$script:RunsDir      = Join-Path $script:OemDir 'agent-runs'
$script:DiscoverCandidates = @(
    (Join-Path $script:OemDir 'discover_apps.ps1'),
    'C:\Users\Public\winpodx\discover_apps.ps1'
)
$script:Prefix       = 'http://127.0.0.1:8765/'   # loopback bind ONLY
$script:EventQueue   = [System.Collections.Concurrent.ConcurrentQueue[string]]::new()

if (-not (Test-Path $script:RunsDir)) { New-Item -ItemType Directory -Path $script:RunsDir -Force | Out-Null }

function Read-Token {
    if (-not (Test-Path $script:TokenPath)) { throw "agent_token.txt missing at $($script:TokenPath)" }
    $t = (Get-Content -Path $script:TokenPath -TotalCount 1 -ErrorAction Stop).Trim()
    if (-not $t) { throw "agent_token.txt is empty" }
    return $t
}
$script:Token = Read-Token

function Compare-Constant([string]$a, [string]$b) {
    if ($null -eq $a -or $null -eq $b) { return $false }
    if ($a.Length -ne $b.Length) { return $false }
    $diff = 0
    for ($i = 0; $i -lt $a.Length; $i++) { $diff = $diff -bor ([int][char]$a[$i] -bxor [int][char]$b[$i]) }
    return ($diff -eq 0)
}

function Test-Auth($req) {
    $h = $req.Headers['Authorization']
    if (-not $h -or -not $h.StartsWith('Bearer ')) { return $false }
    return (Compare-Constant -a $h.Substring(7) -b $script:Token)
}

function Write-Log([string]$method, [string]$path, [string]$auth, [int]$code, [int]$ms) {
    $ts = (Get-Date).ToUniversalTime().ToString('o')
    $line = "$ts $method $path auth=$auth code=$code ${ms}ms"
    try { Add-Content -Path $script:LogPath -Value $line -ErrorAction SilentlyContinue } catch { }
    $script:EventQueue.Enqueue((ConvertTo-Json -Compress @{ ts=$ts; level='info'; msg=$line }))
}

function Send-Json($resp, [int]$code, $obj) {
    $resp.StatusCode = $code
    $resp.ContentType = 'application/json; charset=utf-8'
    $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -Compress -Depth 6 $obj))
    $resp.ContentLength64 = $bytes.Length
    $resp.OutputStream.Write($bytes, 0, $bytes.Length)
    $resp.OutputStream.Close()
}

function Begin-Sse($resp) {
    $resp.StatusCode = 200
    $resp.SendChunked = $true
    $resp.ContentType = 'text/event-stream'
    $resp.Headers.Add('Cache-Control', 'no-cache')
    $resp.Headers.Add('Connection', 'keep-alive')
}

function Send-SseLine($resp, [string]$ev, [string]$data) {
    $sb = [Text.StringBuilder]::new()
    if ($ev) { [void]$sb.Append("event: $ev`n") }
    [void]$sb.Append("data: $data`n`n")
    $bytes = [Text.Encoding]::UTF8.GetBytes($sb.ToString())
    try {
        $resp.OutputStream.Write($bytes, 0, $bytes.Length)
        $resp.OutputStream.Flush()
        return $true
    } catch { return $false }
}

function Read-Body($req) {
    if (-not $req.HasEntityBody) { return '' }
    $sr = [IO.StreamReader]::new($req.InputStream, $req.ContentEncoding)
    try { return $sr.ReadToEnd() } finally { $sr.Dispose() }
}

function Run-Exec([string]$encodedCommand, [int]$timeoutSec = 60, [string]$transcript = $null) {
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = 'powershell.exe'
    $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -EncodedCommand $encodedCommand"
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow  = $true
    $proc = [Diagnostics.Process]::new()
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $finished = $proc.WaitForExit($timeoutSec * 1000)
    if (-not $finished) {
        try { $proc.Kill() } catch { }
        $result = @{ rc = 124; stdout = ''; stderr = 'timeout' }
    } else {
        $result = @{ rc = $proc.ExitCode; stdout = $proc.StandardOutput.ReadToEnd(); stderr = $proc.StandardError.ReadToEnd() }
    }
    if ($transcript) {
        try {
            "=== $((Get-Date).ToUniversalTime().ToString('o')) rc=$($result.rc) ===`n--- stdout ---`n$($result.stdout)`n--- stderr ---`n$($result.stderr)`n" |
                Add-Content -Path $transcript -ErrorAction SilentlyContinue
        } catch { }
    }
    return $result
}

function Get-ApplyPayload([string]$step) {
    switch ($step) {
        'max_sessions' {
            return @"
`$pTs  = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
`$pTcp = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
`$desired = 50
Set-ItemProperty -Path `$pTcp -Name MaxInstanceCount -Value `$desired -Type DWord -Force
Set-ItemProperty -Path `$pTs  -Name fSingleSessionPerUser -Value 0 -Type DWord -Force
Write-Output 'max_sessions applied'
"@
        }
        'rdp_timeouts' {
            return @"
`$mp = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'
if (-not (Test-Path `$mp)) { New-Item -Path `$mp -Force | Out-Null }
Set-ItemProperty -Path `$mp -Name MaxIdleTime -Value 0 -Type DWord -Force
Set-ItemProperty -Path `$mp -Name MaxDisconnectionTime -Value 30000 -Type DWord -Force
Set-ItemProperty -Path `$mp -Name MaxConnectionTime -Value 0 -Type DWord -Force
Set-ItemProperty -Path `$mp -Name KeepAliveEnable -Value 1 -Type DWord -Force
Set-ItemProperty -Path `$mp -Name KeepAliveInterval -Value 1 -Type DWord -Force
`$ws = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
Set-ItemProperty -Path `$ws -Name MaxIdleTime -Value 0 -Type DWord -Force
Set-ItemProperty -Path `$ws -Name MaxDisconnectionTime -Value 30000 -Type DWord -Force
Set-ItemProperty -Path `$ws -Name MaxConnectionTime -Value 0 -Type DWord -Force
Set-ItemProperty -Path `$ws -Name KeepAliveTimeout -Value 1 -Type DWord -Force
Write-Output 'rdp_timeouts applied'
"@
        }
        'oem' {
            return @"
`$ErrorActionPreference = 'Continue'
try {
    Get-NetAdapter -ErrorAction Stop | Where-Object { `$_.Status -ne 'Disabled' } | ForEach-Object {
        try { Set-NetAdapterPowerManagement -Name `$_.Name -AllowComputerToTurnOffDevice 'Disabled' -ErrorAction Stop } catch { }
    }
} catch { }
& sc.exe failure TermService reset= 86400 actions= restart/5000/restart/5000/restart/5000 | Out-Null
Write-Output 'oem applied'
"@
        }
        'multi_session' {
            return @"
`$rdprrap = `$null
foreach (`$p in @('C:\OEM\rdprrap\rdprrap-conf.exe','C:\OEM\rdprrap-conf.exe','C:\Program Files\rdprrap\rdprrap-conf.exe')) {
    if (-not `$rdprrap -and (Test-Path `$p)) { `$rdprrap = `$p }
}
if (-not `$rdprrap) { Write-Output 'rdprrap-conf not found; multi-session left disabled'; exit 0 }
& `$rdprrap --enable | Out-Null
Write-Output 'multi-session enabled'
"@
        }
        default { return $null }
    }
}

function Stream-Process($resp, [string]$file, [string[]]$args, [string]$transcript) {
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $file
    foreach ($a in $args) { [void]$psi.ArgumentList.Add($a) }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow  = $true
    $proc = [Diagnostics.Process]::new()
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $alive = $true
    while ($alive) {
        $line = $proc.StandardOutput.ReadLine()
        if ($null -eq $line) { $alive = $false; break }
        $payload = ConvertTo-Json -Compress @{ ts=(Get-Date).ToUniversalTime().ToString('o'); level='info'; msg=$line }
        if (-not (Send-SseLine $resp '' $payload)) { try { $proc.Kill() } catch { } ; break }
        if ($transcript) { try { Add-Content -Path $transcript -Value $line -ErrorAction SilentlyContinue } catch { } }
    }
    $proc.WaitForExit()
    $err = $proc.StandardError.ReadToEnd()
    if ($err -and $transcript) { try { Add-Content -Path $transcript -Value "[stderr] $err" -ErrorAction SilentlyContinue } catch { } }
    return $proc.ExitCode
}

# --- listener -----------------------------------------------------------
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($script:Prefix)
$listener.Start()
$script:EventQueue.Enqueue((ConvertTo-Json -Compress @{ ts=$script:StartedAt.ToString('o'); level='info'; msg="winpodx agent $($script:AgentVersion) listening on $($script:Prefix)" }))

while ($listener.IsListening) {
    $ctx = $null
    try { $ctx = $listener.GetContext() } catch { continue }
    $req = $ctx.Request; $resp = $ctx.Response
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $authOk = $false; $code = 200
    try {
        $path = $req.Url.AbsolutePath; $method = $req.HttpMethod

        if ($method -eq 'GET' -and $path -eq '/health') {
            $uptime = [int]((Get-Date).ToUniversalTime() - $script:StartedAt).TotalSeconds
            Send-Json $resp 200 @{ version=$script:AgentVersion; started=$script:StartedAt.ToString('o'); uptime=$uptime }
            $code = 200
        }
        else {
            $authOk = Test-Auth $req
            if (-not $authOk) { Send-Json $resp 401 @{ error='unauthorized' }; $code = 401 }
            elseif ($method -eq 'POST' -and $path -eq '/exec') {
                $body = Read-Body $req
                $obj = $null
                try { $obj = $body | ConvertFrom-Json } catch { }
                if ($null -eq $obj -or -not $obj.script) { Send-Json $resp 400 @{ error='missing script' }; $code = 400 }
                else {
                    $transcript = Join-Path $script:RunsDir ("exec-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
                    $r = Run-Exec -encodedCommand $obj.script -timeoutSec 60 -transcript $transcript
                    Send-Json $resp 200 $r; $code = 200
                }
            }
            elseif ($method -eq 'GET' -and $path -eq '/events') {
                Begin-Sse $resp
                $lastPing = Get-Date
                $open = $true
                while ($open) {
                    $msg = $null
                    if ($script:EventQueue.TryDequeue([ref]$msg)) {
                        $open = Send-SseLine $resp '' $msg
                    } else {
                        Start-Sleep -Milliseconds 200
                        if (((Get-Date) - $lastPing).TotalSeconds -ge 15) {
                            $open = Send-SseLine $resp '' (ConvertTo-Json -Compress @{ ts=(Get-Date).ToUniversalTime().ToString('o'); level='ping'; msg='keepalive' })
                            $lastPing = Get-Date
                        }
                    }
                }
                $code = 200
            }
            elseif ($method -eq 'POST' -and $path -like '/apply/*') {
                $step = $path.Substring(7)
                $payload = Get-ApplyPayload $step
                if (-not $payload) { Send-Json $resp 400 @{ error="unknown step: $step" }; $code = 400 }
                else {
                    Begin-Sse $resp
                    $transcript = Join-Path $script:RunsDir ("apply-$step-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
                    $bytes = [Text.Encoding]::Unicode.GetBytes($payload)
                    $enc = [Convert]::ToBase64String($bytes)
                    $r = Run-Exec -encodedCommand $enc -timeoutSec 60 -transcript $transcript
                    foreach ($line in ($r.stdout -split "`r?`n")) {
                        if ($line) { [void](Send-SseLine $resp '' (ConvertTo-Json -Compress @{ ts=(Get-Date).ToUniversalTime().ToString('o'); level='info'; msg=$line })) }
                    }
                    if ($r.stderr) {
                        foreach ($line in ($r.stderr -split "`r?`n")) {
                            if ($line) { [void](Send-SseLine $resp '' (ConvertTo-Json -Compress @{ ts=(Get-Date).ToUniversalTime().ToString('o'); level='error'; msg=$line })) }
                        }
                    }
                    [void](Send-SseLine $resp 'done' (ConvertTo-Json -Compress @{ rc=$r.rc }))
                    $code = 200
                }
            }
            elseif ($method -eq 'POST' -and $path -eq '/discover') {
                $script_path = $null
                foreach ($c in $script:DiscoverCandidates) { if (-not $script_path -and (Test-Path $c)) { $script_path = $c } }
                if (-not $script_path) { Send-Json $resp 500 @{ error='discover_apps.ps1 not found' }; $code = 500 }
                else {
                    Begin-Sse $resp
                    $transcript = Join-Path $script:RunsDir ("discover-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
                    $jsonOut = Join-Path $script:RunsDir ("discover-{0:yyyyMMdd-HHmmss}.json" -f (Get-Date))
                    $rc = Stream-Process $resp 'powershell.exe' @('-NoProfile','-ExecutionPolicy','Bypass','-File',$script_path,'-OutFile',$jsonOut) $transcript
                    [void](Send-SseLine $resp 'done' (ConvertTo-Json -Compress @{ rc=$rc; json_file=$jsonOut }))
                    $code = 200
                }
            }
            else { Send-Json $resp 404 @{ error='not found' }; $code = 404 }
        }
    } catch {
        try { Send-Json $resp 500 @{ error = $_.Exception.Message } } catch { }
        $code = 500
    } finally {
        $sw.Stop()
        try { Write-Log $req.HttpMethod $req.Url.AbsolutePath ([string]$authOk) $code ([int]$sw.ElapsedMilliseconds) } catch { }
        try { $resp.Close() } catch { }
    }
}
