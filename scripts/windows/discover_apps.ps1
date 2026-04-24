# discover_apps.ps1 — enumerate installed Windows apps and emit JSON on stdout
#
# Consumed by winpodx.core.discovery. Four sources are scanned inside the
# running guest and unioned, deduping by lowercase executable path or UWP
# AUMID:
#
#   1. Registry App Paths (HKLM + HKCU)
#   2. Start Menu .lnk recursion (ProgramData + every user profile)
#   3. UWP / MSIX packages via Get-AppxPackage + AppxManifest.xml
#   4. Chocolatey + Scoop shims
#
# Output schema per entry (JSON array on stdout):
#
#   { "name": str, "path": str, "args": str,
#     "source": "win32" | "uwp", "wm_class_hint": str,
#     "launch_uri": str, "icon_b64": str }
#
# Semantic contract for `launch_uri`:
#   - source == "uwp"   : bare AUMID of the form `PackageFamilyName!AppId`
#                         (NO `shell:AppsFolder\` prefix — the host prepends
#                         that when building the FreeRDP `/app-cmd`). The
#                         host-side regex `_AUMID_RE` in
#                         `src/winpodx/core/rdp.py` rejects any value that
#                         already carries the prefix.
#   - source == "win32" : empty string.
#
# An optional trailing element `{"_truncated": true}` signals that the
# guest clipped its own output.
#
# Invocation: run inside the Windows guest under Administrator (required
# for Get-AppxPackage -AllUsers). Host side pipes stdout into json.loads.
# `-DryRun` returns a single canned entry without touching Registry/AppX
# so CI runners (no Windows) can smoke-test the JSON shape.

[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Continue'

$MAX_APPS       = 500
$MAX_ICON_BYTES = 1MB
$MAX_NAME_LEN   = 255
$MAX_PATH_LEN   = 1024
$ICON_SIZE      = 32

# --- Helpers ---------------------------------------------------------------

function ConvertTo-IconBase64 {
    param([string]$SourcePath, [int]$Size = $ICON_SIZE)
    if (-not $SourcePath) { return '' }
    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) { return '' }
    $icon = $null; $bmp = $null; $resized = $null; $g = $null; $ms = $null
    try {
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($SourcePath)
        if (-not $icon) { return '' }
        $bmp = $icon.ToBitmap()
        $resized = New-Object System.Drawing.Bitmap($Size, $Size)
        $g = [System.Drawing.Graphics]::FromImage($resized)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($bmp, 0, 0, $Size, $Size)
        $ms = New-Object System.IO.MemoryStream
        $resized.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        $bytes = $ms.ToArray()
        if ($bytes.Length -gt $MAX_ICON_BYTES) { return '' }
        return [Convert]::ToBase64String($bytes)
    } catch {
        return ''
    } finally {
        if ($ms)      { $ms.Dispose() }
        if ($g)       { $g.Dispose() }
        if ($resized) { $resized.Dispose() }
        if ($bmp)     { $bmp.Dispose() }
        if ($icon)    { $icon.Dispose() }
    }
}

function Get-WmClassHint {
    param([string]$ExePath)
    if (-not $ExePath) { return '' }
    try {
        $stem = [System.IO.Path]::GetFileNameWithoutExtension($ExePath)
        if (-not $stem) { return '' }
        $safe = ($stem.ToLower() -replace '[^a-z0-9_-]', '')
        if ($safe.Length -gt 64) { $safe = $safe.Substring(0, 64) }
        return $safe
    } catch { return '' }
}

function Get-DisplayName {
    param([string]$ExePath, [string]$Fallback = '')
    try {
        $item = Get-Item -LiteralPath $ExePath -ErrorAction Stop
        $vi = $item.VersionInfo
        if ($vi -and $vi.FileDescription -and $vi.FileDescription.Trim()) {
            return $vi.FileDescription.Trim()
        }
    } catch { }
    if ($Fallback) { return $Fallback }
    try { return [System.IO.Path]::GetFileNameWithoutExtension($ExePath) } catch { return '' }
}

function Read-IconBytesFromFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        if ($bytes.Length -eq 0) { return '' }
        if ($bytes.Length -gt $MAX_ICON_BYTES) { return '' }
        return [Convert]::ToBase64String($bytes)
    } catch { return '' }
}

# --- DryRun short-circuit for CI smoke tests -------------------------------

if ($DryRun) {
    $canned = @(
        [ordered]@{
            name          = 'Notepad (DryRun)'
            path          = 'C:\Windows\notepad.exe'
            args          = ''
            source        = 'win32'
            wm_class_hint = 'notepad'
            launch_uri    = ''
            icon_b64      = ''
        }
    )
    # @(...) wrapper forces array even for single element on PS 5.1.
    ConvertTo-Json -InputObject @($canned) -Depth 6 -Compress
    exit 0
}

