# Changelog

**English** | [한국어](docs/CHANGELOG.ko.md)

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.9.3] - 2026-04-26

### Fixed
- **Patch-version migrate skipped Windows-side apply ("already current" trap).** kernalix7 upgraded from 0.1.9.x to 0.1.9.2 and got `winpodx 0.1.9.2: already current. Nothing to migrate.` — but the actual Windows guest never received the v0.1.9.1 RDP-timeout / v0.1.9.2 OEM v7-baseline runtime fixes. Root cause: `_version_tuple(...)[:3]` truncated `0.1.9.1` and `0.1.9.2` to the same `(0, 1, 9)` tuple, so `inst_cmp >= cur_cmp` triggered the early-return BEFORE the runtime apply step ran. Migrate now still runs the idempotent runtime apply on the "already current" path so patch-version users still receive Windows-side fixes shipped after their last successful migrate.

### Added
- **`winpodx pod apply-fixes`** standalone CLI command. Idempotent — calls `_apply_max_sessions`, `_apply_rdp_timeouts`, `_apply_oem_runtime_fixes` against the running pod and prints a per-helper OK/FAIL table. Exit code 0 on full success, 2 if pod isn't running / backend unsupported, 3 if any helper failed. Safe to re-run any time.
- **GUI Tools-page "Apply Windows Fixes" button.** Same runtime apply triggered from the Qt GUI — fires the helpers on a worker thread, surfaces success / failure via the existing toast/info-label channel. Useful for users who want the fixes applied without dropping to the CLI.
- **install.sh auto-fires `winpodx pod apply-fixes`** at the end of every install, after the migrate wizard. Failure-tolerant (`|| true`) — silent skip if the pod isn't running. This guarantees a fresh `curl | bash` always lands the latest Windows-side fixes on existing guests, regardless of whether migrate's version comparison saw a "real" upgrade.
- **Public `provisioner.apply_windows_runtime_fixes(cfg)` API** returning a `{helper_name: "ok" | "failed: ..."}` map so the CLI / GUI / migrate paths share a single entry point and surface uniform per-helper status.

## [0.1.9.2] - 2026-04-26

### Fixed
- **Windows-side fixes from v0.1.9 / v0.1.9.1 weren't reaching existing guests.** kernalix7 reported "마이그레이션 잘 되는거 맞아? 윈도에 적용 안되는거같은데" — and they were right. install.bat (the OEM script) only runs at dockur's first-boot unattended setup, so users on 0.1.6 / 0.1.7 / 0.1.8 / 0.1.9 / 0.1.9.1 never picked up NIC power-save off (OEM v7), TermService failure-recovery actions (OEM v7), or RDP timeout disable + KeepAlive (OEM v8) without recreating the container. Compounding this, the v0.1.9.1 `_apply_rdp_timeouts` runtime helper was wired into `provisioner.ensure_ready` AFTER its `check_rdp_port` early-return — so the helper never fired against an already-healthy pod.
  - `provisioner.ensure_ready`: probe `pod_status` once at the top and run all idempotent runtime applies (`_apply_max_sessions`, `_apply_rdp_timeouts`, new `_apply_oem_runtime_fixes`) BEFORE the RDP early-return. Re-applied after pod-start in the cold-pod path. ~1.5s overhead per call; idempotent so re-runs are no-ops.
  - new `provisioner._apply_oem_runtime_fixes(cfg)` pipes the OEM v7 baseline (NIC `Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false`, `sc.exe failure TermService` recovery actions) to existing guests via `podman exec powershell` — same stdin-pipe transport `discover_apps.ps1` uses.
  - `winpodx migrate`: when crossing the 0.1.9 boundary, proactively call all three apply helpers (with pod-state probe + interactive offer to start a stopped pod). Output reports per-helper success / failure so users can see exactly what landed without recreating their container.

## [0.1.9.1] - 2026-04-26

