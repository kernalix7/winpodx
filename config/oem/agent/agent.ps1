# =====================================================================
# winpodx guest agent (Phase 1) — minimal /health HTTP server
# =====================================================================
#
# Purpose
# -------
# Single-endpoint HTTP server that responds to GET /health so the host
# can detect when the Windows guest is fully booted and ready. Phase 1
# stays deliberately minimal: no auth, no /exec, no SSE. Phase 2 will
# add bearer auth + /exec on top of this same listener; later phases
# add streaming.
#
# Invariants
# ----------
#   * Bind: 127.0.0.1:8765 ONLY (loopback inside the Windows VM).
#     dockur's user-mode QEMU NAT forwards this to the Linux container's
#     loopback 8765, and compose's `127.0.0.1:8765:8765/tcp` forwards
#     that to the host's loopback. The listener is therefore reachable
#     ONLY from the host that owns the pod — see docs/AGENT_V2_DESIGN.md
#     "Architecture".
#   * /health takes NO authentication. It is the readiness signal; the
#     host needs to probe it before any token has been delivered.
#   * Wait-Token loop polls C:\OEM\agent_token.txt with bounded backoff,
#     never throws. Phase 1 doesn't *use* the token (no auth on /health,
#     no auth-gated endpoints yet) but the loop is wired in now so the
#     token is ready in $script:Token when Phase 2 turns auth on.
#     Anti-goal #6 in AGENT_V2_DESIGN: throwing kills the process and
#     HKCU\Run does not auto-restart.
# =====================================================================

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$script:AgentVersion = '0.2.2-rev1'
$script:StartedAt    = (Get-Date).ToUniversalTime().ToString('o')
$script:OemDir       = 'C:\OEM'
$script:TokenPath    = 'C:\OEM\agent_token.txt'
$script:LogPath      = 'C:\OEM\agent.log'
$script:Prefix       = 'http://127.0.0.1:8765/'

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

function Write-Log([string]$method, [string]$path, [int]$code, [int]$ms) {
    $ts = (Get-Date).ToUniversalTime().ToString('o')
    $line = "$ts $method $path code=$code ${ms}ms"
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

# Wait for the token before binding. Phase 1 does not consume it, but
# pre-loading keeps Phase 2's auth wiring a single line away.
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
        try {
            if ($method -eq 'GET' -and $path -eq '/health') {
                $payload = @{
                    version    = $script:AgentVersion
                    ok         = $true
                    started_at = $script:StartedAt
                }
                Send-Json $resp 200 $payload
                $code = 200
            } else {
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
        Write-Log $method $path $code ([int]$sw.ElapsedMilliseconds)
    }
} finally {
    try { $listener.Stop() } catch { }
    try { $listener.Close() } catch { }
}
