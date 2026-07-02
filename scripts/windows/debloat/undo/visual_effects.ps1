# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: visual effects -> "let Windows decide" and other tweaks for more responsiveness

Write-Host "[visual_effects] Restoring animations + other responsive tweaks..."

$perfValues = @(
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"; Name="VisualFXSetting"; Value=0},
	@{Path="HKCU:\Control Panel\Desktop"; Name="UserPreferencesMask"; Value=([byte[]](0x9E,0x1E,0x07,0x80,0x12,0x00,0x00,0x00))},
	@{Path="HKCU:\Control Panel\Desktop"; Name="FontSmoothing"; Value="2"},
	@{Path="HKCU:\Control Panel\Desktop"; Name="DragFullWindows"; Value="1"},
	@{Path="HKCU:\Control Panel\Desktop\WindowMetrics"; Name="MinAnimate"; Value="1"},
	@{Path="HKCU:\Software\Microsoft\Windows\DWM"; Name="EnableAeroPeek"; Value=1},
	@{Path="HKCU:\Software\Microsoft\Windows\DWM"; Name="AlwaysHibernateThumbnails"; Value=0},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="TaskbarAnimations"; Value=1},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="IconsOnly"; Value=0},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="ListviewShadow"; Value=1},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="ListviewAlphaSelect"; Value=1},

	# Misc.
    @{Path="HKCU:\Control Panel\Keyboard"; Name="KeyboardDelay"; Value="1"},
	@{Path="HKCU:\Control Panel\Mouse"; Name="MouseHoverTime"; Value="400"},
	@{Path="HKCU:\Control Panel\Desktop"; Name="CursorBlinkRate"; Value="530"},
	@{Path="HKCU:\Control Panel\Desktop"; Name="MenuShowDelay"; Value="400"}
)

foreach ($item in $perfValues) {
    New-Item -Path $item.Path -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path $item.Path -Name $item.Name -Value $item.Value -Force -ErrorAction SilentlyContinue
}
