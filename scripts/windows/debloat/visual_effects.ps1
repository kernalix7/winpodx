# SPDX-License-Identifier: MIT
# winpodx debloat: visual effects -> "best performance" (sysguides recipe).
#
# Sets VisualFXSetting=2 (custom), then explicitly enables the three
# settings sysguides recommends keeping ON for readability:
#   * Show thumbnails instead of icons
#   * Show window contents while dragging
#   * Smooth edges of screen fonts
# Everything else (animations, fades, taskbar transitions) goes off.

Write-Host "[visual_effects] Setting VisualFXSetting -> 'custom'..."
New-Item -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" -Force -ErrorAction SilentlyContinue | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" -Name "VisualFXSetting" -Value 3 -Type DWord -Force -ErrorAction SilentlyContinue

Write-Host "[visual_effects] Disabling animations + taskbar transitions..."
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "UserPreferencesMask" -Value ([byte[]](0x90,0x12,0x03,0x80,0x10,0x00,0x00,0x00)) -Type Binary -Force -ErrorAction SilentlyContinue
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop\WindowMetrics" -Name "MinAnimate" -Value "0" -Type String -Force -ErrorAction SilentlyContinue

Write-Host "[visual_effects] Disabling taskbar animations..."
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "TaskbarAnimations" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue

Write-Host "[visual_effects] Disabling listview shadow + fade out..."
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "ListviewShadow" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "ListviewAlphaSelect" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
