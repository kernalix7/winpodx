# =====================================================================
# winpodx reverse-open — remove the Linux app handlers from Windows.
#
# Mirrors register-apps.ps1. Walks `HKCU\Software\Classes` for any
# subkey starting with `winpodx-` and removes it; then walks every
# `<ext>\OpenWithProgids` subkey under the same root and strips any
# value whose name starts with `winpodx-`.
#
# Idempotent: missing keys / missing values are silently OK. Designed
# so running it on a system that's never had reverse-open enabled is
# a no-op.
# =====================================================================

[CmdletBinding()]
param(
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

# Remove every winpodx-<slug> ProgID subkey.
$progIds = @()
try {
    $progIds = Get-ChildItem -LiteralPath $classesRoot -ErrorAction Stop |
        Where-Object { $_.PSChildName -like 'winpodx-*' } |
        Select-Object -ExpandProperty PSChildName
} catch {
    Write-LogLine 'WARN' "enumerate classes failed: $($_.Exception.Message)"
}

$removedProgIds = 0
foreach ($progId in $progIds) {
    $progRoot = Join-Path $classesRoot $progId
    if ($DryRun) {
        Write-LogLine 'INFO' "[dry-run] would remove $progRoot"
        $removedProgIds++
        continue
    }
    try {
        Remove-Item -LiteralPath $progRoot -Recurse -Force -ErrorAction Stop
        Write-LogLine 'INFO' "removed $progRoot"
        $removedProgIds++
    } catch {
        Write-LogLine 'WARN' "could not remove ${progRoot}: $($_.Exception.Message)"
    }
}

# Strip winpodx-<slug> entries from each <ext>\OpenWithProgids.
$removedExtRefs = 0
try {
    $extKeys = Get-ChildItem -LiteralPath $classesRoot -ErrorAction Stop |
        Where-Object { $_.PSChildName -like '.*' }
} catch {
    $extKeys = @()
}
foreach ($ext in $extKeys) {
    $opw = Join-Path $ext.PSPath 'OpenWithProgids'
    if (-not (Test-Path -LiteralPath $opw)) { continue }
    $props = $null
    try {
        $props = Get-ItemProperty -LiteralPath $opw -ErrorAction Stop
    } catch {
        continue
    }
    foreach ($prop in $props.PSObject.Properties) {
        if ($prop.Name -like 'winpodx-*') {
            if ($DryRun) {
                Write-LogLine 'INFO' "[dry-run] would strip $($prop.Name) from $opw"
                $removedExtRefs++
                continue
            }
            try {
                Remove-ItemProperty -LiteralPath $opw -Name $prop.Name -Force -ErrorAction Stop
                $removedExtRefs++
            } catch {
                Write-LogLine 'WARN' "could not strip $($prop.Name) from ${opw}: $($_.Exception.Message)"
            }
        }
    }
}

Write-LogLine 'INFO' "done. progids=$removedProgIds ext_refs=$removedExtRefs"
exit 0
