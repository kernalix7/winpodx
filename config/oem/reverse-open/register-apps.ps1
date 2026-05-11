# =====================================================================
# winpodx reverse-open — register the Linux app handlers in Windows.
#
# Reads `C:\Users\Public\winpodx\reverse-open\apps.json` (synced from
# the host) and creates per-app registry entries that surface each
# Linux app in Windows Explorer's "Open with" submenu for the MIME
# extensions it advertises.
#
# Registry shape per app (see design doc §"Guest side → register-apps.ps1"):
#
#   HKCU\Software\Classes\winpodx-<slug>\
#     (Default)                = "<name>"
#     DefaultIcon              = "<ico path>,0"
#     shell\open\(Default)     = "Open with <name>"
#     shell\open\command\(Default) =
#       "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle
#        Hidden -File <shim> -Slug <slug> -File \"%1\""
#
#   HKCU\Software\Classes\<ext>\OpenWithProgids\
#     winpodx-<slug> = ""    (empty value — Explorer treats the value
#                             name as the ProgID handle)
#
# We use HKCU (not HKCR) so the per-user enumerable. Explorer reads
# OpenWithProgids from both HKLM\Software\Classes and HKCU\Software\
# Classes; HKCU writes don't require admin and survive a guest user
# session reset.
#
# unregister-apps.ps1 (sibling script) walks the same shape and removes
# everything; the registry diff between register and unregister is by
# design exhaustive so a partial run leaves no orphan handlers.
# =====================================================================

[CmdletBinding()]
param(
    [string]$AppsJson = 'C:\Users\Public\winpodx\reverse-open\apps.json',
    [string]$IconsDir = 'C:\Users\Public\winpodx\reverse-open\icons',
    [string]$ShimPath = 'C:\Users\Public\winpodx\reverse-open\shim\winpodx-reverse-open-shim.ps1',
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

# Curated MIME → extension table covering the 80 most common types
# the design doc identifies. Loaded from a JSON blob the host syncs
# alongside apps.json under the same dir; missing file → fall back to
# the minimal built-in table below so registration still completes
# for at least the basic types.
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
    # Best-effort fallback — derive an extension by stripping the
    # type/ prefix. Won't help with `application/octet-stream` etc but
    # avoids missing the long tail entirely.
    if ($Mime -match '^[a-z]+/(.+)$') {
        return @(".${matches[1]}".ToLowerInvariant())
    }
    return @()
}

function Set-RegValue([string]$Key, [string]$Name, [string]$Value) {
    if (-not (Test-Path -LiteralPath $Key)) {
        New-Item -Path $Key -Force | Out-Null
    }
    if ($Name -eq '') {
        New-ItemProperty -Path $Key -Name '(default)' -Value $Value -PropertyType String -Force | Out-Null
    } else {
        New-ItemProperty -Path $Key -Name $Name -Value $Value -PropertyType String -Force | Out-Null
    }
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

if (-not (Test-Path -LiteralPath $ShimPath)) {
    Write-LogLine 'ERROR' "shim missing at $ShimPath — refusing to register handlers that point at a nonexistent path"
    exit 3
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
    $mimes = @()
    if ($app.PSObject.Properties['mime_types']) {
        foreach ($m in $app.mime_types) { $mimes += [string]$m }
    }
    if ($mimes.Count -eq 0) {
        Write-LogLine 'WARN' "skip $slug — no mime types"
        $skipped++
        continue
    }
    $icoPath = Join-Path $IconsDir "$slug.ico"

    $progId = "winpodx-$slug"
    $progRoot = "HKCU:\Software\Classes\$progId"
    $shellOpen = Join-Path $progRoot 'shell\open'
    $shellCmd = Join-Path $shellOpen 'command'

    # The shim is invoked with -Slug and -File. `%1` is the file path
    # that Windows substitutes; quoting it allows spaces. We escape
    # quotes manually because the cmdline goes through the registry
    # value verbatim.
    $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ShimPath`" -Slug `"$slug`" -File `"%1`""
    $friendly = "Open with $name (Linux)"

    if ($DryRun) {
        Write-LogLine 'INFO' "[dry-run] would register $slug for $($mimes -join ',') -> $cmd"
        $registered++
        continue
    }

    Set-RegValue $progRoot '' $friendly
    Set-RegValue $progRoot 'FriendlyTypeName' $friendly
    if (Test-Path -LiteralPath $icoPath) {
        Set-RegValue (Join-Path $progRoot 'DefaultIcon') '' "$icoPath,0"
    }
    Set-RegValue $shellOpen '' $friendly
    Set-RegValue $shellCmd '' $cmd

    # Attach to each MIME's OpenWithProgids subkey. The value name is
    # the ProgID; the value content is conventionally empty.
    $exts = New-Object System.Collections.Generic.HashSet[string]
    foreach ($mime in $mimes) {
        foreach ($ext in Resolve-MimeExtensions $mime) {
            if (-not $ext.StartsWith('.')) { continue }
            $extLower = $ext.ToLowerInvariant()
            if ($exts.Add($extLower)) {
                $extKey = "HKCU:\Software\Classes\$extLower\OpenWithProgids"
                if (-not (Test-Path -LiteralPath $extKey)) {
                    New-Item -Path $extKey -Force | Out-Null
                }
                New-ItemProperty -Path $extKey -Name $progId -Value '' -PropertyType String -Force | Out-Null
            }
        }
    }
    Write-LogLine 'INFO' "registered $slug for $($exts.Count) extension(s)"
    $registered++
}

Write-LogLine 'INFO' "done. registered=$registered skipped=$skipped"
exit 0
