# Agent-First Install Architecture — Design Document

**Status**: v2 (post 5-team review)
**Tracking**: relates to #126 (cachyos cascade), #121 / #122 (btrfs install.bat death), #143 (half-uninstalled state guard, just merged), and the cachyos rotate-password / app-run intermittent kernalix7 has been hitting since v0.4.3
**Author**: kernalix7
**Last updated**: 2026-05-08
**Implementation branch**: `feat/agent-first-install`

## Review log

| Round | Date | Reviewers | Outcome |
|---|---|---|---|
| v1 | 2026-05-08 | author draft | initial sketch, 10 open questions |
| v2 | 2026-05-08 | core, security, qa, cli, desktop | 49 amendments integrated; 7 of 10 open questions resolved; 3 deferred to Phase 2 implementation |

## Goals

Restructure the Windows-guest first-boot install sequence so that the
**agent** (`agent.ps1`, the bearer-authed HTTP `/exec` endpoint we
ship) comes up FIRST — immediately after the Defender exclusion — and
every subsequent install step (rdprrap install + activation,
vbs_launchers, multi-session activation, OEM tweaks) is gated by a
**`/health`-verified agent ready signal** rather than running blindly.

Once agent is ready, the host's apply chain and all subsequent
host→guest commands route through the agent (HTTP `/exec`) by default.
FreeRDP RemoteApp is only used as a fallback when the agent is
genuinely unreachable — not as a happy-path companion. This
eliminates the cachyos-style drive-redirect class of bugs from the
install path entirely.

## Non-goals

- Replacing FreeRDP for **app launches** — RemoteApp is still the
  user-facing window protocol. This document is about the
  **automation channel** the host uses to configure the guest, not
  the protocol that paints app windows.
- Re-architecting Windows first-boot itself (autounattend.xml,
  Sysprep flow). dockur owns that; we work within it.
- Changing the agent's protocol or wire format. The improvements are
  in *when* and *how* agent is brought up, not what it does.
- libvirt / manual backends — agent-first applies only to
  podman/docker. libvirt keeps the legacy flow.

## Acceptance criteria

End-to-end: a user runs
`curl … install.sh | bash -s -- --main` on any supported host and
the install completes deterministically — either succeeding with a
fully-applied Windows guest, or failing fast with a clear,
actionable error pointing at the specific step that broke.

Specifically:

1. **Agent is up before any other configuration step.** install.bat
   stages and starts agent.ps1 immediately after the Defender
   exclusion. Subsequent steps only run after agent `/health` is
   verified responsive.
2. **Agent crash is recoverable.** If agent dies between steps,
   install.bat's in-process watchdog respawns it.
   HKCU\Run + the inline watchdog are the autostart pair (see
   §"Autostart pair").
3. **Idempotent + resumable.** Each step writes a `<step>.done`
   marker. install.bat re-runs (e.g. after a partial failure) skip
   completed steps cleanly, with a post-condition re-verify pass to
   catch reality drift.
4. **Performance-adaptive.** Agent `/health` verification uses
   exponential backoff with bounded retry, accommodating fast
   x86_64 NVMe (3-5 minute installs), slow HDD-btrfs (rare now,
   post-#125), and Pi 5 SD-card class hardware (15-30 minute
   target).
5. **Zero FreeRDP dependency on the install path.** Once agent is
   confirmed up, the host's apply chain runs entirely through the
   agent. FreeRDP fallback is reserved for upgrade scenarios where
   an old install (no agent) is being migrated to agent-first.
6. **Diagnostic visibility.** `winpodx pod install-status` shows
   per-step state, elapsed time, last log lines. `--json` for
   tooling. `--logs` interleaves install + container streams.
7. **No regression on healthy installs.** Existing successful
   install paths on openSUSE / Ubuntu / Fedora x86_64 continue to
   work without user-visible change. The restructuring is internal.

## Resolved decisions

The v1 draft had 10 open questions. v2 resolves 7 explicitly; 3 are
deferred to Phase 2 implementation as they require touching code to
answer well.

