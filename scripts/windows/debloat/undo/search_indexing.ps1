# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: re-enable WSearch service.

Write-Host "[search_indexing] Re-enabling WSearch (Automatic + start)..."
Set-Service -Name "WSearch" -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name "WSearch" -ErrorAction SilentlyContinue
