# =====================================================================
# winpodx reverse-open — register the Linux app handlers in Windows.
#
# Reads `C:\Users\Public\winpodx\reverse-open\apps.json` (synced from
# the host) and creates per-app "Open with..." entries that surface each
# Linux app in Windows Explorer's right-click menu for the MIME
# extensions it advertises.
#
# Why per-app .exe hard links
# ----------------------------
# 1. Distinct binary paths per app. Earlier revisions registered each
#    Linux app as a winpodx-<slug> ProgID whose `shell\open\command`
#    invoked powershell.exe (and later wscript.exe) with the shim
#    script. Windows' Open With menu deduplicates by underlying EXE
#    path, so all N entries collapsed into a single item. A per-slug
#    binary path is the fix.
#
# 2. .exe (Rust shim, `windows_subsystem = "windows"`) is what we
#    register now. Console-less PE subsystem = no flash when the user
#    clicks "Open with → <app>". Earlier .cmd/.vbs wrappers flashed
#    cmd.exe or used wscript's generic icon; the Rust .exe has neither
#    problem AND its embedded icon can be overridden per slug via the
#    `Applications\<exe>\DefaultIcon` registry value.
#
# 3. NTFS hard links keep the disk footprint flat. The shim is one
#    physical binary; per-slug files (`winpodx-<slug>.exe`) are hard
#    links to the same inode. 95 apps cost ~300 KB on disk total, not
#    300 KB × 95 = 28 MB. The shim reads its own filename at runtime
#    (`std::env::current_exe`) to figure out which slug it represents.
#
# We register under `HKCU\Software\Classes\Applications\<exe>\`
# rather than as ProgIDs because that's the canonical Windows path
# for per-app handlers; the OpenWithList ext linkage (rather than
# OpenWithProgids) is the matching surface.
# =====================================================================

