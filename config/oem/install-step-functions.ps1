# =====================================================================
# install-step-functions.ps1 -- Phase 0/0.5/0.6/1/2/3 step bodies +
# orchestrator for the agent-first install state machine.
#
# Sourced by install.bat (after install-state-helpers.ps1) and by
# install-resume.ps1. Every Invoke-Step-<name> follows the contract
# documented in docs/design/AGENT_FIRST_INSTALL_DESIGN.md
# (§"Component contracts -> install.bat"):
#
#   1. If marker exists AND verify_post_condition_<name> succeeds -> skip.
#   2. If marker exists but post_condition fails -> log drift, delete
#      marker, fall through to a fresh run.
#   3. Verify preconditions (agent /health for steps after Phase 1).
#   4. Run step body.
#   5. Verify post_condition.
#   6. On post_condition fail -> Increment-WinpodxRetry, return non-zero.
#   7. On retries-exhausted (3) -> Write-WinpodxFailure, return non-zero.
#   8. On success -> New-WinpodxMarker.
#
# Functions exported (install.bat / install-resume.ps1 entry points):
#   * Invoke-InstallStateMachine            - runs all 10 steps in order
#   * Invoke-Step-defender_exclusion        - Phase 0
#   * Invoke-Step-state_dir_ready           - Phase 0.5
#   * Invoke-Step-token_staged              - Phase 0.6
#   * Invoke-Step-agent_ready               - Phase 1
#   * Invoke-Step-rdprrap_installed         - Phase 2
#   * Invoke-Step-vbs_launchers             - Phase 2
#   * Invoke-Step-oem_runtime_fixes         - Phase 2
#   * Invoke-Step-max_sessions              - Phase 2
#   * Invoke-Step-multi_session_active      - Phase 2
#   * Invoke-Step-install_complete          - Phase 3
#   * Start-WinpodxWatchdog                 - launch watchdog.ps1 detached
#
# Dependencies:
#   * install-state-helpers.ps1 already dot-sourced by the caller
#   * Helpers used: New-WinpodxMarker / Test-WinpodxMarker /
#     Initialize-WinpodxStateDir / Increment-WinpodxRetry /
#     Get-WinpodxRetry / Write-WinpodxLog / Write-WinpodxFailure /
#     Invoke-WinpodxRedact / $PHASE_ORDER
# =====================================================================

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ----- Module-scoped constants ---------------------------------------

$script:WpxStateDir       = 'C:\winpodx\install-state'
$script:WpxAgentDir       = 'C:\winpodx\agent'
$script:WpxOemDir         = 'C:\OEM'
$script:WpxOemAgentDir    = 'C:\OEM\agent'
$script:WpxLaunchersDir   = 'C:\Users\Public\winpodx\launchers'
$script:WpxRdprrapDir     = 'C:\winpodx\rdprrap'
$script:WpxAgentTokenSrc  = 'C:\OEM\agent\agent_token.txt'
$script:WpxAgentTokenDst  = 'C:\winpodx\agent\agent_token.txt'
$script:WpxAgentScriptSrc = 'C:\OEM\agent\agent.ps1'
$script:WpxAgentScriptDst = 'C:\winpodx\agent\agent.ps1'
$script:WpxWatchdogSrc    = 'C:\OEM\agent\watchdog.ps1'
$script:WpxWatchdogDst    = 'C:\winpodx\agent\watchdog.ps1'
# install-resume.ps1 stays in C:\OEM\ (dockur stages it natively at first
# boot); the Scheduled Task points there directly. No copy step needed.
$script:WpxResumePath     = 'C:\OEM\install-resume.ps1'
$script:WpxAgentHealthUrl = 'http://127.0.0.1:8765/health'
$script:WpxAgentExecUrl   = 'http://127.0.0.1:8765/exec'
$script:WpxMaxRetries     = 3
$script:WpxRetryBackoff   = @(5, 30, 90)

# Defender exclusion target paths + processes. Both Paths and Processes
# branches are required: Paths covers on-disk scans; Processes covers
# in-flight scans of the watchdog/agent process tree once spawned.
$script:WpxDefenderPaths = @(
    'C:\winpodx',
    'C:\winpodx\agent',
    'C:\OEM',
    'C:\OEM\agent'
)
$script:WpxDefenderProcs = @(
    'agent.ps1',
    'watchdog.ps1',
    'rdprrap-installer.exe'
)

# ----- Internal: agent HTTP helpers ----------------------------------

# Read the agent token from the staged copy (or the OEM source as
# fallback during pre-Phase-0.6 self-tests). Returns $null on failure.
function Get-WinpodxAgentToken {
    foreach ($p in @($script:WpxAgentTokenDst, $script:WpxAgentTokenSrc)) {
        if (Test-Path -LiteralPath $p) {
            try {
                $t = (Get-Content -Path $p -TotalCount 1 -ErrorAction Stop).Trim()
                if ($t) { return $t }
            } catch { }
        }
    }
    return $null
}

