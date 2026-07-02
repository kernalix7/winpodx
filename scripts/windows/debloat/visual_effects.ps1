# SPDX-License-Identifier: MIT
# winpodx debloat: visual effects -> "best performance" and other tweaks for more responsiveness
#
# Sets VisualFXSetting=3 (custom), then explicitly enables five settings for better readability:
#   * Show thumbnails instead of icons
#   * Smooth edges of screen fonts
#   * Show shadows under windows (for better visibility of white windows when they are present all over the screen)
#   * Show translucent selection rectangle (it's inconsistent and applies only to the Desktop, not the rest of apps like File Explorer – imo it's better to have consistent rectangle for everything)
#   * Use drop shadows for icon labels on the desktop (for better visibility of desktop items with some light background applied)
#
# Everything else (animations, fades, taskbar transitions) goes off,
# including "Show window contents while dragging", because it is somehow disabled when Windows is installed (whatever dockur/windows is applying in the answer file)
# and to try to diagnose an issue, when windows with no control buttons / just small close one aren't normally draggable while in FreeRDP.

Write-Host "[visual_effects] Disabling animations + applying other responsive tweaks..."

$perfValues = @(
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects"; Name="VisualFXSetting"; Value=3},
	@{Path="HKCU:\Control Panel\Desktop"; Name="UserPreferencesMask"; Value=([byte[]](0x90,0x12,0x07,0x80,0x10,0x00,0x00,0x00))},
	@{Path="HKCU:\Control Panel\Desktop"; Name="FontSmoothing"; Value="2"},
	@{Path="HKCU:\Control Panel\Desktop"; Name="DragFullWindows"; Value="0"},
	@{Path="HKCU:\Control Panel\Desktop\WindowMetrics"; Name="MinAnimate"; Value="0"},
	@{Path="HKCU:\Software\Microsoft\Windows\DWM"; Name="EnableAeroPeek"; Value=0},
	@{Path="HKCU:\Software\Microsoft\Windows\DWM"; Name="AlwaysHibernateThumbnails"; Value=0},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="TaskbarAnimations"; Value=0},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="IconsOnly"; Value=0},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="ListviewShadow"; Value=1},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"; Name="ListviewAlphaSelect"; Value=1},
	
	# Misc.
	# Set shortest keyboard character repeat delay – really useful while typing!
    @{Path="HKCU:\Control Panel\Keyboard"; Name="KeyboardDelay"; Value="0"},
	
	# Accelerate previews appearance of taskbar items
	@{Path="HKCU:\Control Panel\Mouse"; Name="MouseHoverTime"; Value="20"},
	
	# Accelerate frequency of cursor flickering
	@{Path="HKCU:\Control Panel\Desktop"; Name="CursorBlinkRate"; Value="250"},
	
	# Remove delay of appearance of context menu
	@{Path="HKCU:\Control Panel\Desktop"; Name="MenuShowDelay"; Value="20"}
)

foreach ($item in $perfValues) {
    New-Item -Path $item.Path -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path $item.Path -Name $item.Name -Value $item.Value -Force -ErrorAction SilentlyContinue
}
