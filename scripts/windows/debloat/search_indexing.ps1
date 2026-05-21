# SPDX-License-Identifier: MIT
# winpodx debloat: Windows Search indexing service.
#
# HIGH RISK item -- this is the *indexing* side of search, not the
# Cortana/Bing web result side (web_search.ps1). With WSearch off,
# Start menu app launches still work, but Start menu file search
# becomes file-system-walk slow. Keep on if you live in Start menu
# search; turn off if you live in alt-tab + File Explorer.

Write-Host "[search_indexing] Stopping + disabling WSearch service..."
Stop-Service -Name "WSearch" -Force -ErrorAction SilentlyContinue
Set-Service -Name "WSearch" -StartupType Disabled -ErrorAction SilentlyContinue
