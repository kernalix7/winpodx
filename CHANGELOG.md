# Changelog

**English** | [한국어](docs/CHANGELOG.ko.md)

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.3] - 2026-04-21

### CI
- **`obs-publish.yml`**: Poll `/public/build/...` instead of `/build/...`. The `runservice` `OBS_TOKEN` can only trigger services — OBS `/build/` endpoints require authentication, and anonymous reads are only allowed through the `/public/` mirror. Previously the wait-for-build loop hit HTTP 401 until the 60-minute timeout.

### Packaging
- **Debian**: `debian/rules` adds `override_dh_auto_test:` to skip test execution during `.deb` build. Upstream CI already runs `pytest` across Python 3.11-3.13, and pybuild's unittest discover mode couldn't find `pytest` in the minimal Build-Depends set.

## [0.1.2] - 2026-04-21

### CI
- **`obs-publish.yml` / `debs-publish.yml`**: Trigger on `push: tags: v*.*.*` instead of `release: [published]`. GitHub Actions blocks recursive workflow triggers initiated by `GITHUB_TOKEN`, so a Release created by `release.yml` was not firing the downstream publishers. Tag push cascades normally; all three workflows now run in parallel and converge on the same Release via `softprops/action-gh-release@v2` (create-or-update) and `gh release upload --clobber`.

### Packaging
- **RPM spec**: `%files` globs `winpodx-*.dist-info/` instead of pinning to `%{version}`. Prevents OBS build failures when `pyproject.toml` has drifted past the latest git tag (OBS builds the wheel from HEAD `pyproject.toml` but filenames the tarball from `@PARENT_TAG@`).

## [0.1.1] - 2026-04-21

### Packaging
- **Prebuilt packages via Release channels**: RPMs (openSUSE Tumbleweed, Leap 15.6/16.0, Slowroll, Fedora 42/43) come from OBS `home:Kernalix7/winpodx`; `.deb` (Debian 12/13, Ubuntu 24.04/25.04/25.10) comes from GitHub Actions `debs-publish.yml`; sdist + wheel come from `release.yml`. All three attach to the same GitHub Release.
- **RPM spec**: Use `python313` on Leap 16.0 / Tumbleweed (`suse_version >= 1600`), keep `python311` on Leap 15.x. Explicit `python3-pluggy` `BuildRequires` on Fedora 42 resolves the pluggy/pluggy1.3 ambiguity.
- **OBS `_service`**: Removed `debtransform` step (OBS workers don't have the service installed, and public OBS can't build Debian source format `3.0 (native)`). `.deb` packaging moved to GitHub Actions container matrix with `dpkg-buildpackage`.
- **Debian packaging**: Added `debian/` directory at project root, `3.0 (native)` format with `dh-python + pybuild + pyproject`.

### CI
- **`check-windows-updates.yml`**: Failed with "GitHub Actions is not permitted to create or approve pull requests" because the repo setting is off. Rewritten to open a tracking Issue instead of pushing a branch and opening a PR. No repo setting changes required; uses only the default `GITHUB_TOKEN`.

### Docs
- **README**: New "Install" section covers openSUSE (zypper), Fedora (dnf), Debian/Ubuntu (apt `.deb`), and source install paths.
- **README**: New "Releasing & Packaging" section documents the three channels, plus the one-time `osc token` → `OBS_TOKEN` GitHub Secret setup.
- **Korean README**: Mirrored to match English README changes.
- **`packaging/obs/README.md`**: Documents `.deb` handling moved to Actions; AppImage declared unsupported (Python + Qt + FreeRDP + Podman deps negate portability).

