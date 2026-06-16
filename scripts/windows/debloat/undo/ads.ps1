# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: ads & suggestions

Write-Host "[ads] Restoring ContentDeliveryManager suggestions..."
$adKeys = @(
    "SubscribedContent-310093Enabled",
    "SubscribedContent-314563Enabled",
    "SubscribedContent-338387Enabled",
    "SubscribedContent-338388Enabled",
    "SubscribedContent-338389Enabled",
    "SubscribedContent-338393Enabled",
    "SubscribedContent-353694Enabled",
    "SubscribedContent-353696Enabled",
    "SubscribedContent-353698Enabled",
    "SilentInstalledAppsEnabled",
    "SystemPaneSuggestionsEnabled",
    "SoftLandingEnabled",
    "RotatingLockScreenEnabled",
    "RotatingLockScreenOverlayEnabled"
)
$path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
foreach ($name in $adKeys) {
    Remove-ItemProperty -Path $path -Name $name -Force -ErrorAction SilentlyContinue
}
