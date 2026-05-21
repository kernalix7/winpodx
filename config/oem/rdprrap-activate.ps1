# SPDX-License-Identifier: MIT
# rdprrap-activate.ps1 -- single source of truth for rdprrap activation,
# usable both at OEM Sysprep time (synchronous from install.bat) and at
# runtime (detached from `winpodx pod multi-session on`).
#
# Background: rdprrap activates by patching termsrv.dll's ServiceDll
# registry entry to point at termwrap.dll. The patch only takes effect
# when TermService starts fresh, so activation requires `net stop
# TermService /y && net start TermService`. That cycle kills every
# active RDP session.
#
# Two invocation modes:
#
# 1) OEM time (install.bat, console session):
#    powershell -NoProfile -ExecutionPolicy Bypass -File rdprrap-activate.ps1
#    -> no -Detached: skips the 2s parent-response wait, runs
#      synchronously, exits with the activation rc so install.bat can
#      branch on it. TermService cycle is safe -- install.bat runs from
#      FirstLogonCommands in the local console session and TermService
#      only manages RDP sessions, so the cycle doesn't tear down our
#      cmd.exe parent.
#
# 2) Runtime (cli.pod multi-session on, agent's user session):
#    wscript.exe hidden-launcher.vbs powershell.exe ... -Detached
#    -> -Detached: sleeps 2s before any work so the parent /exec
#      response can land at the host before TermService restart kills
#      the agent's RDP session. Subsequent run via HKCU\Run reads the
#      .activation_status marker -- same marker install.bat writes -- so
#      `winpodx pod multi-session status` and `apply-fixes` report
#      OEM-time and runtime activations through one surface.
#
# Requires the calling user to be in BUILTIN\Administrators with High
# integrity (true at OEM time, true under dockur's autologon defaults
# for the agent at runtime).

[CmdletBinding()]
param(
    [switch]$Detached
)

# Default ErrorActionPreference ('Continue') -- errors surface to the
# log instead of being silently swallowed. The few calls that genuinely
# tolerate failure (registry probes, log writes) opt in to
# -ErrorAction SilentlyContinue locally.
$ErrorActionPreference = 'Continue'

$rdprrapDir = 'C:\winpodx\rdprrap'
$logPath = "$rdprrapDir\install.log"
$statusPath = "$rdprrapDir\.activation_status"
$installer = "$rdprrapDir\rdprrap-installer.exe"

[void](New-Item -ItemType Directory -Path $rdprrapDir -Force -ErrorAction SilentlyContinue)

function Append-Log([string]$msg) {
    $line = "$((Get-Date).ToUniversalTime().ToString('o')) $msg"
    Add-Content -LiteralPath $logPath -Value $line -ErrorAction SilentlyContinue
}

function Set-Status([string]$value) {
    Set-Content -LiteralPath $statusPath -Value $value -Force -ErrorAction SilentlyContinue
}

# Detached mode (runtime via /exec): give the parent call ~2s to land
# its response at the host before we trigger TermService cycle that
# kills the agent's session. Synchronous mode (OEM install.bat): no
# such caller -- skip the wait.
if ($Detached) {
    Start-Sleep -Seconds 2
    Append-Log '=== runtime rdprrap activation triggered (detached) ==='
} else {
    Append-Log '=== rdprrap activation triggered (synchronous, OEM) ==='
}

# Extract the bundled zip if rdprrap-installer.exe isn't staged. Covers
# the case where install.bat's extract step failed at OEM time
# (extract-failed marker) -- we don't want activation to be impossible
# without container recreate.
if (-not (Test-Path -LiteralPath $installer)) {
    Append-Log 'rdprrap-installer.exe missing; attempting to extract from C:\OEM bundle'
    $zip = Get-ChildItem -LiteralPath 'C:\OEM' -Filter 'rdprrap-*.zip' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $zip) {
        Append-Log 'FAIL: no rdprrap-*.zip found under C:\OEM -- cannot proceed'
        Set-Status 'extract-failed'
        exit 1
    }
    try {
        Expand-Archive -LiteralPath $zip.FullName -DestinationPath $rdprrapDir -Force -ErrorAction Stop
        # Flatten an inner rdprrap-<version>/ folder if Expand-Archive
        # nested one (matches install.bat's flatten step).
        $inner = Get-ChildItem -LiteralPath $rdprrapDir -Directory -Filter 'rdprrap-*' |
            Select-Object -First 1
        if ($inner) {
            Get-ChildItem -LiteralPath $inner.FullName -Force |
                Move-Item -Destination $rdprrapDir -Force
            Remove-Item -LiteralPath $inner.FullName -Recurse -Force
        }
    } catch {
        Append-Log "FAIL: Expand-Archive: $($_.Exception.Message)"
        Set-Status 'extract-failed'
        exit 1
    }
    if (-not (Test-Path -LiteralPath $installer)) {
        Append-Log 'FAIL: rdprrap-installer.exe still missing after extract'
        Set-Status 'extract-failed'
        exit 1
    }
    Append-Log 'extract OK'
}