# --- Accumulator -----------------------------------------------------------

$results = New-Object System.Collections.Generic.List[object]
$seen    = @{}

function Add-Result {
    param([hashtable]$Entry)
    if (-not $Entry) { return }
    if ($results.Count -ge $MAX_APPS) { return }
    $name = [string]$Entry.name
    $path = [string]$Entry.path
    if (-not $name -or -not $path) { return }
    if ($name.Length -gt $MAX_NAME_LEN) { return }
    if ($path.Length -gt $MAX_PATH_LEN) { return }
    $key = if ($Entry.launch_uri) { ([string]$Entry.launch_uri).ToLower() }
           else { $path.ToLower() }
    if ($seen.ContainsKey($key)) { return }
    $seen[$key] = $true
    $results.Add([ordered]@{
        name          = $name
        path          = $path
        args          = [string]$Entry.args
        source        = [string]$Entry.source
        wm_class_hint = [string]$Entry.wm_class_hint
        launch_uri    = [string]$Entry.launch_uri
        icon_b64      = [string]$Entry.icon_b64
    }) | Out-Null
}

# --- Source 1: Registry App Paths ------------------------------------------

foreach ($hive in 'HKLM:', 'HKCU:') {
    $root = Join-Path $hive 'Software\Microsoft\Windows\CurrentVersion\App Paths'
    if (-not (Test-Path $root)) { continue }
    try {
        Get-ChildItem -Path $root -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $props = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
                if (-not $props) { return }
                $default = $props.'(default)'
                if (-not $default) { return }
                $exe = ([string]$default).Trim('"')
                if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return }
                $stem = [System.IO.Path]::GetFileNameWithoutExtension($exe)
                Add-Result @{
                    name          = Get-DisplayName -ExePath $exe -Fallback $stem
                    path          = $exe
                    args          = ''
                    source        = 'win32'
                    wm_class_hint = Get-WmClassHint $exe
                    launch_uri    = ''
                    icon_b64      = ConvertTo-IconBase64 $exe
                }
            } catch { }
        }
    } catch { }
}

# --- Source 2: Start Menu .lnk files ---------------------------------------

