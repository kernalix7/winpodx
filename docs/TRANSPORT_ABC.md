# winpodx Transport ABC — host→guest channel contract

**Status**: contract spec, Sprint 0 of `feat/redesign`. Both Track A (host
modular refactor) and Track B (agent-v2 feature) implement against this
spec so they converge cleanly.

**Branch**: `feat/redesign`. Don't change this contract on a different
branch without bumping the version line below.

**Spec version**: 1.

---

## Purpose

The host has multiple ways to run a PowerShell script inside the Windows
guest:

- **FreeRDP RemoteApp** — slow (~5-10s per call), shows a brief PS
  window flash, but always available once RDP works.
- **HTTP agent** — fast (~50ms localhost roundtrip), invisible, only
  available once `agent.ps1` is bound.
- (future) **Other channels** — if we ever add WSMan/WinRM, etc.

These differ in performance + UI artifacts but expose the same operation:
"run this script, return rc/stdout/stderr". Callers shouldn't care which
channel is used; they should declare what they need and let the dispatcher
pick.

This document defines the abstract `Transport` interface every concrete
channel must implement, plus the `dispatch` helper that picks the best
available transport.

## Module layout

```
src/winpodx/core/transport/
    __init__.py       # public re-exports: Transport, TransportError, dispatch, ExecResult, etc.
    base.py           # the ABC + result types
    freerdp.py        # FreerdpTransport — wraps windows_exec.run_in_windows
    agent.py          # AgentTransport — wraps AgentClient
    dispatch.py       # dispatch(): pick agent if /health responds, else freerdp
```

Tests live under `tests/test_transport/`.

## Public API (Python)

```python
# src/winpodx/core/transport/base.py
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExecResult:
    """Result of a one-shot script execution.

    rc/stdout/stderr come from the script itself, not the transport.
    Transport-level failures (channel down, auth, timeout) raise
    TransportError subclasses instead of returning a bad ExecResult.
    """

    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


@dataclass(frozen=True)
class HealthStatus:
    """Result of a transport-level health probe.

    `available` is the only field every transport must populate; the
    rest is best-effort optional metadata."""

    available: bool
    version: Optional[str] = None
    detail: Optional[str] = None


class TransportError(RuntimeError):
    """Base for any transport-level failure (channel down, auth, malformed
    response). Distinct from a script-level non-zero rc, which lives
    inside ExecResult."""


class TransportUnavailable(TransportError):
    """Transport is not reachable (connection refused, no FreeRDP binary,
    no agent /health response, etc). Caller may fall back to another
    transport via dispatch()."""


class TransportAuthError(TransportError):
    """Transport reachable but rejected our auth (agent 401/403, FreeRDP
    bad password). Do NOT silently fall back to another transport — auth
    failures usually mean config drift, not channel state."""


class TransportTimeoutError(TransportError):
    """Server accepted the request but didn't finish in time."""


class Transport(ABC):
    """Abstract host->guest command channel.

    Implementations: FreerdpTransport (slow, always-on after RDP works),
    AgentTransport (fast, available after install.bat finishes).

    Callers should declare intent in business terms (apply fix X, run
    discovery, etc.) via higher-level modules; this layer just runs
    PowerShell.
    """

    name: str  # human-readable name, e.g. "freerdp" or "agent"

    @abstractmethod
    def health(self) -> HealthStatus:
        """Cheap probe (~50ms-2s budget) used by dispatch() to pick a
        transport. MUST NOT raise on transient failures — return
        ``HealthStatus(available=False, detail=str(e))`` instead. May
        raise on configuration errors (missing FreeRDP binary etc) so
        the caller surfaces the problem rather than silently falling
        back."""

    @abstractmethod
    def exec(
        self,
        script: str,
        *,
        timeout: int = 60,
        description: str = "winpodx-exec",
    ) -> ExecResult:
        """Run ``script`` as PowerShell on the guest.

        ``description`` is a short human-readable label for log lines and
        for FreeRDP RemoteApp's task name. Caller is trusted; do not
        inject characters that break the underlying channel.

        Raises:
            TransportUnavailable: channel down (caller may fall back).
            TransportAuthError: auth rejected (do NOT fall back).
            TransportTimeoutError: server-side timeout.
            TransportError: any other channel-level failure.

        Returns ExecResult even when script's rc != 0 — that's a
        script-level result, not a transport-level error.
        """

    @abstractmethod
    def stream(
        self,
        script: str,
        on_progress: Callable[[str], None],
        *,
        timeout: int = 600,
        description: str = "winpodx-stream",
    ) -> ExecResult:
        """Run ``script`` and call ``on_progress(line)`` for each progress
        line the script emits via the agreed channel:

        * FreerdpTransport: tail the progress file written via
          ``Write-WinpodxProgress`` (existing protocol in windows_exec.py).
        * AgentTransport: SSE feed from /apply/{step}, /discover, etc.

        Returns the final ExecResult after the stream closes. Same error
        contract as exec().
        """
```

## Dispatcher

```python
# src/winpodx/core/transport/dispatch.py
from winpodx.core.transport.base import Transport, TransportUnavailable
from winpodx.core.transport.freerdp import FreerdpTransport
from winpodx.core.transport.agent import AgentTransport


def dispatch(cfg, *, prefer: str | None = None) -> Transport:
    """Pick the best available transport for cfg.

    Default policy:
      1. If AgentTransport.health().available, use it.
      2. Else fall back to FreerdpTransport.

    ``prefer="freerdp"`` forces FreerdpTransport (used for password
    rotation — see anti-goal below). ``prefer="agent"`` raises
    TransportUnavailable if agent isn't up rather than falling back.
    """
    ...
```

## Behavioral rules (binding on every implementation)

### 1. health() never raises on transient state

A connection refused, timeout, or 5xx becomes
`HealthStatus(available=False, detail=...)`. Only raise for
configuration errors that prevent the transport from ever working
(FreeRDP binary missing on PATH, no token file when one is required,
invalid cfg).

This is what makes `dispatch()` safe — it can call `health()` on every
transport without try/except chains.

### 2. exec() is the only retry boundary

Implementations do their own internal retry (e.g. agent retries
once on a transient HTTP 503; FreeRDP doesn't retry — too expensive).
Callers MUST NOT wrap exec() in their own retry loop without checking
the exception type first; specifically, never retry on
`TransportAuthError`.

### 3. Outputs are bytes-clean strings

`stdout` / `stderr` in `ExecResult` are decoded UTF-8 strings with
errors replaced. Callers that need bytes must use a different transport
(future work; not in v1).

### 4. PowerShell scripts must be self-contained

Callers pass complete PowerShell source. The transport may wrap it
(e.g. FreerdpTransport adds the result-file harness) but does not
parse, modify, or interpret the user's script.

### 5. Timeouts are enforced server-side when possible

AgentTransport: `/exec` endpoint enforces `timeout` via job kill.
FreerdpTransport: `subprocess.run(timeout=...)` on the FreeRDP process.
Either way, a hung script doesn't leak past the timeout.

### 6. (superseded 2026-05-07) Password rotation may use any Transport

Historical: this rule prohibited using Transport for password rotation,
arguing (a) the host needs to authenticate FreeRDP with the OLD password
and (b) routing via AgentTransport would expose the new password to the
agent process memory.

Both arguments no longer hold:

- AgentTransport authenticates with a bearer token, not the user
  password. Bootstrapping is independent of which password is in cfg.
- Both transports expose the new password equally — via PowerShell
  argv and `net user` argv on the guest, visible to any other guest
  process via Task Manager / WMI. The agent path adds one in-memory
  HTTP request buffer; the FreeRDP path adds one on-disk script file
  under `~/.local/share/winpodx/windows-exec/`. Neither is strictly
  worse from a memory-exposure standpoint.

In practice the prohibition caused a real bug: cachyos's xfreerdp3
build has a broken bidirectional drive redirect, so the FreeRDP-only
rotation path timed out at 45s with no recourse. `core/rotation/` now
prefers AgentTransport with FreeRDP fallback.

The pre-rotation `_ROTATION_PENDING_MARKER` is still required and
unchanged — it's the recovery contract for ANY transport that
disconnects mid-rotation.

## Anti-goals (do NOT do these)

- **Don't add a generic key-value store** to ExecResult. If a script
  needs structured output, parse stdout in the caller.
- **Don't make Transport awaitable.** v1 is sync. Async wrappers can
  come later if/when the GUI needs them.
- **Don't add cancellation tokens to exec().** A canceled script leaves
  the guest in an undefined state. If you need cancellation, use
  stream() with a stop event in `on_progress`.
- **Don't make the dispatcher cache transport instances**. Each
  caller gets a fresh Transport. State (token, base URL) lives on
  cfg + the transport's __init__ args, not the instance.

## Transport-specific notes

### FreerdpTransport

- Wraps `windows_exec.run_in_windows`.
- `health()` checks for `xfreerdp` / `xfreerdp3` etc on PATH and probes
  the RDP TCP port. Returns available=True if RDP port answers, even
  if the actual RemoteApp call would fail — that's a per-call concern.
- `exec()` is unchanged from current behaviour: 5-10s, brief PS flash.
- `stream()` uses the existing `Write-WinpodxProgress` file-tail
  protocol (already in `windows_exec.py`).

### AgentTransport

- Wraps `AgentClient` (already on `feat/agent-v2`).
- `health()` calls `AgentClient.health()` with a 2s timeout. Connection
  refused / timeout / 5xx → `available=False`. JSON parse error → also
  `available=False` with a clear `detail`.
- `exec()` POSTs to `/exec` with bearer auth. Maps 401/403 to
  `TransportAuthError`.
- `stream()` consumes `/exec`'s SSE variant (Phase 4 of agent-v2; not
  in Sprint 1).

## Versioning

This spec is v1. Breaking changes require a new spec doc + version
bump. Both Track A and Track B implementations declare
`SPEC_VERSION = 1` so a mismatch fails fast at import time.

## What changes WHERE in the codebase

### New (Sprint 1-2 of feat/redesign)

- `src/winpodx/core/transport/{__init__,base,freerdp,agent,dispatch}.py`
- `tests/test_transport/{test_base,test_freerdp,test_agent,test_dispatch}.py`

### Modified callers (Sprint 2-3)

Today's `run_in_windows` callers move to `dispatch(cfg).exec(...)`:

- `core/migrate/` (new module — uses Transport from day one)
- `core/discovery/` (after Step 3 extraction)
- `cli/pod.py` `apply-fixes` handler

### Untouched (intentional)

- `core/rdp/launch.py` — RemoteApp launches for actual user apps stay
  on FreeRDP directly. Transport is for command channels, not for the
  user-facing app windows.

### Migrated 2026-05-07

- `core/rotation/` — now agent-first via `dispatch(cfg, prefer="agent")`
  with `run_in_windows` as explicit fallback. See rule #6 (superseded).
