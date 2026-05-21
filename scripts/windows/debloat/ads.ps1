# SPDX-License-Identifier: MIT
# winpodx debloat: ads & suggestions (Start menu / lock screen / settings)

Write-Host "[ads] Disabling ContentDeliveryManager suggestion keys..."
$adKeys = @(
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-338388Enabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-338389Enabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-353698Enabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SilentInstalledAppsEnabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SystemPaneSuggestionsEnabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SoftLandingEnabled"; Value=0}
)
foreach ($key in $adKeys) {
    New-Item -Path $key.Path -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path $key.Path -Name $key.Name -Value $key.Value -Type DWord -Force -ErrorAction SilentlyContinue
}
