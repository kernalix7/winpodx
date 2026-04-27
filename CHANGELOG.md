# Changelog

**English** | [한국어](docs/CHANGELOG.ko.md)

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0.8] - 2026-04-27

### Fixed
- **`winpodx app refresh` discovered apps but never registered them in the desktop menu.** The refresh path persisted `app.toml` + icons under `~/.local/share/winpodx/discovered/` but the actual `.desktop` entries were only created by the separate `winpodx app install-all` command — so users saw "Discovered N app(s)" then had no apps in their DE menu. v0.2.0.8 has refresh auto-install entries for the discovered set inline (best-effort: failures are warned but don't abort the refresh) and refresh the icon cache afterwards.
- **PowerShell window flashed on every app launch.** `ensure_ready`'s self-heal apply path fired three FreeRDP RemoteApp PowerShell payloads on every single app launch — even though `-WindowStyle Hidden` makes them tiny, they still flashed visibly each time, which got annoying fast. The applies are idempotent on the registry side, so re-running them on warm pods accomplished nothing visible. v0.2.0.8 stamps `~/.config/winpodx/.applies_stamp` with `<winpodx_version>:<container_StartedAt>` after a successful self-heal — subsequent launches short-circuit until the pod restarts (so TermService / NIC settings re-apply after a Windows reboot) or winpodx upgrades.



### Fixed
- **`pod wait-ready --logs` showed no `[container]` lines on a fast container.** Two issues: (1) the tail was started with `--tail 0` which means "show only logs emitted from now onwards", but dockur often prints Windows ISO download progress + boot stage transitions *before* wait-ready runs — so the user saw nothing. (2) Only `stdout` was being drained; dockur splits progress across stdout (download bytes/sec) and stderr (boot phase), so half the messages were silently dropped. v0.2.0.7 bumps `--tail 100` so the user sees recent context immediately, and drains both streams in parallel threads.



### Fixed
- **`wait_for_windows_responsive` collapsed in <1s on a still-booting guest, defeating the entire `pod wait-ready` UX.** The helper waited correctly for the RDP TCP port to open, then fired exactly **one** FreeRDP RemoteApp probe — a single failure (which is what every still-booting guest produces, rc=147 connection-reset) returned False immediately and the caller's 600s timeout was effectively ignored. v0.2.0.6 turns the probe into a retry loop that fires repeated 5-20s probes until either one succeeds or the overall `timeout` expires (paced 3s between attempts so we don't pin a CPU spinning FreeRDP). Now `pod wait-ready --timeout 600` actually waits up to 10 minutes — observable in the elapsed-time stamp incrementing during phase 3.



### Added
- **`winpodx pod wait-ready [--timeout SEC] [--logs]`** — multi-phase wait gate for Windows VM first-boot. Polls three checkpoints with elapsed-time stamps so the user actually sees progress instead of a silent multi-minute hang:
  - `[1/3] Container running` (~5s)
  - `[2/3] RDP port open` (typically 30-90s)
  - `[3/3] Windows ready (RemoteApp probes OK)` (typically 2-8 min on first boot)
  With `--logs`, container stdout is tailed in a background thread and surfaced as `[container] ...` lines so the user can see Windows actually doing work (Sysprep, OEM apply, etc.) instead of a black box.

### Changed
- **`install.sh` is now single-shot — install.sh exits when the install is actually finished, not when the container started.** The flow is now `setup` → `pod wait-ready --logs` (up to 10 min with progress + container logs) → `migrate` (apply runs cleanly since guest is now ready) → `app refresh` (discovery runs cleanly). Previously the user saw `Installation complete!` while Windows was still silently booting and had to wait again on first app launch. Skip the wait with `WINPODX_NO_WAIT=1` for CI / non-interactive setups; skip discovery with `WINPODX_NO_DISCOVERY=1`.
- Removed the redundant `winpodx pod apply-fixes` call from install.sh — `migrate`'s "always-apply" path (since v0.1.9.3) already runs the apply, so calling it again just doubled the wait.