# GET /health -- no auth. $true if 200, $false otherwise. Bounded 5s.
function Test-WinpodxAgentHealth {
    try {
        $r = Invoke-WebRequest -Uri $script:WpxAgentHealthUrl `
            -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# Wait for /health to come back, polling every 2s up to $TimeoutSec.
# Returns $true on success, $false on timeout.
function Wait-WinpodxAgentHealth([int]$TimeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-WinpodxAgentHealth) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

# POST /exec with a base64-encoded PowerShell snippet. Returns a hashtable
# with keys: ok / rc / stdout / stderr (or ok=$false on transport / auth).
function Invoke-WinpodxAgentExec([string]$Script, [int]$TimeoutSec = 60) {
    $token = Get-WinpodxAgentToken
    if (-not $token) {
        return @{ ok = $false; rc = -1; stdout = ''; stderr = 'no_token' }
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($Script)
    $b64   = [Convert]::ToBase64String($bytes)
    $body  = ConvertTo-Json -Compress -Depth 4 @{ script = $b64; timeout_sec = $TimeoutSec }
    $headers = @{ Authorization = "Bearer $token" }
    try {
        $r = Invoke-RestMethod -Uri $script:WpxAgentExecUrl -Method Post `
            -Headers $headers -Body $body -ContentType 'application/json' `
            -TimeoutSec ([Math]::Max($TimeoutSec + 10, 15)) -ErrorAction Stop
        return @{
            ok     = $true
            rc     = [int]$r.rc
            stdout = [string]$r.stdout
            stderr = [string]$r.stderr
        }
    } catch {
        return @{ ok = $false; rc = -1; stdout = ''; stderr = $_.Exception.Message }
    }
}

# ----- Internal: step contract runner --------------------------------

# Generic step runner. Each Invoke-Step-<name> hands us its slug, phase,
# error class, the body scriptblock, the post-condition scriptblock, and
# (optionally) a precondition scriptblock. Implements the 8-point
# contract from the design doc.
function Invoke-WinpodxStep {
    param(
        [Parameter(Mandatory)] [string]      $Name,
        [Parameter(Mandatory)] [int]         $Phase,
        [Parameter(Mandatory)] [string]      $ErrorClass,
        [Parameter(Mandatory)] [scriptblock] $Body,
        [Parameter(Mandatory)] [scriptblock] $VerifyPostCondition,
        [scriptblock]                        $VerifyPreCondition = { $true }
    )

    Write-WinpodxLog -Level 'INFO' -Step $Name -Event 'start'

    # 1. Marker present + post-condition holds -> skip.
    if (Test-WinpodxMarker -Name $Name) {
        $postOk = $false
        try { $postOk = [bool](& $VerifyPostCondition) } catch { $postOk = $false }
        if ($postOk) {
            Write-WinpodxLog -Level 'INFO' -Step $Name -Event 'skip_marker'
            return 0
        }
        # 2. Drift -- delete marker, treat as fresh.
        Write-WinpodxLog -Level 'WARN' -Step $Name -Event 'drift' `
            -Extra @{ reason = 'post_condition_failed_with_marker' }
        try {
            Remove-Item -LiteralPath (Join-Path $script:WpxStateDir "$Name.done") `
                -Force -ErrorAction Stop
        } catch {
            Write-WinpodxLog -Level 'WARN' -Step $Name -Event 'drift_marker_delete_failed' `
                -Extra @{ detail = $_.Exception.Message }
        }
    }

    # 3. Pre-conditions (agent /health for the steps the orchestrator
    #    flagged as agent-gated; the gate itself is the scriptblock).
    $preOk = $false
    try { $preOk = [bool](& $VerifyPreCondition) } catch { $preOk = $false }
    if (-not $preOk) {
        Write-WinpodxLog -Level 'ERROR' -Step $Name -Event 'precondition_failed'
        return 1
    }

    # 4-7. Body + post-condition + retry loop.
    $attempt = (Get-WinpodxRetry -Name $Name) + 1
    Write-WinpodxLog -Level 'INFO' -Step $Name -Event 'run' -Extra @{ attempt = $attempt }

    $bodyRc = 0
    try {
        $bodyRc = [int](& $Body)
    } catch {
        Write-WinpodxLog -Level 'ERROR' -Step $Name -Event 'body_threw' `
            -Extra @{ detail = $_.Exception.Message }
        $bodyRc = 1
    }

    $postOk = $false
    if ($bodyRc -eq 0) {
        try { $postOk = [bool](& $VerifyPostCondition) } catch { $postOk = $false }
    }

    if (-not $postOk) {
        Write-WinpodxLog -Level 'ERROR' -Step $Name -Event 'postcondition_failed' `
            -Extra @{ attempt = $attempt; body_rc = $bodyRc }
        $newCount = Increment-WinpodxRetry -Name $Name
        if ($newCount -ge $script:WpxMaxRetries) {
            Write-WinpodxFailure `
                -Step $Name -Phase $Phase `
                -Attempt $newCount -MaxAttempts $script:WpxMaxRetries `
                -ExitCode $bodyRc `
                -ErrorClass $ErrorClass `
                -ErrorSummary "step '$Name' failed post-condition after $newCount attempts"
        }
        return 1
    }

    # 8. Success -- write marker.
    New-WinpodxMarker -Name $Name
    Write-WinpodxLog -Level 'INFO' -Step $Name -Event 'done'
    return 0
}

# ----- Step bodies ---------------------------------------------------

# --- Phase 0: defender_exclusion -------------------------------------

function Test-WinpodxDefenderExclusionPresent {
    # Read both Paths and Processes branches. Each value's data must be
    # present (registry stores values with empty-string default; non-zero
    # length indicates the exclusion was committed).
    $pathsKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows Defender\Exclusions\Paths'
    $procsKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows Defender\Exclusions\Processes'

    foreach ($p in $script:WpxDefenderPaths) {
        try {
            $v = (Get-ItemProperty -Path $pathsKey -Name $p -ErrorAction Stop).$p
        } catch {
            return $false
        }
        if ($null -eq $v) { return $false }
    }
    foreach ($pr in $script:WpxDefenderProcs) {
        try {
            $v = (Get-ItemProperty -Path $procsKey -Name $pr -ErrorAction Stop).$pr
        } catch {
            return $false
        }
        if ($null -eq $v) { return $false }
    }
    return $true
}

function Invoke-Step-defender_exclusion {
    Invoke-WinpodxStep `
        -Name 'defender_exclusion' -Phase 0 -ErrorClass 'defender_exclusion_failed' `
        -VerifyPostCondition {
            # Read-after-write succeeds AND, after a 60s pause, still
            # holds. The 60s window catches GPO sweeps that revert our
            # write moments after we make it (security review #8).
            if (-not (Test-WinpodxDefenderExclusionPresent)) { return $false }
            Start-Sleep -Seconds 60
            return (Test-WinpodxDefenderExclusionPresent)
        } `
        -Body {
            $pathsKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows Defender\Exclusions\Paths'
            $procsKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows Defender\Exclusions\Processes'

            New-Item -Path $pathsKey -Force -ErrorAction SilentlyContinue | Out-Null
            New-Item -Path $procsKey -Force -ErrorAction SilentlyContinue | Out-Null

            foreach ($p in $script:WpxDefenderPaths) {
                Set-ItemProperty -Path $pathsKey -Name $p -Value 0 -Type DWord -Force
            }
            foreach ($pr in $script:WpxDefenderProcs) {
                Set-ItemProperty -Path $procsKey -Name $pr -Value 0 -Type DWord -Force
            }

            # Belt + suspenders: also call Add-MpPreference so realtime
            # scanning honours the exclusion immediately, not only after
            # the next gpupdate cycle.
            try {
                Add-MpPreference -ExclusionPath $script:WpxDefenderPaths -ErrorAction SilentlyContinue
                Add-MpPreference -ExclusionProcess $script:WpxDefenderProcs -ErrorAction SilentlyContinue
            } catch { }
            return 0
        }
}

# --- Phase 0.5: state_dir_ready --------------------------------------

function Invoke-Step-state_dir_ready {
    Invoke-WinpodxStep `
        -Name 'state_dir_ready' -Phase 0 -ErrorClass 'state_dir_init_failed' `
        -VerifyPostCondition {
            (Test-Path -LiteralPath $script:WpxStateDir) -and `
            (Test-Path -LiteralPath (Join-Path $script:WpxStateDir 'install_session_id.txt'))
        } `
        -Body {
            Initialize-WinpodxStateDir
            # Mint install_session_id.txt on FRESH install only -- resume
            # reuses the prior ID per design doc resolved decision #8.
            $sidPath = Join-Path $script:WpxStateDir 'install_session_id.txt'
            if (-not (Test-Path -LiteralPath $sidPath)) {
                $sid = [guid]::NewGuid().ToString()
                Set-Content -LiteralPath $sidPath -Value $sid -Encoding ASCII -NoNewline
            }
            return 0
        }
}

# --- Phase 0.6: token_staged -----------------------------------------

function Invoke-Step-token_staged {
    Invoke-WinpodxStep `
        -Name 'token_staged' -Phase 0 -ErrorClass 'token_stage_failed' `
        -VerifyPostCondition {
            if (-not (Test-Path -LiteralPath $script:WpxAgentTokenDst)) { return $false }
            try {
                $t = (Get-Content -Path $script:WpxAgentTokenDst -TotalCount 1 -ErrorAction Stop).Trim()
            } catch { return $false }
            return [bool]$t
        } `
        -Body {
            if (-not (Test-Path -LiteralPath $script:WpxAgentTokenSrc)) {
                Write-WinpodxLog -Level 'ERROR' -Step 'token_staged' `
                    -Event 'src_missing' -Extra @{ src = $script:WpxAgentTokenSrc }
                return 1
            }
            $dstDir = Split-Path -Parent $script:WpxAgentTokenDst
            if (-not (Test-Path -LiteralPath $dstDir)) {
                New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
            }
            Copy-Item -LiteralPath $script:WpxAgentTokenSrc `
                -Destination $script:WpxAgentTokenDst -Force

            # User-only ACL: remove inherited, grant current user R+W,
            # deny everyone else implicitly. icacls is more reliable than
            # PS Acl APIs against Windows's odd default DACLs on copies.
            $user = "$env:USERDOMAIN\$env:USERNAME"
            if (-not $env:USERDOMAIN) { $user = $env:USERNAME }
            & icacls.exe $script:WpxAgentTokenDst /inheritance:r 2>&1 | Out-Null
            & icacls.exe $script:WpxAgentTokenDst /grant:r ("${user}:(R,W)") 2>&1 | Out-Null
            return 0
        }
}

# --- Phase 1: agent_ready --------------------------------------------

# Return $true when /health 200, bearer round-trip, and /exec smoke all
# pass. Used as the agent_ready post-condition AND as the precondition
# for every Phase-2 step.
function Test-WinpodxAgentReady {
    if (-not (Test-WinpodxAgentHealth)) { return $false }
    $r = Invoke-WinpodxAgentExec -Script "Write-Output 'ok'" -TimeoutSec 10
    if (-not $r.ok) { return $false }
    if ($r.rc -ne 0) { return $false }
    if ($r.stdout.Trim() -ne 'ok') { return $false }
    return $true
}

# Register the HKCU\Run watchdog entry. We register watchdog.ps1, not
# agent.ps1 directly: watchdog.ps1's first action on launch is to spawn
# agent.ps1 if it isn't already up. This way HKCU\Run gives us autostart
# AND respawn-on-crash with one entry.
function Register-WinpodxWatchdogAutostart {
    $key  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $name = 'WinpodxAgent'
    $cmd  = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
            $script:WpxWatchdogDst + '"'
    if (-not (Test-Path -LiteralPath $key)) {
        New-Item -Path $key -Force | Out-Null
    }
    Set-ItemProperty -Path $key -Name $name -Value $cmd -Force
}

# Register the install-resume Scheduled Task. logon trigger; the task
# itself is a no-op when install_failure.json is absent, so it's safe
# to leave registered after a successful install.
function Register-WinpodxResumeTask {
    $action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $script:WpxResumePath + '"')
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName 'winpodx-install-resume' -Action $action `
        -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

function Start-WinpodxAgent {
    if (-not (Test-Path -LiteralPath $script:WpxAgentScriptDst)) {
        Write-WinpodxLog -Level 'ERROR' -Step 'agent_ready' -Event 'agent_script_missing' `
            -Extra @{ path = $script:WpxAgentScriptDst }
        return 1
    }
    # Spawn detached so install.bat doesn't wait on the agent's event loop.
    Start-Process powershell.exe `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                        '-File', $script:WpxAgentScriptDst) `
        -WindowStyle Hidden | Out-Null
    return 0
}

function Start-WinpodxWatchdog {
    if (-not (Test-Path -LiteralPath $script:WpxWatchdogDst)) {
        Write-WinpodxLog -Level 'WARN' -Step 'agent_ready' -Event 'watchdog_script_missing' `
            -Extra @{ path = $script:WpxWatchdogDst }
        return 1
    }
    Start-Process powershell.exe `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                        '-File', $script:WpxWatchdogDst) `
        -WindowStyle Hidden | Out-Null
    return 0
}

function Invoke-Step-agent_ready {
    Invoke-WinpodxStep `
        -Name 'agent_ready' -Phase 1 -ErrorClass 'agent_self_test_failed' `
        -VerifyPostCondition {
            # Three-step self-test (design §10): /health 200, bearer
            # round-trip via Authorization header (Get-WinpodxAgentToken
            # would have failed if the token wasn't readable), and
            # /exec smoke returning rc=0 with stdout="ok".
            Test-WinpodxAgentReady
        } `
        -Body {
            # Stage agent.ps1 + watchdog.ps1 to C:\winpodx\agent\.
            if (-not (Test-Path -LiteralPath $script:WpxAgentDir)) {
                New-Item -ItemType Directory -Path $script:WpxAgentDir -Force | Out-Null
            }
            if (Test-Path -LiteralPath $script:WpxAgentScriptSrc) {
                Copy-Item -LiteralPath $script:WpxAgentScriptSrc `
                    -Destination $script:WpxAgentScriptDst -Force
            }
            if (Test-Path -LiteralPath $script:WpxWatchdogSrc) {
                Copy-Item -LiteralPath $script:WpxWatchdogSrc `
                    -Destination $script:WpxWatchdogDst -Force
            }
            # install-resume.ps1 is NOT copied -- dockur natively stages
            # C:\OEM\install-resume.ps1 and the Scheduled Task references
            # that path directly (matches design doc verbatim).

            # URL ACL for HttpListener prefix (agent binds http://+:8765/).
            & netsh.exe http delete urlacl url=http://+:8765/ 2>&1 | Out-Null
            & netsh.exe http add urlacl url=http://+:8765/ user=Everyone listen=yes 2>&1 | Out-Null

            # Firewall rule for the agent port.
            & netsh.exe advfirewall firewall delete rule name=winpodx-agent 2>&1 | Out-Null
            & netsh.exe advfirewall firewall add rule name=winpodx-agent dir=in `
                action=allow protocol=tcp localport=8765 2>&1 | Out-Null

            Register-WinpodxWatchdogAutostart
            Register-WinpodxResumeTask

            $rc = Start-WinpodxAgent
            if ($rc -ne 0) { return $rc }

            # Give the agent a moment to bind before the post-condition
            # self-test calls /health.
            if (-not (Wait-WinpodxAgentHealth -TimeoutSec 60)) {
                Write-WinpodxLog -Level 'ERROR' -Step 'agent_ready' -Event 'health_timeout'
                return 1
            }
            return 0
        }
}

# --- Phase 2 step factory -------------------------------------------

# All Phase-2 steps share the same precondition (agent /health up + exec
# round-trip) and run their body via /exec. This factory keeps the
# per-step bodies in one place.
function Invoke-WinpodxAgentStep {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $ErrorClass,
        [Parameter(Mandatory)] [string] $BodyScript,
        [Parameter(Mandatory)] [string] $VerifyScript,
        [int] $TimeoutSec = 120
    )

    Invoke-WinpodxStep `
        -Name $Name -Phase 2 -ErrorClass $ErrorClass `
        -VerifyPreCondition { Test-WinpodxAgentReady } `
        -VerifyPostCondition {
            $r = Invoke-WinpodxAgentExec -Script $VerifyScript -TimeoutSec 30
            if (-not $r.ok) { return $false }
            if ($r.rc -ne 0) { return $false }
            return ($r.stdout.Trim() -eq 'verified')
        } `
        -Body {
            $r = Invoke-WinpodxAgentExec -Script $BodyScript -TimeoutSec $TimeoutSec
            if (-not $r.ok) {
                Write-WinpodxLog -Level 'ERROR' -Step $Name -Event 'agent_exec_failed' `
                    -Extra @{ stderr = $r.stderr }
                return 1
            }
            if ($r.rc -ne 0) {
                Write-WinpodxLog -Level 'ERROR' -Step $Name -Event 'agent_exec_nonzero' `
                    -Extra @{ rc = $r.rc; stderr = $r.stderr }
                return $r.rc
            }
            return 0
        }
}

