# winpodx - Windows Update toggle
# Usage: toggle_updates.ps1 -Action enable|disable|status
# Runs inside the Windows container only.

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("enable", "disable", "status")]
    [string]$Action
)

$ErrorActionPreference = "SilentlyContinue"

$Services = @("wuauserv", "UsoSvc", "WaaSMedicSvc")

function Get-UpdateStatus {
    $wuauserv = Get-Service wuauserv -ErrorAction SilentlyContinue
    if ($wuauserv -and $wuauserv.StartType -ne "Disabled") {
        Write-Host "enabled"
    } else {
        Write-Host "disabled"
    }
}

function Disable-Updates {
    Write-Host "[winpodx] Disabling Windows Update..."

    foreach ($svc in $Services) {
        Stop-Service $svc -Force -ErrorAction SilentlyContinue
        Set-Service $svc -StartupType Disabled -ErrorAction SilentlyContinue
    }

    # Block update-related scheduled tasks
    $tasks = @(
        "\Microsoft\Windows\WindowsUpdate\Scheduled Start"
        "\Microsoft\Windows\UpdateOrchestrator\Schedule Scan"
        "\Microsoft\Windows\UpdateOrchestrator\USO_UxBroker"
    )
    foreach ($task in $tasks) {
        schtasks /Change /TN $task /Disable 2>$null
    }

    # Block Windows Update domains via hosts file (belt and suspenders)
    $hostsFile = "C:\Windows\System32\drivers\etc\hosts"
    $marker = "# winpodx-update-block"
    $blockEntries = @(
        "$marker"
        "0.0.0.0 update.microsoft.com"
        "0.0.0.0 windowsupdate.microsoft.com"
        "0.0.0.0 download.windowsupdate.com"
        "0.0.0.0 dl.delivery.mp.microsoft.com"
        "0.0.0.0 ctldl.windowsupdate.com"
        "$marker-end"
    )

    # Remove existing block if present, then add
    $content = Get-Content $hostsFile -ErrorAction SilentlyContinue | Where-Object {
        $_ -notmatch "winpodx-update-block" -and
        $_ -notmatch "^0\.0\.0\.0.*(update\.microsoft|windowsupdate|dl\.delivery)"
    }
    $content += $blockEntries
    Set-Content $hostsFile $content -Force

    Write-Host "[winpodx] Windows Update disabled"
}

function Enable-Updates {
    Write-Host "[winpodx] Enabling Windows Update..."

    foreach ($svc in $Services) {
        Set-Service $svc -StartupType Manual -ErrorAction SilentlyContinue
        Start-Service $svc -ErrorAction SilentlyContinue
    }

    # Re-enable scheduled tasks
    $tasks = @(
        "\Microsoft\Windows\WindowsUpdate\Scheduled Start"
        "\Microsoft\Windows\UpdateOrchestrator\Schedule Scan"
        "\Microsoft\Windows\UpdateOrchestrator\USO_UxBroker"
    )
    foreach ($task in $tasks) {
        schtasks /Change /TN $task /Enable 2>$null
    }

    # Remove hosts file block
    $hostsFile = "C:\Windows\System32\drivers\etc\hosts"
    $content = Get-Content $hostsFile -ErrorAction SilentlyContinue | Where-Object {
        $_ -notmatch "winpodx-update-block" -and
        $_ -notmatch "^0\.0\.0\.0.*(update\.microsoft|windowsupdate|dl\.delivery)"
    }
    Set-Content $hostsFile $content -Force

    Write-Host "[winpodx] Windows Update enabled"
}

switch ($Action) {
    "enable"  { Enable-Updates }
    "disable" { Disable-Updates }
    "status"  { Get-UpdateStatus }
}
