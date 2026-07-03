# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: ads & suggestions (Start menu / lock screen / settings)

Write-Host "[ads] Restoring ContentDeliveryManager + advertising suggestions..."

$adValues = @(
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="ShowSyncProviderNotifications"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="Start_IrisRecommendations"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="Start_AccountNotifications"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement"; Name="ScoobeSystemSettingEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Privacy"; Name="TailoredExperiencesWithDiagnosticDataEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\Windows.ActionCenter.SmartOptOut"; Name="Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo"; Name="Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="RotatingLockScreenOverlayEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-338387Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-338393Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-353694Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-353696Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-338389Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-310093Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="ContentDeliveryAllowed"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="FeatureManagementEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="OemPreInstalledAppsEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="PreInstalledAppsEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="PreInstalledAppsEverEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContentEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-314563Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-338388Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-353698Enabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SilentInstalledAppsEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SystemPaneSuggestionsEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SoftLandingEnabled"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="RotatingLockScreenEnabled"}
)

foreach ($item in $adValues) {
    Remove-ItemProperty -Path $item.Path -Name $item.Name -Force -ErrorAction SilentlyContinue
}

# Advertising ID itself will be removed on either ad enable / disable, as an exception to the list
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo" -Name "Id" -Force -ErrorAction SilentlyContinue
