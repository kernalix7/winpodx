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
reliable delivery path every other guest script (`agent.ps1`, `power-monitor.ps1`,
rdprrap, …) uses. `install.bat` copies it to `C:\winpodx\media_monitor.ps1` and
registers it in the HKCU Run key. The copy step searches, in order:

1. **`%~dp0media_monitor.ps1`** (= `C:\OEM\media_monitor.ps1`, preferred).
   Always present because it rides the existing `/oem` bind mount.
2. `C:\winpodx-scripts\media_monitor.ps1` (only if a future compose mount
   provides it; not wired today).
3–6. `\\tsclient\home\…\config\oem\media_monitor.ps1` for several install
   layouts — **belt-and-braces only**: `\\tsclient` (RDP drive redirection) is
   not mounted at first boot, when there is no RDP session yet, so these never
   fire during the unattended install. Kept for hand re-runs from a live session.

If none match, `install.bat` prints a warning; the USB auto-mapper stays
disabled until a boot that finds the script. With the file in the OEM bundle,
option 1 always matches on a normal install (#613).
