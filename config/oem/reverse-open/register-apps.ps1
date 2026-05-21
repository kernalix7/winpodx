# SPDX-License-Identifier: MIT
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
# Icon surface — embedded in the per-slug EXE (rcedit)
# -----------------------------------------------------
# Earlier revisions tried `Applications\<exe>\DefaultIcon` and a
# per-slug ProgID with `\DefaultIcon` — neither is honoured by the
# Win10/Win11 "Open with…" chooser when rendering entries registered
# under `Applications\<exe>`. Explorer reads the chooser entry icon
# from the EXE's embedded PE resource, period.
#
# The fix: each per-slug `winpodx-<slug>.exe` is now an INDEPENDENT
# COPY of the shim (NOT a hard link) whose icon resource has been
# overwritten with `<slug>.ico` via vendored `rcedit.exe`. PR #165's
# inode-sharing optimisation is sacrificed (~500 KB × N apps on disk)
# in exchange for icons that actually show up.
#
# Registration surfaces stay the same:
#
#   a. `HKCU\Software\Classes\Applications\<exe>\` — FriendlyAppName +
#      SupportedTypes + shell\open\command. Required for the entry to
#      appear in the "Open with → Choose another app" long dialog at
#      all.
#
#   b. `HKCU\Software\Classes\<ext>\OpenWithList\<exe>` — SUB-KEY (NOT
#      a value). Drives the inline short "Open with" menu visibility
#      (Win10 + Win11). #166's fix.
#
#   c. `HKCU\Software\Classes\winpodx-<slug>` — per-slug ProgID with
#      shell\open\command, linked to each extension via
#      `<ext>\OpenWithProgids\winpodx-<slug>`. No `DefaultIcon` —
#      Explorer ignores it for the chooser anyway, and the embedded
#      EXE icon already covers every chooser path.
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
    # rcedit (electron/rcedit, MIT) — patches a per-slug `.exe` copy
    # to embed the matching `.ico` as its icon resource. Without this
    # the Open With chooser falls back to the generic .exe glyph.
    [string]$RcEditExe = 'C:\Users\Public\winpodx\reverse-open\bin\rcedit.exe',
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

if (-not (Test-Path -LiteralPath $RcEditExe)) {
    Write-LogLine 'ERROR' "rcedit binary missing at $RcEditExe — required to embed per-slug icons into the per-slug .exe copies"
    exit 3
}

