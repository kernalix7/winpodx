# WinPodX OEM post-install

Files in this directory are mounted read-only into the guest at `/oem` by
`config/oem:/oem:Z` in the generated compose.yaml. The dockur/windows image
executes `install.bat` once, on first boot, after Windows OOBE finishes.

## Files

| File | Purpose |
|------|---------|
| `install.bat` | One-shot first-boot configurator: DNS, RDP/NLA, RemoteApp, firewall, power plan, telemetry lockdown, USB media auto-mapper hookup. |
| `toggle_updates.ps1` | Runtime toggle for Windows Update (`enable`/`disable`/`status`). Edits `hosts` with `-Encoding ASCII` (PS 5.1 ANSI default and PS 7 UTF-8-BOM both break the Windows DNS client's `hosts` parser). |

## Media monitor wiring

`media_monitor.ps1` ships **in this OEM bundle** (`config/oem/media_monitor.ps1`),
so dockur stages it into `C:\OEM\` during the unattended install — the same
reliable delivery path every other guest script uses. `install.bat` copies it
from `%~dp0` (= `C:\OEM\`) to `C:\winpodx\media_monitor.ps1` and registers it in
the HKCU Run key. Copy-source order:

1. **`%~dp0media_monitor.ps1`** (= `C:\OEM\media_monitor.ps1`, preferred) — always
   present because it rides the existing `/oem` bind mount.
2. `C:\winpodx-scripts\media_monitor.ps1` — only if a future compose mount
   provides it; not wired today.
3–6. `\\tsclient\home\…\config\oem\media_monitor.ps1` — **belt-and-braces only**:
   `\\tsclient` (RDP drive redirection) isn't mounted at first boot, so these
   never fire during the unattended install.

### How it maps drives

The host redirects removable media so each volume appears as a subfolder of
`\\tsclient\media` (`\\tsclient\media\<LABEL>`). `media_monitor.ps1` **polls**
that path every few seconds and `net use`-maps each volume to a free drive
letter (and unmaps it when the volume disappears).

It polls rather than using a `FileSystemWatcher` because `\\tsclient` is an RDP
drive redirection, and redirected drives do **not** deliver directory-change
notifications — a watcher never fires for a USB plugged in after the session
starts (#613). Polling is the only reliable hotplug mechanism on a redirected
drive. The script runs once per interactive logon (WinpodxMedia HKCU\Run); in a
session without the media redirection (the console/autologon session) it just
idles, doing the real work in the RDP session where `\\tsclient\media` exists.