| # | Question | Resolution |
|---|---|---|
| 1 | Defender exclusion ordering | **Phase 0 (first), as v1 sketched.** Pre-condition for everything else; Phase 0.5 then creates `C:\winpodx\install-state\`. *(core review #1)* |
| 2 | Agent autostart triple — overkill? | **Drop the agent-autostart Scheduled Task; keep HKCU\Run + watchdog.** Three legs was over-engineered; the only scenario the Task covered was already covered by HKCU\Run on next logon. The watchdog handles the in-install respawn case. **Note:** a *separate* Scheduled Task (`winpodx-install-resume`, logon trigger) IS added for a different purpose — to re-run install-resume.ps1 when the agent itself is dead and the host can't `/exec` back in. See §"install-resume". *(core review #2 + security review #6)* |
| 3 | Watchdog: in-process vs separate ps1 | **In-process loop.** Simpler; dies with install.bat which is what we want (no orphan watchdogs after install). *(core review unchanged from v1)* |
| 4 | State directory location | **`C:\winpodx\install-state\`.** install.bat already owns `C:\winpodx\` (Defender exclusion target). Add explicit Phase 0.5 step to create the state dir before any marker write. *(core review #1)* |
| 5 | install-resume auto-trigger vs explicit | **Auto-trigger with three guards: (a) clear stderr notice before resuming; (b) once-per-session-id constraint to prevent infinite loops; (c) `WINPODX_NO_AUTO_RESUME=1` escape hatch.** The user mental model after `install.sh` fails is "re-run and it should fix itself"; explicit `install-resume` is friction. *(cli review #2)* |
| 6 | Non-podman backends | **libvirt / manual = legacy flow only.** Agent-first is podman/docker only for v1. Branch is at the wait-ready orchestrator; install.bat itself stays unified across backends (no agent-first vs legacy fork inside install.bat). *(core review #8)* |
| 7 | Telemetry / install duration | **Scope-out for v1.** Any future collection must be opt-in, no auto-upload, with a written privacy contract before code lands. *(security review #7)* |
| 8 | Resume — keep prior session markers? | **Yes, with `<step>.done.session-<old_id>` archive.** A few KB for forensic continuity. Resume reuses the *original* session ID (qa review #5). |
| 9 | Defender broken / fights us | **Read-after-write verification at Phase 0; if exclusion is reverted within 60s, fail loud with "GPO is removing winpodx Defender exclusions; install cannot proceed safely on this machine."** No silent retry on a fight we can't win. *(security review #8)* |
| 10 | Agent self-test before `agent_ready.done` | **Yes — three-step self-test: (i) `/health` 200; (ii) bearer round-trip; (iii) `/exec` of `Write-Output 'agent-self-test'` returns rc=0.** Adds ~500ms but catches more failures (agent up but token mismatch, agent up but `/exec` broken, etc.). *(v1 author proposal — adopted)* |

## Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│                   WINDOWS GUEST (first boot)                     │
│                                                                  │
│  install.bat (FirstLogonCommand) — state machine                 │
│  ────────────────────────────────────────────                    │
│                                                                  │
│  Phase 0   Defender exclusion (registry write)                   │
│            ├─ verify post-write read-back                        │
│            ├─ if reverted in 60s → fail with GPO message         │
│            └─ marker: defender_exclusion.done                    │
│                                                                  │
│  Phase 0.5 Create state dir C:\winpodx\install-state\            │
│            └─ marker: state_dir_ready.done                       │
│                                                                  │
│  Phase 0.6 Stage agent token (ACL'd User-only via icacls)        │
│            ├─ source: C:\OEM\agent\agent_token.txt               │
│            ├─ persists for install lifetime (NOT delete-after-   │
│            │  first-read — agent re-reads on every cold start)   │
│            └─ marker: token_staged.done                          │
│                                                                  │
│  Phase 1   Agent install + autostart  ★ NEW ORDERING ★           │
│            ├─ Stage agent.ps1 to C:\winpodx\agent\               │
│            │   (NOT C:\Users\Public\winpodx\agent\ — Defender    │
│            │    exclusion at C:\winpodx\ already covers it)      │
│            ├─ Register HKCU\Run\winpodx-agent                    │
│            ├─ Register Scheduled Task winpodx-install-resume     │
│            │   (logon trigger, runs install-resume.ps1 if        │
│            │    install_failure.json is present at logon)        │
│            ├─ Spawn agent.ps1 (immediate first-launch)           │
│            ├─ Self-test: /health 200 → bearer rt → /exec 'Write- │
│            │             Output' rc=0 (all three must pass)      │
│            └─ marker: agent_ready.done                           │
│                ↑                                                 │
│                │ Host can now talk to guest via /exec            │
│                                                                  │
│  Phase 2   Idempotent setup steps (each gated by                 │
│            agent /health re-check, retry on transient failure)   │
│            ├─ Step: firewall rule for port 8765                  │
│            ├─ Step: rdprrap installer                            │
│            │   - Pre-cond: agent /health responsive              │
│            │   - Action: extract bundle, run installer.exe       │
│            │   - Verify post-cond: rdprrap_version.txt exists,   │
│            │     version matches expected                        │
│            │   - Retry: up to 3× with backoff (5s, 30s, 90s)     │
│            │   - On Defender lock: wait + retry                  │
│            │   - On terminal failure: structured log + fail loud │
│            │   - marker: rdprrap_installed.done                  │
│            ├─ Step: vbs_launchers staging                        │
│            ├─ Step: oem_runtime_fixes (RDP timeouts, NIC PM, ...)│
│            ├─ Step: max_sessions registry                        │
│            └─ Step: multi_session activation (rdprrap-activate)  │
│                - Pre: rdprrap_installed.done present             │
│                - Action: activate, schedule TermService restart  │
│                - Post: agent re-verified after TermService cycle │
│                - marker: multi_session_active.done               │
│                                                                  │
│  Phase 3   Final marker                                          │
│            ├─ Token rotation (now-only — old token zeroed,       │
│            │  new token rolled in agent + on host)               │
│            └─ marker: install_complete.done (host wait-ready key)│
│                                                                  │
│  Watchdog (in-process during install.bat run):                   │
│   - Polls agent /health every 30s                                │
│   - Probe debounce: 2× retry (2s, 5s backoff) before counting    │
│     a death, so Defender-induced 5s stalls don't trip it         │
│   - On confirmed death: respawn via Start-Process; wait up to    │
│     60s for /health to come back                                 │
│   - 3 respawn cycles → write install_failure.json, exit 1        │
│   - Watchdog dies with install.bat (intended; no orphan)         │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
                                ▲
                                │ HTTP /exec (bearer auth, base64 PS)
                                │ + read-only \tsclient access for                                logs / markers
                                │
┌───────────────────────────────┴──────────────────────────────────┐
│                          LINUX HOST                              │
│                                                                  │
│  install.sh (no behaviour change at the script layer)            │
│                                                                  │
│  winpodx setup (host-side state dirs, compose, token gen)        │
│                                                                  │
│  winpodx pod start                                               │
│                                                                  │
│  winpodx pod wait-ready (NEW SEMANTICS, 5 stages):               │
│      Stage 0  container running                  (60s)           │
│      Stage 1  RDP port 3390 open                 (90s)           │
│      Stage 2  agent /health responsive          (15min default,  │
│                                                  cfg.install.    │
│                                                  wait_ready_     │
│                                                  stage2_secs)    │
│      Stage 3  install_complete.done present    (30min default,   │
│               (read via agent /exec)             cfg.install.    │
│                                                  wait_ready_     │
│                                                  stage3_secs)    │
│      Stage 4  install_failure.json absent       (concurrent w/3) │
│      ─────────                                                   │
│      Each stage emits structured progress to stderr.             │
│      Total 5-stage: backoff schedule cfg-overridable.            │
│                                                                  │
│  winpodx migrate / app run / etc. — already agent-first via      │
│   dispatch() in dispatch.py; no change at this layer.            │
│                                                                  │
│  Diagnostic surface:                                             │
│   - winpodx pod install-status [--json] [--logs]                 │
│   - winpodx pod install-resume [--non-interactive] [--yes]       │
│       └─ /exec → Start-Process powershell -File C:\OEM\          │
│                  install-resume.ps1 (re-enters state machine)    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Why agent-first

Empirical evidence from past two weeks of smoke-testing:

- **#121 / #122**: install.bat dies during rdprrap install (Defender +
  btrfs CoW interaction); agent never installed; user has no recovery.
- **#126 (xiyeming, cachyos)**: install.bat completes but cachyos's
  experimental VAAPI xfreerdp3 build kills the host's FreeRDP-based
  apply chain; some applies succeed, others fail with `BIO_read
  retries exceeded`.
- **#143 (just merged)**: half-uninstalled state symptom is the same
  shape — install path assumes preconditions and falls over without
  recovery when reality diverges.
- **kernalix7's cachyos heisenbug**: `app run` works right after pod
  restart, then degrades — likely related to rdprrap's TermService
  cycle interacting with cachyos in a way the host has no visibility
  into.

The common thread: install relies on **brittle implicit ordering +
silent failure recovery via FreeRDP**. Agent-first replaces both with
explicit ordering, verified at each step, on a single robust channel.

## Component contracts

### Guest side: `install.bat`

State machine with these properties:

- Reads `C:\winpodx\install-state\<step>.done` markers to determine
  what's already complete on resume.
- Each step is a function with a uniform contract:

  ```powershell
  function step_<name> {
      $marker = "C:\winpodx\install-state\<name>.done"

      # 1. Read marker AND re-verify post-condition (catch reality drift —
      #    e.g. user manually removed Defender exclusion after step ran).
      if (Test-Path $marker) {
          if (verify_post_condition_<name>) { return 0 }
          # Drift detected: log it, delete the marker, fall through.
          Write-WinpodxLog "drift" -step "<name>" -reason "post-cond failed"
          Remove-Item $marker
      }

      # 2. Verify preconditions (agent /health for non-Phase-0/0.5/0.6/1 steps).
      if (-not check_preconditions_<name>) {
          Write-WinpodxLog "precond_fail" -step "<name>"; return 1
      }

      # 3. Run the step.
      $rc = run_<name>
      if ($rc -ne 0) {
          incrment_retry_<name>; return $rc
      }

      # 4. Verify post-condition (state check, not just exit code).
      if (-not verify_post_condition_<name>) {
          Write-WinpodxLog "postcond_fail" -step "<name>"
          incrment_retry_<name>; return 1
      }

      # 5. Atomic write of marker file (empty sentinel; rename-from-temp).
      New-Item -ItemType File -Path $marker -Force
      return 0
  }
  ```

- Orchestrator runs each step in order; on non-zero with retries
  exhausted, writes `install_failure.json` (sanitized — see §Schema)
  and exits with Windows event log entry.

State directory layout:

```
C:\winpodx\install-state\
├── install_session_id.txt          ← UUID of this install run.
│                                     Rotated on FRESH install only;
│                                     resume reuses prior ID.
├── defender_exclusion.done         ← Phase 0 marker (empty sentinel)
├── state_dir_ready.done            ← Phase 0.5 marker
├── token_staged.done               ← Phase 0.6 marker
├── agent_ready.done                ← Phase 1 marker (gates everything else)
├── rdprrap_installed.done          ← Phase 2 markers
├── vbs_launchers.done
├── oem_runtime_fixes.done
├── max_sessions.done
├── multi_session_active.done
├── install_complete.done           ← Phase 3 marker (host waits for this)
├── install.log                     ← rolling install log, structured
│                                     JSON-per-line; redacted
├── install_failure.json            ← present iff failed; sanitized
│                                     diagnostic snapshot
├── retry_counts.json               ← per-step retry counter
└── archive/                        ← prior session markers (forensic)
    └── <step>.done.session-<old_id>
