# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: telemetry & diagnostics

Write-Host "[telemetry] Restoring AllowTelemetry policy to default..."
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection" -Name "AllowTelemetry" -Force -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\DataCollection" -Name "AllowTelemetry" -Force -ErrorAction SilentlyContinue

Write-Host "[telemetry] Re-enabling DiagTrack + dmwappushservice..."
Set-Service -Name "DiagTrack" -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name "DiagTrack" -ErrorAction SilentlyContinue
Set-Service -Name "dmwappushservice" -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name "dmwappushservice" -ErrorAction SilentlyContinue
