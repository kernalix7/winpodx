# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: Cortana + Start menu web search lookups

Write-Host "[web_search] Restoring Cortana policy..."
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search" -Name "AllowCortana" -Force -ErrorAction SilentlyContinue

Write-Host "[web_search] Restoring Bing web results in Windows Search..."
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search" -Name "BingSearchEnabled" -Force -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search" -Name "ConnectedSearchUseWeb" -Force -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKCU:\SOFTWARE\Policies\Microsoft\Windows\Explorer" -Name "DisableSearchBoxSuggestions" -Force -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Search" -Name "BingSearchEnabled" -Force -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Search" -Name "CortanaConsent" -Force -ErrorAction SilentlyContinue

Write-Host "[web_search] Restoring Edge desktop search widget bar..."
Remove-ItemProperty -Path "HKCU:\SOFTWARE\Policies\Microsoft\Edge" -Name "WebWidgetAllowed" -Force -ErrorAction SilentlyContinue
