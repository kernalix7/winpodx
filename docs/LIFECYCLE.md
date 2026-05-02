# winpodx Lifecycle & Processes

End-to-end reference for how a winpodx pod is installed, upgraded, migrated, and kept healthy. Each section describes one phase: who fires it, what it does, where the code lives, and what failure modes it handles.

> **Audience.** Maintainers and advanced users who need to understand or debug any winpodx code path. For day-to-day usage see [README.md](../README.md).

---

## Table of contents

1. [Phases overview](#1-phases-overview)
2. [Fresh install](#2-fresh-install-no-existing-config)
3. [Sysprep first boot (install.bat)](#3-sysprep-first-boot-installbat)
4. [Upgrade install (existing config)](#4-upgrade-install-existing-config)
5. [Migrate (`winpodx migrate`)](#5-migrate-winpodx-migrate)
6. [Apply chain (`apply_windows_runtime_fixes`)](#6-apply-chain-apply_windows_runtime_fixes)
7. [Multi-session activation](#7-multi-session-activation)
8. [Container image pinning](#8-container-image-pinning)
9. [Discovery (`winpodx app refresh`)](#9-discovery-winpodx-app-refresh)
10. [Transport selection](#10-transport-selection-agent-vs-freerdp)
11. [Recovery scenarios](#11-recovery-scenarios)

---

## 1. Phases overview

```
                 ┌─────────────────┐
                 │  install.sh     │
                 │  (host side)    │
                 └────────┬────────┘
                          │
        ┌─────────────────┴──────────────────┐
        │                                    │
   no existing config              existing config
        │                                    │
        ▼                                    ▼
┌───────────────┐               ┌─────────────────────┐
│ winpodx setup │               │ skip setup          │
│  (interactive │               │ stage agent token   │
│   or default) │               └──────────┬──────────┘
│ writes:       │                          │
│  winpodx.toml │                          │
│  compose.yaml │                          │
│  agent_token  │                          │
└───────┬───────┘                          │
        │                                  │
        └──────────────┬───────────────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │ winpodx pod         │
            │   wait-ready        │ (3 phases: container,
            │                     │  RDP port, FreeRDP probe)
            └──────────┬──────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │ winpodx migrate     │ (only if existing config)
            │  - version compare  │
            │  - image pin align  │
            │  - apply chain      │
            └──────────┬──────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │ winpodx app refresh │
            │  (3-layer race-free │
            │   discovery)        │
            └─────────────────────┘
```

The shaded boxes are entry points; everything else is implementation. Each is described in detail below.

---

## 2. Fresh install (no existing config)

**Trigger.** `~/.config/winpodx/winpodx.toml` does not exist. Typically a brand-new user running `curl -sSL .../install.sh | bash`.

**Flow.**

1. `install.sh` checks distro, installs missing dependencies (podman, podman-compose, freerdp, libnotify), verifies Python ≥ 3.9.
2. `install.sh` extracts winpodx source to `~/.local/bin/winpodx-app/` and writes the `winpodx` launcher to `~/.local/bin/winpodx`.
3. `install.sh` runs `python3 -m winpodx setup --non-interactive` (`src/winpodx/cli/setup_cmd.py::handle_setup`).
4. Setup writes `~/.config/winpodx/winpodx.toml`:
   - `cfg.pod.image` defaults to `DOCKUR_IMAGE_PIN` (a SHA-pinned `docker.io/dockurr/windows@sha256:…` digest — see [§8](#8-container-image-pinning)).
   - `cfg.rdp.password` randomized.
   - `cfg.pod.backend` autodetected (podman > docker > libvirt).
5. Setup runs `generate_compose(cfg)` which writes `~/.config/winpodx/compose.yaml`.
6. Setup runs `_ensure_oem_token_staged()` which writes `~/.config/winpodx/agent_token.txt` and copies it to the OEM bind-mount source dir.
7. `install.sh` calls `winpodx pod wait-ready --timeout 3600 --logs`. dockur pulls the pinned image, downloads the Windows ISO (~7.5 GB), runs Sysprep with our OEM bundle ([§3](#3-sysprep-first-boot-installbat)) — typically 5-10 min on first install.
8. Migrate is **skipped** (no `installed_version.txt` to compare against).
9. `install.sh` calls `winpodx app refresh` ([§9](#9-discovery-winpodx-app-refresh)).

**End state.** Fully provisioned pod, multi-session active, agent running under wscript wrapper, app menu populated.

---

## 3. Sysprep first boot (`install.bat`)

**Where.** `config/oem/install.bat`. Lives in the OEM bind-mount → copied by dockur into `C:\OEM\` at first boot → invoked once via `unattend.xml`'s `FirstLogonCommands` as the local console session of the autologon user.

`WINPODX_OEM_VERSION` (top of file) is the bundle version. Bumped per release whenever install.bat or its sibling resources change.

**What it does, in order.**

1. **TermService recovery actions** — `sc.exe failure TermService reset= 86400 actions= restart/5000/...`. Survives transient TermService crashes without manual intervention.
2. **MaxInstanceCount + multi-session registry** — `HKLM:\...\Terminal Server\WinStations\RDP-Tcp\MaxInstanceCount`, `fSingleSessionPerUser = 0`. Authoritative cap at OEM time; runtime apply chain syncs it later if `cfg.pod.max_sessions` changes.
3. **rdprrap install** (multi-session enabler):
   - SHA256-verified bundle extraction from `C:\OEM\rdprrap-*.zip` to `C:\winpodx\rdprrap\`.
   - Delegates the install + verify + marker to **`rdprrap-activate.ps1`** (single source of truth for both OEM-time and runtime activation; see [§7](#7-multi-session-activation)).
4. **NIC / RDP timeout settings** — disables idle/disconnect/connection timeouts so RemoteApp sessions don't drop after 1 h.
5. **media_monitor.ps1 staging + autostart** — copies `media_monitor.ps1` to `C:\winpodx\` and registers `HKCU\Run\WinpodxMedia` with the **wscript+hidden-launcher.vbs wrapper** so the autostart doesn't flash a PS console (since OEM v19 — earlier versions used bare `powershell.exe -WindowStyle Hidden` which leaked a ~50 ms conhost flash).
6. **VBS launcher staging** — copies `hidden-launcher.vbs`, `launch_uwp.vbs`, `launch_uwp.ps1`, `agent-respawn.ps1`, `rdprrap-activate.ps1` to `C:\Users\Public\winpodx\launchers\`. Public dir is universally writable so the agent (User-level) can later overwrite these during runtime migrations.
7. **Agent autostart** — registers `HKCU\Run\WinpodxAgent` with the wscript+hidden-launcher.vbs wrapper pointing at `C:\OEM\agent.ps1`.
8. **URL ACL pre-registration** — `netsh http add urlacl` for the agent's `http://+:8765/` listener, so the User-level agent can bind without admin elevation at runtime.
9. **OEM marker** — writes `C:\winpodx\oem_version.txt` so the host can probe what bundle this guest was provisioned with.

**Idempotency.** install.bat runs **once**. After Sysprep finishes, dockur never re-runs it. All ongoing maintenance is handled by the host-side apply chain ([§6](#6-apply-chain-apply_windows_runtime_fixes)).

---

## 4. Upgrade install (existing config)

**Trigger.** `~/.config/winpodx/winpodx.toml` already exists. Typically `curl -sSL .../install.sh | bash -s -- --main` to update an existing winpodx installation.

**Flow.**

1. `install.sh` re-extracts source to `~/.local/bin/winpodx-app/` (host code update).
2. Runs `winpodx setup --non-interactive` — detects existing config, prints `Existing config found ..., skipping setup`, runs `_ensure_oem_token_staged()`, returns. **No compose regeneration here**, so the running pod is undisturbed by the source code update.
3. Runs `winpodx pod wait-ready` — typically completes in seconds since the container is already up and warm.
4. Runs `winpodx migrate` ([§5](#5-migrate-winpodx-migrate)) — the canonical place where existing-pod migration happens.
5. Runs `winpodx app refresh` to refresh the discovered app menu.

The only path that mutates the guest is migrate. install.sh itself is purely host-side after step 2.

---

## 5. Migrate (`winpodx migrate`)

**Code.** `src/winpodx/cli/migrate.py::run_migrate`.

**Goal.** Leave the existing pod in the state a *fresh main install would produce*. Three independent migration steps run in order:

```
        installed_version vs current
                    │
       ┌────────────┼─────────────┐
       │            │             │
   None         already       cross-version
   (fresh)      current      (e.g. 0.1.7 → 0.3.1)
       │            │             │
       ▼            ▼             ▼
   record       run apply     print whats-new
   version      chain         + run apply chain
                              + (optional) refresh
```

### 5.1 Version detection

`_detect_installed_version()` reads `~/.config/winpodx/installed_version.txt`. If absent and a config exists, assumes pre-tracker (v0.1.7) baseline.

`_version_tuple()` extracts leading digits per dot-segment (so `0.3.0-RTM1`, `0.3.0rc1`, `0.3.0+dev` all parse to `(0, 3, 0)` for `[:3]` comparison purposes). Pre-PR #82 the parser stopped at the first non-int segment, returning `(0, 3)` for RTM-suffixed strings — that 2-tuple lex-compared less than every shipped `(0, 3, 0)`, dropping the apply chain on the floor for every RTM user.

### 5.2 Steps that always run

Regardless of version (when an existing config is present):

1. **`_probe_password_sync`** — pre-flight FreeRDP auth probe. If the password drifted (cfg vs Windows account out of sync), prints a diagnostic pointing at `winpodx pod sync-password`.
2. **`_ensure_canonical_image_pin`** — rewrites `cfg.pod.image` and `compose.yaml` to `DOCKUR_IMAGE_PIN` if they don't match. One container recreate cost on next `pod start` (volume preserved — ~30 s, no ISO redownload). Idempotent: matching pins → no-op.
3. **`_apply_runtime_fixes_to_existing_guest`** — calls `apply_windows_runtime_fixes(cfg)` ([§6](#6-apply-chain-apply_windows_runtime_fixes)).

### 5.3 Steps that run on cross-version upgrade only

- `_print_whats_new` — pulls release notes from `_VERSION_NOTES` for every version in `(installed, current]` and prints them.
- `_maybe_cleanup_legacy_bundled` — only when crossing the v0.1.9 boundary, offers to remove the 14 stale `.desktop` entries left over from the bundled-profiles era.

### 5.4 Why "always current" still runs the apply chain

Patch versions (0.1.9.x) collapse to the same `(0, 1, 9)` tuple under `[:3]` truncation. Without firing apply on this path, an upgrade `0.1.9.0 → 0.1.9.2` would silently skip every fix shipped in `0.1.9.x`. Helpers are idempotent, so re-running on a healthy pod is a marker probe + no-op return per helper.

---

## 6. Apply chain (`apply_windows_runtime_fixes`)

**Code.** `src/winpodx/core/provisioner.py::apply_windows_runtime_fixes`. Called by `winpodx pod apply-fixes`, the GUI Tools-page button, and migrate.

**Order matters** — each step assumes earlier steps have run:

```
1. max_sessions          MaxInstanceCount registry sync
2. rdp_timeouts          disable idle / disconnect / connection timeouts
3. oem_runtime_fixes     NIC power-save off, TermService recovery, …
4. vbs_launchers         push VBS files + agent-respawn + WinpodxMedia rewrite
5. multi_session         marker probe + (if needed) detached activation
```

**Per-helper contract.** Each helper builds a single PowerShell payload, sends it via `_apply_via_transport` (agent /exec preferred, FreeRDP RemoteApp fallback), and returns. All helpers are idempotent — running on a pod that's already at-or-past the relevant fix produces a marker probe + no-op return.

**Per-helper detail.**

### 6.1 `_apply_max_sessions`

Writes `MaxInstanceCount` to `HKLM:\...\WinStations\RDP-Tcp` and clears `fSingleSessionPerUser` at the Terminal Server root. Does **not** restart TermService — the apply runs *inside* an RDP session served by that very service; restart would kill the apply mid-flight. Registry write alone is enough; new value picked up on next natural cycle.

### 6.2 `_apply_rdp_timeouts`

Writes the registry keys that disable RDP's idle / disconnect / max-session timeouts and enables keep-alive. Without this Windows drops active RemoteApp sessions after the 1 h default idle, and NAT/firewall idle-cleanup can kill the underlying TCP.

### 6.3 `_apply_oem_runtime_fixes`

Catch-all for OEM-time settings that *should* persist but sometimes don't:

- NIC power-management off (`Set-NetAdapterPowerManagement`)
- TermService recovery actions (5 s restart, 3 attempts)
- ApplicationFrameHost / explorer.exe stability tweaks

### 6.4 `_apply_vbs_launchers`

Five files pushed via a single `/exec` round-trip:

| File | Purpose |
|---|---|
| `hidden-launcher.vbs` | Generic GUI-subsystem wrapper, propagates SW_HIDE to any spawned child |
| `launch_uwp.vbs` | RemoteApp-friendly UWP launcher, calls launch_uwp.ps1 hidden |
| `launch_uwp.ps1` | C#-helper-class IApplicationActivationManager activator (no PS-level COM cast issues) |
| `agent-respawn.ps1` | Detached agent restart (kills old, spawns new under wscript wrapper) |
| `rdprrap-activate.ps1` | Runtime rdprrap activator (see [§7](#7-multi-session-activation)) |

Then writes `HKCU\Run\WinpodxAgent` and `HKCU\Run\WinpodxMedia` to use the wscript+hidden-launcher.vbs wrapper. `WinpodxMedia` rewrite is conditional on the legacy entry existing (avoids creating a stale entry on pods where install.bat skipped media_monitor staging).

Finally spawns `agent-respawn.ps1` detached so the new wrapper takes effect immediately without requiring a user logout — `/health` blips for 3-4 s and recovers under the new wrapper.

### 6.5 `_apply_multi_session`

See [§7](#7-multi-session-activation).

---

## 7. Multi-session activation

**Goal.** rdprrap patches `termsrv.dll` so multiple RDP sessions of the same user can coexist. Without it, each new RDP connection replaces the previous session — the dreaded "Select a session to reconnect to" dialog.

**Single source of truth.** `config/oem/rdprrap-activate.ps1` is invoked from both OEM-time (synchronous, from install.bat) and runtime (detached, from `_apply_multi_session` or `winpodx pod multi-session on`).

### 7.1 Activation mechanism

Two-step:

1. `rdprrap-installer install --skip-restart` — patches `HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters\ServiceDll` to point at `termwrap.dll` (rdprrap's wrapper DLL).
2. `net stop TermService /y && net start TermService` — TermService loads the new DLL on fresh start.

Step 2 kills every active RDP session (because TermService manages them).

### 7.2 OEM-time path (synchronous)

install.bat runs from `FirstLogonCommands` in the **local console session**. TermService manages **RDP sessions only**, so the cycle in step 2 doesn't tear down the cmd.exe parent. install.bat invokes `rdprrap-activate.ps1` without `-Detached`, waits synchronously for the script to exit, and branches on the rc.

### 7.3 Runtime path (detached)

The agent runs *inside* a user RDP session — the session that gets killed in step 2. An inline `/exec` would die mid-flight before the response could return.

`_apply_multi_session` and `winpodx pod multi-session on` therefore spawn `rdprrap-activate.ps1 -Detached` via wscript+hidden-launcher.vbs:

```
host /exec  ──► agent.ps1  ──► Start-Process wscript.exe ...rdprrap-activate.ps1 -Detached
                    │                   │
              returns OK to host       (sleeps 2 s — host response time)
                                        ↓
                                 install + TermService cycle
                                        ↓
                                 marker := 'enabled' / 'installer-failed' / etc.
                                        ↓
                                 (agent's session died ~mid-flow)
```

User reconnects → HKCU\Run fires → fresh agent comes up under wscript wrapper.

### 7.4 Idempotency: marker + ServiceDll cross-check

`_apply_multi_session` reads `C:\winpodx\rdprrap\.activation_status` (written by `rdprrap-activate.ps1`):

| Marker value | Action |
|---|---|
| `enabled` | No-op return. Fast path. |
| missing / `not-activated` / `installer-failed` / `extract-failed` | Cross-check ServiceDll. If `termwrap.dll` already there: write `enabled` to marker, no-op return (PR #85 — handles the case where install.bat marked it failed but the patch landed anyway). Otherwise: spawn detached activator. |

This belt-and-suspenders avoids cycling TermService on pods that are *already* working but had an OEM-time partial failure. Pre-PR #85, every apply-fixes call on such pods killed the agent.

### 7.5 `winpodx pod multi-session on/off/status`

- **`on`** — same code path as the apply-chain step. Detached spawn, returns "OK: activation queued" with a clear note about the ~10 s disconnect cost.
- **`off`** — inline `rdprrap-conf --disable`. Disable just clears the registry patch; TermService doesn't need cycling until next reboot, so the agent's session is safe.
- **`status`** — marker probe. Same source the apply-fixes multi_session step uses, so output is consistent across surfaces.

---

## 8. Container image pinning

**Code.** `DOCKUR_IMAGE_PIN` constant in `src/winpodx/core/config.py`.

**Why.** Pre-pin (≤ v0.3.0), `cfg.pod.image` defaulted to `:latest`. Every `podman-compose up` re-resolved the tag against whatever dockur had pushed. When the digest changed (frequent — dockur's release cadence is daily-ish), podman-compose treated the spec as different and **recreated the container** → fresh ISO download → multi-minute Sysprep → loss of guest state.

**Format.** `docker.io/dockurr/windows@sha256:<64-char-hex>`.

**Update procedure (release-time).**

```
TOKEN=$(curl -sSL "https://auth.docker.io/token?service=registry.docker.io&scope=repository:dockurr/windows:pull" | jq -r .token)
curl -sSL -H "Authorization: Bearer $TOKEN" -I \
  -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json' \
  -H 'Accept: application/vnd.oci.image.index.v1+json' \
  "https://registry-1.docker.io/v2/dockurr/windows/manifests/latest" \
  | grep -i '^docker-content-digest:'
```

Paste the digest into `DOCKUR_IMAGE_PIN`, bump version, ship.

**Migration.** Migrate's `_ensure_canonical_image_pin` rewrites `cfg.pod.image` + `compose.yaml` for existing pods. One container recreate on next `pod start` (volume preserved — ~30 s, no ISO redownload, no Sysprep). Idempotent.

**User opt-in update.** `winpodx setup --update-image` is the **only** path that ever pulls a fresh `:latest`:

1. `podman pull docker.io/dockurr/windows:latest`
2. `podman image inspect ... -f '{{json .RepoDigests}}'` → resolve to digest
3. Filter to docker.io entry → `cfg.pod.image := <digest>`
4. Regenerate `compose.yaml`
5. Print "next pod start will recreate container ~30 s, volume preserved"

---

## 9. Discovery (`winpodx app refresh`)

**Code.** `src/winpodx/core/discovery/__init__.py::discover_apps` (host) + `scripts/windows/discover_apps.ps1` (guest).

**Default timeout.** 180 s.

**Three race avoidance layers.**

### 9.1 Layer 1: guest readiness gate

`discover_apps.ps1` head:

```
poll every 1 s for:
  AppXSvc.Status -eq 'Running'  AND
  ProgramData Start Menu .lnk count > 0

require 3 consecutive stable samples
bounded at 60 s
```

Catches the Sysprep-just-finished window where AppX is still installing inbox apps and Start Menu indexer is mid-propagation.

### 9.2 Layer 2: host transport readiness

`_wait_for_transport_ready(cfg, max_wait_sec=30)` polls agent `/health` and RDP port. Returns as soon as either responds. Catches the migrate-just-cycled-TermService window where the agent is mid-respawn.

### 9.3 Layer 3: retry-on-empty

After the first pass, `_looks_suspiciously_empty(apps)`:

- Total count < 5 (stock Win11 always has 15+)
- OR UWP count == 0 (Calculator / Settings / Terminal always present)

If suspicious: wait 8 s, retry once. Picks the larger result so retry never regresses.

### 9.4 Discovery sources

The script unions five sources, deduping by lowercase executable path or UWP AUMID:

1. **Registry App Paths** (`HKLM` + `HKCU`)
2. **Start Menu .lnk recursion** (ProgramData + every user profile)
3. **UWP / MSIX packages** via `Get-AppxPackage` + `AppxManifest.xml`
4. **Chocolatey + Scoop shims**
5. **Essentials allowlist** — File Explorer / Calculator / Settings always emitted with synthesized stubs, since they aren't enumerated as `.lnk` files.

Junk filter: hides uninstallers, redistributables, `LicenseManagerShellExt`, `WindowsPackageManagerServer`, etc. User overrides via `hidden = true` in `app.toml` survive subsequent refreshes.

### 9.5 Output

Persisted under `~/.local/share/winpodx/discovered/`. Each app gets a `.toml` plus a `.desktop` entry registered under `~/.local/share/applications/` so the user's launcher menu populates immediately.

---

## 10. Transport selection (agent vs FreeRDP)

**Code.** `src/winpodx/core/transport/__init__.py::dispatch`.

**Two transports.**

| Transport | Mechanism | Window flash | Default timeout | Latency |
|---|---|---|---|---|
| **Agent** | HTTP `/exec` on `127.0.0.1:8765`, bearer-authed | None (CreateNoWindow=$true) | 60 s | 100-300 ms |
| **FreeRDP** | RemoteApp PS invocation via xfreerdp | Unavoidable PS console | 30 s | 3-5 s |

**Selection rule.** `dispatch(cfg)` calls agent `/health` with a 1-2 s timeout. If the agent answers, returns `AgentTransport`. Otherwise returns `FreerdpTransport`.

**Used by.** `core.updates`, `core.daemon.sync_windows_time`, `cli.pod.multi-session`, `cli.main.debloat`, GUI Tools-page debloat handler — all go through `windows_exec.run_via_transport`.

**Not used by (intentionally).** Password rotation and `winpodx pod sync-password` rescue path — both need direct credential auth, must use FreeRDP.

### 10.1 Verifying which transport is active

```
PYTHONPATH=src python3 -c "
from winpodx.core.config import Config
from winpodx.core.transport import dispatch
print(type(dispatch(Config.load())).__name__)"
```

`AgentTransport` → /exec path. `FreerdpTransport` → fallback.

### 10.2 Agent process tree

```
HKCU\Run\WinpodxAgent triggers at user logon:
  wscript.exe hidden-launcher.vbs  (GUI subsystem, no console)
    └─ powershell.exe -File C:\OEM\agent.ps1  (SW_HIDE inherited)
       └─ child PS for each /exec call
          (ProcessStartInfo with CreateNoWindow=$true)
```

Agent listener: `http://+:8765/` with `netsh http add urlacl` pre-registered (User-level, no admin needed). Token: `C:\OEM\agent_token.txt` (bind-mounted from host).

---

## 11. Recovery scenarios

### 11.1 Agent dies and stays dead

**Symptom.** `curl http://127.0.0.1:8765/health` exits 56 / no response.

**Cause.** TermService was cycled (multi-session activation, manual restart, etc.). Agent's RDP session died with it. HKCU\Run only fires at user logon, not on service restart.

**Fix.** Open any Windows app — the new RDP session triggers HKCU\Run → fresh agent comes up under wscript wrapper. If no app launches and the pod stays idle, the agent stays dead.

**Prevention.** PR #85 (ServiceDll cross-check before activation) avoids the most common case where activation was redundant. install.bat OEM v15+ writes the marker correctly so subsequent applies hit the fast path.

### 11.2 PS console flashes on every app launch

**Symptom.** Brief black console appears for ~50 ms after each launch (UWP or Win32, doesn't matter).

**Diagnosis.** Probe `HKCU\Run` values via agent /exec. Look for any entry not wrapped in `wscript.exe ... hidden-launcher.vbs`. Common culprits:

- `WinpodxMedia` — fixed in PR #84 (was bare `powershell.exe -WindowStyle Hidden`).
- `WinpodxAgent` — fixed in PR #58.

**Fix.** `winpodx pod apply-fixes` rewrites both entries (the `vbs_launchers` step is conditional on legacy entries existing, so it's safe to re-run).

### 11.3 "Select a session to reconnect to" dialog

**Symptom.** Multi-session not active. Each new app launch replaces the previous session.

**Diagnosis.** Probe `HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters\ServiceDll`:

- `C:\Program Files\RDP Wrapper\termwrap.dll` → rdprrap registry-patched. Multi-session should work; if dialog still appears, TermService loaded the old `termsrv.dll` and never cycled. Run `winpodx pod multi-session on` to cycle it (~10 s disconnect, then OK).
- `C:\Windows\System32\termsrv.dll` → not patched. Run `winpodx pod multi-session on` to install + activate.

### 11.4 Container recreated unexpectedly

**Symptom.** Pod restarts unexpectedly. dockur logs show fresh Windows install or ISO redownload.

**Cause (pre-PR #83).** `image: :latest` re-resolved by podman-compose. dockur pushed a new `:latest` since last `up`. New digest → spec mismatch → recreate.

**Fix.** Migrate (PR #83's `_ensure_canonical_image_pin`) rewrites compose to a pinned digest. Future `:latest` pushes don't disturb the user.

### 11.5 Discovery returns empty / partial

**Symptom.** `winpodx app refresh` finishes but the menu only shows a handful of apps, or no UWP entries.

**Cause (pre-PR #86).** First-boot race — AppXSvc still deploying / Start Menu indexer still propagating / agent mid-respawn.

**Fix.** PR #86 layered race avoidance. Look at the script's stderr output (visible in `apply-fixes` logs) for `[discover] stable (...) — proceeding` (good) vs `[discover] stability budget exceeded` (the pod really took >60 s to settle). Re-run `winpodx app refresh` if you suspect the budget was hit.

### 11.6 Agent token mismatch

**Symptom.** All `/exec` calls fail with 401.

**Cause.** `~/.config/winpodx/agent_token.txt` (host) doesn't match `C:\OEM\agent_token.txt` (guest). Usually after a manual edit or a partial restore.

**Fix.** Re-run `winpodx setup --non-interactive` — `_ensure_oem_token_staged()` regenerates and stages a fresh token. Restart the pod for the agent to pick it up.

### 11.7 Pod won't start

**Symptom.** `winpodx pod start` reports the container is up but `wait-ready` never gets past phase 1 or 2.

**Diagnosis.** `podman logs winpodx-windows --tail 50`. Look for:

- `proc.sh: line 137: -1: substring expression < 0` → dockur internal bug from a `:latest` push. Should be impossible post-PR #83 since the pin protects against this.
- `mknod: /dev/net/tun: File exists` → harmless warning, not the root cause.
- BdsDxe boot loop without `Windows started successfully` → guest is mid-Sysprep. Just wait.

If the container itself isn't starting (podman exits immediately), check `podman ps -a --filter name=winpodx` and `podman inspect winpodx-windows -f '{{.State.Error}}'`.

---

## See also

- **[CHANGELOG.md](../CHANGELOG.md)** — release history.
- **[AGENT_V2_DESIGN.md](AGENT_V2_DESIGN.md)** — agent protocol design notes.
- **[TRANSPORT_ABC.md](TRANSPORT_ABC.md)** — transport abstraction internals.
