# Changelog

**English** | [한국어](docs/CHANGELOG.ko.md)

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Zero-config auto-provisioning**: First app launch auto-creates config, compose.yaml, starts pod, and registers desktop entries
- **14 bundled app definitions**: Word, Excel, PowerPoint, Outlook, OneNote, Access, Notepad, Explorer, CMD, PowerShell, Paint, Calculator, VS Code, Teams
- **Auto suspend/resume**: Container pauses when idle, auto-resumes on app launch, with graceful shutdown via stop_event
- **Password auto-rotation**: Cryptographically secure random password (20-char), auto-rotated every 7 days (configurable via `password_max_age`), with rollback on failure
- **`winpodx rotate-password`**: Manual password rotation command
- **Office lock file cleanup**: `winpodx cleanup` removes `~$*.*` lock files from home directory
- **Windows time sync**: `winpodx timesync` forces clock resync after host sleep/wake
- **Windows debloat**: `winpodx debloat` disables telemetry, ads, Cortana, search indexing
- **Power management**: `winpodx power --suspend/--resume` for manual container pause/unpause
- **System diagnostics**: `winpodx info` shows display, dependencies, and config status
- **Desktop notifications**: Notifies on app launch via D-Bus/notify-send
- **Smart DPI scaling**: Auto-detects scale from GNOME, KDE Plasma 5/6, Sway, Hyprland, Cinnamon, env vars, xrdb
- **Qt system tray**: Pod controls, app launchers, maintenance tools, idle monitor, auto-refresh
- **Backend abstraction**: Podman (default), Docker, libvirt/KVM, manual RDP with unified interface
- **Compose.yaml generation**: Auto-generated for Podman/Docker backends with dockur/windows image
- **Per-app taskbar separation**: Each app gets its own WM_CLASS and `StartupWMClass` for independent taskbar icons
- **Windows build pinning**: Feature updates blocked via `TargetReleaseVersion` registry policy, security updates allowed
- **CI: Upstream update monitoring**: Weekly checks for new dockur/windows releases — creates PRs automatically
- **GUI: Container restart prompt**: Prompts to restart container when CPU, RAM, or port settings change
- **GUI: Scale as dropdown**: FreeRDP scale limited to valid values (100%/140%/180%) via QComboBox
- **GUI: Concurrent launch protection**: Threading lock prevents simultaneous app launch crashes
- **GUI: Windows Update toggle**: Enable/Disable buttons with status display, triple-layer block (services + scheduled tasks + hosts file)
- **Sound and printer**: RDP audio (`/sound:sys:alsa`) and printer redirection (`/printer`) enabled by default
- **USB drive sharing**: Removable media auto-shared via `/drive:media` — USB drives plugged in after session start appear as subfolders without reconnecting
- **USB device redirection**: `/usb:auto` enabled by default — if FreeRDP urbdrc plugin is available, USB devices appear as real USB in Windows; falls back to drive sharing if not
- **USB auto drive mapping**: Windows-side FileSystemWatcher script auto-maps USB subfolders to drive letters (E:, F:, ...) when plugged in, unmaps when removed — event-driven, no polling
- Desktop integration: `.desktop` entries, hicolor icons, MIME type registration, icon cache refresh
- argparse-based CLI: app, pod, config, setup, tray, info, cleanup, timesync, debloat, power, rotate-password commands
- TOML configuration with 0600 file permissions for credential protection
- FreeRDP session management with process tracking (.cproc files) and zombie process reaper
- winapps.conf import for migration from existing setups

### Security
- Atomic config writes: `os.fsync(fd)` + parent directory fsync + `os.replace()` — prevents torn writes on power loss
- Symlink / path-traversal hardening in `bundled_data_path()`: `.resolve()` + `is_relative_to()` checks and `copy2(..., follow_symlinks=False)` — blocks installs pointing outside bundled data dirs
- Subprocess timeouts in desktop integration (`update_icon_cache`, `notify-send`) — prevents indefinite hangs from unresponsive helpers
- `import_winapps_config` now filters untrusted `RDP_FLAGS` through `_filter_extra_flags()` — prevents malicious winapps.conf injecting arbitrary FreeRDP flags
- Config and compose.yaml files created with 0600 permissions
- RDP certificate: `/cert:ignore` for localhost only, `/cert:tofu` for remote connections
- Password filtered from log output
- App name validation (alphanumeric + dash/underscore only) to prevent injection
- Notification text sanitized (control characters removed, HTML escaped, length limited)
- PID file exclusive locking (`fcntl.flock`) to prevent race conditions on concurrent launches
- Zombie process reaper (daemon thread per RDP process) to prevent process table leaks
- Config `_apply()` uses `dataclasses.fields()` allowlist to prevent arbitrary attribute injection
- SecurityLayer=2 (TLS) for encrypted RDP channel in OEM install and registry template
- TLS-only RDP authentication for Podman backend (`/sec:tls`) — NLA/Kerberos fails in `podman unshare` namespace
- Exit code 145 (SIGTERM) treated as normal app close, not error
- Subprocess error handling with timeout in debloat (CLI + GUI)
- PowerShell username escaping: single quotes doubled to prevent command injection in `net user` calls
- Password timestamp timezone handling: naive timestamps upgraded to UTC, `TypeError` caught alongside `ValueError`