# --- Phase 2: rdprrap_installed --------------------------------------

function Invoke-Step-rdprrap_installed {
    # Body: extract bundled rdprrap zip with tar (sidesteps
    # Expand-Archive's Defender deadlock -- see install.bat history),
    # run the installer (which patches termsrv.dll). Activation /
    # TermService cycle stays decoupled (Phase 2 step
    # multi_session_active handles the cycle).
    $body = @'
$ErrorActionPreference = 'Stop'
$pin = 'C:\OEM\rdprrap_version.txt'
if (-not (Test-Path -LiteralPath $pin)) { Write-Output 'pin_missing'; exit 1 }
$cfg = @{}
foreach ($line in Get-Content -LiteralPath $pin) {
    if ($line -match '^(?<k>[a-zA-Z_]+)=(?<v>.+)$') { $cfg[$matches.k] = $matches.v.Trim() }
}
$ver = $cfg.version; $name = $cfg.filename; $sha = $cfg.sha256
if (-not $ver -or -not $name -or -not $sha) { Write-Output 'pin_incomplete'; exit 1 }
$src = "C:\OEM\$name"
if (-not (Test-Path -LiteralPath $src)) { Write-Output 'bundle_missing'; exit 1 }
$got = (Get-FileHash -LiteralPath $src -Algorithm SHA256).Hash
if ($got -ne $sha.ToUpperInvariant() -and $got -ne $sha.ToLowerInvariant() -and $got.ToLowerInvariant() -ne $sha.ToLowerInvariant()) {
    Write-Output "sha_mismatch:expected=$sha:got=$got"; exit 1
}
$dir = 'C:\winpodx\rdprrap'
if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
& "$env:SystemRoot\System32\tar.exe" -xf $src -C $dir
foreach ($d in Get-ChildItem -LiteralPath $dir -Directory -Filter 'rdprrap-*') {
    Copy-Item -Path (Join-Path $d.FullName '*') -Destination $dir -Recurse -Force
    Remove-Item -LiteralPath $d.FullName -Recurse -Force
}
$exe = Join-Path $dir 'rdprrap-installer.exe'
if (-not (Test-Path -LiteralPath $exe)) { Write-Output 'installer_missing'; exit 1 }
# Fire the installer (it patches termsrv.dll's ServiceDll). Synchronous;
# rc!=0 propagates upward through /exec.
$p = Start-Process -FilePath $exe -ArgumentList '/S' -Wait -PassThru -WindowStyle Hidden
if ($p.ExitCode -ne 0) { Write-Output ("installer_rc=" + $p.ExitCode); exit $p.ExitCode }
Set-Content -LiteralPath (Join-Path $dir '.installed_version') -Value $ver -Encoding ASCII
Write-Output 'installed'
exit 0
'@

    $verify = @'
$dir = 'C:\winpodx\rdprrap'
$pin = 'C:\OEM\rdprrap_version.txt'
$marker = Join-Path $dir '.installed_version'
if (-not (Test-Path -LiteralPath $marker)) { Write-Output 'no_marker'; exit 1 }
$cur = (Get-Content -LiteralPath $marker -TotalCount 1).Trim()
$expected = ''
if (Test-Path -LiteralPath $pin) {
    foreach ($line in Get-Content -LiteralPath $pin) {
        if ($line -match '^version=(?<v>.+)$') { $expected = $matches.v.Trim() }
    }
}
if ($cur -ne $expected) { Write-Output ("version_mismatch:" + $cur + ":" + $expected); exit 1 }
if (-not (Test-Path -LiteralPath (Join-Path $dir 'rdprrap-installer.exe'))) { Write-Output 'exe_missing'; exit 1 }
Write-Output 'verified'
exit 0
'@

    Invoke-WinpodxAgentStep -Name 'rdprrap_installed' `
        -ErrorClass 'rdprrap_install_failed' `
        -BodyScript $body -VerifyScript $verify -TimeoutSec 180
}

