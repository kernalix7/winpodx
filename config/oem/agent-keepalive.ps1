# SPDX-License-Identifier: MIT
# agent-keepalive.ps1 -- idempotent "ensure the winpodx guest agent is
# running" watchdog, driven by the WinpodxAgentKeepAlive scheduled task.
#
# Problem this solves
# -------------------
# The agent's only autostart is an HKCU\Run entry, which fires exactly
# once per interactive logon. agent.ps1 runs as a child of the autologon
# interactive session. When that session is torn down -- e.g. RDP
# single-session enforcement kicks it when a FreeRDP connection arrives
# while rdprrap multi-session is not (yet) active, or a TermService cycle
# during rdprrap (re)activation -- the agent process dies with the
# session and HKCU\Run does NOT re-fire. The agent then stays dead until
# the pod reboots (re-running autologon -> HKCU\Run). Reproduced
# repeatedly: /health times out on a pod that has been up for hours;
# `pod restart` revives it.
#
# This script is the persistent watchdog HKCU\Run never was. The
# WinpodxAgentKeepAlive scheduled task runs it AtLogOn and every 1 minute
# indefinitely; each run is a cheap idempotent check:
#
#   * If an agent.ps1 process is already running AND :8765 is listening,
#     no-op. We NEVER kill a healthy agent -- this is a starter, not a
#     respawner (agent-respawn.ps1 stays the kill+relaunch path used by
#     the apply chain / guest-sync when the agent.ps1 *source* changed).
#   * Otherwise, (re)launch agent.ps1 via the existing
#     hidden-launcher.vbs wrapper so there is no PowerShell/console flash
#     -- the exact same windowless launch HKCU\Run / install.bat use.
#
# Principal
# ---------
# This script is registered to run as the INTERACTIVE autologon user
# (the same account the agent has always run as), NOT as SYSTEM / S4U.
# Reasoning (see PR fix/agent-keepalive):
#   * The agent's /exec runs PowerShell that callers expect in the
#     user's context: app discovery enumerates the user's Start Menu /
#     per-user installed apps, and reverse-open registers per-user HKCU
#     handlers. A SYSTEM / session-0 principal would silently change
#     HKCU and the Start Menu view out from under those callers.
#   * Keeping the agent in the user context means the World-SID urlacl
#     reservation (sddl WD) and the World-readable C:\OEM\agent_token.txt
#     remain reachable exactly as before -- no security or auth change.
# The cost: a user-context task only runs while an interactive session
# exists, so it covers (1) a crashed-but-session-alive agent (back within
# ~1 min via the repeating trigger) and (2) re-logon (AtLogOn trigger).
# It does NOT by itself cover a session kick with no re-logon -- that
# case is handled by making rdprrap activation idempotent so the kick
# does not happen in the first place (see _apply_multi_session +
# rdprrap-activate.ps1). The keep-alive's 1-minute repetition is also the
# backstop that brings the agent back AFTER a TermService cycle settles.

$ErrorActionPreference = 'SilentlyContinue'

$agentScript = 'C:\OEM\agent.ps1'
$launcher    = 'C:\Users\Public\winpodx\launchers\hidden-launcher.vbs'
$port        = 8765

# 1. Is an agent.ps1 process alive? Match the command line the same way
#    agent-respawn.ps1 does (classic powershell.exe + pwsh.exe), and
#    exclude this keep-alive script + the respawn helper so we never
#    treat our own process tree as "the agent".
function Test-AgentProcess {
    try {
        $procs = @(
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.CommandLine -and
                    $_.CommandLine -like '*agent.ps1*' -and
                    $_.CommandLine -notlike '*agent-keepalive*' -and
                    $_.CommandLine -notlike '*agent-respawn*' -and
                    ($_.Name -ieq 'powershell.exe' -or $_.Name -ieq 'pwsh.exe')
                }
        )
        return ($procs.Count -gt 0)
    } catch { return $false }
}

# 2. Is the listener actually up? A process can be alive but wedged before
#    HttpListener.Start() (e.g. mid Wait-Token); treat "process present
#    but port not listening" as healthy enough to leave alone for THIS
#    pass -- killing it is agent-respawn's job, not ours. We only launch
#    when there is NO agent process at all, OR the port is dead with no
#    owning agent process. The conservative rule: launch only when no
#    agent.ps1 process is running.
function Test-AgentPort {
    try {
        $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if ($conn) { return $true }
    } catch { }
    # Get-NetTCPConnection missing on very old builds -- fall back to netstat.
    try {
        $ns = netstat -ano | Select-String -Pattern (":" + $port + "\s.*LISTENING")
        if ($ns) { return $true }
    } catch { }
    return $false
}

$agentRunning = Test-AgentProcess
$portListening = Test-AgentPort

# Healthy: an agent process exists AND the port is listening. No-op.
if ($agentRunning -and $portListening) {
    exit 0
}

# A wedged-but-alive agent (process present, port not yet up) is left for
# its own bind-retry loop / agent-respawn; we don't double-launch on top
# of a live process (that would race two HttpListener binds on :8765).
if ($agentRunning) {
    exit 0
}

# 3. (#751) Double-launch guard. At logon this task's AtLogOn trigger
#    races HKCU\Run's own agent launch: the wscript -> powershell spawn
#    can take several seconds on a cold guest, so both can observe "no
#    agent" and each start one. The loser then fails HttpListener bind
#    5x against the winner and logs a FATAL that reads like the agent
#    crashed (seen verbatim in #751's agent.log). Wait out the spawn
#    window and re-check; only launch if the agent is still absent.
Start-Sleep -Seconds 10
if ((Test-AgentProcess) -or (Test-AgentPort)) {
    exit 0
}

# No agent process at all -> (re)launch via the windowless wrapper.
if (Test-Path -LiteralPath $launcher) {
    Start-Process wscript.exe -ArgumentList @(
        $launcher,
        'powershell.exe',
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $agentScript
    ) | Out-Null
} else {
    # Launcher missing (manual delete / pre-OEM-v14 pod that somehow lost
    # it). Direct hidden powershell flashes a ~50ms conhost, but a brief
    # flash beats a dead agent. The apply chain's vbs_launchers step
    # re-stages the wrapper, so this fallback is transient.
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $agentScript
    ) | Out-Null
}
exit 0
