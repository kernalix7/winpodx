# media_monitor.ps1 — Auto-map USB drives as network drives in Explorer
# Uses FileSystemWatcher (event-driven, no polling) to detect USB mount/unmount.
# Maps new USB subfolders in \\tsclient\media to drive letters (E:, F:, ...).
# Runs as a background process, started via registry Run key.

$mediaPath = "\\tsclient\media"
$availableLetters = @("E","F","G","H","I","J","K","L","N","O","P","Q","R","S","T","U","V","W","X","Y","Z")
$mapped = @{}

function Get-NextFreeLetter {
    foreach ($letter in $script:availableLetters) {
        $drive = "${letter}:"
        if (-not (Test-Path $drive) -and -not ($script:mapped.ContainsValue($letter))) {
            return $letter
        }
    }
    return $null
}

function Sync-Drives {
    # Enumerate USB subfolders. A transient SMB hiccup here should NOT abort
    # the whole sync — Get-ChildItem already has -ErrorAction SilentlyContinue,
    # so a failed enum yields an empty list and the next event retries.
    $children = @(Get-ChildItem -Path $script:mediaPath -Directory -ErrorAction SilentlyContinue)

    $current = @{}
    foreach ($child in $children) {
        $current[$child.Name] = $true

        if (-not $script:mapped.ContainsKey($child.Name)) {
            $letter = Get-NextFreeLetter
            if (-not $letter) { continue }

            # Rely on `net use` for error handling instead of gating on
            # Test-Path — the Test-Path/net-use sequence is non-atomic
            # (the drive may appear/disappear between the two calls), and
            # Test-Path against `E:` can block for ~1-2s on stale SMB mounts.
            # Any failure is logged at the exit code and retried next tick.
            try {
                & net use "${letter}:" $child.FullName /persistent:no 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    $script:mapped[$child.Name] = $letter
                }
                # Non-zero exit = not fatal. Most common causes:
                #   - letter races another mount (resolved next tick)
                #   - share momentarily unavailable (resolved next tick)
                #   - system error 85 (already in use) — recovers after unmount
            } catch {
                # net use can throw on environment-level failures (missing
                # binary, elevated-context mismatch). Swallow so the watcher
                # thread stays alive; the next Created/Deleted event will retry.
            }
        }
    }

    $toRemove = @()
    foreach ($entry in $script:mapped.GetEnumerator()) {
        if ($current.ContainsKey($entry.Key)) { continue }

        try {
            & net use "$($entry.Value):" /delete /yes 2>&1 | Out-Null
            # Drop tracking on success, OR when the drive letter is already
            # gone (unmount may have raced us). Keep tracking if the letter
            # still resolves — we'll retry on the next sync tick.
            if ($LASTEXITCODE -eq 0) {
                $toRemove += $entry.Key
            } elseif (-not (Get-PSDrive -Name $entry.Value -ErrorAction SilentlyContinue)) {
                $toRemove += $entry.Key
            }
        } catch {
            # Non-fatal: retry on next sync.
        }
    }
    foreach ($key in $toRemove) {
        $script:mapped.Remove($key)
    }
}

# Wait for RDP media share to become available
while (-not (Test-Path $mediaPath)) {
    Start-Sleep -Seconds 3
}

# Initial sync — map any USB drives already plugged in
Sync-Drives

# Watch for changes (event-driven, no polling)
$watcher = New-Object System.IO.FileSystemWatcher
$watcher.Path = $mediaPath
$watcher.NotifyFilter = [IO.NotifyFilters]::DirectoryName
$watcher.IncludeSubdirectories = $false
$watcher.EnableRaisingEvents = $true

Register-ObjectEvent $watcher "Created" -Action { Sync-Drives } | Out-Null
Register-ObjectEvent $watcher "Deleted" -Action { Sync-Drives } | Out-Null
Register-ObjectEvent $watcher "Renamed" -Action { Sync-Drives } | Out-Null

# Keep script alive (events fire on background threads)
Wait-Event -Timeout ([int]::MaxValue)
