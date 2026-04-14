# Extract installed programs from Windows for winpodx app discovery
# Based on winapps ExtractPrograms.ps1

$outputDir = "C:\ProgramData\winpodx"
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

$programs = @()

# Registry paths for installed programs
$regPaths = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
)

foreach ($path in $regPaths) {
    $items = Get-ItemProperty $path -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -and $_.DisplayIcon }

    foreach ($item in $items) {
        $programs += [PSCustomObject]@{
            Name        = $item.DisplayName
            InstallPath = $item.InstallLocation
            Icon        = $item.DisplayIcon
            Publisher   = $item.Publisher
        }
    }
}

# Start Menu shortcuts
$startMenuPaths = @(
    "$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
)

$shortcuts = @()
foreach ($smPath in $startMenuPaths) {
    if (Test-Path $smPath) {
        $lnkFiles = Get-ChildItem -Path $smPath -Filter "*.lnk" -Recurse
        foreach ($lnk in $lnkFiles) {
            $shell = New-Object -ComObject WScript.Shell
            $shortcut = $shell.CreateShortcut($lnk.FullName)
            $shortcuts += [PSCustomObject]@{
                Name       = [System.IO.Path]::GetFileNameWithoutExtension($lnk.Name)
                TargetPath = $shortcut.TargetPath
                IconPath   = $shortcut.IconLocation
            }
        }
    }
}

# Export results
$programs | ConvertTo-Json -Depth 3 | Out-File "$outputDir\programs.json" -Encoding UTF8
$shortcuts | ConvertTo-Json -Depth 3 | Out-File "$outputDir\shortcuts.json" -Encoding UTF8

Write-Host "Exported $($programs.Count) programs and $($shortcuts.Count) shortcuts to $outputDir"
