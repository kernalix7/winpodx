# SPDX-License-Identifier: MIT
# media_monitor.ps1 - Auto-map USB drives as drive letters in the guest.
# The host redirects removable media so each volume shows up as a subfolder of
# \\tsclient\media (\\tsclient\media\<LABEL>). This maps each one to a free
# drive letter (E:, F:, ...) and unmaps it when the volume goes away.
#
# Delivered + registered at RUNTIME by the host agent (provisioner
# _apply_media_monitor), NOT by install.bat -- it must never sit in the OEM
# bundle / C:\OEM, because adding a file there re-triggers the intermittent
# Defender/rdprrap install deadlock that hangs first boot (#613/#638). The
# WinpodxMedia HKCU\Run entry starts one instance per interactive logon
# (full desktop AND each RemoteApp session), since drive mappings are
# per-logon-session.
#
# Why polling (not FileSystemWatcher): \\tsclient is an RDP drive redirection,
# and redirected drives do NOT deliver directory-change notifications, so a
# FileSystemWatcher never fires for a USB plugged in after the session starts
# (verified #613). Polling Get-ChildItem is the only reliable hotplug path.

$mediaPath = "\\tsclient\media"
$pollSeconds = 5
$availableLetters = @("E","F","G","H","I","J","K","L","N","O","P","Q","R","S","T","U","V","W","X","Y","Z")
$mapped = @{}

# One instance per session. The Run entry can fire more than once in a session;
# a mutex WITHOUT the "Global\" prefix is session-local, so duplicates exit
# instead of mapping the same volume to two letters.
$script:__mmMutex = New-Object System.Threading.Mutex($false, "winpodx-media-monitor")
if (-not $script:__mmMutex.WaitOne(0)) { return }

# net use adds the drive to the session but does NOT tell the shell, so an
# already-open Explorer never shows the new drive. SHChangeNotify after each
# map/unmap refreshes it live. The shell32 P/Invoke is compiled lazily (first
# map) so the common no-USB case never shells out to csc.exe.
$script:__shellReady = $false

function Notify-Shell([string]$root, [int]$eventId) {
    # eventId: 0x100 = SHCNE_DRIVEADD, 0x80 = SHCNE_DRIVEREMOVED.
    # uFlags 0x0005 = SHCNF_PATHW (dwItem1 is a wide path like "E:\").
    try {
        if (-not $script:__shellReady) {
            Add-Type -Namespace WinPodX -Name Shell -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll", CharSet = System.Runtime.InteropServices.CharSet.Auto)]
public static extern void SHChangeNotify(int wEventId, uint uFlags, System.IntPtr dwItem1, System.IntPtr dwItem2);
'@
            $script:__shellReady = $true
        }
        $ptr = [System.Runtime.InteropServices.Marshal]::StringToHGlobalUni($root)
        try {
            [WinPodX.Shell]::SHChangeNotify($eventId, 0x0005, $ptr, [System.IntPtr]::Zero)
        } finally {
            [System.Runtime.InteropServices.Marshal]::FreeHGlobal($ptr)
        }
    } catch {
    }
}

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
                    Notify-Shell "${letter}:\" 0x100   # SHCNE_DRIVEADD
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
                Notify-Shell "$($entry.Value):\" 0x80   # SHCNE_DRIVEREMOVED
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

# Poll loop. Sync-Drives maps newly-appeared volumes and unmaps gone ones, so a
# single periodic call handles both plug and unplug. When the media share isn't
# present (console session, or before the RDP redirection is up) Test-Path is
# false and we just idle until it appears.
while ($true) {
    try {
        if (Test-Path $mediaPath) { Sync-Drives }
    } catch {
    }
    Start-Sleep -Seconds $pollSeconds
}
