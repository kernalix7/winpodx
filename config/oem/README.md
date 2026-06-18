# WinPodX OEM post-install

Files in this directory are mounted read-only into the guest at `/oem` by
`config/oem:/oem:Z` in the generated compose.yaml. The dockur/windows image
executes `install.bat` once, on first boot, after Windows OOBE finishes.

## Files

| File | Purpose |
|------|---------|
| `install.bat` | One-shot first-boot configurator: DNS, RDP/NLA, RemoteApp, firewall, power plan, telemetry lockdown, desktop shortcuts to the `\\tsclient` shares. |
| `toggle_updates.ps1` | Runtime toggle for Windows Update (`enable`/`disable`/`status`). Edits `hosts` with `-Encoding ASCII` (PS 5.1 ANSI default and PS 7 UTF-8-BOM both break the Windows DNS client's `hosts` parser). |

## USB media

There is no drive-letter auto-mapper. Removable media plugged into the host is
redirected by FreeRDP and reachable in every session at `\\tsclient\media\<LABEL>`;
`install.bat` puts a desktop **USB** shortcut pointing at `\\tsclient\media`. A
real block device (its own drive letter, raw access) is available through USB
passthrough. The old `media_monitor.ps1` that mapped each volume to a drive
letter was removed (#613/#638): it could not surface a letter reliably in
RemoteApp (RAIL) sessions and, when shipped in the OEM bundle, re-triggered the
intermittent Defender/rdprrap first-boot install deadlock.
