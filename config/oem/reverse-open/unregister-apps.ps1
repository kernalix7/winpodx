# =====================================================================
# winpodx reverse-open — remove the Linux app handlers from Windows.
#
# Mirrors register-apps.ps1's per-app .exe hard-link scheme:
#   * Strip winpodx-<slug>.exe entries from every <ext>\OpenWithList
#     subkey under HKCU\Software\Classes
#   * Remove HKCU\Software\Classes\Applications\winpodx-<slug>.exe
#   * Delete the matching .exe hard links from $BinDir (and the
#     source shim binary)
#
# Legacy scrub: earlier revisions of register-apps.ps1 used .cmd or
# .vbs wrappers, or registered winpodx-<slug> ProgIDs under
# HKCU\Software\Classes\winpodx-<slug> with OpenWithProgids
# attachments. This script also walks + removes those so users who
# hit any prior revision don't end up with orphans.
#
# Idempotent: missing keys / missing values / missing files are
# silently OK.
# =====================================================================

[CmdletBinding()]
param(
    [string]$BinDir = 'C:\Users\Public\winpodx\reverse-open\bin',
    [string]$StartMenuDir = $(Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Linux Apps'),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Write-LogLine([string]$Level, [string]$Msg) {
    $ts = (Get-Date).ToUniversalTime().ToString('o')
    Write-Host "$ts [$Level] $Msg"
}

$classesRoot = 'HKCU:\Software\Classes'
if (-not (Test-Path -LiteralPath $classesRoot)) {
    Write-LogLine 'INFO' 'no HKCU classes root — nothing to clean'
    exit 0
}

# --- legacy ProgIDs (pre-fix revision) ---
# Pre-PR-#164 builds registered winpodx-<slug> as a bare ProgID directly
# under HKCU\Software\Classes. We exclude any winpodx-*.exe / .cmd /
# .vbs children here because those belong to the Applications\
# scrubber (or to the legacy wrapper file scrub below).
$legacyProgIds = @()
try {
    $legacyProgIds = Get-ChildItem -LiteralPath $classesRoot -ErrorAction Stop |
        Where-Object {
            $_.PSChildName -like 'winpodx-*' -and
            $_.PSChildName -notlike '*.exe' -and
            $_.PSChildName -notlike '*.cmd' -and
            $_.PSChildName -notlike '*.vbs'
        } |
        Select-Object -ExpandProperty PSChildName
} catch {
    Write-LogLine 'WARN' "enumerate legacy ProgIDs failed: $($_.Exception.Message)"
}
$removedLegacy = 0
foreach ($progId in $legacyProgIds) {
    $progRoot = Join-Path $classesRoot $progId
    if ($DryRun) {
        Write-LogLine 'INFO' "[dry-run] would remove legacy ProgID $progRoot"
        $removedLegacy++
        continue
    }
    try {
        Remove-Item -LiteralPath $progRoot -Recurse -Force -ErrorAction Stop
        Write-LogLine 'INFO' "removed legacy ProgID $progRoot"
        $removedLegacy++
    } catch {
        Write-LogLine 'WARN' "could not remove ${progRoot}: $($_.Exception.Message)"
    }
}

# --- per-app wrappers under Applications\ (.exe / .cmd / .vbs) ---
# Current scheme: .exe hard links. Older revisions used .cmd then
# .vbs — we scrub all three patterns so re-installing over an old
# layout doesn't leave dangling registry entries.
$apps = @()
$appsRoot = Join-Path $classesRoot 'Applications'
if (Test-Path -LiteralPath $appsRoot) {
    try {
        $apps = Get-ChildItem -LiteralPath $appsRoot -ErrorAction Stop |
            Where-Object {
                $_.PSChildName -like 'winpodx-*.exe' -or
                $_.PSChildName -like 'winpodx-*.cmd' -or
                $_.PSChildName -like 'winpodx-*.vbs'
            } |
            Select-Object -ExpandProperty PSChildName
    } catch {
        Write-LogLine 'WARN' "enumerate Applications failed: $($_.Exception.Message)"
    }
}
$removedApps = 0
foreach ($appName in $apps) {
    $appKey = Join-Path $appsRoot $appName
    if ($DryRun) {
        Write-LogLine 'INFO' "[dry-run] would remove $appKey"
        $removedApps++
        continue
    }
    try {
        Remove-Item -LiteralPath $appKey -Recurse -Force -ErrorAction Stop
        Write-LogLine 'INFO' "removed $appKey"
        $removedApps++
    } catch {
        Write-LogLine 'WARN' "could not remove ${appKey}: $($_.Exception.Message)"
    }
}

# --- per-ext OpenWithList sub-keys + legacy values + OpenWithProgids ---
# Current scheme: OpenWithList\winpodx-<slug>.exe SUB-KEYS (this is the
# Windows convention). Pre-fix builds wrote VALUES under OpenWithList
# with the same name — we scrub both so an upgrade from the broken
# revision leaves no orphans.
$removedExtRefs = 0
try {
    $extKeys = Get-ChildItem -LiteralPath $classesRoot -ErrorAction Stop |
        Where-Object { $_.PSChildName -like '.*' }
} catch {
    $extKeys = @()
}
foreach ($ext in $extKeys) {
    # Strip winpodx-*.exe / .cmd / .vbs sub-keys under OpenWithList.
    $owlKey = Join-Path $ext.PSPath 'OpenWithList'
    if (Test-Path -LiteralPath $owlKey) {
        try {
            $children = Get-ChildItem -LiteralPath $owlKey -ErrorAction Stop |
                Where-Object { $_.PSChildName -like 'winpodx-*' }
        } catch {
            $children = @()
        }
        foreach ($child in $children) {
            $childPath = $child.PSPath
            if ($DryRun) {
                Write-LogLine 'INFO' "[dry-run] would remove sub-key $childPath"
                $removedExtRefs++
                continue
            }
            try {
                Remove-Item -LiteralPath $childPath -Recurse -Force -ErrorAction Stop
                $removedExtRefs++
            } catch {
                Write-LogLine 'WARN' "could not remove ${childPath}: $($_.Exception.Message)"
            }
        }
    }
    # Strip winpodx-* legacy VALUES under OpenWithList + any
    # winpodx-* OpenWithProgids attachments.
    foreach ($subName in @('OpenWithList', 'OpenWithProgids')) {
        $subKey = Join-Path $ext.PSPath $subName
        if (-not (Test-Path -LiteralPath $subKey)) { continue }
        $props = $null
        try {
            $props = Get-ItemProperty -LiteralPath $subKey -ErrorAction Stop
        } catch {
            continue
        }
        foreach ($prop in $props.PSObject.Properties) {
            if ($prop.Name -like 'winpodx-*') {
                if ($DryRun) {
                    Write-LogLine 'INFO' "[dry-run] would strip value $($prop.Name) from $subKey"
                    $removedExtRefs++
                    continue
                }
                try {
                    Remove-ItemProperty -LiteralPath $subKey -Name $prop.Name -Force -ErrorAction Stop
                    $removedExtRefs++
                } catch {
                    Write-LogLine 'WARN' "could not strip $($prop.Name) from ${subKey}: $($_.Exception.Message)"
                }
            }
        }
    }
}

# --- delete the per-slug wrapper files (.exe / .cmd / .vbs) and shim ---
# Hard links share an inode with the source shim, so removing each
# .exe just drops a name; the inode is freed when the last name goes
# (and we delete the source shim explicitly at the end). Legacy .cmd
# / .vbs files are removed for upgrade hygiene.
$removedFiles = 0
if (Test-Path -LiteralPath $BinDir) {
    foreach ($pat in @('winpodx-*.exe', 'winpodx-*.cmd', 'winpodx-*.vbs')) {
        try {
            $files = Get-ChildItem -LiteralPath $BinDir -Filter $pat -ErrorAction Stop
        } catch {
            $files = @()
        }
        foreach ($f in $files) {
            if ($DryRun) {
                Write-LogLine 'INFO' "[dry-run] would delete $($f.FullName)"
                $removedFiles++
                continue
            }
            try {
                Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
                $removedFiles++
            } catch {
                Write-LogLine 'WARN' "could not delete $($f.FullName): $($_.Exception.Message)"
            }
        }
    }
    # Also remove the master shim binary (separate from the
    # per-app hard links, named winpodx-reverse-open-shim.exe).
    $shimPath = Join-Path $BinDir 'winpodx-reverse-open-shim.exe'
    if (Test-Path -LiteralPath $shimPath) {
        if ($DryRun) {
            Write-LogLine 'INFO' "[dry-run] would delete $shimPath"
            $removedFiles++
        } else {
            try {
                Remove-Item -LiteralPath $shimPath -Force -ErrorAction Stop
                $removedFiles++
            } catch {
                Write-LogLine 'WARN' "could not delete ${shimPath}: $($_.Exception.Message)"
            }
        }
    }
}

# --- Start Menu shortcuts directory ---
$removedShortcuts = 0
if (Test-Path -LiteralPath $StartMenuDir) {
    if ($DryRun) {
        Write-LogLine 'INFO' "[dry-run] would delete shortcut dir $StartMenuDir"
        $removedShortcuts = 1
    } else {
        try {
            Remove-Item -LiteralPath $StartMenuDir -Recurse -Force -ErrorAction Stop
            $removedShortcuts = 1
        } catch {
            Write-LogLine 'WARN' "could not remove ${StartMenuDir}: $($_.Exception.Message)"
        }
    }
}

# --- Desktop folder shortcut + Quick Access pin ---------------------------
# Mirror cleanup for the discoverability aids register-apps.ps1 lands
# alongside the per-slug registrations.
$removedFolderShortcuts = 0
if (-not $DryRun) {
    # Desktop\Linux Apps.lnk
    try {
        $desktopDir = [Environment]::GetFolderPath('Desktop')
        if ($desktopDir) {
            $desktopLnk = Join-Path $desktopDir 'Linux Apps.lnk'
            if (Test-Path -LiteralPath $desktopLnk) {
                Remove-Item -LiteralPath $desktopLnk -Force -ErrorAction Stop
                $removedFolderShortcuts++
            }
        }
    } catch {
        Write-LogLine 'WARN' "could not remove Desktop folder shortcut: $($_.Exception.Message)"
    }

    # Quick Access unpin. The Shell.Application "unpinfromhome" verb
    # only works if the pinned item still exists on disk, so we run
    # this BEFORE the Start Menu folder deletion above would have
    # taken effect — except that section already ran. Fall back to
    # walking the AutomaticDestinations file the pin lives in.
    try {
        $shellApp = New-Object -ComObject Shell.Application
        # The Quick Access folder is exposed as "shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}"
        $qa = $shellApp.Namespace('shell:::{679f85cb-0220-4080-b29b-5540cc05aab6}')
        if ($qa) {
            foreach ($it in $qa.Items()) {
                if ($it.Name -eq 'Linux Apps' -or $it.Path -eq $StartMenuDir) {
                    $it.InvokeVerb('unpinfromhome')
                    $removedFolderShortcuts++
                    break
                }
            }
        }
    } catch {
        Write-LogLine 'WARN' "could not unpin from Quick Access: $($_.Exception.Message)"
    }
}

# Invalidate Open With chooser cache so the removed handlers stop
# appearing immediately. See register-apps.ps1 for rationale.
if (-not $DryRun) {
    try {
        $ie4uinit = Join-Path $env:SystemRoot 'System32\ie4uinit.exe'
        if (Test-Path -LiteralPath $ie4uinit) {
            & $ie4uinit -show 2>&1 | Out-Null
        }
    } catch {
        # best-effort — the registry scrub already succeeded above
    }
}

Write-LogLine 'INFO' "done. legacy_progids=$removedLegacy apps=$removedApps ext_refs=$removedExtRefs files=$removedFiles start_menu=$removedShortcuts folder_shortcuts=$removedFolderShortcuts"
exit 0