# --- Phase 2: vbs_launchers ------------------------------------------

function Invoke-Step-vbs_launchers {
    $body = @'
$ErrorActionPreference = 'Stop'
$dst = 'C:\Users\Public\winpodx\launchers'
if (-not (Test-Path -LiteralPath $dst)) { New-Item -ItemType Directory -Path $dst -Force | Out-Null }
$files = @(
    'hidden-launcher.vbs',
    'launch_uwp.vbs',
    'launch_uwp.ps1',
    'agent-respawn.ps1',
    'rdprrap-activate.ps1'
)
foreach ($f in $files) {
    $src = Join-Path 'C:\OEM' $f
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $dst $f) -Force
    }
}
Write-Output 'staged'
exit 0
'@

    $verify = @'
$dst = 'C:\Users\Public\winpodx\launchers'
$required = @('hidden-launcher.vbs', 'launch_uwp.vbs', 'launch_uwp.ps1')
foreach ($f in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $dst $f))) {
        Write-Output ("missing:" + $f); exit 1
    }
}
Write-Output 'verified'
exit 0
'@

    Invoke-WinpodxAgentStep -Name 'vbs_launchers' `
        -ErrorClass 'vbs_launcher_stage_failed' `
        -BodyScript $body -VerifyScript $verify -TimeoutSec 60
}

