# SPDX-License-Identifier: MIT
# winpodx debloat: SuperFetch (SysMain). No spinning disk in the VM ->
# SysMain's RAM-resident prefetch cache is wasted RAM that the guest
# could spend on actual workloads.

Write-Host "[sysmain] Stopping + disabling SysMain..."
Stop-Service -Name "SysMain" -Force -ErrorAction SilentlyContinue
Set-Service -Name "SysMain" -StartupType Disabled -ErrorAction SilentlyContinue
