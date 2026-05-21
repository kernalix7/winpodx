# SPDX-License-Identifier: MIT
# winpodx debloat: telemetry & diagnostics

Write-Host "[telemetry] Disabling AllowTelemetry policy..."
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection" -Name "AllowTelemetry" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\DataCollection" -Name "AllowTelemetry" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue

Write-Host "[telemetry] Stopping DiagTrack + dmwappushservice..."
Stop-Service -Name "DiagTrack" -Force -ErrorAction SilentlyContinue
Set-Service -Name "DiagTrack" -StartupType Disabled -ErrorAction SilentlyContinue
Stop-Service -Name "dmwappushservice" -Force -ErrorAction SilentlyContinue
Set-Service -Name "dmwappushservice" -StartupType Disabled -ErrorAction SilentlyContinue