# --- Phase 2: oem_runtime_fixes --------------------------------------

function Invoke-Step-oem_runtime_fixes {
    # Idle / disconnect timeouts, NIC power-management, TermService
    # recovery actions. Pulled wholesale from the legacy install.bat
    # body -- same registry surface, just driven via /exec now.
    $body = @'
$ErrorActionPreference = 'Stop'
$tsKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'
$rdpKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
foreach ($k in @($tsKey, $rdpKey)) {
    if (-not (Test-Path -LiteralPath $k)) { New-Item -Path $k -Force | Out-Null }
}
Set-ItemProperty -Path $tsKey -Name 'MaxIdleTime'         -Value 0     -Type DWord -Force
Set-ItemProperty -Path $tsKey -Name 'MaxDisconnectionTime'-Value 30000 -Type DWord -Force
Set-ItemProperty -Path $tsKey -Name 'MaxConnectionTime'   -Value 0     -Type DWord -Force
Set-ItemProperty -Path $tsKey -Name 'KeepAliveEnable'     -Value 1     -Type DWord -Force
Set-ItemProperty -Path $tsKey -Name 'KeepAliveInterval'   -Value 1     -Type DWord -Force
Set-ItemProperty -Path $tsKey -Name 'fInheritInitialProgram' -Value 1 -Type DWord -Force
Set-ItemProperty -Path $rdpKey -Name 'MaxIdleTime'         -Value 0     -Type DWord -Force
Set-ItemProperty -Path $rdpKey -Name 'MaxDisconnectionTime'-Value 30000 -Type DWord -Force
Set-ItemProperty -Path $rdpKey -Name 'MaxConnectionTime'   -Value 0     -Type DWord -Force
Set-ItemProperty -Path $rdpKey -Name 'KeepAliveTimeout'    -Value 1     -Type DWord -Force
Set-ItemProperty -Path $rdpKey -Name 'UserAuthentication'  -Value 0     -Type DWord -Force
Set-ItemProperty -Path $rdpKey -Name 'SecurityLayer'       -Value 2     -Type DWord -Force
try {
    Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {$_.Status -ne 'Disabled'} |
        Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false -ErrorAction SilentlyContinue
} catch { }
& sc.exe failure TermService reset= 86400 actions= restart/5000/restart/5000/restart/5000 | Out-Null
Write-Output 'applied'
exit 0
'@

    $verify = @'
$tsKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'
try {
    $v = (Get-ItemProperty -Path $tsKey -Name 'MaxDisconnectionTime' -ErrorAction Stop).MaxDisconnectionTime
} catch { Write-Output 'missing_maxdisconnect'; exit 1 }
if ($v -ne 30000) { Write-Output ("bad_maxdisconnect:" + $v); exit 1 }
Write-Output 'verified'
exit 0
'@

    Invoke-WinpodxAgentStep -Name 'oem_runtime_fixes' `
        -ErrorClass 'oem_runtime_fixes_failed' `
        -BodyScript $body -VerifyScript $verify -TimeoutSec 60
}