if (-not (Test-Path -LiteralPath $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}

# Helper: stage a per-slug .exe by copying the source shim and then
# embedding the per-slug icon into its PE resource section. Returns
# $true on success, $false on failure (logged).
#
# The copy step replaces the earlier hard-link approach. Hard links
# share an inode, which means an icon embedded into one name would
# also appear on every other name pointing at the same inode — not
# what we want. We need N independent .exe files with N different
# embedded icons, so each must be a real copy.
function New-PerSlugExe([string]$ExePath, [string]$IconPath) {
    if (Test-Path -LiteralPath $ExePath) {
        Remove-Item -LiteralPath $ExePath -Force -ErrorAction SilentlyContinue
    }
    try {
        Copy-Item -LiteralPath $ShimExe -Destination $ExePath -Force -ErrorAction Stop
    } catch {
        Write-LogLine 'WARN' "could not copy shim to ${ExePath}: $($_.Exception.Message)"
        return $false
    }
    if (-not [string]::IsNullOrEmpty($IconPath) -and (Test-Path -LiteralPath $IconPath)) {
        try {
            $rcOutput = & $RcEditExe $ExePath --set-icon $IconPath 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-LogLine 'WARN' "rcedit failed for ${ExePath} (rc=$LASTEXITCODE): $rcOutput"
                return $false
            }
        } catch {
            Write-LogLine 'WARN' "rcedit invocation failed for ${ExePath}: $($_.Exception.Message)"
            return $false
        }
    }
    return $true
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
        Write-LogLine 'INFO' "[dry-run] would copy $ShimExe -> $exePath + embed $icoPath + register $friendly for $($mimes -join ',')"
        $registered++
        continue
    }

    # Stage the per-slug .exe as an independent copy with the per-slug
    # icon embedded into its PE resource section. Without the embed,
    # the Open With chooser entry renders with the generic .exe icon.
    [void](New-PerSlugExe -ExePath $exePath -IconPath $icoPath)
    if (-not (Test-Path -LiteralPath $icoPath)) {
        Write-LogLine 'WARN' "icon missing for $slug at $icoPath — chooser entry will use the unmodified shim icon"
    }

    $appRoot = "HKCU:\Software\Classes\Applications\$exeName"
    Set-NamedValue $appRoot 'FriendlyAppName' $friendly
    Set-DefaultValue (Join-Path $appRoot 'shell\open\command') "`"$exePath`" `"%1`""

    # --- per-slug ProgID (long-dialog surface) ---------------------------
    # The ProgID anchors the `<ext>\OpenWithProgids\winpodx-<slug>`
    # value (written in the per-extension loop below) so the entry
    # surfaces in both the short Open With menu and the long "Choose
    # another app" dialog. DefaultIcon intentionally omitted — Explorer
    # ignores it for chooser entries, and the embedded EXE icon
    # already covers every chooser path.
    $progIdRoot = "HKCU:\Software\Classes\winpodx-$slug"
    Set-DefaultValue $progIdRoot $friendly
    Set-NamedValue $progIdRoot 'FriendlyTypeName' $friendly
    Set-DefaultValue (Join-Path $progIdRoot 'shell\open\command') "`"$exePath`" `"%1`""

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
                # OpenWithList — Windows convention is a SUB-KEY named
                # after the executable, NOT a value under OpenWithList.
                # The values under \OpenWithList\ are the MRU list (a,
                # b, c, MRUList), which is for tracking the user's most
                # recently used picks — Explorer reads the sub-key
                # names to populate the inline short "Open with" menu.
                # Writing a value here makes us invisible to the short
                # menu (it appears only in the long "Choose another
                # app" dialog).
                $extKey = "HKCU:\Software\Classes\$extLower\OpenWithList\$exeName"
                if (-not (Test-Path -LiteralPath $extKey)) {
                    New-Item -Path $extKey -Force | Out-Null
                }
                # OpenWithProgids — VALUE (not sub-key). Names the
                # per-slug ProgID we registered above so Explorer
                # resolves the entry's icon from the ProgID's
                # DefaultIcon. Empty REG_NONE value is the documented
                # convention.
                $owpKey = "HKCU:\Software\Classes\$extLower\OpenWithProgids"
                if (-not (Test-Path -LiteralPath $owpKey)) {
                    New-Item -Path $owpKey -Force | Out-Null
                }
                New-ItemProperty -Path $owpKey -Name "winpodx-$slug" -Value ([byte[]]@()) -PropertyType None -Force | Out-Null
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

        # If the per-app .exe doesn't exist (because the app was
        # skipped at the registration pass for having no Linux
        # defaults), stage it now so the shortcut has a target —
        # icon embedded if available, plain copy otherwise.
        if (-not (Test-Path -LiteralPath $exePath)) {
            [void](New-PerSlugExe -ExePath $exePath -IconPath $icoPath)
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

# --- Desktop folder shortcut + Quick Access pin ----------------------------
#
# Win11's "Open with → Choose another app → Look for another app on
# this PC" surface is a regular file-browse dialog. Without a hint, the
# user has to navigate to %APPDATA%\Microsoft\Windows\Start Menu\
# Programs\Linux Apps\ — long path, not obvious. We drop two
# discoverability aids that point at the existing $StartMenuDir:
#
#   1. Desktop\Linux Apps.lnk — folder shortcut on the desktop. The
#      dialog's left sidebar always has Desktop; one click + one
#      double-click gets the user to the .lnk list.
#   2. Quick Access pin — adds the folder to File Explorer's left
#      sidebar (and every Common Item Dialog inherits that sidebar).
#      Implemented via the documented "pintohome" shell verb.
#
# Both are best-effort: failure logs a WARN but doesn't fail the
# script, since the per-extension Open With registration above is
# what actually delivers the feature.
$folderShortcuts = 0
if (-not $DryRun -and (Test-Path -LiteralPath $StartMenuDir)) {
    # Desktop folder shortcut.
    try {
        $desktopDir = [Environment]::GetFolderPath('Desktop')
        if ($desktopDir -and (Test-Path -LiteralPath $desktopDir)) {
            $desktopLnk = Join-Path $desktopDir 'Linux Apps.lnk'
            $lnk = $shell.CreateShortcut($desktopLnk)
            $lnk.TargetPath = $StartMenuDir
            $lnk.Description = 'Linux apps available via winpodx'
            # Use the generic shell32 folder icon (index 4) so the
            # shortcut renders as a normal folder. Trying to embed our
            # own .ico here would require shipping a winpodx-folder.ico;
            # the generic folder glyph is sufficient and unsurprising.
            $lnk.IconLocation = 'shell32.dll,4'
            $lnk.Save()
            $folderShortcuts++
            Write-LogLine 'INFO' "wrote Desktop folder shortcut: $desktopLnk"
        }
    } catch {
        Write-LogLine 'WARN' "could not write Desktop folder shortcut: $($_.Exception.Message)"
    }

    # Quick Access pin via Shell.Application's "pintohome" verb.
    # This is the documented mechanism for File Explorer's "Pin to
    # Quick access" right-click action.
    try {
        $shellApp = New-Object -ComObject Shell.Application
        $folder = $shellApp.Namespace($StartMenuDir)
        if ($folder) {
            $item = $folder.Self
            # Look for the localised verb; fall back to the canonical
            # English name. Localised systems (e.g. ko-KR
            # "즐겨찾기에 고정") still respond to "pintohome" when
            # invoked programmatically.
            $verb = $item.Verbs() | Where-Object { $_.Name -replace '&', '' -match '(pintohome|Pin to Quick access|즐겨찾기에 고정)' } | Select-Object -First 1
            if ($null -eq $verb) {
                # Direct invoke by canonical name — works on all locales.
                $item.InvokeVerb('pintohome')
            } else {
                $verb.DoIt()
            }
            $folderShortcuts++
            Write-LogLine 'INFO' "pinned Linux Apps to Quick Access"
        }
    } catch {
        Write-LogLine 'WARN' "could not pin to Quick Access: $($_.Exception.Message)"
    }
}

# --- invalidate Explorer's Open With cache --------------------------------
#
# Win11 caches the per-extension Application list aggressively. After
# writing OpenWithList sub-keys + Applications\<exe>\SupportedTypes,
# the next right-click → "Open with" can still surface a stale list
# until the user logs off / on, OR until `ie4uinit.exe -show` is
# invoked. `-show` is documented as "Refresh icon cache + Open With
# list" and lands instantly (no UI side-effect for the user).
#
# Best-effort: missing binary OR non-zero exit is logged but doesn't
# fail the script — the registration itself is still durable. We also
# poke the Shell.Application "Refresh" to nudge any open Explorer
# windows.
if (-not $DryRun) {
    try {
        $ie4uinit = Join-Path $env:SystemRoot 'System32\ie4uinit.exe'
        if (Test-Path -LiteralPath $ie4uinit) {
            & $ie4uinit -show 2>&1 | Out-Null
            Write-LogLine 'INFO' 'invalidated Open With chooser cache'
        } else {
            Write-LogLine 'WARN' "ie4uinit not at $ie4uinit — chooser cache may be stale until next logon"
        }
    } catch {
        Write-LogLine 'WARN' "ie4uinit refresh failed: $($_.Exception.Message)"
    }

    # SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0) — the
    # canonical "file associations changed, refresh per-entry icons"
    # signal. `ie4uinit -show` covers the OpenWith MRU list but does
    # NOT invalidate the shell icon cache; without this P/Invoke,
    # per-slug ProgID DefaultIcons land in the registry but Explorer
    # keeps painting the previous (often generic) icon until next
    # logon. Shell32 export, present on every supported Windows.
    try {
        Add-Type -Namespace WinPodx -Name Shell32Ext -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll")]
public static extern void SHChangeNotify(int wEventId, uint uFlags, System.IntPtr dwItem1, System.IntPtr dwItem2);
'@ -ErrorAction Stop
        # SHCNE_ASSOCCHANGED = 0x08000000; SHCNF_IDLIST = 0x0000
        [WinPodx.Shell32Ext]::SHChangeNotify(0x08000000, 0x0000, [System.IntPtr]::Zero, [System.IntPtr]::Zero)
        Write-LogLine 'INFO' 'SHChangeNotify(SHCNE_ASSOCCHANGED) — icon cache invalidated'
    } catch {
        Write-LogLine 'WARN' "SHChangeNotify failed (icons may need re-logon to refresh): $($_.Exception.Message)"
    }
}

Write-LogLine 'INFO' "done. registered=$registered skipped=$skipped start_menu=$startMenuCount folder_shortcuts=$folderShortcuts"
exit 0
