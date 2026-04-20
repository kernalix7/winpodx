# find_teams.ps1 - Locate the New Teams (MSIX) executable on the guest.
# Prints absolute path to stdout (exit 0); prints nothing on failure (exit 1).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File find_teams.ps1

$ErrorActionPreference = "SilentlyContinue"

function Write-Path {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    Write-Output $Path
    return $true
}

# 1. Preferred: stable App Execution Alias installed by MSIX.
$alias = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\ms-teams.exe"
if (Write-Path $alias) { exit 0 }

# 2. Query the MSIX package directly (New Teams, 2024+).
try {
    $pkg = Get-AppxPackage -Name "MSTeams" -ErrorAction SilentlyContinue |
        Sort-Object -Property Version -Descending |
        Select-Object -First 1
    if ($pkg) {
        $candidate = Join-Path $pkg.InstallLocation "ms-teams.exe"
        if (Write-Path $candidate) { exit 0 }
        # Some builds ship the exe under a subdirectory.
        $fallback = Get-ChildItem -Path $pkg.InstallLocation -Filter "ms-teams.exe" `
            -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($fallback -and (Write-Path $fallback.FullName)) { exit 0 }
    }
} catch {
    # No Appx support on this edition; fall through.
}

# 3. Last resort: PATH lookup via where.exe (covers classic Teams installers
#    that drop ms-teams.exe into a PATH-visible directory).
try {
    $whereOutput = & where.exe ms-teams.exe 2>$null
    if ($LASTEXITCODE -eq 0 -and $whereOutput) {
        $first = ($whereOutput -split "`r?`n" | Where-Object { $_ })[0]
        if (Write-Path $first) { exit 0 }
    }
} catch {
    # where.exe absent; fall through.
}

# 4. Classic Teams install path (pre-2024).
$classic = Join-Path $env:LOCALAPPDATA "Microsoft\Teams\current\Teams.exe"
if (Write-Path $classic) { exit 0 }

exit 1