## [0.1.0] - 2026-04-21

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
- **CI: Upstream update monitoring**: Weekly checks for new dockur/windows releases; creates PRs automatically
- **GUI: Container restart prompt**: Prompts to restart container when CPU, RAM, or port settings change
- **GUI: Scale as dropdown**: FreeRDP scale limited to valid values (100%/140%/180%) via QComboBox
- **GUI: Concurrent launch protection**: Threading lock prevents simultaneous app launch crashes
- **GUI: Windows Update toggle**: Enable/Disable buttons with status display, triple-layer block (services + scheduled tasks + hosts file)
- **Sound and printer**: RDP audio (`/sound:sys:alsa`) and printer redirection (`/printer`) enabled by default
- **USB drive sharing**: Removable media auto-shared via `/drive:media`; USB drives plugged in after session start appear as subfolders without reconnecting
- **USB device redirection**: `/usb:auto` enabled by default; if FreeRDP urbdrc plugin is available, USB devices appear as real USB in Windows; falls back to drive sharing if not
- **USB auto drive mapping**: Windows-side FileSystemWatcher script auto-maps USB subfolders to drive letters (E:, F:, ...) when plugged in, unmaps when removed (event-driven, no polling)
- Desktop integration: `.desktop` entries, hicolor icons, MIME type registration, icon cache refresh
- argparse-based CLI: app, pod, config, setup, tray, info, cleanup, timesync, debloat, power, rotate-password commands
- TOML configuration with 0600 file permissions for credential protection
- FreeRDP session management with process tracking (.cproc files) and zombie process reaper
- winapps.conf import for migration from existing setups

### Performance
- **`find_freerdp()` success caching**: hand-rolled module-level cache (not `lru_cache`) that stores successful lookups only. First call scans PATH and (as fallback) runs `flatpak list` with a 10s timeout; subsequent calls in the same process are free. ``None`` (not-found) results are deliberately not cached so the long-running tray/GUI processes pick up a mid-session FreeRDP install on the next launch attempt instead of staying broken until restart. Biggest win on cold RDP launches, and avoids the 10s flatpak probe entirely on systems that have xfreerdp3 installed normally
- **`winapps.conf` import stamps `password_updated`**: the 7-day rotation clock starts at import time. Previously an imported config either (a) never auto-rotated with the new fast-path logic or (b) was rotated on the very first launch under the old code, silently replacing the credential the user just migrated in; neither was desirable
- **Skip `pod_status()` subprocess on fresh install**: `_auto_rotate_password` now returns early when `password_updated` is empty. Previously the rotation path unconditionally invoked `backend.is_running` + `net user` even on first launch, adding a 100-500ms subprocess round-trip before the user's first app could start. `setup_cmd` and `rotate-password` both stamp the timestamp, so the fast path only kicks in for brand-new or hand-edited configs, exactly when rotation cannot make an informed decision anyway
### Cleanup
- **Compose module extraction**: `_yaml_escape`, `_build_compose_content`, `generate_compose`, `generate_compose_to`, `generate_password`, and the YAML templates moved from `cli/setup_cmd.py` to a new `core/compose.py`. Eliminates the core→cli reverse dependency (`provisioner.py` previously did `from winpodx.cli.setup_cmd import _generate_compose, _generate_password` inside function bodies). `setup_cmd.py` now re-exports the private-aliased names for backward compatibility so existing imports and test patches continue to work
- Compose atomic write: added `os.fsync(fd)` before `os.replace()` to prevent zero-byte `compose.yaml` after a crash between rename-metadata commit and data flush (matches the pattern already used in `config.py:save()`)
- `_recreate_container`: `compose down` now captures stdout/stderr and warns on unexpected non-zero return codes. "No such container" is still treated as benign (common on fresh setup) but other runtime errors are surfaced instead of silently swallowed
- Compose template generation: `_yaml_escape`, `_find_oem_dir`, `_build_compose_content` promoted to module-level helpers; `_generate_compose` and `_generate_compose_to` share one builder (-52 lines in `cli/setup_cmd.py`)
- Daemon container commands: `_run_container_cmd` helper consolidates `suspend_pod`/`resume_pod`/`is_pod_paused`; each now 10 lines instead of 25-30 (-48 lines)
- Display scaling: `detect_scale_factor` now delegates to `detect_raw_scale` instead of re-running the DE dispatch cascade (-12 lines)
- `PasswordFilter`: removed duplicate keyword tuple; regex is now the single source of truth
- RDP flag allowlist comments: 24-line and 21-line historical-design banners compressed to headers; dict values self-document
- Provisioner: hoisted `datetime`/`Path` imports to module top, dropped forward-ref string + noqa on `_rotation_marker_path`, trimmed private helper docstrings
- `utils/deps.py`: removed dead `REQUIRED_DEPS` constant and inlined `check_backends()` into `check_all()`
- `utils/compat.py`: removed identity-mapping `FLAVOR_MAP`; uses `_VALID_BACKENDS` directly
- `desktop/notify.py`: removed unused `notify_app_launched` wrapper
- Test suite: stripped docstrings from test functions per project convention (CLAUDE.md), dropped a redundant signature-introspection test and duplicate `PasswordFilter` tests covered by audit5. Total: **228 → 225 tests** (higher signal), **~240 LoC removed** across the refactor

