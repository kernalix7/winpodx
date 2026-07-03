# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: Cortana + Start menu web search lookups

Write-Host "[web_search] Restoring Cortana + Start menu web search lookups..."

$webValues = @(
  @{Path="HKLM:\Software\Policies\Microsoft\Windows\Windows Search"; Name="AllowCortana"},
  @{Path="HKLM:\Software\Policies\Microsoft\Windows\Windows Search"; Name="BingSearchEnabled"},
  @{Path="HKLM:\Software\Policies\Microsoft\Windows\Windows Search"; Name="ConnectedSearchUseWeb"},
  @{Path="HKCU:\Software\Policies\Microsoft\Windows\Explorer"; Name="DisableSearchBoxSuggestions"},
  @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Search"; Name="BingSearchEnabled"},
  @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\Search"; Name="CortanaConsent"},
  @{Path="HKCU:\Software\Policies\Microsoft\Edge"; Name="WebWidgetAllowed"}
)

foreach ($item in $webValues) {
  Remove-ItemProperty -Path $item.Path -Name $item.Name -Force -ErrorAction SilentlyContinue
}