### Fixed
- **Bogus "cfg.password does not match Windows" warning on every fresh `--purge` install.** v0.1.9.5 added `_probe_password_sync` to detect cfg/Windows password drift before apply, but its error classifier matched on `"no result file"` OR `"auth"` in the FreeRDP error string. On a still-booting guest (which is exactly what every fresh install hits), FreeRDP returns rc=147 `ERRCONNECT_CONNECT_TRANSPORT_FAILED` (connection reset by peer) wrapped in the host's `"No result file written"` envelope — and the classifier saw `"no result file"` and yelled drift. v0.2.0.4 fixes this two ways:
  1. The probe now waits on `wait_for_windows_responsive(timeout=180)` first; if the guest isn't ready, it skips with `(probe deferred — guest still booting; will retry on next ensure_ready)` instead of misfiring.
  2. The classifier now distinguishes transport-level failures (`rc=131`, `rc=147`, `transport_failed`, `connection reset`) from genuine auth failures (`logon_failure`, `STATUS_LOGON_FAILURE`, etc.) — only the latter trigger the "run sync-password" warning.



### Fixed
- **Discovery hit the same boot race the apply path used to.** v0.2.0.1 gated `_apply_*` and `pod apply-fixes` on `wait_for_windows_responsive`, but `winpodx migrate`'s "Run app discovery now?" prompt and `provisioner._auto_discover_if_empty` (fired by ensure_ready on first pod boot) still launched the FreeRDP RemoteApp channel without a probe. On a fresh `--purge` reinstall the Windows VM was still booting inside QEMU when discovery fired, so the scan collapsed with `ERRCONNECT_CONNECT_TRANSPORT_FAILED [0x0002000D]` (rc=147, connection reset by peer) and the user ended up with an empty app menu. v0.2.0.3 wires the same probe into both discovery call sites — discovery now waits, then either scans or skips with a "Re-run later with: winpodx app refresh" pointer.
- **First-boot timeout 90s → 180s.** Real-world fresh installs on slower hardware can take more than 90s for Windows + RDP + activation handshake. Bumped the wait budget on all three apply / discovery probes to 180s so a one-shot install actually completes the apply round on first try.



### Fixed
- **Fresh `--purge` reinstall reported a bogus "0.1.7 -> X detected" upgrade.** `winpodx setup` saved `winpodx.toml` but never stamped `installed_version.txt`, so the follow-up `winpodx migrate` (which `install.sh` chains automatically) saw the config + missing marker and hit the pre-tracker fallback that assumes baseline 0.1.7. The fallback is correct for genuine upgrades from before the marker existed, but for a fresh install it ran every migration step needlessly and printed a confusing "What's new in 0.1.8 / 0.1.9 / …" wall. v0.2.0.2 has setup write the current version to `installed_version.txt` if it doesn't already exist, so a fresh install reports as the current version (no migration steps fire) while a real upgrade flow still works as before.