# --- Phase 2: max_sessions -------------------------------------------

function Invoke-Step-max_sessions {
    $body = @'
$ErrorActionPreference = 'Stop'
$tsRoot = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
$rdpKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
foreach ($k in @($tsRoot, $rdpKey)) {
    if (-not (Test-Path -LiteralPath $k)) { New-Item -Path $k -Force | Out-Null }
}
Set-ItemProperty -Path $tsRoot -Name 'fDenyTSConnections'    -Value 0  -Type DWord -Force
Set-ItemProperty -Path $tsRoot -Name 'fSingleSessionPerUser' -Value 0  -Type DWord -Force
Set-ItemProperty -Path $rdpKey -Name 'MaxInstanceCount'      -Value 50 -Type DWord -Force
Write-Output 'applied'
exit 0
'@

    $verify = @'
$tsRoot = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
$rdpKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
try {
    $deny = (Get-ItemProperty -Path $tsRoot -Name 'fDenyTSConnections' -ErrorAction Stop).fDenyTSConnections
    $single = (Get-ItemProperty -Path $tsRoot -Name 'fSingleSessionPerUser' -ErrorAction Stop).fSingleSessionPerUser
    $maxc = (Get-ItemProperty -Path $rdpKey -Name 'MaxInstanceCount' -ErrorAction Stop).MaxInstanceCount
} catch { Write-Output 'read_failed'; exit 1 }
if ($deny -ne 0)   { Write-Output ("deny:" + $deny); exit 1 }
if ($single -ne 0) { Write-Output ("single:" + $single); exit 1 }
if ($maxc -lt 2)   { Write-Output ("maxc:" + $maxc); exit 1 }
Write-Output 'verified'
exit 0
'@

    Invoke-WinpodxAgentStep -Name 'max_sessions' `
        -ErrorClass 'max_sessions_failed' `
        -BodyScript $body -VerifyScript $verify -TimeoutSec 30
}