### Fixed
- Config `_apply()` bool coercion: `bool("false")` was returning `True` — now uses explicit string mapping
- Password rotation rollback: revert was using the already-overwritten new password instead of the original
- RDP `launch_app()` lock file leak: PID file not cleaned up when `Popen` fails
- DPI detection: `_xrdb_scale()` zero DPI guard to prevent 0.0 scale factor
- YAML escape: `_yaml_escape()` now handles `\n` and `\r` to prevent YAML structure injection
- libvirt `get_ip()`: added returncode check and `TimeoutExpired` exception handling
- FreeRDP RemoteApp: removed `/rfx` flag that caused immediate transport failure in RAIL mode
- RDP reaper thread: stderr pipe deadlock — `proc.wait()` could hang indefinitely once the 64KB pipe buffer filled; now uses `communicate()` and stores last 2KB on the session
- TOML writer: control characters 0x00-0x1F and 0x7F were emitted raw, breaking the file; now escaped as `\uXXXX`
- media_monitor.ps1: `net use /delete` exit code ignored; now keeps tracking if unmount fails so next sync can retry
- RDP session reuse: `_find_existing_session` accepted any process with `winpodx` in cmdline (including `winpodx app list`), silently returning a fake session on PID reuse. Unified into `process.is_freerdp_pid()` that matches only `freerdp`/`xfreerdp`
- `linux_to_unc`: silently returned UNC paths for directories not shared over RDP (e.g. `/tmp`), causing Windows "path not found" errors. Now raises `ValueError` outside `$HOME`/media share; caller converts to a clear user-facing error
- Password rotation state marker: if both `cfg.save()` and Windows password rollback fail, a `.rotation_pending` marker is persisted so `ensure_ready()` warns on every launch until the user runs `winpodx rotate-password`
- `unregister_mime_types`: destroyed `mimeapps.list` by deleting whole lines whose values contained a winpodx entry, orphaning unrelated app associations. Now parses with `configparser`, removes only the targeted entries, and writes atomically
- Desktop entries & theme index: explicit `encoding="utf-8"` — non-ASCII `full_name` (Korean/Japanese) previously crashed install on `C`/`POSIX` locales
- GUI icon lookup: `Path(__file__).parent × 4` worked only in source layout and broke after `pip install`. New `bundled_data_path()` helper resolves from source, wheel share-data, and `~/.local/share/winpodx/data/`
- Pod start race: container started but RDP port not yet listening caused the first app launch to fail. `pod.start()` now calls `backend.wait_for_ready(timeout=cfg.pod.boot_timeout)` before returning
- Hardcoded container name: multiple modules used the literal `"winpodx-windows"`, so users who customized the container couldn't run winpodx. Now flows through `cfg.pod.container_name`
- `setup` EOF handling: `input()` raised `EOFError` on piped stdin, crashing non-interactive setup. New `_ask()` helper detects non-TTY and returns defaults; `handle_rotate_password` now uses a three-phase commit with a temp compose file
- Password alphabet: removed `

 from generated passwords — broke PowerShell single-quote escaping in a subset of tickets; kept `!@#%&*` which survive every shell context we use
- App install retry: `winpodx app install-all` failed silently on `RuntimeError`. Now widened to `(ProvisionError, RuntimeError)` and refreshes the icon cache after bulk install
- Wayland DPI detection: previously picked the first output only. Now iterates all outputs and takes the max scale; falls back to Qt `devicePixelRatio()` when compositor exposes no scale
- Desktop entry icon hygiene: raster formats silently installed into `hicolor/scalable/apps/` (spec requires SVG). Now enforces SVG for scalable and falls back to size-specific dirs for raster icons
- App profile writes: `gui/app_dialog.save_app_profile` now uses explicit `encoding="utf-8"` — same non-ASCII crash that affected desktop entries
- Teams app path: `data/apps/teams/app.toml` pointed at the old Classic Teams executable. Now targets `%LOCALAPPDATA%\Microsoft\WindowsApps\ms-teams.exe`
- Explorer app categories: removed `Office` (invalid for a file manager) and added `FileTools`, `System`
- CI audit job: `pip install -e .[all]` failed on GitHub runners without libvirt headers. New `all-no-libvirt` extra keeps the audit scope while unblocking CI
- Test isolation: new `tests/conftest.py` autouse fixture redirects `HOME` and `XDG_*` to tmp dirs — prevents tests from scribbling into the developer's real config

### Changed
- Default RDP port changed from 3389 to 3390 (avoids collision with other containers)
- Default VNC port set to 8007 (avoids collision with LinOffice on 8006)
- FreeRDP search order: xfreerdp3 → xfreerdp → sdl-freerdp3 → sdl-freerdp → flatpak
- `wlfreerdp` removed from search order (deprecated upstream by FreeRDP project)
- Uninstall always removes container (previously only with `--purge`)
- RemoteApp (RAIL) enabled via `fDisabledAllowList` + `fInheritInitialProgram` registry keys — seamless app windows without full desktop
- `podman unshare --rootless-netns` wrapper for FreeRDP — required for rootless Podman RDP access
- Per-app desktop notification removed (was noisy on every launch)

### Removed
- **RDPWrap multi-session**: Removed all RDPWrap binaries, scripts, CI workflows, and Python modules — multi-session support will be developed as a separate project
- `data/templates/app.desktop.j2` (unused Jinja2 template)
- Dead code: `icons_cache_dir()`, `decode_base64_icon()`, `MISSING_DEPS_MSG`