### Fixed
- **GUI SEGV when clicking the Apps "Refresh Apps" button on a pod-not-running guest.** Reported by kernalix7 against 0.1.9: `_on_refresh_failed` constructed a `QMessageBox(self)` directly inside the queued-signal callback frame, and PySide6 + Qt 6.x can SEGV deep in the dialog's font-inheritance path (`QApplication::font(parentWidget)` -> `QMetaObject::className()`) when the parent's metaobject is queried mid-callback. The QMessageBox build is now deferred via `QTimer.singleShot(0, ...)` so the signal handler frame unwinds first. The Info page's first-fetch is also deferred out of `__init__` for the same reason. The Info page worker class was hoisted to module level (was redefined every refresh), gains a busy-state reentrancy guard, and now properly `deleteLater`s both the worker and the QThread on completion.
- **RDP sessions still drop mid-use after host suspend / long idle.** v0.1.9 Bug B fix only handled the "RDP unreachable" path; sessions could still be terminated by the Windows-side TermService timeouts (1h `MaxIdleTime` default). install.bat (OEM v7 -> v8) and a new `_apply_rdp_timeouts` provisioner step now write `MaxIdleTime=0`, `MaxDisconnectionTime=0`, `MaxConnectionTime=0`, `KeepAliveEnable=1` + `KeepAliveInterval=1` to both `HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services` and the `RDP-Tcp` WinStation, plus `KeepAliveTimeout=1` on the WinStation so TCP keep-alive fires every minute. Existing 0.1.x guests get the runtime apply on the next `ensure_ready` without needing container recreation.

## [0.1.9] - 2026-04-25

### Changed
- **Discovery-first refactor.** The 14 bundled app profiles (`word-o365`, `excel-o365`, ..., `notepad`, `cmd`, ...) shipped under `data/apps/` were removed. The Linux app menu now populates exclusively via `winpodx app refresh`, which is fired automatically by `provisioner.ensure_ready` the first time the Windows pod is reachable and the discovered tree is empty. Manual rescan stays the same: `winpodx app refresh` from the CLI or the "Refresh Apps" button on the GUI Apps page. `AppInfo.source` drops the `"bundled"` enum value — only `"discovered"` and `"user"` remain. `winpodx migrate` upgrading from any 0.1.x &lt; 0.1.9 prompts to remove legacy `~/.local/share/applications/winpodx-{14-bundled-slugs}.desktop` files (skipped automatically under `--non-interactive`).

### Added
- **Info page (CLI + GUI).** New `core.info.gather_info(cfg)` returns a 5-section snapshot — System (winpodx version, OEM bundle version, rdprrap version, distro, kernel), Display, Dependencies, Pod (state, uptime, RDP/VNC reachability probes, active session count), Config (with the existing budget warning). `winpodx info` is rewritten to print all five sections. The Qt main window grows a 5th tab ("Info") with one card per section and an explicit "Refresh Info" button that re-runs `gather_info` on a `QThread`. All probes are hard-bounded so a sick pod can't block the panel.

### Fixed
- **Bug A: `winpodx app refresh` on Windows.** v0.1.8 used `podman cp host:discover_apps.ps1 container:C:/winpodx-discover.ps1`, which fails because dockur/windows is a Linux container running the actual Windows guest inside QEMU — the C: drive lives in a virtual disk that `podman cp` cannot write. The script body is now piped via `podman exec -i container powershell -NoProfile -ExecutionPolicy Bypass -Command -` over stdin, removing the staging step entirely. Stderr containing recognizable runtime strings ("no such container", "is not running", etc.) is reclassified to `kind="pod_not_running"` so the cli still routes to exit code 2 + the "run `winpodx pod start --wait`" hint.
- **Bug B: RDP unreachable after host suspend / long idle.** Symptom: VNC port 8007 still works but RDP port 3390 doesn't accept connections — Windows TermService stalls and the virtual NIC enters power-save. New `core.pod.recover_rdp_if_needed(cfg)` detects the asymmetry, runs `podman exec powershell Restart-Service -Force TermService; w32tm /resync /force`, and re-probes RDP up to three times with backoff. Wired into `provisioner.ensure_ready` post-`_ensure_pod_running`. OEM bundle bumps 6 → 7 so `install.bat` adds preventive `Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false` plus `sc.exe failure TermService reset=86400 actions=restart/5000/restart/5000/restart/5000` for Windows-side self-recovery.

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
