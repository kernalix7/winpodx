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
    if (-not (Test-Path $script:mediaPath)) { return }

    $current = @{}
    Get-ChildItem -Path $script:mediaPath -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $current[$_.Name] = $true

        if (-not $script:mapped.ContainsKey($_.Name)) {
            $letter = Get-NextFreeLetter
            if ($letter) {
                net use "${letter}:" $_.FullName /persistent:no 2>$null
                if ($LASTEXITCODE -eq 0) {
                    $script:mapped[$_.Name] = $letter
                }
            }
        }
    }

    $toRemove = @()
    foreach ($entry in $script:mapped.GetEnumerator()) {
        if (-not $current.ContainsKey($entry.Key)) {
            net use "$($entry.Value):" /delete /yes 2>$null
            $toRemove += $entry.Key
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