```

### Guest side: agent autostart pair

Two complementary mechanisms:

1. **HKCU\Run\winpodx-agent** — fires when User logs in. Standard
   Windows pattern; survives reboot. Pinned at install time.

2. **In-process watchdog** during install.bat's run — polls `/health`
   every 30s, debounced (2× retry at 2s/5s before counting a death),
   respawns up to 3 times. Watchdog dies with install.bat (intended).

Why not a third autostart Scheduled Task: the v1 draft proposed it
for the "install.bat exits before agent registered" scenario, but
that scenario is impossible given the new ordering — Phase 1
explicitly waits for `agent_ready.done` and won't let install.bat
proceed until then.

### Guest side: install-resume Scheduled Task (separate purpose)

A **different** Scheduled Task — `winpodx-install-resume`, logon
trigger — exists for the case where:

1. install.bat dies (Defender, OOM, …)
2. agent is also dead (otherwise host can `/exec` install-resume)
3. User logs in / pod reboots

The logon-triggered task fires `install-resume.ps1` if
`install_failure.json` is present. The script re-enters the state-
machine main loop, picking up at the first non-`.done` step.

Without this task, a host that can't reach the agent has no way to
trigger resume — chicken-and-egg.

### Host side: `winpodx pod wait-ready` (new five-stage)

| Stage | Probe | Default budget | Cfg key | Required? |
|---|---|---|---|---|
| 0 | Container running | 60s | (hardcoded) | Yes |
| 1 | RDP port 3390 open | 90s after Stage 0 | (hardcoded) | Yes |
| 2 | agent `/health` 200 OK | **15min** after Stage 1 | `wait_ready_stage2_secs` | Yes (canonical) |
| 3 | `install_complete.done` present (read via `/exec`) | **30min** after Stage 2 | `wait_ready_stage3_secs` | Yes for fresh install |
| 4 | `install_failure.json` absent | concurrent with 3 | (hardcoded) | Yes |

Adaptive backoff schedule (per stage):

```
first_probe_at = 5s
backoff = doubles each retry, capped at 60s
schedule: 5s → 10s → 20s → 40s → 60s → 60s → ... (until budget exhausted)
```

Fresh-vs-upgrade distinction (per core review #6):

- `install_session_id.txt` **absent** → **legacy upgrade**. Stage 2
  falls back to a FreeRDP-RemoteApp ping after 60s budget; Stage 3
  is skipped entirely (no `install_complete.done` from legacy flow).
- `install_session_id.txt` **present**, `install_complete.done` absent
  → **fresh install in progress**. Run all 5 stages.
- both present → **healthy**. Wait-ready returns immediately at Stage
  3 success.

Per-stage progress streams to stderr:

```
Stage 0/4: container running                 (+12s)
Stage 1/4: RDP port 3390 open                (+45s)
Stage 2/4: agent /health up                  (+1m 17s)
Stage 3/4: guest install in progress         (+1m 22s, on multi_session_activate)
Stage 3/4: install complete                  (+12m 41s)
```

`--logs` flag streams two interleaved sources:

```
[container] 2026-05-08T09:17:40Z container started
[install]   2026-05-08T09:17:51Z step=multi_session attempt=1 starting...
[container] 2026-05-08T09:18:02Z rdprrap service restarting...
```

If agent unreachable at any point, `[install]` stream silently drops
with a one-line notice: `"Agent unreachable — install.log not
available; showing container log only."`

