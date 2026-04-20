# winpodx OEM post-install

Files in this directory are mounted read-only into the guest at `/oem` by
`config/oem:/oem:Z` in the generated compose.yaml. The dockur/windows image
executes `install.bat` once, on first boot, after Windows OOBE finishes.

## Files

| File | Purpose |
|------|---------|
| `install.bat` | One-shot first-boot configurator: DNS, RDP/NLA, RemoteApp, firewall, power plan, telemetry lockdown, USB media auto-mapper hookup. |
| `toggle_updates.ps1` | Runtime toggle for Windows Update (`enable`/`disable`/`status`). Edits `hosts` with `-Encoding ASCII` (PS 5.1 ANSI default and PS 7 UTF-8-BOM both break the Windows DNS client's `hosts` parser). |

## Media monitor wiring (see `install.bat:102` region)

`install.bat` copies `scripts/windows/media_monitor.ps1` to
`C:\winpodx\media_monitor.ps1` and registers it in the HKCU Run key. The copy
step searches the following sources, in order:

1. **`C:\winpodx-scripts\media_monitor.ps1`** (preferred). Mount
   `scripts/windows` into the guest at `C:\winpodx-scripts` via compose
   (editable installs, wheel installs, and flatpak all work uniformly this
   way, no dependency on where the Linux package ended up).
2. `\\tsclient\home\.local\share\winpodx\scripts\windows\media_monitor.ps1`
   (pip wheel install: `sys.prefix/share/winpodx/...` when `sys.prefix` is
   the user's home).
3. `\\tsclient\home\.local\pipx\venvs\winpodx\share\winpodx\scripts\windows\media_monitor.ps1`
   (pipx install).
4. `\\tsclient\home\winpodx\scripts\windows\media_monitor.ps1` (source
   checkout at `~/winpodx`).
5. `\\tsclient\home\.local\bin\winpodx-app\scripts\windows\media_monitor.ps1`
   (legacy manual install path, kept for backward compatibility).

If none match, `install.bat` prints a warning and leaves the Run key pointing
at a non-existent file; the USB auto-mapper is disabled until the next boot
that finds the script.

### Recommended: compose mount

Platform/QA owns `pyproject.toml` and `data/`, but compose generation lives
in `src/winpodx/cli/setup_cmd.py` (CLI team). To enable option 1 above, the
CLI team needs to add a bind mount to `_COMPOSE_TEMPLATE_BASE`:

```yaml
    volumes:
      - winpodx-data:/storage:Z
      - {oem_dir}:/oem:Z
      - {scripts_dir}:/oem/winpodx-scripts:ro,Z   # <- add
```

…and arrange for `install.bat` to `xcopy /Y /E C:\winpodx-scripts*` into
`C:\winpodx-scripts` during first boot (dockur exposes `/oem` at
`C:\OEM\` on the Windows side; a `/oem/winpodx-scripts` subdir would be
visible there). Until that lands, the fallback search in `install.bat`
covers the common install layouts.

See the TODO(M4) comment in `install.bat` for the handoff point.