# --- Phase 2: multi_session_active -----------------------------------

function Invoke-Step-multi_session_active {
    # Special: TermService restart kills the agent process (it runs as
    # User in a session that loses its TermService backbone for a few
    # seconds). The watchdog respawns agent.ps1; this step just polls
    # /health back. The body returns 0 once /exec is reachable again
    # AND ServiceDll on TermService points at the patched DLL.
    Invoke-WinpodxStep `
        -Name 'multi_session_active' -Phase 2 -ErrorClass 'multi_session_activate_failed' `
        -VerifyPreCondition { Test-WinpodxAgentReady } `
        -VerifyPostCondition {
            # ServiceDll reads via /exec post-cycle. We re-check /health
            # first because the agent may still be respawning.
            if (-not (Wait-WinpodxAgentHealth -TimeoutSec 90)) { return $false }
            $verify = @'
$k = 'HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters'
try { $v = (Get-ItemProperty -Path $k -Name 'ServiceDll' -ErrorAction Stop).ServiceDll }
catch { Write-Output 'no_servicedll'; exit 1 }
if ($v -notmatch 'termwrap\.dll$') { Write-Output ("servicedll:" + $v); exit 1 }
Write-Output 'verified'
exit 0
'@
            $r = Invoke-WinpodxAgentExec -Script $verify -TimeoutSec 30
            return ($r.ok -and $r.rc -eq 0 -and $r.stdout.Trim() -eq 'verified')
        } `
        -Body {
            # Activation script (the existing rdprrap-activate.ps1) is
            # the source of truth. Run via /exec; agent will die when
            # TermService restarts; watchdog brings it back.
            $body = @'
$ErrorActionPreference = 'Continue'
$activate = 'C:\OEM\rdprrap-activate.ps1'
if (-not (Test-Path -LiteralPath $activate)) { Write-Output 'activate_missing'; exit 1 }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $activate
$rc = $LASTEXITCODE
Write-Output ("activate_rc=" + $rc)
exit $rc
'@
            # Fire-and-forget: we expect the agent to die during this.
            # Use a short timeout because /exec will likely return an
            # error when the agent's HttpListener gets torn down.
            $r = Invoke-WinpodxAgentExec -Script $body -TimeoutSec 30
            if ($r.ok -and $r.rc -ne 0) {
                Write-WinpodxLog -Level 'WARN' -Step 'multi_session_active' `
                    -Event 'activate_nonzero' -Extra @{ rc = $r.rc; stderr = $r.stderr }
            }
            # Either /exec returned cleanly or it died with the
            # TermService cycle. In both cases we now wait for /health.
            if (-not (Wait-WinpodxAgentHealth -TimeoutSec 90)) {
                Write-WinpodxLog -Level 'ERROR' -Step 'multi_session_active' `
                    -Event 'agent_did_not_recover'
                return 1
            }
            return 0
        }
}

# --- Phase 3: install_complete ---------------------------------------

