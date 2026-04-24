# Changelog

**English** | [한국어](docs/CHANGELOG.ko.md)

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.8] - 2026-04-25

### Added
- **Dynamic Windows-app discovery.** A new `winpodx app refresh` CLI subcommand and a "Refresh Apps" button on the Qt GUI's Apps page now enumerate the apps actually installed on the Windows guest and register them alongside the 14 bundled profiles. Inside the container, `scripts/windows/discover_apps.ps1` scans Registry `App Paths` (HKLM + HKCU), Start Menu `.lnk` recursion, UWP/MSIX packages via `Get-AppxPackage` + `AppxManifest.xml`, and Chocolatey / Scoop shims, returning a JSON array with base64-encoded icons extracted from the real binaries / package logos. The host side (`winpodx.core.discovery`) copies the script via `podman cp`, executes it with `podman exec powershell`, and writes the results under `~/.local/share/winpodx/discovered/<slug>/` as TOML + PNG/SVG icon files. Bundled profiles, user-authored entries, and discovered entries live in three separate directories and merge at load time (user > discovered > bundled on slug collision) so a rediscovery run only touches the discovered tree.
- **UWP RemoteApp launching.** `rdp.build_rdp_command` now accepts a `launch_uri` + strict-regex-validated AUMID (`<PackageFamilyName>!<AppId>`) and maps UWP apps to `/app:program:explorer.exe,cmd:shell:AppsFolder\<AUMID>`. Per-slug `winpodx-uwp-<aumid-slug>` fallback for `/wm-class` keeps Linux taskbar grouping distinct when two UWP apps share the same hint.
- **PowerShell Core smoke test in CI.** A new `discover-apps-ps` job installs `pwsh` on the Ubuntu runner and runs `discover_apps.ps1 -DryRun` on every PR, validating that stdout parses as the JSON array shape `core.discovery` expects.
- **Post-upgrade migration wizard.** A new `winpodx migrate` CLI subcommand shows per-version release notes for every version the user has skipped over and optionally runs `winpodx app refresh` so the Windows-app menu populates in one step. `install.sh` now invokes `winpodx migrate` automatically at the end of every upgrade (existing `~/.config/winpodx/winpodx.toml` detected); opt out with `WINPODX_NO_MIGRATE=1`. Flags `--no-refresh` (skip only the refresh prompt) and `--non-interactive` (disable all prompts) are available for automation. The wizard tracks installed version at `~/.config/winpodx/installed_version.txt`; pre-0.1.8 installs without that marker are treated as upgrading from `0.1.7`.
- **`pod.max_sessions` is now configurable.** Default stays 10; clamped to `[1, 50]`. `ensure_ready()` reads the value, compares against the guest's current `HKLM:\...\Terminal Server\MaxInstanceCount`, and rewrites + restarts `TermService` only when they disagree — active RemoteApp sessions aren't dropped every provision. `fSingleSessionPerUser=0` is also re-asserted on every apply. A rough memory budget helper (`estimate_session_memory`, `check_session_budget` in `winpodx.core.config`) surfaces a warning via `winpodx config show`, `winpodx config set`, `winpodx info`, and the GUI Settings page **only when `max_sessions` over-subscribes `ram_gb`** — the default config stays silent.
- **`install.sh` local-path flags for offline / air-gapped installs.** `--source PATH` copies winpodx from a local directory instead of `git clone` (validates `pyproject.toml` + `src/winpodx/` are present). `--image-tar PATH` preloads the Windows container image via `podman load -i` (or `docker load -i`) so first boot doesn't hit the registry. `--skip-deps` skips the distro dependency install phase entirely and fails early if required tools aren't already present. Every flag has a matching environment variable (`WINPODX_SOURCE`, `WINPODX_IMAGE_TAR`, `WINPODX_SKIP_DEPS`) so `curl | bash` callers can compose them too. `install.sh --help` prints the full usage.

### Changed
- `AppInfo` gains `source: "bundled" | "discovered" | "user"`, `args`, `wm_class_hint`, and `launch_uri` fields so the GUI can badge discovered entries and so RDP launches can target UWP apps.
- `desktop.entry._install_icon` now dispatches between `hicolor/scalable/apps/` (SVG) and `hicolor/32x32/apps/` (PNG) based on the icon file's extension, so discovered apps' extracted PNG icons install cleanly alongside the bundled SVG ones.

## [0.1.7] - 2026-04-23

### Changed
- **Bundled rdprrap bumped to v0.1.3 (license-compliance release).** Upstream withdrew the 0.1.0, 0.1.1, and 0.1.2 GitHub release assets. 0.1.0 / 0.1.1 were missing the upstream source-level attribution notices required by the three projects rdprrap ports code from: `stascorp/rdpwrap` (Apache-2.0), `llccd/TermWrap` (MIT), and `llccd/RDPWrapOffsetFinder` (MIT). 0.1.2 shipped `NOTICE` + `vendor/licenses/` and closed the legal gap but listed only 9 of the 16 rdpwrap-derived Rust sources and had an internally inconsistent copyright line in the `rdprrap-conf` About dialog. 0.1.3 expands the `NOTICE` to all 16 sources (grouped by upstream binary — RDPWInst / RDPConf / RDPCheck), aligns the About-dialog copyright to match `LICENSE`, and cites CC BY 4.0 for the adapted Contributor Covenant text. It also carries forward the registry-readback fix that avoided the `termsrv.dlll` corruption in `OriginalServiceDll`. New bundle SHA256 is pinned in `config/oem/rdprrap_version.txt`; first-boot OEM version bumped to 6 so existing guests re-run the install path and pick up the compliant bundle.