# Run the installer up to 3 times. Captures full stdout+stderr so a
# final installer-failed surfaces actionable diagnostics.
$installOk = $false
for ($i = 1; $i -le 3; $i++) {
    Append-Log "installer attempt $i"
    try {
        $output = & $installer install --skip-restart 2>&1
        Append-Log ($output | Out-String).TrimEnd()
        if ($LASTEXITCODE -eq 0) { $installOk = $true; break }
        Append-Log "installer attempt $i exit=$LASTEXITCODE"
    } catch {
        Append-Log "installer attempt $i raised: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 3
}
if (-not $installOk) {
    Set-Status 'installer-failed'
    Append-Log 'FINAL: installer-failed'
    exit 1
}

# OEM-mode (synchronous, no -Detached): SKIP the TermService cycle.
#
# Background: kernalix7 reported 2026-05-03 that install.bat's session
# was being killed mid-script. The forensics: install.log ended at
# `FINAL: enabled` (rdprrap-activate.ps1's last line) but the
# `(echo rdprrap-activate.ps1 exit=N)>>install.log` line that
# install.bat writes IMMEDIATELY after the PS call -- never landed.
# install.bat's cmd.exe died between PS exit and that line. Setup.log
# was never created, launchers never staged, HKCU\Run never set, agent
# never spawned -- the entire post-rdprrap section of install.bat was
# silently lost.
#
# Cause: in dockur's autologon flow, the Sysprep first-logon session
# IS managed through TermService. `net stop TermService /y` therefore
# kills the autologon User session, and install.bat's cmd.exe (a
# child of that session) dies with it. The earlier docstring assumed
# the session was a "local console" insulated from TermService -- that
# assumption was wrong for dockur's setup.
#
# Fix: at OEM time we ONLY patch the registry (rdprrap-installer
# already did that above with --skip-restart). The actual TermService
# cycle is deferred to install.bat's tail, AFTER all other setup
# (launcher staging, HKCU\Run, agent spawn, setup_done.txt) has
# committed -- so when the cycle eventually does kill install.bat,
# everything we needed to write is already on disk.
#
# Detached mode (-Detached, runtime path from `winpodx pod multi-
# session on`): cycle synchronously here. We're already a detached
# wscript-spawned process; the host's /exec response landed before
# we even started, so killing our session doesn't lose anything.

if (-not $Detached) {
    Append-Log 'OEM mode: skipping TermService cycle (install.bat will do it last)'
    Set-Status 'patched-pending-cycle'
    Append-Log 'FINAL: patched-pending-cycle (TermService cycle deferred to install.bat tail)'
    exit 0
}

Append-Log 'restarting TermService to load termwrap.dll'
$stopOut = & cmd.exe /c 'net stop TermService /y' 2>&1
Append-Log ($stopOut | Out-String).TrimEnd()
$startOut = & cmd.exe /c 'net start TermService' 2>&1
Append-Log ($startOut | Out-String).TrimEnd()

# Verify ServiceDll actually flipped.
Start-Sleep -Seconds 2
$svcDll = (Get-ItemProperty `
    -Path 'HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters' `
    -Name ServiceDll -ErrorAction SilentlyContinue).ServiceDll
Append-Log "ServiceDll=$svcDll"

if ($svcDll -match 'termwrap') {
    Set-Status 'enabled'
    Append-Log 'FINAL: enabled'
    exit 0
} else {
    Set-Status 'not-activated'
    Append-Log 'FINAL: not-activated (ServiceDll did not flip after TermService cycle)'
    exit 1
}
