# SPDX-License-Identifier: MIT
# winpodx debloat: visual effects -> "best performance" (sysguides recipe) plus
# a couple of RDP-friendly DWM tweaks.
#
# Sets VisualFXSetting=3 (custom), keeps thumbnails + font smoothing on for
# readability, and turns the rest (animations, fades, taskbar transitions, Aero
# Peek, saved-thumbnail caching) off so less is composited over the RDP link.

Write-Host "[visual_effects] Applying 'best performance' visual settings..."

$perfValues = @(
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"; Name="VisualFXSetting"; Value=3; Type="DWord"},
    @{Path="HKCU:\Control Panel\Desktop"; Name="UserPreferencesMask"; Value=([byte[]](0x90,0x12,0x03,0x80,0x10,0x00,0x00,0x00)); Type="Binary"},
    @{Path="HKCU:\Control Panel\Desktop"; Name="FontSmoothing"; Value="2"; Type="String"},
    @{Path="HKCU:\Control Panel\Desktop\WindowMetrics"; Name="MinAnimate"; Value="0"; Type="String"},
    @{Path="HKCU:\Software\Microsoft\Windows\DWM"; Name="EnableAeroPeek"; Value=0; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\DWM"; Name="AlwaysHibernateThumbnails"; Value=0; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="TaskbarAnimations"; Value=0; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="IconsOnly"; Value=0; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="ListviewShadow"; Value=0; Type="DWord"},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="ListviewAlphaSelect"; Value=0; Type="DWord"}
)

foreach ($item in $perfValues) {
    New-Item -Path $item.Path -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path $item.Path -Name $item.Name -Value $item.Value -Type $item.Type -Force -ErrorAction SilentlyContinue
}