[CmdletBinding()]
param(
    [string]$AppsJson = 'C:\Users\Public\winpodx\reverse-open\apps.json',
    [string]$IconsDir = 'C:\Users\Public\winpodx\reverse-open\icons',
    [string]$BinDir = 'C:\Users\Public\winpodx\reverse-open\bin',
    # Rust-built reverse-open shim (PE subsystem = windows, no console
    # flash, custom icon overridable per-slug via the Applications
    # subkey's DefaultIcon value). Each per-slug entry is a hard
    # link to this single binary.
    [string]$ShimExe = 'C:\Users\Public\winpodx\reverse-open\bin\winpodx-reverse-open-shim.exe',
    [string]$StartMenuDir = $(Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Linux Apps'),
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# --- helpers --------------------------------------------------------------

function Write-LogLine([string]$Level, [string]$Msg) {
    $ts = (Get-Date).ToUniversalTime().ToString('o')
    Write-Host "$ts [$Level] $Msg"
}

function Test-SlugValid([string]$Slug) {
    return $Slug -match '^[a-z0-9-]+$'
}

function Set-DefaultValue([string]$Key, [string]$Value) {
    # PowerShell's New-ItemProperty -Name '(default)' creates a value
    # named LITERALLY "(default)" -- it does NOT set the real unnamed
    # default value. The canonical way is Set-Item -Value, which the
    # registry provider routes to the default.
    if (-not (Test-Path -LiteralPath $Key)) {
        New-Item -Path $Key -Force | Out-Null
    }
    Set-Item -LiteralPath $Key -Value $Value
}

function Set-NamedValue([string]$Key, [string]$Name, [string]$Value) {
    if (-not (Test-Path -LiteralPath $Key)) {
        New-Item -Path $Key -Force | Out-Null
    }
    New-ItemProperty -Path $Key -Name $Name -Value $Value -PropertyType String -Force | Out-Null
}

# Curated MIME → extension table covering the most common types.
# Long-tail types fall through to a per-type-string fallback below
# (`Resolve-MimeExtensions`).
$script:DefaultMimeExt = @{
    'text/plain'       = @('.txt')
    'text/xml'         = @('.xml')
    'text/html'        = @('.html', '.htm')
    'text/css'         = @('.css')
    'text/markdown'    = @('.md', '.markdown')
    'application/json' = @('.json')
    'application/pdf'  = @('.pdf')
    'application/xml'  = @('.xml')
    'application/zip'  = @('.zip')
    'image/png'        = @('.png')
    'image/jpeg'       = @('.jpg', '.jpeg')
    'image/gif'        = @('.gif')
    'image/svg+xml'    = @('.svg')
    'image/webp'       = @('.webp')
    'image/bmp'        = @('.bmp')
    'image/tiff'       = @('.tif', '.tiff')
    'audio/mpeg'       = @('.mp3')
    'audio/ogg'        = @('.ogg')
    'audio/flac'       = @('.flac')
    'audio/wav'        = @('.wav')
    'video/mp4'        = @('.mp4')
    'video/webm'       = @('.webm')
    'video/x-matroska' = @('.mkv')
    'video/quicktime'  = @('.mov')
}

function Resolve-MimeExtensions([string]$Mime) {
    if ($script:DefaultMimeExt.ContainsKey($Mime)) {
        return $script:DefaultMimeExt[$Mime]
    }
    if ($Mime -match '^[a-z]+/(.+)$') {
        return @(".${matches[1]}".ToLowerInvariant())
    }
    return @()
}

# --- main -----------------------------------------------------------------

Write-LogLine 'INFO' "reading apps from $AppsJson"
if (-not (Test-Path -LiteralPath $AppsJson)) {
    Write-LogLine 'ERROR' 'apps.json missing — nothing to register'
    exit 1
}

$manifest = $null
try {
    $manifest = Get-Content -LiteralPath $AppsJson -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
} catch {
    Write-LogLine 'ERROR' "apps.json parse failed: $($_.Exception.Message)"
    exit 2
}

if ($null -eq $manifest -or -not $manifest.PSObject.Properties['apps']) {
    Write-LogLine 'ERROR' 'apps.json has no apps array'
    exit 2
}

if (-not (Test-Path -LiteralPath $ShimExe)) {
    Write-LogLine 'ERROR' "shim binary missing at $ShimExe — refusing to register handlers that point at a nonexistent path"
    exit 3
}

if (-not (Test-Path -LiteralPath $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}

# Helper: create a hard link from $LinkPath to $TargetPath. Falls back
# to a copy if the link command fails (e.g. cross-volume, or NTFS
# permissions). PowerShell's New-Item supports `-ItemType HardLink`
# since Win10, but the registry-friendly fallback is plain copy.
function New-HardLinkOrCopy([string]$LinkPath, [string]$TargetPath) {
    if (Test-Path -LiteralPath $LinkPath) {
        Remove-Item -LiteralPath $LinkPath -Force -ErrorAction SilentlyContinue
    }
    try {
        New-Item -ItemType HardLink -Path $LinkPath -Target $TargetPath -Force -ErrorAction Stop | Out-Null
        return $true
    } catch {
        try {
            Copy-Item -LiteralPath $TargetPath -Destination $LinkPath -Force -ErrorAction Stop
            return $false
        } catch {
            Write-LogLine 'WARN' "could not create $LinkPath (hardlink + copy both failed): $($_.Exception.Message)"
            return $false
        }
    }
}

$registered = 0
$skipped = 0
foreach ($app in $manifest.apps) {
    $slug = [string]$app.slug
    if (-not (Test-SlugValid $slug)) {
        Write-LogLine 'WARN' "skip invalid slug '$slug'"
        $skipped++
        continue
    }
    $name = [string]$app.name
    if ([string]::IsNullOrWhiteSpace($name)) { $name = $slug }

    # Honour the user's Linux-side default-handler choices from
    # ~/.config/mimeapps.list. Registering an app for every MIME it
    # *can* handle would flood the Windows "Open with" menu with
    # entries for every editor / image viewer / etc. -- noisy and
    # actively unhelpful. Instead, surface ONLY the apps the user has
    # explicitly set as their default on Linux, and only for the
    # extensions matching those MIME types.
    #
    # An app with empty `is_default_for` (the user hasn't picked it
    # as default for anything) is skipped entirely. The user can still
    # widen the registration scope later via the host-side allowlist
    # surface; the design doc covers that path in Phase 4.
    $mimes = @()
    if ($app.PSObject.Properties['is_default_for']) {
        foreach ($m in $app.is_default_for) { $mimes += [string]$m }
    }
    if ($mimes.Count -eq 0) {
        Write-LogLine 'INFO' "skip $slug — not the Linux default for any MIME type"
        $skipped++
        continue
    }
    $icoPath = Join-Path $IconsDir "$slug.ico"
    $exeName = "winpodx-$slug.exe"
    $exePath = Join-Path $BinDir $exeName
    $friendly = "Open with $name (Linux)"

    if ($DryRun) {
        Write-LogLine 'INFO' "[dry-run] would hard-link $exePath -> $ShimExe + register $friendly for $($mimes -join ',')"
        $registered++
        continue
    }

    # Hard-link the generic shim to its per-slug name. Same inode,
    # zero additional disk usage. The shim reads its own filename
    # at runtime to figure out which slug to embed in the request.
    [void](New-HardLinkOrCopy -LinkPath $exePath -TargetPath $ShimExe)

    $appRoot = "HKCU:\Software\Classes\Applications\$exeName"
    Set-NamedValue $appRoot 'FriendlyAppName' $friendly
    if (Test-Path -LiteralPath $icoPath) {
        Set-DefaultValue (Join-Path $appRoot 'DefaultIcon') "$icoPath,0"
    }
    Set-DefaultValue (Join-Path $appRoot 'shell\open\command') "`"$exePath`" `"%1`""

    # SupportedTypes lists every extension this app handles. The
    # value name is the extension; the value content is conventionally
    # empty. Windows uses this to decide whether to display the entry
    # in "Open with" for a given file type.
    $stKey = Join-Path $appRoot 'SupportedTypes'
    if (-not (Test-Path -LiteralPath $stKey)) {
        New-Item -Path $stKey -Force | Out-Null
    }

    $exts = New-Object System.Collections.Generic.HashSet[string]
    foreach ($mime in $mimes) {
        foreach ($ext in Resolve-MimeExtensions $mime) {
            if (-not $ext.StartsWith('.')) { continue }
            $extLower = $ext.ToLowerInvariant()
            if ($exts.Add($extLower)) {
                New-ItemProperty -Path $stKey -Name $extLower -Value '' -PropertyType String -Force | Out-Null
                # OpenWithList — alternative attach point that better
                # surfaces per-Application entries than OpenWithProgids.
                $extKey = "HKCU:\Software\Classes\$extLower\OpenWithList"
                if (-not (Test-Path -LiteralPath $extKey)) {
                    New-Item -Path $extKey -Force | Out-Null
                }
                New-ItemProperty -Path $extKey -Name $exeName -Value '' -PropertyType String -Force | Out-Null
            }
        }
    }
    Write-LogLine 'INFO' "registered $slug (exe=$exeName) for $($exts.Count) extension(s)"
    $registered++
}

# --- Start Menu shortcuts for ALL discovered apps --------------------------
#
# Per-user Linux Apps menu folder. Carries every discovered app
# (regardless of whether the Linux user designated it as the default
# for any MIME type) so:
#   1. The apps launch directly from Start Menu (no file argument
#      needed -- the .cmd handles missing %1 gracefully).
#   2. The user can pick a non-default Linux app for one-shot file
#      open by going "Right-click → Open with → Choose another app
#      → Look for another app on this PC" and browsing to
#      %APPDATA%\Microsoft\Windows\Start Menu\Programs\Linux Apps
#      to select a .lnk.
#
# This is the spiritual answer to "default가 없는 앱들은 어떻게
# 할까?" -- the Linux defaults stream into the canonical Windows
# "Open with" menu; the rest land in a discoverable Start Menu
# folder.

$startMenuCount = 0
if (-not $DryRun) {
    if (-not (Test-Path -LiteralPath $StartMenuDir)) {
        New-Item -ItemType Directory -Path $StartMenuDir -Force | Out-Null
    }
    $shell = New-Object -ComObject WScript.Shell

    foreach ($app in $manifest.apps) {
        $slug = [string]$app.slug
        if (-not (Test-SlugValid $slug)) { continue }
        $name = [string]$app.name
        if ([string]::IsNullOrWhiteSpace($name)) { $name = $slug }
        $icoPath = Join-Path $IconsDir "$slug.ico"
        $exePath = Join-Path $BinDir "winpodx-$slug.exe"

        # If the per-app .exe link doesn't exist (because the app
        # was skipped at the registration pass for having no Linux
        # defaults), hard-link it now so the shortcut has a target.
        if (-not (Test-Path -LiteralPath $exePath)) {
            [void](New-HardLinkOrCopy -LinkPath $exePath -TargetPath $ShimExe)
        }

        # Sanitise the name for use as a filename — strip illegal
        # chars and trim. Display label stays in the .lnk's
        # Description (visible in tooltips).
        $safeName = ($name -replace '[\\/:*?"<>|]', '_').Trim()
        if ([string]::IsNullOrWhiteSpace($safeName)) { $safeName = $slug }
        $lnkPath = Join-Path $StartMenuDir "$safeName.lnk"

        try {
            $lnk = $shell.CreateShortcut($lnkPath)
            $lnk.TargetPath = $exePath
            $lnk.Description = "$name (Linux)"
            if (Test-Path -LiteralPath $icoPath) {
                $lnk.IconLocation = "$icoPath,0"
            }
            $lnk.Save()
            $startMenuCount++
        } catch {
            Write-LogLine 'WARN' "could not write shortcut for ${slug}: $($_.Exception.Message)"
        }
    }
}

Write-LogLine 'INFO' "done. registered=$registered skipped=$skipped start_menu=$startMenuCount"
exit 0
