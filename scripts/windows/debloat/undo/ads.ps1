# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: ads & suggestions

Write-Host "[ads] Restoring ContentDeliveryManager suggestions..."
$adKeys = @(
    "SubscribedContent-338388Enabled",
    "SubscribedContent-338389Enabled",
    "SubscribedContent-353698Enabled",
    "SilentInstalledAppsEnabled",
    "SystemPaneSuggestionsEnabled",
    "SoftLandingEnabled"
)
$path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
foreach ($name in $adKeys) {
    Remove-ItemProperty -Path $path -Name $name -Force -ErrorAction SilentlyContinue
}
