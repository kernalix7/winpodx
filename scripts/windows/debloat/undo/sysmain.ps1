# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: SuperFetch (SysMain).

Write-Host "[sysmain] Re-enabling SysMain..."
Set-Service -Name "SysMain" -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name "SysMain" -ErrorAction SilentlyContinue
