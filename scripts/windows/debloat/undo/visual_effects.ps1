# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: visual effects -> Windows defaults ("let Windows decide").

Write-Host "[visual_effects] Restoring visual effects to Windows defaults..."

$perfValues = @(
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"; Name="VisualFXSetting"; Value=0; Type="DWord"},
    @{Path="HKCU:\Control Panel\Desktop"; Name="UserPreferencesMask"; Value=([byte[]](0x9E,0x1E,0x07,0x80,0x12,0x00,0x00,0x00)); Type="Binary"},
    @{Path="HKCU:\Control Panel\Desktop"; Name="FontSmoothing"; Value="2"; Type="String"},
    @{Path="HKCU:\Control Panel\Desktop\WindowMetrics"; Name="MinAnimate"; Value="1"; Type="String"},
    @{Path="HKCU:\Software\Microsoft\Windows\DWM"; Name="EnableAeroPeek"; Value=1; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\DWM"; Name="AlwaysHibernateThumbnails"; Value=1; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="TaskbarAnimations"; Value=1; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="IconsOnly"; Value=0; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="ListviewShadow"; Value=1; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="ListviewAlphaSelect"; Value=1; Type="DWord"}
)

foreach ($item in $perfValues) {
    New-Item -Path $item.Path -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path $item.Path -Name $item.Name -Value $item.Value -Type $item.Type -Force -ErrorAction SilentlyContinue
}