### Host side: `winpodx pod install-status`

Output format (cli review #1, summary-first):

```
winpodx-windows  install  RUNNING  (+12m)  session abcd1234
────────────────────────────────────────────────────────────
  Phase 0    defender exclusion        done   +0:12
  Phase 0.5  state dir ready           done   +0:13
  Phase 0.6  token staged              done   +0:13
  Phase 1    agent ready               done   +0:45
  Phase 2    rdprrap install           done   +2:12
  Phase 2    vbs launchers             done   +2:18
  Phase 2    oem runtime fixes         done   +2:24
  Phase 2    max sessions              done   +2:26
  Phase 2    multi-session activate    running  (+0:42)  ◀
  Phase 3    install complete          pending

Last 20 lines of install.log (2026-05-08T09:17:51Z):
  [INFO] step=multi_session attempt=1 starting...
  ...
```

Headline values: `RUNNING` (yellow), `DONE` (green), `FAILED at <step>`
(red), `AGENT UNREACHABLE` (yellow). Color suppressed with
`--no-color` and on non-tty stdout. Step status: `done` ✓ green,
`running` ◀ yellow, `failed` ✗ red, `pending` · grey.

`--json` output (machine-parseable, GUI-consumable):

```json
{
  "session_id": "abcd1234-5678-...",
  "status": "running",
  "elapsed_seconds": 742,
  "agent_reachable": true,
  "marker_state_cached": false,
  "steps": [
    {"phase": 0, "name": "defender_exclusion", "status": "done",
     "elapsed_seconds": 12},
    {"phase": 2, "name": "multi_session_activate", "status": "running",
     "elapsed_seconds": 42, "attempt": 1}
  ],
  "failure": null
}
```

`status` enum: `running` / `done` / `failed` / `unknown`. `failure` is
null or the (sanitized) `install_failure.json` content.

Agent-unreachable behaviour (cli review #9): show **last cached
marker state**, labelled `[cached]`, with the agent's last-seen
timestamp:

```
winpodx-windows  install  AGENT UNREACHABLE  (last seen +3m ago)
────────────────────────────────────────────────────────────────
  [cached] Phase 2  rdprrap install   done
  [cached] Phase 2  vbs launchers     done
  ...
  Note: install.log not available. Marker state from last agent contact.
```

JSON: `"agent_reachable": false`, `"marker_state_cached": true`.

`--non-interactive` implies `--json`.

### Host side: `winpodx pod install-resume`

CLI behaviour (cli review #2):

- **Auto-trigger** on next `winpodx app run` if `install_failure.json`
  is present, with three guards:
  1. Stderr notice before resuming: `"Incomplete install detected
     (session abcd1234, failed at multi-session activate). Resuming
     — pass WINPODX_NO_AUTO_RESUME=1 to skip."`
  2. Once-per-session-id: same session can't auto-trigger twice.
     User must `install-resume --force` for the second run.
  3. `WINPODX_NO_AUTO_RESUME=1` env-var escape hatch.

- **Mechanism**: host POSTs `/exec` to agent with payload:

  ```powershell
  Start-Process powershell -ArgumentList \
      "-NoProfile -ExecutionPolicy Bypass -File C:\OEM\install-resume.ps1" \
      -WindowStyle Hidden
  ```

  `install-resume.ps1` re-enters install.bat's main loop. Marker
  re-verification ensures already-completed steps are skipped (after
  post-condition check passes).

- **If agent is down**: fallback path is the
  `winpodx-install-resume` Scheduled Task (logon trigger) that
  install.bat registered at Phase 1. User runs `winpodx pod restart`
  to log out and back in; task fires; resume runs.

- `--non-interactive` / `--yes` flag suppresses the
  "already-complete, resume anyway?" prompt for `install.sh`'s
  scripted path.

- Stage 4 failure error template (cli review #5):

  ```
  ERROR: Windows guest install failed.

    Failed step:  multi-session activate (Phase 2)
    Attempts:     3 of 3
    Last error:   rdprrap-activate.exe exited 1 — TermService did
                  not restart within 90s
    Session ID:   abcd1234 (started 2026-05-08 09:15:23, elapsed 14m)
    Log:          winpodx pod install-status --logs

  To retry:       winpodx pod install-resume
  Full details:   winpodx pod install-status
  ```

### Config schema additions

`core/config.py` gets a new `InstallConfig` dataclass (core review #5):

```python
@dataclass
class InstallConfig:
    agent_first: bool = False                    # feature flag (Phase 1-3); flips to True in Phase 4
    wait_ready_stage2_secs: int = 900            # 15 min default
    wait_ready_stage3_secs: int = 1800           # 30 min default
    auto_resume: bool = True                     # auto-trigger on app run
    watchdog_max_respawns: int = 3
    watchdog_probe_debounce_count: int = 2
    watchdog_probe_debounce_secs: list[int] = field(default_factory=lambda: [2, 5])

    def __post_init__(self) -> None:
        # Coerce / validate. Never raise; cap to safe ranges.
        ...
```

Persisted under `[install]` in `winpodx.toml`. Default flip is no-op
for existing TOMLs (key absence = default = False).

### Guest install state (host-side mirror)

NEW module `core/install_state.py` (core review #7) — distinct from
`cfg`, since this is *runtime guest state*:

```python
@dataclass
class GuestInstallStep:
    phase: int
    name: str
    status: Literal["pending", "running", "done", "failed"]
    elapsed_seconds: float
    attempt: int = 1

@dataclass
class GuestInstallState:
    session_id: str | None
    overall_status: Literal["running", "done", "failed", "unknown"]
    elapsed_seconds: float
    agent_reachable: bool
    marker_state_cached: bool
    steps: list[GuestInstallStep]
    failure: dict | None  # sanitized install_failure.json content

def fetch_install_state(cfg: Config) -> GuestInstallState:
    """Read markers + retry_counts.json + (optionally) install_failure.json
    via agent /exec. On agent unreachable, return cached state from
    `~/.local/state/winpodx/last_install_state.json` with
    marker_state_cached=True."""
```

Mirrors `core/pod/health.py` rather than `cfg.pod.storage_path`.

## Schemas

### `install_failure.json` (qa review #6, security review #3)

Pinned schema at `docs/design/install_failure.schema.json` (JSON
Schema draft-07). Required fields:

```json
{
  "session_id": "abcd1234-...",
  "failed_step": "multi_session_activate",
  "phase": 2,
  "attempt": 3,
  "max_attempts": 3,
  "exit_code": 1,
  "error_class": "rdprrap_activate_failed",
  "error_summary": "TermService did not restart within 90s",
  "timestamp_utc": "2026-05-08T09:17:51Z",
  "environment": {
    "windows_build": "10.0.26100.0",
    "disk_fs": "ntfs",
    "free_bytes": 12345678901,
    "ram_total_mb": 8192
  },
  "last_log_lines": [
    "[INFO] step=multi_session attempt=3 starting...",
    "[ERROR] step=multi_session rdprrap-activate.exe exited 1"
  ]
}
```

**Sanitization rules** (security review #3):

- `error_summary` and `last_log_lines[]` are filtered through a
  redactor on write that strips:
  - `net user <user> <pw>` argv → `net user <user> <REDACTED>`
  - `Authorization: Bearer <token>` → `Authorization: Bearer <REDACTED>`
  - `password=...`, `token=...`, `apikey=...` (case-insensitive) → `<KEY>=<REDACTED>`
  - Any base64 blob > 40 chars (likely credential) → `<BASE64-REDACTED>`
- The redactor is a unit-tested function (qa review §"Test plan").
- Reject any payload that fails schema validation; log "schema
  validation failed" instead of writing a bad file.

### `retry_counts.json`

Simple counter dict per step:

```json
{
  "rdprrap_install": 0,
  "vbs_launchers": 0,
  "multi_session_activate": 2
}
```

### `install.log`

JSON-per-line (one event per line):

```
{"ts":"2026-05-08T09:17:51Z","level":"INFO","step":"multi_session","attempt":1,"event":"start"}
{"ts":"2026-05-08T09:18:01Z","level":"INFO","step":"multi_session","attempt":1,"event":"command","cmd":"rdprrap-activate.exe"}
{"ts":"2026-05-08T09:18:33Z","level":"ERROR","step":"multi_session","attempt":1,"event":"failed","exit_code":1}
```

Redacted on write (same redactor as install_failure.json).

## Performance and adaptive timeouts

Verified hardware matrix:

| Host | Disk | Approx install time | Stage-3 budget head-room |
|---|---|---|---|
| NVMe ext4 + modern x86_64 CPU | 250 MB/s | 3-5 min | 6× |
| NVMe btrfs (post-#125 NoCoW) + modern CPU | 250 MB/s | 3-5 min | 6× |
| HDD ext4 | 100 MB/s | 8-12 min | 2.5× |
| HDD btrfs (pre-#125 NoCoW) | 5-15 MB/s effective | 30-60 min | borderline; rare now |
| Pi 5 aarch64 (#141, post-aarch64 support) | SD card | 15-30 min target | 1× |

Stage 2 default 15min covers the slowest healthy case. Stage 3
default 30min covers the long tail. Both configurable via
`cfg.install`.

User progress visibility (cli review §"--logs"): every probe in every
stage emits a structured stderr line; the user always sees something
moving, never a multi-minute silent wait.

## Crash and edge-case handling

Comprehensive enumeration (v1 had 12 rows, v2 expands to 18):

| Scenario | Detection | Recovery |
|---|---|---|
| Agent process dies mid-install | Watchdog `/health` debounced fail | Respawn via `Start-Process`; wait 60s; if dead 3× consecutive, write failure marker |
| install.bat itself dies (Defender kill, OOM, …) | Host wait-ready Stage 3 timeout | User runs `winpodx pod install-resume`; or auto-trigger on next `app run` |
| install.bat dies AND agent dies | Logon-triggered Scheduled Task `winpodx-install-resume` fires on next pod start | Re-runs install.bat from last marker |
| rdprrap installer fails (extract / registry race) | Step verify finds rdprrap_version.txt missing | Retry 3× with backoff; on terminal failure: structured failure marker + exit |
| TermService cycle (rdprrap activate) loses agent connection | Watchdog `/health` debounced fail | Wait + respawn; if agent comes back ≤60s, mark step complete; else fail loud |
| Network partition mid-install | Host stage probe times out | Stage retry with backoff; on full budget exhaustion: surface "host can't reach guest" + suggest `podman logs` |
| Disk full mid-install | Step write fails (PowerShell Out-File error) | Step exit non-zero; install_failure.json includes "disk full" hint |
| Power loss during install | Markers are atomic (Out-File temp + rename); on next pod start, install.bat resumes | Same as install.bat dying |
| User runs `install.sh` while pod already healthy | Host detects all markers present | install.sh exits early with "already installed" message; no work performed |
| User runs `install-resume` on already-complete install | All markers present | No-op, exit 0 with "all steps complete" |
| **Defender exclusion reverted by GPO** | Read-after-write verification post-Phase-0 | If reverted within 60s, fail loud with GPO-detection message; no silent retry (security review #8) |
| Agent token rotation mid-install | Host sees 401 from `/exec` | Token doesn't rotate during install (post-Phase-3 only) — this scenario is impossible by design |
| Wall-clock skew (host vs guest) | install.log timestamp validation finds nonsense | Log warning but continue; we don't depend on absolute time |
| Marker write succeeds but post-condition fails on resume | Resume checks both marker AND post-cond | Drift detected; delete marker, retry as fresh (core review #4) |
| **Agent restart loses token** (file deleted prematurely) | Agent fails `/health` self-test on restart | Token persists for install lifetime now (security review #1); not deleted until Phase 3 |
| **Agent stages to non-excluded path** | Defender flags repeated respawn as persistence pattern | Stage agent.ps1 to `C:\winpodx\agent\` (covered by exclusion); not `C:\Users\Public\winpodx\agent\` (security review #5) |
| User triggers `install.sh` mid-install (rerun) | install.sh detects `install_session_id.txt` + no `install_complete.done` | Inform user "install in progress, see `winpodx pod install-status`"; exit 0 without re-running |
| Two pods on same host (future) | install_session_id.txt is per-pod; markers are per-pod state dir | Tracked under #pod_id namespacing for v2; punt for now |

## Security threat model

(Updated with security-team review.)

### Trust boundaries

- **Guest is the user's own VM** (not adversarial in the strong sense).
  Marker spoofing is technically possible but offers no privilege gain
  — the worst the user can do is degrade their own install (security
  review #4). Document as accepted risk.
- **Host runs as the user**, not root. Agent runs as the auto-logon
  User in the guest. Bearer token gates the `/exec` channel (already
  existing infra).

### Token bootstrap (security review #1)

- Token staged at `C:\OEM\agent\agent_token.txt` with `icacls
  /grant:r <User>:(R)` (User-only, read-only) and inheritance disabled.
- Persists for install lifetime — agent re-reads on every cold start
  until Phase 3 completes.
- Phase 3 rotates the token: new token written to host-side
  `cfg`-staging dir + agent's running cert; old token in
  `C:\OEM\agent\agent_token.txt` is overwritten with zeros and
  deleted.
- If `winpodx-install-resume` task fires *after* token rotation
  somehow (shouldn't happen by sequencing), agent is up with the new
  token; old failure scenarios are obsolete.

### install_failure.json (security review #3)

- Schema-pinned (see §Schemas).
- All free-text fields (`error_summary`, `last_log_lines[]`) pass
  through a redactor at write time.
- Redactor unit-tested.
- Schema validation rejects payloads with unknown / extra fields.

### Defender exclusion regression (security review #8)

- Phase 0 writes the exclusion, then reads it back after 60s.
- If the value is reverted (GPO sweep), fail loud with: `"GPO is
  removing winpodx Defender exclusions; install cannot proceed safely
  on this machine. Contact your Windows administrator or use a non-
  managed machine."`
- No silent retry — we cannot win against active GPO management.

### Agent staging path (security review #5)

- Agent stages to `C:\winpodx\agent\` (covered by Phase 0 Defender
  exclusion).
- NOT `C:\Users\Public\winpodx\agent\` (the previous OEM bundle
  layout) — that path is not in our exclusion and may trigger
  Defender's persistence-pattern heuristics on watchdog respawn.
- A migration step in Phase 1 install.bat moves any pre-existing
  `C:\Users\Public\winpodx\agent\` to `C:\winpodx\agent\`.

### Watchdog / Defender interaction (security review #5 + crash table)

- 3 respawns within 60s on an excluded path is well within Defender's
  acceptable threshold.
- All retry budgets are configurable (`cfg.install.watchdog_max_*`)
  for users on hostile environments.

### Marker file integrity (security review #4)

- Empty sentinel files; no executable content; no integrity
  signature needed.
- Atomic write via Out-File-to-temp + Rename-Item.
- Threat: a hostile guest could spoof markers. Documented as
  accepted (no privilege gain in our threat model).

## CLI surface

Updated per cli-team review:

```
winpodx pod install-status [--json] [--no-color]
   Show install step progress, last log lines, current state.
   --json: machine-parseable output (implied by --non-interactive).
   --no-color: suppress ANSI colors.

winpodx pod install-resume [--non-interactive] [--yes] [--force]
   Retry a failed or incomplete guest install.
   --non-interactive / --yes: suppress confirmation prompts.
   --force: re-run even on already-complete install (or to override
            the once-per-session-id auto-trigger guard).

winpodx pod wait-ready [--timeout SECONDS] [--logs]
   (Existing — semantics expanded to 5 stages.)
   --logs: interleave [container] and [install] streams by timestamp.
```

`pod --help` additions:

```
  install-status    Show install step progress and last log lines
  install-resume    Retry a failed or incomplete guest install
```

## GUI integration

(Per desktop-team review — Phase 3 stretch goal, can land in a
follow-up PR.)

### `InstallProgressPage` (new)

- Lives in `gui/main_window.py`'s `QStackedWidget`.
- Shown when `install_complete.done` is absent AND
  `install_session_id.txt` is present.
- **Replaces the app menu page** while in progress (not banner-above
  — empty app grid is confusing during install; user has nothing
  actionable there).
- Polls `winpodx pod install-status --json` every 2s when window is
  focused, 5s when minimised, paused when not visible (battery-
  conscious).
- Renders:
  - `QProgressBar` (steps complete / total).
  - Per-step `QLabel` with elapsed time and status icon.
  - Scrollable last-20-log-lines `QTextEdit`.
- Auto-transitions to app menu on `install_complete.done`.
- Settings page stays accessible via top nav so user can inspect
  health/config during install.

### `notify_install_complete` / `notify_install_failed`

- New helpers in `desktop/notify.py`.
- Complete: `"winpodx ready" / "{N} Windows apps registered. Click
  to open."`, normal urgency, `winpodx` icon.
- Failed: `"winpodx install failed" / "Step '{step}' failed after
  retries. Click to retry."`, critical urgency, `dialog-error` icon,
  with `--action=retry=Retry install` invoking `winpodx pod
  install-resume`.

### `AgentConnectionMonitor`

- Qt Signal-emitting QObject wrapping `/exec` calls.
- 3 consecutive failures → emit `connection_lost` → GUI shows a
  dismissable yellow `QFrame` banner: `"Agent connection lost —
  reconnecting"`.
- Backoff 5s → 30s.
- Per-call error popups suppressed while banner is up.
- Restored connection emits `connection_restored` → clear banner.

### Settings: Install Health card

```
QGroupBox "Install Health"
├─ QLabel  "Installation: complete" (green) / "in progress" (yellow) / "failed" (red)
├─ QLabel  "Last run: 5 minutes ago — N / N phases complete"
├─ QPushButton "View Details" → modal with marker timeline (read-only InstallProgressPage)
└─ QPushButton "Re-run install" → confirm + invokes install-resume non-blocking
```

### Tray icon state

- `winpodx-installing` icon variant (yellow tint) when state ≠
  complete.
- `winpodx-error` (red) on failure.
- Default icon when complete.
- Tooltip text reflects state: `"Windows pod — installing (step 3/7)"`.
- Polled via `install-status --json` every 10s only when state ≠
  complete (battery-conscious).

### First-launch flow

- Always go to app menu on launch.
- Banner-once on first explicit launch after install: tracked via
  `~/.local/state/winpodx/welcome_shown` sentinel.
- Time-window heuristic ("install completed < 5min ago") rejected as
  fragile (desktop review #6).

### i18n

Out-of-scope for v1 (English-only). Pin a future track for
gettext / Qt linguist before any translations land.

### Accessibility

`pytest-qt` accessibility smoke test (added to dev deps). Progress
bar `setAccessibleName("Install progress")`, step labels
`setAccessibleDescription` updated on each poll, all buttons reachable
via Tab.

## Test plan

(Per qa-team review — substantially expanded from v1.)

### Unit tests (host)

- `core/install_state.py`: `GuestInstallState` parsing, step status
  derivation, failure JSON sanitization.
- `core/agent_install_state.py` (new): marker read/write, retry
  counter, atomic update, race resilience under concurrent invocations.
- `cli/pod_install_status.py`: parses markers, formats output (text
  / JSON), agent-unreachable cached fallback.
- `cli/pod_install_resume.py`: `/exec` payload construction, mode
  flags (`--force`, `--non-interactive`).
- `core/config.py:InstallConfig`: defaults, validation, TOML round-
  trip, default-flip migration.
- New wait-ready stages: each tested in isolation with mocked
  subprocess / HTTP probe.
- **Hypothesis property tests**: concurrent marker reads (watchdog)
  + writes (orchestrator) preserve atomicity. Corrupted /
  truncated `retry_counts.json` deserialises safely.
- **Backoff math test**: assert sum of probe intervals at the
  documented schedule (5s→10s→20s→40s→60s cap) equals each stage's
  budget within ±5%.
- **`install-resume` invariants**: complete→no-op, half-failed→picks
  up at first non-`.done`, double-run→idempotent.
- **`install-status` parser fuzz**: empty file, partial JSON-per-
  line, unknown step name, future-dated timestamp.
- **Watchdog debounce**: 3-failure rule uses *consecutive* failures
  with hysteresis — agent restarting cleanly inside 60s should reset
  count.
- **Performance regression**: assert wall-clock install ≤ N min on
  CI's reference x86_64 NVMe runner; fail loud on regression.
- **`install_failure.json` redactor**: tested against payloads with
  `net user`, `Authorization:`, `password=`, base64 blobs.

### Integration tests (host + mocked guest)

- Fresh install: all stages succeed end-to-end, all markers written.
- Mid-install agent crash: watchdog respawns, install completes.
- Step retry exhaustion: install_failure.json written, host wait-
  ready surfaces clear error.
- Resume on partial install: skips completed, picks up failed.
- install-status during running install: shows correct state.
- Agent-unreachable during status: shows cached state.
- Backward-compat: v0.4.3 → agent-first upgrade on existing pod
  (markers absent, Stage 2 FreeRDP fallback works).

### Mocking strategy (qa review #2)

- **pwsh-on-Linux** for unit tests of step functions. Refactor
  install.bat into `.ps1` with pure functions taking injected paths
  + HTTP client; pwsh runs in CI for free.
- **Linux-host mock harness**: fake `\\tsclient\home\` as a tmpdir +
  fake agent HTTP server for integration.
- **NO WSL / Windows containers** in CI (heavyweight, flaky, no
  aarch64).

### Smoke tests (real Windows guest)

- openSUSE Tumbleweed x86_64 (kernalix7's primary).
- cachyos x86_64 (reproduces #126-class issues).
- Ubuntu LTS x86_64 (CI-friendly).
- Fedora 41 x86_64 (RPM-family coverage; OBS publishes there).
- Debian 13 Trixie x86_64 (#140's repro distro).
- Pi 5 aarch64 (post-#141; manual checkpoint protocol).
- Power-loss simulation: SIGKILL the podman container mid-Phase-2,
  restart, assert resume completes.

Skip Alpine/musl and Asahi for v1 (out-of-scope per #142). Document
deferred.

### CI cadence (qa review #4)

- **Per-PR**: unit + integration (pwsh + mock harness) only.
- **Tag pushes + `ci-smoke` PR label**: full Windows-guest smoke
  matrix.
- SLA: smoke catches Windows-side regressions within 24h of tag.
- Estimated cost: ~2-3h/tag total CI time.

### Pi 5 manual checkpoint

`docs/testing/pi5_manual_checklist.md` keyed to release tags. Phase-
by-phase user-runnable checks (`winpodx pod install-status` snapshot
at each phase, expected timing). Block release on kernalix7's
checkbox until aarch64 self-hosted CI lands (sibling track at #141).

## CI / build / packaging

### Phase 1 ships

- `core/install_state.py` (new module)
- `core/config.py` extension (new `InstallConfig` dataclass)
- `cli/pod_install_status.py` + `cli/pod_install_resume.py`
- All unit + integration tests
- `docs/design/install_failure.schema.json`

### Phase 2 ships

- New `install.bat` state machine
- `install-resume.ps1` and supporting OEM scripts
- `winpodx-install-resume` Scheduled Task registration
- pwsh-on-Linux test harness
- Smoke test on openSUSE / cachyos / Ubuntu / Fedora / Debian

### Phase 3 ships

- New `pod wait-ready` (5-stage)
- `pod install-status` / `pod install-resume` CLI surface
- `--json`, `--logs`, etc. flags
- GUI `InstallProgressPage` + notifications + tray + Settings card
- Backward-compat path for v0.4.3 → agent-first upgrade

### Phase 4 ships

- `cfg.install.agent_first` default flips to `True`
- Legacy install path deprecated (warning on use)
- v0.5.0 release

### Phase 5 ships

- Legacy install path removed
- `dispatch.py` simplified (no agent-first vs legacy fork)
- v0.6.0 release

### Packaging single-source-of-truth

`packaging/OEM_BLOBS.txt` (introduced in #135) extended to include
`install.bat` + new OEM scripts. Verify-oem-blobs CI gate ensures
every packaging manifest references every OEM file.

### Code signing

Same cosign keyless infrastructure as #133 (Go shim) + #136 (deb /
rpm / wheel) for any binary blobs. install.bat is plaintext; covered
by source-tarball SHA256.

## Roll-out

5-phase, all under `cfg.install.agent_first` feature flag in 0.4.x;
default flips in 0.5.0:

| Phase | Scope | PR target |
|---|---|---|
| 1 | Foundations: state-dir contract, marker helpers, structured install.log writer, retry primitives, `install_failure.schema.json`, redactor. Feature flag `cfg.install.agent_first = False` default. No behaviour change. | early 0.4.x |
| 2 | install.bat refactor: state machine, watchdog, autostart pair, install-resume.ps1, install-resume Scheduled Task. Smoke-test on openSUSE / cachyos / Ubuntu / Fedora / Debian. Still flag-gated. | mid 0.4.x |
| 3 | Host-side `wait-ready` 5-stage + `install-status` + `install-resume` CLI. GUI `InstallProgressPage` + notifications + tray + Settings card. Backward-compat with legacy installs. | late 0.4.x |
| 4 | Flip the flag default to `True`. Deprecation notice on legacy. | 0.5.0 |
| 5 | Remove legacy install path; simplify `dispatch.py`. | 0.6.0 |

Each phase is its own PR, reviewable in isolation. Smoke-tested on
multi-distro matrix before phase advance.

## Out of scope (related, deferred)

- **macOS host support** (#142, exploratory).
- **ARM32 support** — dockur doesn't ship; minimal demand.
- **libvirt agent-first** — stays legacy. Tracked separately if
  demand emerges.
- **Telemetry / install duration distribution** — opt-in privacy
  contract required first.
- **Replace agent.ps1 with a more featureful agent** — protocol
  unchanged.

## Appendix A: comparison with reverse-open's design

Reverse-open also uses a state-directory + marker-file pattern.
Agent-first install reuses the infrastructure where possible:

- Both use `~/.local/share/winpodx/<feature>/` for host-side state.
- Both have a host-side daemon model (reverse-open's listener;
  agent-first install's wait-ready streaming).
- Both have a guest-side state directory.

Where they diverge:

- Reverse-open is **event-driven** (inotify on `incoming/`); agent-
  first install is **sequential** (state-machine progression).
- Reverse-open is **post-install user-runtime**; agent-first install
  is **install-time**.

Complementary: agent-first install lays the foundation that makes
reverse-open's Phase 2-4 (which depend on agent `/exec` for icon /
app sync) more robust.

## Appendix B: Open questions remaining (deferred to Phase 2 / 3)

Three v1 questions remain — decisions punted to implementation time
because they're better answered with code in hand:

- **State dir creation race** (Phase 0.5 vs install.bat resume
  ordering): if install.bat dies *before* Phase 0.5 finishes, the
  next resume can't read markers because state dir doesn't exist.
  Likely fix: Phase 0.5 is fully idempotent + retried at the very
  start of every install.bat run. Confirm in code review.
- **install-resume.ps1 vs full install.bat re-execution**: lighter
  resume script that calls into shared step functions, vs just
  re-running install.bat with the marker-skip behaviour. Pick during
  Phase 2 PR.
- **Watchdog log volume**: 30s polling + 60s respawn cycle could
  generate verbose install.log entries. Plan: log only state
  *transitions* (up→down, down→up), not every probe. Revisit if
  install.log gets too noisy in smoke testing.

These three don't block the design; they block specific Phase 2
implementation decisions.