### Security
- `container_name` input validation: `PodConfig.__post_init__()` now enforces `^[A-Za-z0-9][A-Za-z0-9_.-]*$` (the Podman/Docker accepted charset). Rejected values fall back to `winpodx-windows`, stopping a hand-edited config from leaking whitespace, slashes, or shell metacharacters into `podman exec` argument lists or the compose YAML template
- Icon install symlink guard: `_install_icon()` now refuses symlinked `icon_path` values outright and passes `follow_symlinks=False` to `shutil.copy2`. Prevents a malicious/stray symlink in an app definition from causing the copy to read whatever the target points at and plant it in the shared hicolor tree as that app's icon
- RDP flag allowlist hardened: prefix matching replaced with per-flag argument-shape validation. `/drive` now restricts share names to `{home, media}`; `/serial`, `/parallel`, `/smartcard`, `/usb` each have explicit allowlists; adversarial winapps.conf payloads like `/drive:etc,/etc` or `/serial:/dev/tty` are dropped with a warning
- winapps.conf import: if any RDP_FLAGS entry is filtered, `extra_flags` is cleared entirely (all-or-nothing) instead of silently persisting the partial set, forcing explicit user opt-in
- Compose template format-string injection: usernames/passwords containing `{...}` previously triggered `IndexError` or leaked values into adjacent fields (`{password}` → USERNAME). `_yaml_escape` now escapes `{`/`}` so user values can never be interpreted as `str.format()` placeholders
- Bundled apps directory symlink guard: `load_app` now rejects entries whose resolved path escapes `bundled_apps_dir()` (mirrors the hardening already in `bundled_data_path()`)
- `PasswordFilter` logging: clears `record.args` after redacting `record.msg` so re-emission or late formatting can't desync and leak the raw password
- TLS-only RDP (`/sec:tls`) now applied for all backends (previously Podman-only). The OEM install disables NLA on Windows unconditionally, so Docker/libvirt/manual users previously hit TLS handshake errors
- Atomic config writes: `os.fsync(fd)` + parent directory fsync + `os.replace()` to prevent torn writes on power loss
- Symlink / path-traversal hardening in `bundled_data_path()`: `.resolve()` + `is_relative_to()` checks and `copy2(..., follow_symlinks=False)` to block installs pointing outside bundled data dirs
- Subprocess timeouts in desktop integration (`update_icon_cache`, `notify-send`) to prevent indefinite hangs from unresponsive helpers
- `import_winapps_config` now filters untrusted `RDP_FLAGS` through `_filter_extra_flags()` to prevent malicious winapps.conf injecting arbitrary FreeRDP flags
- Config and compose.yaml files created with 0600 permissions
- RDP certificate: `/cert:ignore` for localhost only, `/cert:tofu` for remote connections
- Password filtered from log output
- App name validation (alphanumeric + dash/underscore only) to prevent injection
- Notification text sanitized (control characters removed, HTML escaped, length limited)
- PID file exclusive locking (`fcntl.flock`) to prevent race conditions on concurrent launches
- Zombie process reaper (daemon thread per RDP process) to prevent process table leaks
- Config `_apply()` uses `dataclasses.fields()` allowlist to prevent arbitrary attribute injection
- SecurityLayer=2 (TLS) for encrypted RDP channel in OEM install and registry template
- TLS-only RDP authentication for Podman backend (`/sec:tls`); NLA/Kerberos fails in `podman unshare` namespace
- Exit code 145 (SIGTERM) treated as normal app close, not error
- Subprocess error handling with timeout in debloat (CLI + GUI)
- PowerShell username escaping: single quotes doubled to prevent command injection in `net user` calls
- Password timestamp timezone handling: naive timestamps upgraded to UTC, `TypeError` caught alongside `ValueError`