$startDirs = @()
if ($env:ProgramData) {
    $startDirs += (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs')
}
try {
    $userProfiles = Get-ChildItem -Path 'C:\Users' -Directory -ErrorAction SilentlyContinue
    foreach ($u in $userProfiles) {
        if ($u.Name -in @('Default', 'Default User', 'Public', 'All Users')) { continue }
        $p = Join-Path $u.FullName 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs'
        if (Test-Path -LiteralPath $p) { $startDirs += $p }
    }
} catch { }

$wsh = $null
try { $wsh = New-Object -ComObject WScript.Shell } catch { }

foreach ($d in $startDirs) {
    if (-not $wsh) { break }
    try {
        # L3 hardening: bound recursion depth. Start Menu\Programs layouts
        # with symlink loops or pathologically deep nesting could otherwise
        # stall the guest until the host-side 120s timeout fires. PowerShell
        # 5.1+ honors -Depth on Get-ChildItem. The MAX_APPS post-filter
        # still caps absolute output size.
        Get-ChildItem -Path $d -Recurse -Depth 8 -Filter '*.lnk' -ErrorAction SilentlyContinue |
            ForEach-Object {
                try {
                    if ($_.Name -match '(?i)uninstall|readme|license|eula') { return }
                    $lnk = $wsh.CreateShortcut($_.FullName)
                    $target = [string]$lnk.TargetPath
                    if (-not $target) { return }
                    if ($target -notmatch '\.exe$') { return }
                    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { return }
                    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
                    Add-Result @{
                        name          = Get-DisplayName -ExePath $target -Fallback $baseName
                        path          = $target
                        args          = [string]$lnk.Arguments
                        source        = 'win32'
                        wm_class_hint = Get-WmClassHint $target
                        launch_uri    = ''
                        icon_b64      = ConvertTo-IconBase64 $target
                    }
                } catch { }
            }
    } catch { }
}

# --- Source 3: UWP / MSIX packages -----------------------------------------

try {
    $pkgs = Get-AppxPackage -AllUsers -ErrorAction SilentlyContinue
    foreach ($pkg in $pkgs) {
        try {
            if ($pkg.IsFramework) { continue }
            if ($pkg.SignatureKind -eq 'System') { continue }
            if (-not $pkg.InstallLocation) { continue }
            $manifestPath = Join-Path $pkg.InstallLocation 'AppxManifest.xml'
            if (-not (Test-Path -LiteralPath $manifestPath)) { continue }
            [xml]$manifest = Get-Content -LiteralPath $manifestPath -ErrorAction SilentlyContinue
            if (-not $manifest) { continue }

            $apps = $null
            try { $apps = $manifest.Package.Applications.Application } catch { $apps = $null }
            if (-not $apps) { continue }
            if ($apps -isnot [System.Collections.IEnumerable]) { $apps = @($apps) }

            foreach ($appNode in $apps) {
                try {
                    $appId = [string]$appNode.Id
                    if (-not $appId) { continue }
                    # Emit bare AUMID only. The host-side FreeRDP command
                    # builder (src/winpodx/core/rdp.py) prepends
                    # `shell:AppsFolder\` itself; duplicating the prefix
                    # here would produce `shell:AppsFolder\shell:AppsFolder\...`.
                    $aumid = "$($pkg.PackageFamilyName)!$appId"

                    $ve = $null
                    foreach ($probe in 'VisualElements', 'uap:VisualElements') {
                        try {
                            $probed = $appNode.$probe
                            if ($probed) { $ve = $probed; break }
                        } catch { }
                    }

                    $displayName = [string]$pkg.Name
                    if ($ve) {
                        $dn = [string]$ve.DisplayName
                        if ($dn -and ($dn -notmatch '^ms-resource:')) {
                            $displayName = $dn
                        }
                    }

                    $logoRel = ''
                    if ($ve) {
                        foreach ($attr in 'Square44x44Logo', 'Square30x30Logo', 'SmallLogo', 'Logo') {
                            try {
                                $val = [string]$ve.$attr
                                if ($val) { $logoRel = $val; break }
                            } catch { }
                        }
                    }

                    $iconB64 = ''
                    if ($logoRel) {
                        $logoPath = Join-Path $pkg.InstallLocation $logoRel
                        $iconB64 = Read-IconBytesFromFile $logoPath
                        if (-not $iconB64) {
                            $parent = [System.IO.Path]::GetDirectoryName($logoPath)
                            $stem = [System.IO.Path]::GetFileNameWithoutExtension($logoPath)
                            $ext = [System.IO.Path]::GetExtension($logoPath)
                            foreach ($scale in '100', '200', '400') {
                                $scaled = Join-Path $parent "$stem.scale-$scale$ext"
                                $iconB64 = Read-IconBytesFromFile $scaled
                                if ($iconB64) { break }
                            }
                        }
                    }

                    # path must be non-empty per core contract; use InstallLocation as placeholder.
                    Add-Result @{
                        name          = $displayName
                        path          = [string]$pkg.InstallLocation
                        args          = ''
                        source        = 'uwp'
                        wm_class_hint = ''
                        launch_uri    = $aumid
                        icon_b64      = $iconB64
                    }
                } catch { }
            }
        } catch { }
    }
} catch { }

# --- Source 4: Chocolatey + Scoop shims ------------------------------------

$shimDirs = @()
if ($env:ProgramData) {
    $shimDirs += (Join-Path $env:ProgramData 'chocolatey\bin')
    $shimDirs += (Join-Path $env:ProgramData 'scoop\shims')
}
if ($env:USERPROFILE) {
    $shimDirs += (Join-Path $env:USERPROFILE 'scoop\shims')
}

foreach ($d in $shimDirs) {
    if (-not (Test-Path -LiteralPath $d)) { continue }
    try {
        Get-ChildItem -Path $d -Filter '*.exe' -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $resolved = $_.FullName
                try {
                    $cmd = Get-Command -Name $_.BaseName -CommandType Application -ErrorAction SilentlyContinue
                    if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source -PathType Leaf)) {
                        $resolved = $cmd.Source
                    }
                } catch { }
                Add-Result @{
                    name          = Get-DisplayName -ExePath $resolved -Fallback $_.BaseName
                    path          = $resolved
                    args          = ''
                    source        = 'win32'
                    wm_class_hint = Get-WmClassHint $resolved
                    launch_uri    = ''
                    icon_b64      = ConvertTo-IconBase64 $resolved
                }
            } catch { }
        }
    } catch { }
}

# --- Emit JSON -------------------------------------------------------------

$output = $results.ToArray()
if ($results.Count -ge $MAX_APPS) {
    $output = $output + [ordered]@{ _truncated = $true }
}

# @(...) forces array encoding on PowerShell 5.1 even if the array has
# exactly one element (Compress otherwise emits a bare object).
ConvertTo-Json -InputObject @($output) -Depth 6 -Compress
