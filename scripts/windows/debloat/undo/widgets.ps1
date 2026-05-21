# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: Widgets / news panel.

Write-Host "[widgets] Restoring widgets policy default..."
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Dsh" -Name "AllowNewsAndInterests" -Force -ErrorAction SilentlyContinue

Write-Host "[widgets] Restoring taskbar widgets icon..."
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" -Name "TaskbarDa" -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue
