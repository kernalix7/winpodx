# Guest HTTP Agent — v2 Design

**Status**: Draft, feature branch `feat/agent-v2`. Targeting a future release
after end-to-end verification on a real container.

**Background**: v0.2.2 / v0.2.2.1 / v0.2.2.2 attempted to introduce a guest
HTTP agent. The attempt shipped broken (compose port never exposed, token
delivery had chicken-and-egg, gate detection unreliable). All those releases
were rolled back; main is back at v0.2.1 + install.sh improvements.

This document is the new specification. It is written so each component can
be implemented and tested independently, and so the lessons from the v0.2.2.x
chain are explicit constraints rather than implicit assumptions.

---

## Goals

- Sub-100ms host -> guest call path for non-secret operations (apply payloads,
  log streaming, discovery progress).
- Hide the brief PowerShell-window flash that FreeRDP RemoteApp exec produces.
- Provide a definitive "Windows is ready" signal so the host can stop
  guessing whether install.bat / OOBE has finished.

## Non-goals

- Replace FreeRDP RemoteApp. The RemoteApp channel keeps password rotation,
  app launches (those need a real RDP session anyway), and any operation that
  must stay sensitive.
- Be reachable from anywhere except the host that owns the pod. Bind to
  loopback only, on both legs.

---

## Architecture

```
Host                          Container (Linux)         Windows VM
                              QEMU process inside        inside QEMU
+-------------+  compose       +----------+   QEMU      +-------------+
| AgentClient |--port 8765--+  |          |--user-mode--|  agent.ps1  |
| (Python)    |  127.0.0.1  |  |  :8765   |  hostfwd    |  :8765 HTTP |
+-------------+  :8765:8765 +--|          |  :8765-:8765+-------------+
                                +----------+
                                ^
                                |
                          USER_PORTS=8765 env var
                          dockur translates this into
                          QEMU's `-netdev hostfwd=tcp::8765-:8765`
```

Two-stage forwarding, both legs **explicit and visible in compose.yaml**:

1. `environment: USER_PORTS: "8765"` — dockur's contract for adding a QEMU
   hostfwd. Forwards Windows VM's loopback 8765 to the Linux container's
   loopback 8765.
2. `ports: - "127.0.0.1:8765:8765/tcp"` — podman/docker's standard port
   mapping. Forwards the container's loopback 8765 to the host's loopback
   8765.

**Both legs are required.** v0.2.2 shipped only leg 1 — the agent listened
inside Windows, dockur forwarded it to the container, but no host port
mapping meant `curl http://127.0.0.1:8765/health` from the host always got
connection-refused. That single missing line is what made the entire
v0.2.2 agent feature dead-on-arrival.

## Token delivery

**Pre-stage the token via the existing OEM bind mount.** No FreeRDP roundtrip
required. No race with install.bat.

```
~/.config/winpodx/                 config/oem/                 dockur lays  C:\OEM\agent_token.txt
agent_token.txt          --copy--> agent_token.txt    --mount-->/oem--copy--+
(0600, host)                       (0600, host repo)   (compose)            v
                                                                      agent.ps1 reads
                                                                      Wait-Token loop
                                                                      binds 8765 with
                                                                      bearer auth
```

At `winpodx setup` time:

1. `ensure_agent_token()` generates / loads `~/.config/winpodx/agent_token.txt`
   (mode 0600).
2. The same content is copied to `<oem_dir>/agent_token.txt` (mode 0600).
3. compose.yaml mounts `<oem_dir>:/oem:Z` (already does, since v0.1.6).
4. dockur's first-boot install copies `/oem/*` to `C:\OEM\` inside Windows.
5. `agent.ps1` (started via HKCU\Run at user logon, registered by install.bat)
   reads `C:\OEM\agent_token.txt` on startup and binds the HTTP listener.

`config/oem/agent_token.txt` is .gitignore'd — it carries the per-install
secret and must never enter the repo.

If the OEM mount somehow fails to deliver the file (corner case), agent.ps1's
Wait-Token loop polls every few seconds with bounded backoff, so the listener
binds whenever the token eventually appears.

## Readiness signal

`GET /health` returns 200 with a small JSON status payload. **No auth on
/health.** It is the single, definitive "agent is up, install.bat is done,
rdprrap is active" signal. Everything downstream gates on it:

- `provisioner.ensure_ready` waits for /health to respond before firing
  any FreeRDP RemoteApp work that could race install.bat.
- `winpodx pod wait-ready` polls /health.
- GUI status timer probes /health to render readiness state.

If /health doesn't respond, **no FreeRDP RemoteApp probes are fired
speculatively**. The previous "Phase 3 ping storm" (3s probe interval, ~100
PS-window flashes per resume) is gone. The host simply waits or surfaces
"still booting" to the user, and tries again on the next user-driven
ensure_ready (next app launch).

## Auth

Every endpoint except `/health` requires `Authorization: Bearer <token>`.
The token is the same 32-byte hex string in both:

- `~/.config/winpodx/agent_token.txt` (host, read by `AgentClient._token()`)
- `C:\OEM\agent_token.txt` (guest, read by agent.ps1 at startup)

Constant-time compare. Any mismatch returns 401.

## Endpoints (phased)

### Phase 1 — readiness only

| Method | Path     | Auth | Purpose                                                   |
|--------|----------|------|-----------------------------------------------------------|
| GET    | /health  | no   | Liveness + version probe. Used as the readiness signal.   |

This is enough to **prove the architecture works end-to-end**: host sees
agent, gate logic stops guessing.

### Phase 2 — exec channel

| Method | Path     | Auth | Purpose                                                   |
|--------|----------|------|-----------------------------------------------------------|
| POST   | /exec    | yes  | Run a base64-encoded PowerShell script, 60s server cap.   |

Replaces `run_in_windows` (FreeRDP RemoteApp) for non-sensitive payloads in
`_self_heal_apply`.

### Phase 3 — streaming

| Method | Path             | Auth | Purpose                                          |
|--------|------------------|------|--------------------------------------------------|
| GET    | /events          | yes  | Long-lived SSE feed of agent log events.         |
| POST   | /apply/{step}    | yes  | Multi-step apply with progress lines via SSE.    |
| POST   | /discover        | yes  | App discovery with progress + final JSON.        |

Used by GUI Logs page, Maintenance page, Refresh button.

## Components

```
src/winpodx/core/agent.py             AgentClient: health(), exec(), stream_events(),
                                       post_apply(), post_discover(); exception types.

src/winpodx/utils/agent_token.py      ensure_agent_token() / token_path() — host side.

config/oem/agent/agent.ps1            HTTP server inside Windows. Wait-Token loop,
                                       HttpListener on 127.0.0.1:8765, SSE plumbing.

config/oem/install.bat                Adds: copy agent.ps1 to C:\OEM\, register
                                       HKCU\Run entry. (No \\tsclient\home copy —
                                       token is in the OEM mount already.)

src/winpodx/core/compose.py           USER_PORTS=8765 env + 127.0.0.1:8765:8765/tcp
                                       port mapping in the compose template.

src/winpodx/cli/setup_cmd.py          Stages token to oem_dir/agent_token.txt at
                                       setup time (helper: _ensure_oem_token_staged).

src/winpodx/core/provisioner.py       Uses AgentClient.health() as the readiness
                                       gate; defers FreeRDP work when it doesn't
                                       respond.
```

## Phased rollout

Each phase is a standalone PR-sized chunk. **Each phase ends with the user
running `--main` install on a real container** and confirming the phase
works before the next phase starts. No phase merges to main without
end-to-end verification.

### Phase 1 — minimal /health roundtrip
- compose.py: USER_PORTS env + port mapping
- agent_token.py: token generation
- setup_cmd.py: stage token to oem_dir
- agent.ps1: minimal HttpListener with /health only (no auth needed)
- install.bat: copy agent.ps1 + HKCU\Run
- agent.py: AgentClient.health() only
- Tests: unit + compose port + agent.ps1 syntax

