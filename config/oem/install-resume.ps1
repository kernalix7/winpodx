# install-resume.ps1 -- re-entry point for a guest install that died
# partway through. Two callers reach us:
#
#   (a) The host's `winpodx pod install-resume` posts /exec to the
#       agent with a Start-Process invocation of this script; the
#       agent is alive and the host can stream progress via marker
#       polling.
#   (b) The winpodx-install-resume Scheduled Task fires on user logon
#       (registered by install.bat Phase 1 via Register-WinpodxResumeTask
#       in install-step-functions.ps1) for the case where the agent
#       itself is dead and the host cannot /exec back in.
#
# Either way, the work is the same: re-enter Invoke-InstallStateMachine
# from install-step-functions.ps1. That orchestrator is idempotent --
# each Invoke-Step-<name> checks its .done marker + post-condition and
# either skips or re-runs the body. Drift detection (marker present
# but post-condition failing) is handled inside Invoke-WinpodxStep, so
# we don't duplicate the logic here.
#
# Idempotency contract:
#   - Healthy install (no install_failure.json) -> exit 0 silently. The
#     Scheduled Task fires on every user logon for the lifetime of the
#     pod; per-logon cost must be near-zero.
#   - Mid-install (Phase 0..3 partial) -> orchestrator picks up at the
#     first missing/drifted step and proceeds forward.
#   - Forced re-entry (WINPODX_RESUME_FORCE=1, set by host's
#     `pod install-resume --force`) -> bypass the silent-exit gate and
#     re-enter regardless of failure-file presence.

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

# --- self-check --------------------------------------------------------
#
# Key off install_failure.json (not install_complete.done absence). The
# Scheduled Task fires at every logon, including logons mid-install
# where install_complete.done has not yet been written -- that is the
# normal happy path during Phase 2 and we must not treat it as a
# failure trigger. Only an actual recorded failure justifies waking
# the orchestrator on logon.

$stateDir   = 'C:\winpodx\install-state'
$failureLog = Join-Path $stateDir 'install_failure.json'
$forced     = ($env:WINPODX_RESUME_FORCE -eq '1')

if (-not $forced) {
    if (-not (Test-Path -LiteralPath $failureLog)) {
        # Healthy or mid-install-without-recorded-failure: nothing to do.
        exit 0
    }
}

# --- module sourcing --------------------------------------------------
#
# Both modules ship in C:\OEM\ alongside this script (dockur lays
# config/oem/ contents into C:\OEM\). Helpers must be sourced first
# because the step-functions module references its primitives at parse
# time (Set-StrictMode + module-scoped constants).

$helpersPath = 'C:\OEM\install-state-helpers.ps1'
$stepsPath   = 'C:\OEM\install-step-functions.ps1'

foreach ($p in @($helpersPath, $stepsPath)) {
    if (-not (Test-Path -LiteralPath $p)) {
        # Cannot operate without the shared modules. Surface a single
        # line into the Windows event log so a forensic tail of the
        # pod can spot the missing-file case without needing the
        # install.log (which is also written by the helpers we just
        # failed to source).
        $msg = "install-resume: missing required module $p; cannot proceed"
        try {
            & eventcreate.exe /T ERROR /ID 1 /L APPLICATION `
                /SO winpodx-install-resume /D $msg 2>&1 | Out-Null
        } catch { }
        Write-Output $msg
        exit 2
    }
    . $p
}

# --- archive prior failure record before re-entering -------------------
#
# install-step-functions.ps1's Write-WinpodxFailure only writes
# install_failure.json on a NEW failure, so on a successful resume the
# old failure record would otherwise stay forever, polluting
# `winpodx pod install-status` output and re-firing the logon-task
# self-check forever. Move it aside under archive/ before re-entering.
# Failure during the move is non-fatal: the orchestrator will
# overwrite the file if a new failure happens, and a successful run
# will leave the stale file in place which is annoying but not broken.

$archiveDir = Join-Path $stateDir 'archive'
try {
    if (-not (Test-Path -LiteralPath $archiveDir)) {
        New-Item -ItemType Directory -Path $archiveDir -Force `
            -ErrorAction SilentlyContinue | Out-Null
    }
    if (Test-Path -LiteralPath $failureLog) {
        $stamp     = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
        $archived  = Join-Path $archiveDir "install_failure.$stamp.json"
        Move-Item -LiteralPath $failureLog -Destination $archived `
            -Force -ErrorAction SilentlyContinue
        Write-WinpodxLog -Level 'INFO' -Step '_resume' -Event 'failure_archived' `
            -Extra @{ archived = $archived }
    }
} catch {
    Write-WinpodxLog -Level 'WARN' -Step '_resume' -Event 'failure_archive_failed' `
        -Extra @{ detail = $_.Exception.Message }
}

# --- watchdog re-arm before re-entering --------------------------------
#
# If we got here via the Scheduled Task fallback, the agent is most
# likely dead (otherwise the host would have driven the resume via
# /exec instead). agent_ready's body will respawn the agent itself,
# but starting the watchdog up-front means subsequent agent deaths
# during Phase 2 get respawned without us having to wait for
# Invoke-Step-agent_ready to redo the watchdog autostart registration.
#
# Start-WinpodxWatchdog tolerates a missing watchdog.ps1 (returns 1
# with a logged warning), so this is safe to call before the
# orchestrator stages the watchdog file in agent_ready's body.

if (-not (Test-WinpodxAgentHealth)) {
    Start-WinpodxWatchdog | Out-Null
}

# --- delegate to the orchestrator -------------------------------------

Write-WinpodxLog -Level 'INFO' -Step '_resume' -Event 'resume_start' `
    -Extra @{ forced = $forced }

$rc = Invoke-InstallStateMachine

if ($rc -eq 0) {
    Write-WinpodxLog -Level 'INFO' -Step '_resume' -Event 'resume_complete'
} else {
    Write-WinpodxLog -Level 'ERROR' -Step '_resume' -Event 'resume_failed' `
        -Extra @{ exit_code = $rc }
}

exit $rc
