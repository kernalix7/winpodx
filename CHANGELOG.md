# Changelog

**English** | [한국어](docs/CHANGELOG.ko.md)

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.6] - 2026-04-22

### Added
- **Multi-session RDP — bundled, fully offline.** winpodx now ships [rdprrap](https://github.com/kernalix7/rdprrap) v0.1.0 inside the package (~1.6 MB zip under `config/oem/`) and auto-installs it on first boot, so each RemoteApp window gets its own independent session instead of stealing the previous one. The bundle is mounted into the Windows guest at `C:\OEM\`, sha256-verified against a pin file, then extracted — no network access is required. Failures fall back silently to single-session.
- `winpodx multi-session {status,enable,disable}` — manage the patch from the Linux host without opening an RDP session.

## [0.1.5] - 2026-04-21

### Added
- Prebuilt RPMs for **AlmaLinux 9 / AlmaLinux 10** (also installable on RHEL 9/10 and Rocky 9/10), attached to every GitHub Release.
- Arch Linux AUR packaging (activation pending a one-time maintainer setup; see [`packaging/aur/README.md`](packaging/aur/README.md)).

### Changed
- **Minimum Python lowered from 3.11 to 3.9.** This unblocks clean installs on distros whose default `python3` is 3.9 (RHEL 9 / AlmaLinux 9 / Rocky 9) without requiring an add-on Python module.

### Fixed
- OBS RPM downloads no longer come up empty when picking up newly-published assets.

## [0.1.4] - 2026-04-21

### Fixed
- `.deb` build no longer aborts with "missing files" during `dh_install`.
- OBS publish step tolerates unrelated build-service-side failures on obscure architectures that aren't in our target matrix.

## [0.1.3] - 2026-04-21

### Fixed
- OBS publish step no longer loops on authentication errors while waiting for the build.
- `.deb` build no longer tries to run the test suite (tests run upstream in GitHub Actions instead).

## [0.1.2] - 2026-04-21

### Fixed
- After a tag push, the RPM and `.deb` publish workflows now actually run and attach their artifacts to the Release.
- RPM build is resilient to the upstream `pyproject.toml` version being ahead of the latest git tag.

## [0.1.1] - 2026-04-21

### Added
- **Prebuilt packages per Release**:
  - RPM: openSUSE Tumbleweed, Leap 15.6, Leap 16.0, Slowroll, Fedora 42, Fedora 43.
  - `.deb`: Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10.
  - Source dist + wheel on PyPI-compatible artifacts.
- README "Install" section with per-distro instructions.

### Changed
- AppImage packaging dropped: the required Python + Qt + FreeRDP + Podman dependency surface made a portable single-file bundle impractical.

### Fixed
- Weekly upstream-update check now files a tracking Issue instead of failing with a repo-permission error.

## [0.1.0] - 2026-04-21

Initial public release.

### Added
- **Zero-config auto-provisioning**: first app launch auto-creates config, generates the compose file, starts the container, and registers desktop entries.
- **14 bundled app definitions**: Word, Excel, PowerPoint, Outlook, OneNote, Access, Notepad, Explorer, CMD, PowerShell, Paint, Calculator, VS Code, Teams.
- **Auto suspend / resume**: container pauses when idle and auto-resumes on the next app launch; graceful shutdown on exit.
- **Password auto-rotation**: cryptographically secure 20-char password, rotated every 7 days (configurable), with automatic rollback on failure.
- **Manual password rotation**: `winpodx rotate-password`.
- **Office lock-file cleanup**: `winpodx cleanup` removes `~$*.*` lock files from the home directory.
- **Windows time sync**: `winpodx timesync` forces clock resync after host sleep/wake.
- **Windows debloat**: `winpodx debloat` disables telemetry, ads, Cortana, search indexing.
- **Power management**: `winpodx power --suspend/--resume` for manual container pause/unpause.
- **System diagnostics**: `winpodx info` shows display, dependency, and config status.
- **Desktop notifications** on app launch (D-Bus / `notify-send`).
- **Smart DPI scaling**: auto-detects scale from GNOME, KDE Plasma 5/6, Sway, Hyprland, Cinnamon, env vars, xrdb.
- **Qt system tray**: pod controls, app launchers, maintenance tools, idle monitor, auto-refresh.
- **Multi-backend**: Podman (default), Docker, libvirt/KVM, manual RDP — unified interface.
- **Compose file generation** for Podman/Docker backends, using the `dockur/windows` image.
- **Per-app taskbar separation**: each app gets its own WM_CLASS / `StartupWMClass`.
- **Windows build pinning**: feature updates blocked via `TargetReleaseVersion` policy; security updates still applied.
- **Upstream update monitoring**: weekly CI check for new `dockur/windows` releases.
- **Concurrent launch protection**: threading lock prevents simultaneous app-launch crashes.
- **Windows Update toggle** in GUI (services + scheduled tasks + hosts-file block).
- **Sound + printer** redirection enabled by default.
- **USB drive sharing** with hot-plug support (subfolders appear without reconnecting).
- **USB device redirection** via FreeRDP `urbdrc` when available; graceful fallback to drive sharing.
- **USB auto drive-letter mapping** on the Windows side (event-driven, no polling).
- Desktop integration: `.desktop` entries, hicolor icons, MIME registration, icon-cache refresh.
- TOML configuration with restrictive (`0600`) file permissions for credentials.
- FreeRDP session management with process tracking and a zombie reaper.
- `winapps.conf` import for migrating from existing winapps setups.

### Security
- RDP bound to **127.0.0.1** only; not exposed to the network.
- **TLS-only** RDP channel (SecurityLayer=2); NLA disabled only because RDP is loopback-bound.
- Input validation for container names, app names, and imported RDP flags (strict allowlists).
- Symlink / path-traversal guards on icon install and bundled-data lookups.
- Atomic config writes with `fsync` to prevent torn writes on power loss.
- Password redacted from log output; log-record args cleared to prevent late-formatting leaks.
- Cert policy: `/cert:ignore` only on localhost, `/cert:tofu` on remote connections.
- Exclusive file locking (PID files) to prevent races on concurrent launches.
- Subprocess timeouts on desktop-integration helpers to prevent indefinite hangs.
- Imported `winapps.conf` RDP flag sets are filtered all-or-nothing; partial acceptance is never silent.
- Username escaping on PowerShell invocations to prevent command injection.

### Changed
- Default RDP port: **3390** (avoids collision with other containers on 3389).
- Default VNC port: **8007** (avoids collision with LinOffice on 8006).
- FreeRDP search order: `xfreerdp3 → xfreerdp → sdl-freerdp3 → sdl-freerdp → flatpak`.
- Uninstall always removes the container (previously `--purge` only).
- RemoteApp / RAIL enabled for seamless app windows (no full desktop).
- Per-app desktop notification removed (was noisy on every launch).
