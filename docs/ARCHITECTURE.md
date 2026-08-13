# Architecture

**English** | [한국어](ARCHITECTURE.ko.md)

How WinPodX is put together: the data flow on app launch, the technology stack, and the source tree layout.

## How It Works

```
                     ┌─────────────────────────────┐
  Click "Word"       │     Linux Desktop (KDE,     │
  in app menu  ───>  │     GNOME, Sway, ...)       │
                     └──────────────┬──────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │         WinPodX             │
                     │  ┌─────────────────────┐    │
                     │  │ auto-provision:     │    │
                     │  │  config → password  │    │
                     │  │  → container → RDP  │    │
                     │  │  → desktop entries  │    │
                     │  └─────────────────────┘    │
                     └──────────────┬──────────────┘
                                    │ FreeRDP RemoteApp
                     ┌──────────────▼──────────────┐
                      │ Windows Container (Podman / │
                      │          Docker)            │
                     │   ┌──────────────────────┐  │
                     │   │  Word  Excel  PPT ...│  │
                     │   │ multi-session/rdprrap│  │
                     │   └──────────────────────┘  │
                      │ Host publish :3390 (TLS)    │
                     └─────────────────────────────┘
```

The pod's command channel is a bearer-authed HTTP agent listening on `http://+:8765/` inside the guest and published only on the host's `127.0.0.1:8765`. RDP is likewise published on `127.0.0.1:3390` with TLS encryption. Reverse-open (Linux apps appearing in the Windows "Open with..." menu) runs through a separate host-side listener daemon that receives requests pushed via the `\\tsclient\home` share.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.9-3.14 (stdlib only on 3.11+; `tomli` fallback on 3.9/3.10) |
| CLI | argparse (stdlib) |
| GUI (optional) | PySide6 (Qt6) |
| Config | TOML (stdlib `tomllib` on 3.11+ / `tomli` on 3.9/3.10; built-in writer) |
| RDP | FreeRDP 3+ (xfreerdp, RemoteApp/RAIL) |
| Guest agent | PowerShell `HttpListener` on `http://+:8765/`, host-published at `127.0.0.1:8765` (bearer auth, base64-encoded `/exec` payloads) |
| Container | Podman (default) / Docker ([dockur/windows](https://github.com/dockur/windows)); manual RDP is the containerless backend; libvirt was removed in 0.6.0 |
| Hypervisor | QEMU / KVM (inside the dockur container; host USB / PCI device passthrough is wired at this layer) |
| Reverse-open shim | Rust (`windows_subsystem = "windows"`, embedded per-slug icon via vendored rcedit) |
| i18n | `winpodx.core.i18n` (English-source-as-key, flat JSON catalogs per language) |
| CI | GitHub Actions (lint + test on Python 3.9-3.14 + informational pip-audit) |

## Project Structure

```
winpodx/
├── install.sh             # One-line installer (no pip)
├── uninstall.sh           # Clean uninstaller
├── src/winpodx/
│   ├── cli/               # argparse commands (app, pod, config, setup, host-open, ...)
│   ├── core/              # Config, RDP, provisioner, daemon, guest operations
│   │   ├── pod/           # Compose generation, lifecycle, health, ports, recovery
│   │   ├── transport/     # Agent / FreeRDP Transport ABC + dispatch policy
│   │   ├── discovery/     # Guest app scan, parse, persist (single-file package)
│   │   └── rotation/      # Password rotation + rollback (single-file package)
│   ├── backend/           # Podman, Docker, manual
│   ├── desktop/           # .desktop entries, icons, MIME, tray, notifications
│   ├── display/           # X11/Wayland detection, DPI scaling
│   ├── gui/               # Qt6 window/pages, launcher, dialogs, theme
│   │   └── icons/         # Bundled SVG icon loader, rendering, recolour, cache
│   ├── reverse_open/      # Discovery, ICO conversion, listener daemon, sync transport
│   ├── setup_wizard/      # Privileged host prep (KVM group, subuid/subgid)
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

- **App launch.** `cli.app._run_app()` → `provisioner.ensure_ready()` (config + pending/password rotation; if RDP is not already reachable: dependencies → compose → resume/start → two-stage RDP recovery → desktop entries → reverse-open listener self-heal) → `find_app()` → `rdp.launch_app()` → FreeRDP RemoteApp → `.cproc` tracking + process/window reapers + detached window setup.
- **Discovery.** `app refresh` / provisioning → `core.discovery.discover_apps()` → wait for agent or RDP readiness → `transport.dispatch()` (HTTP agent first, FreeRDP fallback unless agent-only is required) → guest `discover_apps.ps1` → parse bounded JSON/icon data → persist app TOML, icons, desktop entries, MIME types, and URL schemes. The default scan is Start-Menu-only; `desktop.full_app_scan` enables the legacy five-source scan.
- **Provisioning.** Fresh install → `winpodx provision --require-agent` → `finish_provisioning()` (`wait-ready → agent settle → apply fixes → discovery → reverse-open`). Existing-install upgrade → `winpodx migrate`, which performs guest sync/image migration before the same post-create chain.
- **Transport selection.** Host→guest command callers use `core.transport.dispatch()`: a healthy bearer-authenticated HTTP agent wins; otherwise FreeRDP is returned. `prefer="agent"` requires the agent and raises instead of falling back; `prefer="freerdp"` forces FreeRDP. User-facing RemoteApp launches always use FreeRDP directly.
- **App install (Linux side).** AppInfo (TOML) → `.desktop` file generation → icon install → MIME registration → icon cache refresh.
- **File open (host → guest).** Linux path → UNC path conversion (`\\tsclient\home\...`) → RDP `/app-cmd`.
- **Auto suspend.** `daemon.run_idle_monitor()` → no sessions for N seconds → pause the Podman/Docker container → lock file cleanup.
- **Auto resume.** `provisioner` → `daemon.ensure_pod_awake()` → unpause the Podman/Docker container → continue readiness checks.
- **Password rotation.** `ensure_ready()` → check `password_max_age` and running-pod state → change the guest password over the transport layer → regenerate compose + save config → rollback on persistence failure.
- **Reverse-open (guest → host).** Windows Explorer "Open with..." → per-slug `winpodx-<slug>.exe` shim → atomic JSON write to `\\tsclient\home\.local\share\winpodx\reverse-open\incoming\<uuid>.json` → host listener picks it up → `safe_open_unc` TOCTOU-safe path resolution → `xdg-open` invocation on the host.
- **Device passthrough (host → guest).** `winpodx device list / attach <id> / detach <id>` (also a GUI "Devices" page and a tray USB switcher) → device wired through to the guest at the QEMU (dockur) layer. USB hot-plugs live (`cfg.pod.usb_live`, default on); PCI is boot-added and needs a guest restart plus a safety confirmation (`--force` / dialog).

## Guest sync subsystem

**Code.** `src/winpodx/core/guest_sync.py`. Design notes: [docs/design/GUEST_SYNC_DESIGN.md](design/GUEST_SYNC_DESIGN.md).

Upgrading WinPodX on the host updates the host binary, but the guest-side
artifacts staged at first install (`C:\OEM\agent.ps1`, the urlacl reservation,
rdprrap / `shim.exe` / `rcedit.exe`, helper scripts) would otherwise go stale
until the user wipes and reinstalls Windows. Guest sync closes that gap
without a reinstall.

**Key enabler.** `/oem` is a **live bind mount** of the host's `config/oem`
(`{oem_dir}:/oem:Z` in `core/pod/compose.py`), so after a host upgrade the running
container's `/oem` *already* holds the new files — no image rebuild. Delivery
into the guest reuses the same channel as `winpodx guest recover-oem`: tar `/oem`
in the container → serve it over a one-shot HTTP server on `127.0.0.1:8766`
→ guest pulls via the QEMU NAT gateway `10.0.2.2`. Because the agent is alive
during sync, the pull and follow-up fixes run over the bearer-authed `/exec`
endpoint rather than the noVNC paste path.

`sync_guest` is ordered so a partial failure is safe to re-run:

1. **Deliver `/oem`** — guest `Invoke-WebRequest` + `tar -xzf` into `C:\OEM`.
   `install.bat` is **not** re-run (it carries one-shot first-boot logic —
   autologon, account setup — that must not fire on a live install).
2. **urlacl reservation** — re-applies install.bat's netsh block over `/exec`
   (delete overlapping `:8765` reservations, re-add `http://+:8765/` with the
   `WD` SID SDDL).
3. **Idempotent registry / runtime fixes** — calls
   `apply_windows_runtime_fixes(cfg)` (same chain as apply-fixes), which also
   re-activates rdprrap against the refreshed binaries.
4. **Restart the agent** — the agent serves the `/exec` it runs through, so it
   can't `Stop-Process` itself synchronously. A **one-shot scheduled task**
   fires ~5 s later to stop and relaunch `C:\OEM\agent.ps1`; the `/exec` call
   returns first, then the new agent rebinds `:8765` under the corrected urlacl.
5. **Stamp version** — writes `C:\winpodx\install-state\guest_version.json`
   (`{winpodx, oem_bundle}`) only after steps 1–3 succeed.

**Staleness check.** Host current = `winpodx.__version__` +
`core.info._bundled_oem_version()`. `guest_sync_needed(cfg)` reads the stamp via
`/exec`; a stamp that is present **and** older triggers a sync, a missing stamp
is recorded only (no disruption during a first-boot install still in progress).
Auto-runs after pod readiness when `cfg.pod.guest_autosync` (default `True`) is
set, gated to podman/docker. Manual: `winpodx guest sync [--force]` and a
GUI Tools → Sync Guest action. `sync_guest` returns a per-step result map so
the CLI/GUI can render rows.

## Disk auto-grow subsystem

**Code.** `src/winpodx/core/disk.py` (sizing + guest extend), triggered from
`src/winpodx/core/daemon.py` (idle path).

dockur only grows the virtual disk *image* when `cfg.pod.disk_size` increases
and the container is recreated — it never extends the guest's C: partition, and
it has **no online resize**. WinPodX adds an idle-time auto-grow that handles
both ends.

**Trigger.** On pod start / idle, if C: used% exceeds
`cfg.pod.disk_autogrow_threshold_pct` (default 80) **and** the pod is idle.

**Sizing.** Grows the image just enough to restore
`cfg.pod.disk_autogrow_target_free_pct` free (default 30%), rounded up to whole
`cfg.pod.disk_autogrow_increment` steps (default `32G`). The ceiling is the
smaller of the optional `cfg.pod.disk_max_size` and *what the host can actually
back* — `current + (host_free − reserve)`, where the reserve keeps auto-grow
from consuming the last of the host disk. If neither headroom is available the
grow is skipped with a log line.

**Why idle-only.** Since dockur has no online resize, every grow **recreates
the container** (a quick guest reboot). Scheduling it idle-only guarantees it
never interrupts a live RemoteApp session.

**Guest extend.** After the image grows, the new space lands at the end of the
disk but C: still ends where it did. The extend runs over `/exec`:
`Resize-Partition -DriveLetter C`. dockur's Windows layout puts a small WinRE
Recovery partition **right after** C:, blocking the extend — so the step
detaches WinRE (`reagentc /disable`), deletes the blocking recovery partition,
extends C:, then re-enables WinRE (`reagentc /enable`, which falls back to
`C:\Windows` when no dedicated partition is present).

## UI internationalization (i18n)

**Code.** `src/winpodx/core/i18n.py`; catalogs in `src/winpodx/locale/<lang>.json`.

The Linux-side UI text (tray, GUI, CLI) is wrapped in
`winpodx.core.i18n.tr(text)`. The **English string is the catalog key** —
`tr()` looks the source string up in the active-language catalog and falls back
to that same English source per-string on a miss, so an incomplete catalog
never blanks the UI. Catalogs are flat `{ "<english>": "<translation>" }` JSON.
The active language is resolved from `[ui] language` (default `auto`, which maps
the host locale from `$LC_ALL` / `$LC_MESSAGES` / `$LANG`, unknown → English).
Seven languages ship: en, ko, zh, ja, de, fr, it. (Distinct from
`pod.language`, which is the *Windows guest* install language.)

## Advanced: Custom Windows ISO

WinPodX ships first-class support for the dockur-curated Windows
editions (Win10 / 11, LTSC, IoT LTSC, Tiny, Server 2016+). The list
lives in `_KNOWN_WIN_VERSIONS` in `src/winpodx/core/config.py` and
the GUI Settings → Container/VM card exposes it as a dropdown.

If you need to boot a Windows ISO that dockur does **not** curate
(your own pre-loaded installer image, an Enterprise edition with
specific debloat preset, a localised build dockur hasn't tagged),
you can pass it through manually. **This path is unsupported** —
WinPodX's OEM scripts (`install.bat`, `agent.ps1`, `rdprrap`) are
written against the dockur-curated Win10+ family. A custom ISO may
boot but fail to surface the agent, the multi-session enabler, or
RemoteApp discovery. Bug reports specific to custom-ISO installs
fall on you to debug.

With that disclaimer, pass the ISO during a fresh setup:

```bash
winpodx setup --win-iso ~/winpodx-custom.iso
```

Setup stages it as `<storage_path>/custom.iso` before the container
boots, so dockur uses it instead of downloading an image. This requires
the bind-mount storage layout used by fresh installs; existing named-volume
pods must migrate storage before they can use this path.