User test: `curl http://127.0.0.1:8765/health` returns JSON.

### Phase 2 — auth + exec
- agent.ps1: bearer auth on every endpoint except /health, /exec endpoint
- agent.py: AgentClient.exec(), exception types
- Tests: auth pass / fail, exec rc + stdout/stderr handling

User test: `winpodx pod exec 'Write-Output ok'` round-trips via agent.

### Phase 3 — provisioner integration
- provisioner.py: ensure_ready waits for /health; defers FreeRDP if not up
- _self_heal_apply uses run_via_agent_or_freerdp
- Tests: gate semantics, fallback on agent down

User test: app launch on first boot completes without dialog flashes.

### Phase 4 — streaming
- agent.ps1: /events SSE, /apply/{step}, /discover with progress
- agent.py: stream_events, post_apply, post_discover
- Tests: SSE parsing, done-event handling

User test: Logs page in GUI live-tails agent events.

### Phase 5 — GUI integration
- Replace FreeRDP-only paths in GUI with agent paths where applicable
- Refresh button uses agent /discover
- Logs page uses /events

User test: GUI feels snappier; no PS flashes on routine actions.

## Anti-goals (lessons from v0.2.2.x)

These are explicit "do NOT do" rules.

1. **Do not deliver the token via FreeRDP RemoteApp.** That created the
   chicken-and-egg loop: agent needed token to bind, token needed FreeRDP
   to land, FreeRDP needed rdprrap to avoid dialog conflicts, rdprrap was
   set up by install.bat which raced the token push.
2. **Do not invent a "windows is done" heuristic from log strings,
   container age, or any signal other than /health.** Every heuristic we
   tried (dockur sentinel, time fallback, parse-Go-format-timestamps)
   either fired too early or too late. /health responding is the only
   unambiguous proof that install.bat completed AND rdprrap activated AND
   the agent could bind.
3. **Do not fire FreeRDP RemoteApp probes speculatively.** Each one is
   a visible PS window + a chance to trip a single-session conflict
   dialog. If the agent is silent, surface "still booting" to the user
   and let them retry; do not poll FreeRDP every 3s for 5 minutes.
4. **Do not omit the compose port mapping.** Without
   `127.0.0.1:8765:8765/tcp`, the agent's listener inside Windows is
   functionally invisible to the host. This single line is what made
   v0.2.2 dead on arrival.
5. **Do not register the agent via `schtasks /SC ONLOGON /RU User /RL
   HIGHEST`.** The principal name doesn't always match cfg.rdp.user, and
   /RL HIGHEST races dockur's autologon UAC flow. Use HKCU\\...\\Run —
   identical to the existing WinpodxMedia entry, fires for whichever
   account autologon picks.
6. **Do not throw on missing token.** agent.ps1 must Wait-Token-style
   poll until the file appears. Throwing kills the process and HKCU\\Run
   doesn't auto-restart.
7. **Do not block CLI launch on a synchronous resume.** The
   `_maybe_resume_pending` path must skip for `gui` / `tray` so the GUI
   window paints before the resume worker runs in a thread.

## Test strategy

### Per-phase

- Unit (Python): mock HTTP for AgentClient, mock subprocess for token
  generation, mock filesystem for OEM staging.
- Compose validation: parse generated yaml, assert both forwarding-chain
  legs are present.
- agent.ps1 syntax: `pwsh -NoProfile -Command "& { ... parse }"` to catch
  syntax errors without running it.
- agent.ps1 endpoint coverage: regex-scan agent.ps1 for the expected
  endpoint handlers.

### End-to-end (user-driven)

After each phase ships to `feat/agent-v2`, the user runs:

```
curl ... install.sh | bash -s -- --ref feat/agent-v2
```

on a clean container (or an existing one — port binding and OEM mount
both apply on container recreate, no Windows reinstall) and confirms the
phase's user-test behaves correctly.

Only after the user confirms does the phase get cherry-picked or rebased
to main, and a new RTM tag goes out only when the user explicitly says
"approve".
