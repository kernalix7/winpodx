# find_teams.ps1 — Locate the "New Teams" (MSIX) executable on the guest.
#
# Why this exists:
#   data/apps/teams/app.toml points at the stable App Execution Alias at
#   %LOCALAPPDATA%\Microsoft\WindowsApps\ms-teams.exe, which Microsoft
#   installs for every user of the MSIX package. On some images that alias
#   is missing (Alias reset, MSIX not provisioned for the current user,
#   or Teams uninstalled by tenant policy). This script returns the real
#   executable path so winpodx can fall back gracefully.
#
# Usage (Windows-side):
#   powershell -NoProfile -ExecutionPolicy Bypass -File find_teams.ps1
#   -> prints the absolute path to stdout, exit 0 on success
#   -> prints nothing, exit 1 on failure
#
# Linux-side (future integration — see TODO below):
#   When app launch fails because the configured executable is missing,
#   winpodx.core.app could invoke this script over RDP/SSH to discover
#   the current path and update the in-memory AppInfo.executable before
#   retrying. Today this remains a manual diagnostic helper; see
#   data/apps/README.md and data/apps/teams/app.toml for the policy.

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
    # Get-AppxPackage can fail on Windows editions without Appx support —
    # fall through to where.exe.
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
    # where.exe absent (extremely rare) — fall through.
}

# 4. Classic Teams install path (pre-2024). Still found on some images that
#    haven't migrated to New Teams.
$classic = Join-Path $env:LOCALAPPDATA "Microsoft\Teams\current\Teams.exe"
if (Write-Path $classic) { exit 0 }

exit 1