### Fixed
- **Apply cascade collapsed on cold container.** v0.2.0 fired the three idempotent runtime applies (`max_sessions`, `rdp_timeouts`, `oem_runtime_fixes`) the moment `pod_status` reported `RUNNING`. The dockur Linux container reaches `RUNNING` in seconds, but the Windows VM inside QEMU needs another 30–90s before its RDP listener can accept FreeRDP RemoteApp activation. Within that window every apply collapsed with either `ERRCONNECT_CONNECT_TRANSPORT_FAILED [0x0002000D]` (rc=147, RDP socket open but server not initialized — connection reset by peer) on a fresh install, or `ERRCONNECT_ACTIVATION_TIMEOUT [0x0002001C]` (rc=131, FreeRDP connected but activation phase didn't complete) on `winpodx pod restart`. Each apply waited the full 60s timeout, so the cascade ran 3min before surfacing as a Launch Error dialog or "3 of 3 applies failed" panic message during `winpodx setup` → `winpodx migrate`.
- New `wait_for_windows_responsive(cfg, timeout=90)` helper: polls `check_rdp_port`, then fires a 20s no-op `Write-Output 'ping'` probe to confirm the FreeRDP RemoteApp channel is actually live. Used as a precondition by:
  - `ensure_ready()` warm-pod path — skips the self-heal apply block entirely if the guest isn't responsive yet.
  - `winpodx pod apply-fixes` CLI — explicit "Waiting for Windows guest to finish booting (up to 90s)…" message so users know it's not hung.
  - `winpodx migrate` apply step — same wait, with a clear "guest still booting; run apply-fixes later, or just launch any app" message instead of three channel-failure stack traces.
- `_self_heal_apply()` (new) — wraps the warm-pod ensure_ready apply block in `WindowsExecError` swallow so a transient channel failure logs a warning and stops further attempts in the same call instead of cascading. The next ensure_ready picks up where this one left off.



### Fixed
- **`oem_runtime_fixes` failed on first apply with `AllowComputerToTurnOffDevice` parameter error.** v0.1.9.5 shipped the runtime apply through FreeRDP RemoteApp PowerShell, but the payload still passed `Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false`. The cmdlet expects the enum string `'Disabled'` / `'Enabled'`, and on virtual NICs (virtio inside QEMU) the parameter often isn't exposed at all. v0.2.0 wraps the call in `try/catch`, switches to the enum form, and skips adapters that don't support it — apply now passes regardless of NIC topology.
- **migrate password-drift probe timed out at 20s.** FreeRDP's first-contact handshake on a cold pod regularly exceeds 20s (TLS + auth + RemoteApp launch). v0.2.0 bumps the probe budget to 60s so genuine drift detection isn't masked by cold-start latency.

### Added
- **Streaming refresh progress.** `winpodx app refresh` previously hung silently for 30–90s while the guest enumerator walked Registry App Paths, Start Menu, UWP packages, and choco/scoop shims. v0.2.0 adds a streaming progress channel: `windows_exec.run_in_windows` accepts a `progress_callback`, the wrapper exports `$Global:WinpodxProgressFile` + `Write-WinpodxProgress`, and `discover_apps.ps1` emits one line per source. Host CLI surfaces them as `... Scanning Registry App Paths...` etc to stderr (JSON output stays clean).
- **`winpodx pod multi-session {on|off|status}`** — runtime toggle for the bundled rdprrap multi-session RDP patch. Shells out to `rdprrap-conf.exe` inside the Windows guest via FreeRDP RemoteApp, so users no longer need to recreate the container to enable / disable / inspect the patch. Tries `C:\OEM\rdprrap\rdprrap-conf.exe`, `C:\OEM\rdprrap-conf.exe`, `C:\Program Files\rdprrap\rdprrap-conf.exe` in order.
- **Discovery junk filter.** Refresh used to surface uninstallers (`unins000.exe`, "Uninstall …"), redistributables (`vc_redist.x64.exe`, "Microsoft Visual C++ …"), helpers (`crashpad_handler.exe`), inbox accessibility tools (`narrator.exe`, `magnify.exe`, `osk.exe`), system plumbing (`ApplicationFrameHost.exe`, `RuntimeBroker.exe`), and unresolved UWP entries whose DisplayName fell back to `PackageFamilyName` (e.g. `Microsoft.AAD.BrokerPlugin`). v0.2.0 drops these via host-side denylist patterns + executable basename matching + UWP fallback detection. Set `WINPODX_DISCOVERY_INCLUDE_ALL=1` to bypass for debugging.
- **GUI app icons.** Discovered apps now render their actual Windows icon (PNG / SVG) in the launcher's grid cards and list tiles, instead of the colored single-letter avatar. Icons are stored next to `app.toml` under `~/.local/share/winpodx/data/discovered/<slug>/icon.{png,svg}` (already populated since v0.1.8); the GUI now reads them via `QPixmap` (PNG, smooth scaled) and `QSvgRenderer` (SVG, crisp at any size). The letter avatar remains as the fallback when no icon is available.

### Tests
- Streaming progress wrapper: Popen-based test simulates the 3-poll lifecycle with progress-file writes interleaved.
- Junk filter: 11 garbage cases dropped, 4 real apps preserved, env-bypass honored.



### Fixed
- **BOM in result file caused fake "fail" reports.** v0.1.9.4 routed runtime applies through FreeRDP RemoteApp PowerShell, but the wrapper used `Out-File -Encoding utf8` which (in Windows PowerShell 5.1) writes a UTF-8 BOM. The host then `json.loads`'d the file with the default `utf-8` codec which rejected the BOM and reported "result file unparseable: Unexpected UTF-8 BOM". The registry changes from rdp_timeouts and oem_runtime_fixes had **actually applied successfully** — only the parse step failed, leaving the user thinking nothing worked. `windows_exec.run_in_windows` now reads with `utf-8-sig` so the BOM is consumed transparently.
- **`_apply_max_sessions` killed its own RDP session.** The payload included `Restart-Service -Force TermService` to make the new MaxInstanceCount take effect immediately — but TermService is exactly what's hosting the FreeRDP RemoteApp session running the script. The restart killed the session before the wrapper could write its result file, the host saw `ERRINFO_RPC_INITIATED_DISCONNECT [0x00010001]`, and the apply was incorrectly classified as a channel failure even when the registry write itself had landed. v0.1.9.5 removes the in-script `Restart-Service`; the registry write alone is enough, and TermService picks up the new value on its next natural cycle (next pod boot or `winpodx pod restart`).

### Changed (architectural)
- **Migrated every remaining host-to-Windows command path off the broken `podman exec ... powershell.exe` channel** onto `windows_exec.run_in_windows`. Six functions had been silently no-op'ing for releases 0.1.0 through 0.1.9.4 — `podman exec` only reaches the Linux container that hosts QEMU, not the Windows VM running inside, so any call to `powershell.exe` returned `rc=127 executable file not found in $PATH` and the helpers logged a warning then returned. v0.1.9.5 ports them all:
  - `provisioner._change_windows_password` (password rotation — silent for years)
  - `pod.recover_rdp_if_needed` (Bug B TermService restart — never worked; replaced with a container restart since FreeRDP can't authenticate against a dead RDP listener anyway)
  - `daemon.sync_windows_time` (w32tm)
  - `core.updates._exec_toggle` (Windows Update enable/disable/status)
  - `cli/main._cmd_debloat` and `gui/main_window._on_debloat` (debloat.ps1 — was double-broken: `podman cp` to copy the script + `podman exec` to run it)
  - `core/discovery.discover_apps` (Bug A's "fix" via stdin pipe was on the same broken path; now actually goes via FreeRDP RemoteApp)

### Added
- **`winpodx pod sync-password`** CLI command to recover from password drift accumulated under prior releases. Prompts for the "last known working" password (typically the one from initial setup, or the value still in `compose.yml`'s `PASSWORD` env var), authenticates FreeRDP with it, then runs `net user` inside Windows to set the account password to the current cfg.password value. Once the sync completes, password rotation works normally going forward.
- **migrate auto-detects password drift.** When `winpodx migrate` runs and the user is on the "already current" path, it now fires a no-op `Write-Output 'sync-check'` payload through the FreeRDP channel first. If FreeRDP fails with auth/no-result-file, migrate prints a clear "run `winpodx pod sync-password`" pointer instead of letting all three subsequent applies fail with confusing channel errors.
- **Lint test `tests/test_no_broken_podman_exec.py`** — fails CI if any future code under `src/winpodx/` (other than `windows_exec.py` itself) reintroduces the `podman exec ... powershell.exe` pattern. Single canonical channel for Windows-side commands going forward.

### Tests
- `tests/test_provisioner.py` updated to mock `windows_exec.run_in_windows` for `_apply_max_sessions` and assert that `Restart-Service` is no longer in the payload.
- `tests/test_security.py::TestPowerShellEscape` rewritten — `_change_windows_password` now goes through `windows_exec`, so the test inspects the payload string instead of subprocess argv.
- `tests/test_pod.py::test_recover_rdp_*` updated — recover-rdp now restarts the container instead of attempting an exec-based TermService restart.
- `tests/test_daemon.py::test_sync_windows_time_uses_windows_exec_channel` rewritten for the new transport.
- `tests/test_discovery.py` — five tests rewritten to mock `windows_exec.run_in_windows` instead of `subprocess.Popen` + stdin pipe. The `HARD_STDOUT_CAP` flooding test was removed; the cap was specific to the `_run_bounded` path that discovery no longer uses.

## [0.1.9.4] - 2026-04-26

### Fixed
- **Runtime apply finally actually applies.** kernalix7 reported on 2026-04-26 that v0.1.9.1 / v0.1.9.2 / v0.1.9.3 runtime apply paths were silently failing — `podman exec winpodx-windows ...\powershell.exe` returned `rc=127 executable file not found in $PATH`. Root cause: `podman exec` runs commands in the **Linux container** that hosts QEMU, not in the **Windows VM** running inside QEMU; the Linux container has no `powershell.exe`. The helpers (`_apply_max_sessions`, `_apply_rdp_timeouts`, `_apply_oem_runtime_fixes`, `_change_windows_password`) all logged a warning and returned, while the public-facing `apply_windows_runtime_fixes` reported per-helper "ok" because the helpers didn't `raise`. So three previous releases shipped silent no-ops. Three changes:
  1. **New `core/windows_exec.py`** — `run_in_windows(cfg, ps_payload)` launches PowerShell as a FreeRDP RemoteApp and pipes the script through the existing `\\tsclient\home` redirection. Wrapper writes `{rc, stdout, stderr}` JSON back via the same share. The host parses it and returns `WindowsExecResult`. Channel failures (FreeRDP missing, auth fail, timeout, no result file) raise `WindowsExecError`; non-zero script rc surfaces via `WindowsExecResult.rc`.
  2. **`_apply_max_sessions`, `_apply_rdp_timeouts`, `_apply_oem_runtime_fixes` rewritten** — each builds a PS payload, calls `run_in_windows`, and now `raise RuntimeError` on `rc != 0` so failures actually propagate.
  3. **`apply_windows_runtime_fixes` honest reporting** — `try/except` on each helper still works the same way, but now an actual `rc != 0` from inside the Windows VM produces `failed: rc=2 ...` instead of fake `ok`.

  Cost: ~5–10 s per call (RDP handshake + auth + script + disconnect) plus a brief PowerShell window flash that `-WindowStyle Hidden` minimizes. Trade-off: works on existing pods (no container recreate) and the rc check actually means something.

  **Caveat**: requires `cfg.rdp.password` to match the Windows guest's actual password. If password rotation has been silently failing for previous releases (same `podman exec` root cause), the first call here will fail with auth error and the user has to reset the Windows-side password (open `winpodx app run desktop`, run `net user User <password-from-config>`).

### Tests
- 9 new tests in `tests/test_windows_exec.py` covering the full lifecycle: FreeRDP missing, password missing, timeout, no-result-file (auth fail), happy path with result-file roundtrip, non-zero rc propagation, FreeRDP `/app:program:` cmd shape verification, flatpak-style binary splitting, unparseable result JSON.
- `tests/test_provisioner.py` rewritten to mock `windows_exec.run_in_windows` instead of `subprocess.run`. New tests assert each helper raises `RuntimeError` on `rc != 0` and `WindowsExecError` on channel failure.

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