### Documentation
- Add top-level [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) documenting the bundled rdprrap binary and the runtime/optional Python dependencies (PySide6 LGPL, libvirt-python LGPL, docker-py Apache-2.0, tomli MIT).
- `debian/copyright` now declares the bundled rdprrap files separately and notes that the in-ZIP `NOTICE` + `vendor/licenses/` texts satisfy the upstream Apache-2.0 / MIT attribution requirements.

### Fixed
- **`install.sh` now works under `curl … | bash`.** When piped, bash reads from stdin and `BASH_SOURCE[0]` is unset; combined with the `set -u` guard at the top of the script, that aborted the installer at line 205 with `BASH_SOURCE[0]: unbound variable` before the repo could even be cloned. The local-vs-remote branch now defaults the source path to empty and falls through to the git-clone path when there is no local repository. Reported on CachyOS with Python 3.14 / fish shell ([#3](https://github.com/kernalix7/winpodx/issues/3)).

### Security / Compliance
- winpodx 0.1.6, which shipped rdprrap 0.1.0, inherited the same missing-attribution defect. The 0.1.6 GitHub release assets have been withdrawn; the git tag is preserved. Users should install 0.1.7, which is the first winpodx release whose Windows guest receives a compliant rdprrap bundle (0.1.3, with full `NOTICE` + `vendor/licenses/`).

## [0.1.6] - 2026-04-22

### Added
- **Multi-session RDP — bundled, fully offline.** winpodx now ships [rdprrap](https://github.com/kernalix7/rdprrap) v0.1.0 inside the package (~1.6 MB zip under `config/oem/`) and auto-installs it during the Windows unattended setup, so each RemoteApp window gets its own independent session instead of stealing the previous one. The bundle is staged into the Windows guest at `C:\OEM\`, sha256-verified against a pin file, then extracted — no network access is required. Failures fall back silently to single-session. A guest-side management channel (enable/disable/status after install) is planned for a later release.

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
- README "Install" section now lists distro-specific commands.

### Changed
- AppImage packaging removed: Python + Qt + FreeRDP + Podman dependencies reduce its single-file-distribution value to near zero.

### Fixed
- Weekly upstream update checker creates a tracking Issue instead of failing on permission errors.

## [0.1.0] - 2026-04-21

First public release.

### Added
- **Zero-config auto-provisioning**: first app launch creates config, compose file, container, and desktop entries automatically.
- **14 bundled app profiles**: Word, Excel, PowerPoint, Outlook, OneNote, Access, Notepad, Explorer, CMD, PowerShell, Paint, Calculator, VS Code, Teams.
- **Auto suspend / resume**: container pauses on idle, resumes on next app launch; graceful shutdown on exit.
- **Password auto-rotation**: 20-char cryptographic password, 7-day cycle (configurable), automatic rollback on failure.
- **Manual password rotation**: `winpodx rotate-password`.
- **Office lock-file cleanup**: `winpodx cleanup` removes `~$*.*` lock files from the home directory.
- **Windows time sync**: `winpodx timesync` re-synchronizes the Windows clock after host sleep/wake.
- **Windows debloat**: `winpodx debloat` disables telemetry, ads, Cortana, search indexing.
- **Power management**: `winpodx power --suspend/--resume` manually pauses/resumes the container.
- **System diagnostics**: `winpodx info` reports display, dependency, and configuration status.
- **Desktop notifications** (D-Bus / `notify-send`) surface on app launch.
- **Smart DPI scaling**: auto-detects scale from GNOME, KDE Plasma 5/6, Sway, Hyprland, Cinnamon, env vars, and xrdb.
- **Qt system tray**: pod controls, app launchers, maintenance tools, idle monitor, auto-refresh.
- **Multi-backend**: Podman (default), Docker, libvirt/KVM, manual RDP — unified interface.
- Auto-generated **compose files** for Podman/Docker backends (uses the `dockur/windows` image).
- **Per-app taskbar separation**: each app gets a unique WM_CLASS / `StartupWMClass`.
- **Windows build pinning**: `TargetReleaseVersion` policy blocks feature updates while leaving security updates on.
- **Upstream update monitoring**: weekly check for new `dockur/windows` releases.
- **Concurrency protection**: threading locks prevent crashes on simultaneous app launches.
- GUI **Windows Update toggle** (services + scheduled tasks + hosts-file triple block).
- **Sound + printer** redirection enabled by default.
- **USB drive sharing** with hot-plug (reconnect-free sub-folder exposure).
- **USB device redirection** via FreeRDP `urbdrc` when available, graceful fallback to drive sharing.
- Windows-side **USB drive-letter auto-mapping** (event-based, no polling).
- Desktop integration: `.desktop` entries, hicolor icons, MIME registration, icon-cache refresh.
- Restricted-permission (`0600`) TOML configuration file for credential protection.
- FreeRDP session management with process tracking and zombie reaping.
- `winapps.conf` import for migrating existing winapps installs.

### Security
- RDP bound to **127.0.0.1 only** — no network exposure.
- **TLS-only** RDP channel (SecurityLayer=2); NLA disabled only in the loopback-bound setup.