function Invoke-Step-install_complete {
    # Token rotation + final marker. The new token is generated via
    # /exec, written to C:\winpodx\agent\agent_token.txt with the same
    # ACL as Phase 0.6, and the old C:\OEM\agent\agent_token.txt is
    # zeroed + deleted. The agent re-reads the staged token on next
    # cold start; in-process the old token still works until then,
    # which is fine since the install is complete and /exec falls
    # silent until the host explicitly rotates.
    Invoke-WinpodxStep `
        -Name 'install_complete' -Phase 3 -ErrorClass 'install_complete_failed' `
        -VerifyPreCondition { Test-WinpodxAgentReady } `
        -VerifyPostCondition {
            (Test-Path -LiteralPath (Join-Path $script:WpxStateDir 'install_complete.done')) -or `
            (Test-Path -LiteralPath $script:WpxAgentTokenDst)
        } `
        -Body {
            # Generate 32 random bytes -> base64 (44 chars). Use
            # RNGCryptoServiceProvider for cryptographic strength.
            $rng   = [System.Security.Cryptography.RandomNumberGenerator]::Create()
            $bytes = New-Object byte[] 32
            $rng.GetBytes($bytes)
            $newToken = [Convert]::ToBase64String($bytes)
            $rng.Dispose()

            # Write new token to staged location with User-only ACL.
            Set-Content -LiteralPath $script:WpxAgentTokenDst -Value $newToken -Encoding ASCII -NoNewline
            $user = "$env:USERDOMAIN\$env:USERNAME"
            if (-not $env:USERDOMAIN) { $user = $env:USERNAME }
            & icacls.exe $script:WpxAgentTokenDst /inheritance:r 2>&1 | Out-Null
            & icacls.exe $script:WpxAgentTokenDst /grant:r ("${user}:(R,W)") 2>&1 | Out-Null

            # Zero + delete the OEM-staged copy. Best-effort -- the
            # OEM bind mount may have made it read-only; in that case
            # the next pod restart will overwrite it.
            try {
                if (Test-Path -LiteralPath $script:WpxAgentTokenSrc) {
                    $sz = (Get-Item -LiteralPath $script:WpxAgentTokenSrc).Length
                    if ($sz -gt 0) {
                        $zeros = [byte[]]::new($sz)
                        [IO.File]::WriteAllBytes($script:WpxAgentTokenSrc, $zeros)
                    }
                    Remove-Item -LiteralPath $script:WpxAgentTokenSrc -Force -ErrorAction SilentlyContinue
                }
            } catch {
                Write-WinpodxLog -Level 'WARN' -Step 'install_complete' `
                    -Event 'oem_token_cleanup_failed' -Extra @{ detail = $_.Exception.Message }
            }
            return 0
        }
}

# ----- Orchestrator --------------------------------------------------

# Run all 10 steps in $PHASE_ORDER. Stops on first non-zero return,
# leaves install_failure.json in place. Returns the rc of the failing
# step (or 0 on full success).
function Invoke-InstallStateMachine {
    # Bootstrap the state dir before anything else, so marker reads /
    # log writes at Phase 0 (defender_exclusion) have somewhere to go.
    # Initialize-WinpodxStateDir is idempotent; calling it here AND in
    # Phase 0.5's body is intentional (Appendix B in the design doc).
    try {
        Initialize-WinpodxStateDir
    } catch {
        # If even the helper fails, we have no log surface. Fall back
        # to a bare write so smoke-test triage has something to grep.
        $ts = (Get-Date).ToUniversalTime().ToString('o')
        $bare = "$ts ERROR _orchestrator state_dir_bootstrap_failed detail=$($_.Exception.Message)"
        try {
            New-Item -ItemType Directory -Path $script:WpxStateDir -Force `
                -ErrorAction SilentlyContinue | Out-Null
            Add-Content -LiteralPath (Join-Path $script:WpxStateDir 'install.log') `
                -Value $bare -ErrorAction SilentlyContinue
        } catch { }
        return 1
    }

    Write-WinpodxLog -Level 'INFO' -Step '_orchestrator' -Event 'state_machine_start'

    foreach ($entry in $PHASE_ORDER) {
        $name = $entry.name
        $fn   = "Invoke-Step-$name"
        if (-not (Get-Command -Name $fn -ErrorAction SilentlyContinue)) {
            Write-WinpodxLog -Level 'ERROR' -Step '_orchestrator' `
                -Event 'missing_step_function' -Extra @{ fn = $fn }
            return 1
        }
        $rc = & $fn
        if ($rc -ne 0) {
            Write-WinpodxLog -Level 'ERROR' -Step '_orchestrator' `
                -Event 'step_failed' -Extra @{ step = $name; rc = $rc }

            # Special case: agent_ready ships the watchdog. If it
            # succeeded earlier in this run (post-condition holds) and
            # the failing step is Phase 2+, we leave the watchdog up so
            # the install-resume task has a chance to fix things.
            return $rc
        }

        # Right after agent_ready completes, fire the watchdog. The
        # watchdog itself talks to /health; agent must already be up.
        if ($name -eq 'agent_ready') {
            Start-WinpodxWatchdog | Out-Null
        }
    }

    Write-WinpodxLog -Level 'INFO' -Step '_orchestrator' -Event 'state_machine_done'
    return 0
}
