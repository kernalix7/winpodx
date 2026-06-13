# SPDX-License-Identifier: MIT
# discover_apps.ps1 -- enumerate installed Windows apps and emit JSON on stdout
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
#                         (NO `shell:AppsFolder\` prefix -- the host prepends
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

function Get-AppDescription {
    # Pull a one-line description from the executable's version metadata.
    # Win32 binaries rarely fill Comments; ProductName + CompanyName are the
    # only fields we can rely on. The output lands in the Linux .desktop
    # Comment= key (a tooltip), so identical-to-the-name strings are still
    # useful -- better to surface "Microsoft Edge" than the project-wide
    # "Windows application via winpodx" generic stamp.
    #
    # Order:
    #   1. Comments -- most authored field; trust it when set.
    #   2. ProductName when distinct from FileDescription (gives e.g.
    #      "Microsoft Windows Operating System" for inbox tools where the
    #      FileDescription is just "Notepad").
    #   3. CompanyName when present -- "by Microsoft Corporation" is a
    #      meaningful tooltip even when ProductName duplicates the name.
    #   4. Bare ProductName as a last resort (still better than nothing).
    param([string]$ExePath)
    try {
        $item = Get-Item -LiteralPath $ExePath -ErrorAction Stop
        $vi = $item.VersionInfo
        if (-not $vi) { return '' }
        $comments = if ($vi.Comments) { $vi.Comments.Trim() } else { '' }
        if ($comments) { return $comments }
        $product = if ($vi.ProductName) { $vi.ProductName.Trim() } else { '' }
        $fileDesc = if ($vi.FileDescription) { $vi.FileDescription.Trim() } else { '' }
        $company = if ($vi.CompanyName) { $vi.CompanyName.Trim() } else { '' }
        if ($product -and $product -ne $fileDesc) { return $product }
        if ($company) { return "by $company" }
        if ($product) { return $product }
    } catch { }
    return ''
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

function Get-ExeHash {
    # SHA256 of an app's executable, used host-side to detect when an app was
    # updated (icon may have changed) vs unchanged (skip re-extraction). Returns
    # '' for non-file paths (UWP InstallLocation dirs), missing or locked files.
    param([string]$Path)
    if (-not $Path) { return '' }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    try {
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLower()
    } catch { return '' }
}

# --- DryRun short-circuit for CI smoke tests -------------------------------

if ($DryRun) {
    $canned = @(
        [ordered]@{
            name          = 'Notepad (DryRun)'
            description   = 'Plain text editor (DryRun fixture)'
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

# --- Readiness gate (first-boot race avoidance) ----------------------------
# At Sysprep first-boot the user session has just started: AppX deployment
# is still finishing, Start Menu indexer hasn't propagated all .lnk files
# yet, AppXSvc may still be queueing work. discovery firing in this 30-60 s
# window returns partial / empty results -- kernalix7 reported the menu
# populating one install, missing UWP entries the next, despite identical
# config. Both symptoms collapse to "we ran too early".
#
# Gate: wait until BOTH conditions hold or until the bounded budget
# expires (we proceed regardless after timeout -- a partial discovery is
# better than none, and the host's retry-on-empty layer covers the
# all-empty case).
#
#   1. AppXSvc service is Running. If it's StartPending / Stopped the
#      AppX broker can't service Get-AppxPackage queries reliably.
#   2. Start Menu has at least one .lnk under ProgramData. The default
#      Windows Start Menu ships dozens of these; their absence means
#      first-boot Start Menu population is still in flight.
#
# Quiescence: poll every 1 s; require 3 consecutive samples where
# AppXSvc is Running + .lnk count is non-zero before declaring ready.
# This catches the case where AppXSvc flips Running briefly during
# StartPending -> Running -> restart cycles.

$readyBudgetSec = 60
$pollIntervalSec = 1
$stableSamplesNeeded = 3
$readyDeadline = (Get-Date).AddSeconds($readyBudgetSec)
$stableSamples = 0
$systemStartMenu = "$env:ProgramData\Microsoft\Windows\Start Menu\Programs"

[Console]::Error.WriteLine("[discover] waiting for first-boot stability (max ${readyBudgetSec}s)...")
while ((Get-Date) -lt $readyDeadline) {
    $appxRunning = $false
    try {
        $svc = Get-Service -Name AppXSvc -ErrorAction Stop
        $appxRunning = ($svc.Status -eq 'Running')
    } catch {
        $appxRunning = $false
    }
    $lnkCount = 0
    try {
        $lnkCount = (Get-ChildItem -LiteralPath $systemStartMenu -Recurse -Filter '*.lnk' -ErrorAction SilentlyContinue |
            Measure-Object).Count
    } catch {
        $lnkCount = 0
    }
    if ($appxRunning -and $lnkCount -gt 0) {
        $stableSamples++
        if ($stableSamples -ge $stableSamplesNeeded) {
            [Console]::Error.WriteLine("[discover] stable (AppXSvc=Running, .lnk=$lnkCount, samples=$stableSamples) -- proceeding")
            break
        }
    } else {
        $stableSamples = 0
    }
    Start-Sleep -Seconds $pollIntervalSec
}
if ($stableSamples -lt $stableSamplesNeeded) {
    [Console]::Error.WriteLine("[discover] stability budget exceeded (${readyBudgetSec}s); proceeding with potentially partial state")
}

# --- Accumulator -----------------------------------------------------------

$results = New-Object System.Collections.Generic.List[object]
$seen    = @{}

# Build {exe(lower) -> [".ext", ...]} ONCE per run: the file types each app
# registered, the same source Windows Settings > Default apps reads (#545).
#   1. RegisteredApplications -> <Capabilities>\FileAssociations (ext -> ProgID),
#      with the ProgID's open command resolved to the owning .exe. (This is what
#      Notepad / Office / browsers use -- not Applications\<exe>\SupportedTypes.)
#   2. Applications\<exe>\SupportedTypes (value names = extensions) as a backup.
# The host maps the extensions to MIME types for the .desktop MimeType=.
$script:WinpodxExtMap = $null

function Add-ExtTo {
    param([hashtable]$Map, [string]$Exe, [string]$Ext)
    if (-not $Exe -or -not $Ext) { return }
    $e = $Exe.ToLower(); $x = $Ext.ToLower()
    if ($x -notmatch '^\.[a-z0-9]{1,16}$') { return }
    if (-not $Map.ContainsKey($e)) { $Map[$e] = New-Object System.Collections.Generic.List[string] }
    if (-not $Map[$e].Contains($x)) { $Map[$e].Add($x) }
}

function Resolve-ProgidExe {
    param([string]$ProgId)
    if (-not $ProgId) { return $null }
    foreach ($hive in @('HKLM:\SOFTWARE\Classes', 'HKCU:\SOFTWARE\Classes')) {
        $cmdKey = Join-Path $hive ("{0}\shell\open\command" -f $ProgId)
        if (-not (Test-Path -LiteralPath $cmdKey)) { continue }
        $cmd = [string](Get-ItemProperty -LiteralPath $cmdKey -ErrorAction SilentlyContinue).'(default)'
        if ($cmd -and $cmd -match '([^\\/:*?"<>|\r\n]+\.exe)') {
            return [System.IO.Path]::GetFileName($Matches[1].Trim())
        }
    }
    return $null
}

function Build-ExtMap {
    $map = @{}
    try {
        # 1. Per-app Capabilities\FileAssociations via RegisteredApplications.
        foreach ($raHive in @('HKLM:\SOFTWARE\RegisteredApplications', 'HKCU:\SOFTWARE\RegisteredApplications')) {
            if (-not (Test-Path -LiteralPath $raHive)) { continue }
            $ra = Get-ItemProperty -LiteralPath $raHive -ErrorAction SilentlyContinue
            if (-not $ra) { continue }
            foreach ($prop in $ra.PSObject.Properties) {
                if ($prop.Name -in @('PSPath', 'PSParentPath', 'PSChildName', 'PSDrive', 'PSProvider')) { continue }
                $capRel = [string]$prop.Value
                if (-not $capRel) { continue }
                foreach ($root in @('HKLM:\', 'HKCU:\')) {
                    $faKey = Join-Path $root ($capRel + '\FileAssociations')
                    if (-not (Test-Path -LiteralPath $faKey)) { continue }
                    $fa = Get-ItemProperty -LiteralPath $faKey -ErrorAction SilentlyContinue
                    if (-not $fa) { continue }
                    foreach ($p in $fa.PSObject.Properties) {
                        $ext = [string]$p.Name
                        if ($ext -notlike '.*') { continue }
                        $exe = Resolve-ProgidExe ([string]$p.Value)
                        if ($exe) { Add-ExtTo $map $exe $ext }
                    }
                }
            }
        }
        # 2. Applications\<exe>\SupportedTypes backup.
        foreach ($hive in @('HKLM:\SOFTWARE\Classes\Applications', 'HKCU:\SOFTWARE\Classes\Applications')) {
            if (-not (Test-Path -LiteralPath $hive)) { continue }
            Get-ChildItem -LiteralPath $hive -ErrorAction SilentlyContinue | ForEach-Object {
                $exe = $_.PSChildName
                $stKey = Join-Path $_.PSPath 'SupportedTypes'
                if (-not (Test-Path -LiteralPath $stKey)) { return }
                $st = Get-ItemProperty -LiteralPath $stKey -ErrorAction SilentlyContinue
                if ($st) { foreach ($p in $st.PSObject.Properties) { Add-ExtTo $map $exe ([string]$p.Name) } }
            }
        }
    } catch { }
    return $map
}

function Get-AppExtensions {
    # File extensions the given exe handles, from the per-run Capabilities map.
    param([string]$ExePath)
    try {
        if (-not $ExePath) { return @() }
        if ($null -eq $script:WinpodxExtMap) { $script:WinpodxExtMap = Build-ExtMap }
        $exe = ([System.IO.Path]::GetFileName($ExePath)).ToLower()
        if ($script:WinpodxExtMap.ContainsKey($exe)) {
            $lst = $script:WinpodxExtMap[$exe]
            if ($lst.Count -gt 64) { return @($lst.GetRange(0, 64)) }
            return @($lst)
        }
    } catch { return @() }
    return @()
}

function Add-Result {
    param([hashtable]$Entry)
    if (-not $Entry) { return }
    if ($results.Count -ge $MAX_APPS) { return }
    $name = [string]$Entry.name
    $path = [string]$Entry.path
    if (-not $name -or -not $path) { return }
    if ($name.Length -gt $MAX_NAME_LEN) { return }
    if ($path.Length -gt $MAX_PATH_LEN) { return }
    # Reverse-open shims (#48 / v0.5.0) live under
    # C:\Users\Public\winpodx\reverse-open\bin\. They are Windows .exe
    # entries created by winpodx itself to surface Linux host apps in
    # the Windows "Open with..." menu and must not be returned as
    # Windows apps. Match the directory fragment rather than the full
    # path so a future layout change still catches the entries.
    $pathLower = $path.ToLower()
    if ($pathLower -like '*\winpodx\reverse-open\bin\*') { return }
    $key = if ($Entry.launch_uri) { ([string]$Entry.launch_uri).ToLower() }
           else { $pathLower }
    if ($seen.ContainsKey($key)) { return }
    $seen[$key] = $true
    $results.Add([ordered]@{
        name          = $name
        description   = [string]$Entry.description
        path          = $path
        args          = [string]$Entry.args
        source        = [string]$Entry.source
        wm_class_hint = [string]$Entry.wm_class_hint
        launch_uri    = [string]$Entry.launch_uri
        icon_b64      = [string]$Entry.icon_b64
        exe_hash      = Get-ExeHash $path
        extensions    = @(Get-AppExtensions $path)
    }) | Out-Null
}

# v0.2.0 streaming progress: when the host wraps this script via
# windows_exec.run_in_windows with a progress_callback, the wrapper
# defines `Write-WinpodxProgress`. When run standalone (or via a wrapper
# that doesn't define it) the function is missing and the calls would
# error -- define a no-op shim that yields when the real one isn't
# available.
if (-not (Get-Command 'Write-WinpodxProgress' -ErrorAction SilentlyContinue)) {
    function Write-WinpodxProgress($msg) { }
}

# --- Source 1: Registry App Paths ------------------------------------------

Write-WinpodxProgress 'Scanning Registry App Paths...'
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
                    description   = Get-AppDescription $exe
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

Write-WinpodxProgress 'Scanning Start Menu shortcuts...'
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
                    # .lnk shortcuts have a Description property (the Comment
                    # field in shortcut Properties); prefer it when set since
                    # it's user-curated, then fall back to exe metadata.
                    $lnkDesc = ''
                    try { $lnkDesc = [string]$lnk.Description } catch { $lnkDesc = '' }
                    if (-not $lnkDesc) { $lnkDesc = Get-AppDescription $target }
                    Add-Result @{
                        name          = Get-DisplayName -ExePath $target -Fallback $baseName
                        description   = $lnkDesc
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

Write-WinpodxProgress 'Scanning UWP / MSIX packages...'
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
                    $description = ''
                    if ($ve) {
                        $dn = [string]$ve.DisplayName
                        if ($dn -and ($dn -notmatch '^ms-resource:')) {
                            $displayName = $dn
                        }
                        # AppxManifest's <VisualElements Description="..."> is the
                        # Start-menu tooltip -- exactly what we want for the
                        # Linux .desktop Comment field. Skip ms-resource:
                        # indirections that PowerShell can't resolve in a
                        # non-interactive session.
                        $desc = [string]$ve.Description
                        if ($desc -and ($desc -notmatch '^ms-resource:')) {
                            $description = $desc.Trim()
                        }
                    }
                    # Fall back to the package-level <Properties><Description>.
                    if (-not $description) {
                        try {
                            $pkgDesc = [string]$manifest.Package.Properties.Description
                            if ($pkgDesc -and ($pkgDesc -notmatch '^ms-resource:')) {
                                $description = $pkgDesc.Trim()
                            }
                        } catch { }
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
                        description   = $description
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

Write-WinpodxProgress 'Scanning Chocolatey + Scoop shims...'
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
                    description   = Get-AppDescription $resolved
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

# --- Source 5: Essentials (always emit) ------------------------------------
#
# OS staples (File Explorer, Calculator, Settings) sometimes fall through
# the previous sources -- File Explorer has no Start Menu .lnk, and UWP apps
# whose DisplayName is an unresolved ms-resource: lookup get filtered as
# junk by the host because their fallback name is a dotted package id.
# Emit them explicitly here with proper icons + launch args so the host
# always shows them in the app menu without resorting to synthesized stubs.

Write-WinpodxProgress 'Emitting essential apps (File Explorer / Calculator / Settings)...'

# File Explorer -- must launch with a shell: argument so RemoteApp opens a
# window instead of trying to take over as the user shell. ``shell:MyComputerFolder``
# opens the "This PC" view; the cmd: side propagates as explorer.exe args.
try {
    $explorer = Join-Path $env:WINDIR 'explorer.exe'
    if (Test-Path -LiteralPath $explorer) {
        # Pull the real description from explorer.exe's VersionInfo via the
        # same Get-AppDescription helper Source 1-4 use -- no hardcoded
        # string. Stock Win11 returns "Microsoft(R) Windows(R) Operating
        # System" (ProductName, since it differs from FileDescription
        # "Windows Explorer").
        Add-Result @{
            name          = 'File Explorer'
            description   = Get-AppDescription $explorer
            path          = $explorer
            args          = 'shell:MyComputerFolder'
            source        = 'win32'
            wm_class_hint = 'explorer'
            launch_uri    = ''
            icon_b64      = ConvertTo-IconBase64 $explorer
        }
    }
} catch { }

# Resolve a UWP package by family-name prefix and emit the entry, pulling
# the icon from the same AppxManifest path the main UWP scan uses. Skips
# silently if the package isn't installed (some Windows SKUs ship without
# Calculator on the Server image, for example).
function Emit-EssentialUwp([string]$FamilyPrefix, [string]$DisplayName, [string]$AppId, [string]$WmClassHint, [string]$DefaultDescription = '') {
    try {
        $pkg = Get-AppxPackage -AllUsers -ErrorAction SilentlyContinue |
            Where-Object { $_.PackageFamilyName -like "$FamilyPrefix*" } |
            Select-Object -First 1
        if (-not $pkg) { return }
        if (-not $pkg.InstallLocation) { return }
        $manifestPath = Join-Path $pkg.InstallLocation 'AppxManifest.xml'
        if (-not (Test-Path -LiteralPath $manifestPath)) { return }
        [xml]$manifest = Get-Content -LiteralPath $manifestPath -ErrorAction SilentlyContinue
        if (-not $manifest) { return }

        $aumid = "$($pkg.PackageFamilyName)!$AppId"

        # Mine the same Square logo path the main UWP block uses so the
        # icon matches what users see in Start Menu.
        $logoRel = ''
        try {
            $apps = $manifest.Package.Applications.Application
            if ($apps -isnot [System.Collections.IEnumerable]) { $apps = @($apps) }
            foreach ($appNode in $apps) {
                if ([string]$appNode.Id -ne $AppId) { continue }
                $ve = $null
                foreach ($probe in 'VisualElements', 'uap:VisualElements') {
                    try {
                        $probed = $appNode.$probe
                        if ($probed) { $ve = $probed; break }
                    } catch { }
                }
                if ($ve) {
                    foreach ($attr in 'Square44x44Logo', 'Square30x30Logo', 'SmallLogo', 'Logo') {
                        try {
                            $val = [string]$ve.$attr
                            if ($val) { $logoRel = $val; break }
                        } catch { }
                    }
                }
                break
            }
        } catch { }

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

        # Mine the AppxManifest for a real description if available;
        # ms-resource: indirections fall back to a sensible default.
        $emitDesc = ''
        try {
            $apps = $manifest.Package.Applications.Application
            if ($apps -isnot [System.Collections.IEnumerable]) { $apps = @($apps) }
            foreach ($appNode in $apps) {
                if ([string]$appNode.Id -ne $AppId) { continue }
                $ve = $null
                foreach ($probe in 'VisualElements', 'uap:VisualElements') {
                    try { if ($appNode.$probe) { $ve = $appNode.$probe; break } } catch { }
                }
                if ($ve) {
                    $d = [string]$ve.Description
                    if ($d -and ($d -notmatch '^ms-resource:')) { $emitDesc = $d.Trim() }
                }
                break
            }
        } catch { }

        # Fall back to a curated one-liner when the manifest's Description
        # was an ms-resource: indirection (PowerShell can't resolve those
        # in a non-interactive session, so we'd otherwise stamp the
        # generic 'Windows application via winpodx' for staples).
        if (-not $emitDesc -and $DefaultDescription) {
            $emitDesc = $DefaultDescription
        }

        Add-Result @{
            name          = $DisplayName
            description   = $emitDesc
            path          = [string]$pkg.InstallLocation
            args          = ''
            source        = 'uwp'
            wm_class_hint = $WmClassHint
            launch_uri    = $aumid
            icon_b64      = $iconB64
        }
    } catch { }
}

Emit-EssentialUwp 'Microsoft.WindowsCalculator_' 'Calculator' 'App' 'calculator' `
    'Calculator app from the Windows guest'
Emit-EssentialUwp 'windows.immersivecontrolpanel_' 'Settings' 'microsoft.windows.immersivecontrolpanel' 'settings' `
    'Open the Windows guest Settings panel'

# --- Emit JSON -------------------------------------------------------------

$output = $results.ToArray()
if ($results.Count -ge $MAX_APPS) {
    $output = $output + [ordered]@{ _truncated = $true }
}

# @(...) forces array encoding on PowerShell 5.1 even if the array has
# exactly one element (Compress otherwise emits a bare object).
ConvertTo-Json -InputObject @($output) -Depth 6 -Compress
