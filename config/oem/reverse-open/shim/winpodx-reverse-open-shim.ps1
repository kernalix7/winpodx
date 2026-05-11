# =====================================================================
# winpodx reverse-open shim (PowerShell)
#
# Invoked by Windows Explorer when the user picks a Linux app from the
# right-click "Open with" menu. Receives:
#   -Slug <app-slug>    (e.g. "org-kde-kate")
#   -File <Windows path>  (e.g. "C:\Users\User\Documents\notes.xml")
#
# The shim's only job is to atomically write a JSON request to the
# FreeRDP drive redirect, where the host's reverse-open listener
# (winpodx.reverse_open.listener) is watching. The host translates
# the Windows path through `\\tsclient\home\...` back to its POSIX
# equivalent, validates it through Phase 1's TOCTOU-safe
# `safe_open_unc`, and spawns the registered Linux app with the
# resolved file as an argv slot.
#
# Atomicity: we write `<uuid>.json.tmp` then `Rename-Item` to
# `<uuid>.json`. NTFS rename is atomic on the same volume, and the
# host listener only matches `<uuid>.json` (not `.tmp`), so a partial
# write can't trigger a spawn with garbage content.
#
# Design doc §"Guest side → Go shim" (PowerShell shim is the v1
# implementation; Go is a v2 optimisation if startup latency
# matters in practice). PowerShell startup is ~300-500ms on first
# invocation, faster on warm cache. Right-click "Open with" is a
# human-triggered event, so 500ms latency is acceptable for v1.
# =====================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Slug,

    [Parameter(Mandatory = $true)]
    [string]$File
)

$ErrorActionPreference = 'Stop'

# Slug grammar — must match the host-side validator (kebab-case,
# digits OK). Reject anything else immediately so a malformed registry
# entry can't trigger a stray spawn on the host.
if ($Slug -notmatch '^[a-z0-9-]+$') {
    [Console]::Error.WriteLine("shim: invalid slug '$Slug'")
    exit 2
}

# Refuse empty file paths. Windows file managers always pass an
# absolute path, but defensive validation prevents a malicious
# registry handler from triggering an empty-path request that the
# host listener would reject anyway (better to fail fast on the
# guest side and surface to the user).
if ([string]::IsNullOrWhiteSpace($File)) {
    [Console]::Error.WriteLine('shim: empty file path')
    exit 2
}

# Convert the local Windows path to the UNC form the host listener
# expects. The host has the user's `$HOME` exposed at `\\tsclient\home`
# via FreeRDP's `+home-drive` redirect (see core/rdp.py: `+home-drive`
# + `/drive:media,...`).
#
# If the file is already a UNC path (`\\tsclient\<share>\...`), pass
# through verbatim. Otherwise we need the path to be on the redirected
# `\\tsclient\home` share — Explorer hands us the actual on-host POSIX
# path mapped to a local Windows drive letter (Z: by default on dockur);
# this only works if the file is reachable through that redirect, which
# in practice covers the user's $HOME tree and any `/drive:media,...`
# mount.
if ($File.StartsWith('\\')) {
    $unc = $File
} else {
    # Windows path of the form `Z:\path\to\file` — strip the drive
    # letter and prefix with `\\tsclient\home\`. The host validates
    # the resolved path against the share roots map, so a path that
    # doesn't actually live under $HOME will be rejected at validation
    # time with a clear error in the host's reverse-open.log.
    $rest = $File
    if ($File.Length -ge 3 -and $File[1] -eq ':' -and $File[2] -eq '\') {
        $rest = $File.Substring(3)
    }
    $unc = "\\tsclient\home\$rest"
}

# Build the request payload. The schema must match what the host
# listener validates in `winpodx.reverse_open.listener._validate_schema`.
$uid = [guid]::NewGuid().ToString('N')
$payload = @{
    version = 1
    app     = $Slug
    path    = $unc
    ts      = (Get-Date).ToUniversalTime().ToString('o')
    pod_id  = $null
}
$json = $payload | ConvertTo-Json -Compress -Depth 4

# The incoming directory lives under the user's $HOME on the host.
# The `\\tsclient\home` redirect maps directly to the user's home,
# so the Windows-side path is `\\tsclient\home\.local\share\winpodx\
# reverse-open\incoming\`.
$baseUnc = '\\tsclient\home\.local\share\winpodx\reverse-open\incoming'
try {
    if (-not (Test-Path -LiteralPath $baseUnc)) {
        # `Test-Path` on a UNC the host hasn't created returns False;
        # that's fine — the host's `winpodx host-open start-listener`
        # is responsible for mkdir'ing the dir. We surface this clearly
        # so the user knows what to fix.
        [Console]::Error.WriteLine(
            "shim: incoming dir not reachable at $baseUnc. " +
            "Run 'winpodx host-open start-listener' on the Linux host."
        )
        exit 3
    }
} catch {
    [Console]::Error.WriteLine("shim: incoming dir probe failed: $($_.Exception.Message)")
    exit 3
}

$tmpPath = Join-Path $baseUnc "$uid.json.tmp"
$finalPath = Join-Path $baseUnc "$uid.json"

try {
    # Write to .tmp first so a partial write can't be picked up by
    # the host listener (which only matches `<uuid>.json`).
    Set-Content -LiteralPath $tmpPath -Value $json -Encoding ASCII -NoNewline -Force
    # Rename is atomic on NTFS / SMB-mapped shares; the host sees
    # either nothing or a complete file.
    Rename-Item -LiteralPath $tmpPath -NewName "$uid.json" -Force
    exit 0
} catch {
    [Console]::Error.WriteLine("shim: write failed: $($_.Exception.Message)")
    try { Remove-Item -LiteralPath $tmpPath -Force -ErrorAction Stop } catch { }
    exit 1
}
