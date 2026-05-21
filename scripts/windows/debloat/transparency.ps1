# SPDX-License-Identifier: MIT
# winpodx debloat: window transparency / acrylic effects.
#
# Saves GPU + RDP scanout bandwidth. Cosmetic-only change; users on
# capable host GPUs may prefer to keep this on.

Write-Host "[transparency] Disabling transparency / acrylic..."
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" -Name "EnableTransparency" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