### Fixed
- Config `_apply()` bool coercion: `bool("false")` was returning `True`; now uses explicit string mapping
- Password rotation rollback: revert was using the already-overwritten new password instead of the original
- RDP `launch_app()` lock file leak: PID file not cleaned up when `Popen` fails
- DPI detection: `_xrdb_scale()` zero DPI guard to prevent 0.0 scale factor
- YAML escape: `_yaml_escape()` now handles `\n` and `\r` to prevent YAML structure injection
- libvirt `get_ip()`: added returncode check and `TimeoutExpired` exception handling
- FreeRDP RemoteApp: removed `/rfx` flag that caused immediate transport failure in RAIL mode
- RDP reaper thread: stderr pipe deadlock; `proc.wait()` could hang indefinitely once the 64KB pipe buffer filled; now uses `communicate()` and stores last 2KB on the session
- TOML writer: control characters 0x00-0x1F and 0x7F were emitted raw, breaking the file; now escaped as `\uXXXX`
- media_monitor.ps1: `net use /delete` exit code ignored; now keeps tracking if unmount fails so next sync can retry
- RDP session reuse: `_find_existing_session` accepted any process with `winpodx` in cmdline (including `winpodx app list`), silently returning a fake session on PID reuse. Unified into `process.is_freerdp_pid()` that matches only `freerdp`/`xfreerdp`
- `linux_to_unc`: silently returned UNC paths for directories not shared over RDP (e.g. `/tmp`), causing Windows "path not found" errors. Now raises `ValueError` outside `$HOME`/media share; caller converts to a clear user-facing error
- Password rotation state marker: if both `cfg.save()` and Windows password rollback fail, a `.rotation_pending` marker is persisted so `ensure_ready()` warns on every launch until the user runs `winpodx rotate-password`
- `unregister_mime_types`: destroyed `mimeapps.list` by deleting whole lines whose values contained a winpodx entry, orphaning unrelated app associations. Now parses with `configparser`, removes only the targeted entries, and writes atomically
- Desktop entries & theme index: explicit `encoding="utf-8"`; non-ASCII `full_name` (Korean/Japanese) previously crashed install on `C`/`POSIX` locales
- GUI icon lookup: `Path(__file__).parent × 4` worked only in source layout and broke after `pip install`. New `bundled_data_path()` helper resolves from source, wheel share-data, and `~/.local/share/winpodx/data/`
- Pod start race: container started but RDP port not yet listening caused the first app launch to fail. `pod.start()` now calls `backend.wait_for_ready(timeout=cfg.pod.boot_timeout)` before returning
- Hardcoded container name: multiple modules used the literal `"winpodx-windows"`, so users who customized the container couldn't run winpodx. Now flows through `cfg.pod.container_name`
- `setup` EOF handling: `input()` raised `EOFError` on piped stdin, crashing non-interactive setup. New `_ask()` helper detects non-TTY and returns defaults; `handle_rotate_password` now uses a three-phase commit with a temp compose file
- Password alphabet: removed `

 from generated passwords (broke PowerShell single-quote escaping in a subset of tickets); kept `!@#%&*` which survive every shell context we use
