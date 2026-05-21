# SPDX-License-Identifier: MIT
# winpodx debloat: OneDrive uninstall + File Explorer integration removal

Write-Host "[onedrive] Stopping OneDrive..."
Stop-Process -Name "OneDrive" -Force -ErrorAction SilentlyContinue

Write-Host "[onedrive] Running OneDriveSetup uninstall..."
$setup = @(
    "$env:SystemRoot\System32\OneDriveSetup.exe",
    "$env:SystemRoot\SysWOW64\OneDriveSetup.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($setup) {
    Start-Process -FilePath $setup -ArgumentList "/uninstall" -NoNewWindow -Wait -ErrorAction SilentlyContinue
}

Write-Host "[onedrive] Removing File Explorer pane entries..."
$onedriveClsids = @(
    "HKCR:\CLSID\{018D5C66-4533-4307-9B53-224DE2ED1FE6}",
    "HKCR:\Wow6432Node\CLSID\{018D5C66-4533-4307-9B53-224DE2ED1FE6}"
)
foreach ($clsid in $onedriveClsids) {
    if (Test-Path $clsid) {
        Set-ItemProperty -Path $clsid -Name "System.IsPinnedToNameSpaceTree" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[onedrive] Removing scheduled autorun..."
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "OneDrive" -Force -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "OneDriveSetup" -Force -ErrorAction SilentlyContinue
