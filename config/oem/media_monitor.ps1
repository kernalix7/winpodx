# SPDX-License-Identifier: MIT
# media_monitor.ps1 - Auto-map USB drives as drive letters in the guest.
# The host redirects removable media so each volume shows up as a subfolder of
# \\tsclient\media (\\tsclient\media\<LABEL>). This maps each one to a free
# drive letter (E:, F:, ...) and unmaps it when the volume goes away.
# Started via the WinpodxMedia HKCU\Run entry; one instance per logon session.
#
# Why polling (not FileSystemWatcher): \\tsclient is an RDP drive redirection,
# and redirected drives do NOT deliver directory change notifications, so a
# FileSystemWatcher never fires for a USB plugged in after the session starts
# (verified #613). Polling Get-ChildItem is the only reliable way to catch
# hotplug on a redirected drive. Test-Path/Get-ChildItem on \\tsclient\media
# are cheap (~60 ms) and non-blocking even when the share is absent (the
# console/autologon session has no tsclient redirection), so this idles
# harmlessly there and does the real work in the RDP session.

$mediaPath = "\\tsclient\media"
$pollSeconds = 5
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
    # Failures anywhere are non-fatal; the next poll retries.
    $children = @(Get-ChildItem -Path $script:mediaPath -Directory -ErrorAction SilentlyContinue)

    $current = @{}
    foreach ($child in $children) {
        $current[$child.Name] = $true

        if (-not $script:mapped.ContainsKey($child.Name)) {
            $letter = Get-NextFreeLetter
            if (-not $letter) { continue }

            try {
                & net use "${letter}:" $child.FullName /persistent:no 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    $script:mapped[$child.Name] = $letter
                }
            } catch {
            }
        }
    }

    $toRemove = @()
    foreach ($entry in $script:mapped.GetEnumerator()) {
        if ($current.ContainsKey($entry.Key)) { continue }

        try {
            & net use "$($entry.Value):" /delete /yes 2>&1 | Out-Null
            # Drop tracking if delete succeeded or the letter is already gone.
            if ($LASTEXITCODE -eq 0) {
                $toRemove += $entry.Key
            } elseif (-not (Get-PSDrive -Name $entry.Value -ErrorAction SilentlyContinue)) {
                $toRemove += $entry.Key
            }
        } catch {
        }
    }
    foreach ($key in $toRemove) {
        $script:mapped.Remove($key)
    }
}

# Poll loop. Sync-Drives maps newly-appeared volumes and unmaps gone ones, so
# a single periodic call handles both plug and unplug. When the media share
# isn't present (console session, or before the RDP redirection is up) the
# Test-Path is false and we just idle until it appears.
while ($true) {
    try {
        if (Test-Path $mediaPath) { Sync-Drives }
    } catch {
    }
    Start-Sleep -Seconds $pollSeconds
}
