# SPDX-License-Identifier: MIT
# agent-respawn.ps1 -- kill the running agent.ps1 process and spawn a fresh
# one via the hidden-launcher.vbs wrapper.
#
# Background: HKCU\Run only fires once per user session, so the new wscript
# wrapper installed by `_apply_vbs_launchers` doesn't take effect until the
# user logs out and back in (or the pod restarts). That's a hard ask for an
# apply-fixes / migrate step. This script lets the migration close the loop
# itself: kill the old agent (started under the legacy `powershell.exe
# -WindowStyle Hidden -File agent.ps1` invocation), wait for the listener
# port to free, then start the new agent under the wscript wrapper.
#
# Spawned by the migration's last action with
#
#   Start-Process wscript.exe -ArgumentList @(
#       'C:\Users\Public\winpodx\launchers\hidden-launcher.vbs',
#       'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
#       '-File', 'C:\Users\Public\winpodx\launchers\agent-respawn.ps1'
#   )
#
# so this script itself runs windowless under wscript+hidden-launcher.

$ErrorActionPreference = 'SilentlyContinue'

# Give the parent /exec call ~3s to finish delivering its response to the
# host. Killing the agent before its /exec reply lands would surface as a
# spurious "channel failure" on `winpodx pod apply-fixes`.
Start-Sleep -Seconds 3

$ourPid = $PID

# Find the running agent. Match on the cmdline mentioning agent.ps1 and
# exclude this respawn script to avoid suicide. Both classic powershell.exe
# and the newer pwsh.exe count.
$victims = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $ourPid -and
            $_.CommandLine -and
            $_.CommandLine -like '*agent.ps1*' -and
            $_.CommandLine -notlike '*agent-respawn*' -and
            ($_.Name -ieq 'powershell.exe' -or $_.Name -ieq 'pwsh.exe')
        }
)

foreach ($v in $victims) {
    try { Stop-Process -Id $v.ProcessId -Force -ErrorAction SilentlyContinue } catch { }
}

# Wait briefly for port 8765 to release. HttpListener doesn't always release
# immediately -- give the kernel a beat.
Start-Sleep -Milliseconds 800

$launcher = 'C:\Users\Public\winpodx\launchers\hidden-launcher.vbs'
if (-not (Test-Path -LiteralPath $launcher)) {
    # No hidden-launcher.vbs available -> we cannot respawn cleanly. The
    # legacy `Start-Process powershell.exe -WindowStyle Hidden` fallback
    # used to live here, but it leaks a ~50ms conhost flash AND only
    # fires in scenarios where the wrapper file went missing -- which
    # post-OEM-v13 means a manual delete or filesystem corruption, not
    # a normal install. Better to fail loudly: HKCU\Run will refire
    # (via wscript+hidden-launcher.vbs) on the next user logon and
    # bring the agent back without the flash.
    exit 1
}
# wscript.exe is GUI-subsystem so the spawned PowerShell child starts
# windowless from the very first instant. No flash.
Start-Process wscript.exe -ArgumentList @(
    $launcher,
    'powershell.exe',
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', 'C:\OEM\agent.ps1'
) | Out-Null
