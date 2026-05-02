# rdprrap-activate.ps1 — runtime rdprrap activation that survives the
# TermService restart it triggers.
#
# Background: rdprrap activates by patching termsrv.dll's ServiceDll
# registry entry to point at termwrap.dll. The patch only takes effect
# when TermService starts fresh, so activation requires `net stop
# TermService /y && net start TermService`. That cycle kills every
# active RDP session (including the agent's user session). Running it
# from agent /exec inline therefore breaks /exec mid-flight.
#
# This script is meant to be spawned DETACHED via wscript+
# hidden-launcher.vbs (same pattern agent-respawn.ps1 uses). The
# parent /exec response lands at the host before the detached runner
# kills the service. Status is recorded to the same on-disk markers
# install.bat (OEM v15+) writes, so subsequent `winpodx pod apply-
# fixes` calls report the runtime-activation outcome the same way
# they report OEM-time activation.
#
# Usage (typically from cli.pod multi-session enable):
#   wscript.exe C:\Users\Public\winpodx\launchers\hidden-launcher.vbs
#               powershell.exe -NoProfile -ExecutionPolicy Bypass
#               -File C:\Users\Public\winpodx\launchers\rdprrap-activate.ps1
#
# Requires the agent's User to be in BUILTIN\Administrators with High
# integrity — both true under dockur's autologon defaults.

$ErrorActionPreference = 'SilentlyContinue'

$rdprrapDir = 'C:\winpodx\rdprrap'
$logPath = "$rdprrapDir\install.log"
$statusPath = "$rdprrapDir\.activation_status"
$installer = "$rdprrapDir\rdprrap-installer.exe"

# Ensure the directory exists before any logging attempt.
[void](New-Item -ItemType Directory -Path $rdprrapDir -Force -ErrorAction SilentlyContinue)

function Append-Log([string]$msg) {
    $line = "$((Get-Date).ToUniversalTime().ToString('o')) $msg"
    Add-Content -LiteralPath $logPath -Value $line -ErrorAction SilentlyContinue
}

function Set-Status([string]$value) {
    Set-Content -LiteralPath $statusPath -Value $value -Force -ErrorAction SilentlyContinue
}

# Give the parent /exec call ~2s to deliver its response to the host
# before we start work that will eventually kill the agent's session.
Start-Sleep -Seconds 2

Append-Log '=== runtime rdprrap activation triggered ==='

# Extract the bundled zip if rdprrap-installer.exe isn't staged. Covers
# the case where install.bat's extract step failed at OEM time
# (extract-failed marker) — we don't want activation to be impossible
# without container recreate.
if (-not (Test-Path -LiteralPath $installer)) {
    Append-Log 'rdprrap-installer.exe missing; attempting to extract from C:\OEM bundle'
    $zip = Get-ChildItem -LiteralPath 'C:\OEM' -Filter 'rdprrap-*.zip' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $zip) {
        Append-Log 'FAIL: no rdprrap-*.zip found under C:\OEM — cannot proceed'
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

# Cycle TermService so the running TermService picks up the new
# ServiceDll. This will kill the agent's user session — but we're
# detached, so the host's caller already returned. The agent will
# auto-respawn on the next user logon (HKCU\Run, via wscript wrapper).
Append-Log 'restarting TermService to load termwrap.dll'
$stopOut = & cmd.exe /c 'net stop TermService /y' 2>&1
Append-Log ($stopOut | Out-String).TrimEnd()
$startOut = & cmd.exe /c 'net start TermService' 2>&1
Append-Log ($startOut | Out-String).TrimEnd()

# Verify ServiceDll actually flipped. install.bat's OEM-time path uses
# the same registry check; mirror it here for symmetry.
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
