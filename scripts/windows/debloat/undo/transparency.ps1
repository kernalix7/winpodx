# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: window transparency / acrylic.

Write-Host "[transparency] Re-enabling transparency..."
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" -Name "EnableTransparency" -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
