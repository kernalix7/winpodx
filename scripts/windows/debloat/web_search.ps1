# SPDX-License-Identifier: MIT
# winpodx debloat: Cortana + Start menu web search lookups
#
# This is the *search results* side of the deal – the WSearch indexing
# service is a separate item (search_indexing.ps1). Disabling web
# search here keeps local app search working; it only stops Bing
# round-trips for every keystroke.

Write-Host "[web_search] Disabling Cortana + Start menu web search lookups..."

$webValues = @(
	# Disable Cortana policy
	@{Path="HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search"; Name="AllowCortana"; Value=0},
	
	# Disable Bing web results in Windows Search
	@{Path="HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search"; Name="BingSearchEnabled"; Value=0},
	@{Path="HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search"; Name="ConnectedSearchUseWeb"; Value=0},
	@{Path="HKCU:\SOFTWARE\Policies\Microsoft\Windows\Explorer"; Name="DisableSearchBoxSuggestions"; Value=1},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Search"; Name="BingSearchEnabled"; Value=0},
	@{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Search"; Name="CortanaConsent"; Value=0},
	
	# Disable Edge desktop search widget bar
	@{Path="HKCU:\SOFTWARE\Policies\Microsoft\Edge"; Name="WebWidgetAllowed"; Value=0}
)

foreach ($item in $webValues) {
    New-Item -Path $item.Path -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path $item.Path -Name $item.Name -Value $item.Value -Type DWord -Force -ErrorAction SilentlyContinue
}
