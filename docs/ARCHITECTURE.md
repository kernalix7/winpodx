# Architecture

**English** | [한국어](ARCHITECTURE.ko.md)

How winpodx is put together: the data flow on app launch, the technology stack, and the source tree layout.

## How It Works

```
                     ┌─────────────────────────────┐
  Click "Word"       │     Linux Desktop (KDE,     │
  in app menu  ───>  │     GNOME, Sway, ...)       │
                     └──────────────┬──────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │         winpodx             │
                     │  ┌─────────────────────┐    │
                     │  │ auto-provision:     │    │
                     │  │  config → password  │    │
                     │  │  → container → RDP  │    │
                     │  │  → desktop entries  │    │
                     │  └─────────────────────┘    │
                     └──────────────┬──────────────┘
                                    │ FreeRDP RemoteApp
                     ┌──────────────▼──────────────┐
                     │   Windows Container (Podman)│
                     │   ┌──────────────────────┐  │
                     │   │  Word  Excel  PPT ...│  │
                     │   │ multi-session/rdprrap│  │
                     │   └──────────────────────┘  │
                     │   127.0.0.1:3390 (TLS)      │
                     └─────────────────────────────┘
```

The pod's command channel is a bearer-authed HTTP agent listening on `127.0.0.1:8765` inside the guest (loopback only). RDP itself runs on `127.0.0.1:3390` with TLS encryption. Reverse-open (Linux apps appearing in the Windows "Open with..." menu) runs through a separate host-side listener daemon that receives requests pushed via the `\\tsclient\home` share.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.9+ (stdlib only on 3.11+; `tomli` fallback on 3.9/3.10) |
| CLI | argparse (stdlib) |
| GUI (optional) | PySide6 (Qt6) |
| Config | TOML (stdlib `tomllib` on 3.11+ / `tomli` on 3.9/3.10; built-in writer) |
| RDP | FreeRDP 3+ (xfreerdp, RemoteApp/RAIL) |
| Guest agent | PowerShell `HttpListener` on `127.0.0.1:8765` (bearer auth, base64-encoded `/exec` payloads) |
| Container | Podman / Docker ([dockur/windows](https://github.com/dockur/windows)) |
| VM | libvirt / KVM |
| Reverse-open shim | Rust (`windows_subsystem = "windows"`, embedded per-slug icon via vendored rcedit) |
| CI | GitHub Actions (lint + test on 3.9-3.13 + pip-audit) |

## Project Structure

```
winpodx/
├── install.sh             # One-line installer (no pip)
├── uninstall.sh           # Clean uninstaller
├── src/winpodx/
│   ├── cli/               # argparse commands (app, pod, config, setup, host-open, ...)
│   ├── core/              # Config, RDP, pod lifecycle, provisioner, daemon
│   ├── backend/           # Podman, Docker, libvirt, manual
│   ├── desktop/           # .desktop entries, icons, MIME, tray, notifications
│   ├── display/           # X11/Wayland detection, DPI scaling
│   ├── gui/               # Qt6 main window, app dialog, theme, reverse-open Settings card
│   ├── reverse_open/      # Discovery, ICO conversion, listener daemon, sync transport
│   └── utils/             # XDG paths, deps, TOML writer, winapps compat
├── data/                  # winpodx GUI desktop entry + icon + config example
├── config/oem/
│   ├── install.bat        # Windows OEM first-boot orchestration
│   └── reverse-open/      # register-apps.ps1, unregister-apps.ps1, Rust shim, rcedit
├── scripts/windows/       # PowerShell scripts (debloat, time sync, USB mapping, app discovery)
├── packaging/             # OBS / AUR / RHEL spec + maintainer docs
├── debian/                # Debian source package layout
├── docs/                  # User docs (English + Korean mirrors)
├── .github/workflows/     # CI: lint + test + publish (OBS / RHEL / deb / AUR)
└── tests/                 # pytest test suite
```

## Key Data Flows

- **App launch.** CLI → `provisioner.ensure_ready()` (config + password rotation + compose + resume + pod + bundled apps + desktop entries) → FreeRDP session → `.cproc` tracking + reaper thread + desktop notification.
- **App install (Linux side).** AppInfo (TOML) → `.desktop` file generation → icon install → MIME registration → icon cache refresh.
- **File open (host → guest).** Linux path → UNC path conversion (`\\tsclient\home\...`) → RDP `/app-cmd`.
- **Auto suspend.** `daemon.run_idle_monitor()` → no sessions for N seconds → `podman pause` → lock file cleanup.
- **Auto resume.** `provisioner` → `daemon.ensure_pod_awake()` → `podman unpause` → wait for RDP.
- **Password rotation.** `ensure_ready()` → check `password_max_age` → generate new password → save config + compose → recreate container → rollback on failure.
- **Reverse-open (guest → host).** Windows Explorer "Open with..." → per-slug `winpodx-<slug>.exe` shim → atomic JSON write to `\\tsclient\home\.local\share\winpodx\reverse-open\incoming\<uuid>.json` → host listener picks it up → `safe_open_unc` TOCTOU-safe path resolution → `xdg-open` invocation on the host.

## Advanced: Custom Windows ISO

winpodx ships first-class support for the dockur-curated Windows
editions (Win10 / 11, LTSC, IoT LTSC, Tiny, Server 2016+). The list
lives in `_KNOWN_WIN_VERSIONS` in `src/winpodx/core/config.py` and
the GUI Settings → Container/VM card exposes it as a dropdown.

If you need to boot a Windows ISO that dockur does **not** curate
(your own pre-loaded installer image, an Enterprise edition with
specific debloat preset, a localised build dockur hasn't tagged),
you can pass it through manually. **This path is unsupported** —
winpodx's OEM scripts (`install.bat`, `agent.ps1`, `rdprrap`) are
written against the dockur-curated Win10+ family. A custom ISO may
boot but fail to surface the agent, the multi-session enabler, or
RemoteApp discovery. Bug reports specific to custom-ISO installs
fall on you to debug.

With that disclaimer:

1. Place your `.iso` somewhere readable (e.g. `~/winpodx-custom.iso`).
2. Edit your `winpodx.toml` to set `win_version = "custom"`:

   ```toml
   [pod]
   win_version = "custom"
   ```

   winpodx will log a one-line WARNING that the value isn't on its
   known list, then pass it through to dockur as-is.

3. Edit the generated `~/.config/winpodx/compose.yaml` to mount the
   ISO at the path dockur looks for:

   ```yaml
   services:
     windows:
       volumes:
         - ~/winpodx-custom.iso:/storage/custom.iso
         # ...existing volumes stay
   ```

4. Recreate the container:

   ```bash
   winpodx pod stop
   podman compose -f ~/.config/winpodx/compose.yaml up -d
   ```

The compose template is regenerated by `winpodx setup` and
`winpodx pod start` on certain code paths (cpu / ram / port / user
changes via the GUI Save button, for example) — your manual edit
will be overwritten there. Re-apply after any such regeneration.

If you find yourself doing this routinely and the upstream dockur
project doesn't carry your edition, file a feature request: a
narrow `cfg.pod.custom_iso_path` field is on the table but is not
shipped today.