- App install retry: `winpodx app install-all` failed silently on `RuntimeError`. Now widened to `(ProvisionError, RuntimeError)` and refreshes the icon cache after bulk install
- Wayland DPI detection: previously picked the first output only. Now iterates all outputs and takes the max scale; falls back to Qt `devicePixelRatio()` when compositor exposes no scale
- Desktop entry icon hygiene: raster formats silently installed into `hicolor/scalable/apps/` (spec requires SVG). Now enforces SVG for scalable and falls back to size-specific dirs for raster icons
- App profile writes: `gui/app_dialog.save_app_profile` now uses explicit `encoding="utf-8"` (same non-ASCII crash that affected desktop entries)
- Teams app path: `data/apps/teams/app.toml` pointed at the old Classic Teams executable. Now targets `%LOCALAPPDATA%\Microsoft\WindowsApps\ms-teams.exe`
- Explorer app categories: removed `Office` (invalid for a file manager) and added `FileTools`, `System`
- CI audit job: `pip install -e .[all]` failed on GitHub runners without libvirt headers. New `all-no-libvirt` extra keeps the audit scope while unblocking CI
- Test isolation: new `tests/conftest.py` autouse fixture redirects `HOME` and `XDG_*` to tmp dirs to prevent tests from scribbling into the developer's real config
- `check_rdp_port` default was 3389 but project-wide RDP default is 3390; signature now requires an explicit port so callers can't probe the wrong one
- `PodState.PAUSED` added: auto-suspended containers previously showed as STOPPED in the GUI and the resume button never enabled. `podman.is_running() / is_paused()` now map container states correctly
- `uninstall` / cleanup now terminates tracked FreeRDP sessions via `is_freerdp_pid()` before wiping `runtime_dir`, preventing orphaned xfreerdp processes that hold the RDP channel open
- `check_freerdp` (used by `winpodx info`/`setup`) was only probing `xfreerdp3`/`xfreerdp`; now delegates to the runtime `find_freerdp()` which also recognizes sdl-freerdp and the Flatpak wrapper
- Docker backend `wait_for_ready` 5-second polling loop tightened to 1 second for faster first-launch on Docker setups
- `import_winapps_config` RDP_SCALE parser previously dropped non-integer values silently; now parses floats, clamps to [100, 400], and logs out-of-range values
- Compose template cleanups: `group_add: keep-groups` and `run.oci.keep_original_groups` annotation emitted only for Podman backend (Docker rejects keep-groups); `NETWORK: "slirp"` removed so Podman picks its default (pasta / slirp4netns based on availability)
- New `cfg.pod.image` (default `ghcr.io/dockur/windows:latest`) and `cfg.pod.disk_size` (default `64G`): pinnable image tags and adjustable disk size without hand-editing compose.yaml
- `remove_desktop_entry` now calls `unregister_mime_types` so per-app MIME associations in `mimeapps.list` are cleaned up on uninstall (previously stale handlers survived)
- Wayland DE detection: `XDG_CURRENT_DESKTOP="KDE:Budgie"` previously resolved to "kde" because of dict iteration order; now splits on `:` and matches the leading segment first, falling back to substring match
- GUI app-launch cooldown: `_launch_app` previously held a threading lock across a 3-second sleep, freezing the UI on rapid clicks. The lock is now released immediately after `Popen`; a per-app sentinel plus `QTimer.singleShot(3000, ...)` clears the cooldown without blocking
- Notification truncation fixed: `_sanitize` now truncates raw text to 200 chars *before* HTML-escaping so multi-char entities (`&amp;`) can't be sliced mid-entity into `&am`
- KDE sycoca rebuild errors (`kbuildsycoca6`/`kbuildsycoca5`) no longer swallowed; logged at `debug`/`warning` so the "Plasma doesn't show my icon" class of bug becomes debuggable
- Hardcoded container name / RDP port occurrences in CLI and GUI now route through `cfg.pod.container_name` and `cfg.rdp.port`; custom container names work end-to-end
- `config/oem/toggle_updates.ps1` hosts-file writes pinned to `-Encoding ASCII`; PS7 default UTF-8-with-BOM previously broke Windows DNS client parsing
- `scripts/windows/time_sync.ps1` retry loop now checks `$LASTEXITCODE` per attempt; previously broke out after the first iteration even on failure
- `scripts/windows/media_monitor.ps1` `Sync-Drives` no longer gates on `Test-Path` (TOCTOU with `net use`); both add and delete are try/caught, failures retried on next sync tick
- `config/oem/install.bat` media_monitor.ps1 copy path uses a search list (compose mount → pip wheel → pipx → source → legacy) instead of the single hardcoded `\\tsclient\home\.local\bin\...` path that only matched manual installs

### Changed
- Default RDP port changed from 3389 to 3390 (avoids collision with other containers)
- Default VNC port set to 8007 (avoids collision with LinOffice on 8006)
- FreeRDP search order: xfreerdp3 → xfreerdp → sdl-freerdp3 → sdl-freerdp → flatpak
- `wlfreerdp` removed from search order (deprecated upstream by FreeRDP project)
- Uninstall always removes container (previously only with `--purge`)
- RemoteApp (RAIL) enabled via `fDisabledAllowList` + `fInheritInitialProgram` registry keys (seamless app windows without full desktop)
- `podman unshare --rootless-netns` wrapper for FreeRDP (required for rootless Podman RDP access)
- Per-app desktop notification removed (was noisy on every launch)

### Removed
- **RDPWrap multi-session**: Removed all RDPWrap binaries, scripts, CI workflows, and Python modules; multi-session support will be developed as a separate project
- `data/templates/app.desktop.j2` (unused Jinja2 template)
- Dead code: `icons_cache_dir()`, `decode_base64_icon()`, `MISSING_DEPS_MSG`
