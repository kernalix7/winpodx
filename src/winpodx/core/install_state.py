# SPDX-License-Identifier: MIT
"""Host-side mirror of the guest install state machine.

See ``docs/design/AGENT_FIRST_INSTALL_DESIGN.md`` §"Guest install state
(host-side mirror)" for the full design.

The guest writes ``.done`` markers under ``C:\\winpodx\\install-state\\``
as each install step completes. This module reads those markers via the
guest agent's ``/exec`` endpoint and maps them onto a structured
``GuestInstallState`` dataclass that the CLI / GUI can render.

Behaviour summary (per design doc):

* Successful agent fetch -> parse markers, write the parsed state to
  ``$XDG_STATE_HOME/winpodx/last_install_state.json`` (mode 0600,
  atomic temp+rename) so a later agent-down call can still show
  something useful.
* Agent unreachable -> read the cached state file, set
  ``agent_reachable=False`` and ``marker_state_cached=True``.
* Cache miss + agent unreachable -> return a degraded state with
  ``overall_status="unknown"``. Never raises on bad input.

Phase 3 of the agent-first install rollout will wire
``fetch_install_state()`` into ``winpodx pod install-status`` and the
GUI overlay; Phase 1 (this module) only ships the model and parser.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from winpodx.core.config import Config

log = logging.getLogger(__name__)

OverallStatus = Literal["running", "done", "failed", "unknown"]
StepStatus = Literal["pending", "running", "done", "failed"]

# Canonical step order. Matches the markers documented in
# AGENT_FIRST_INSTALL_DESIGN.md "State directory layout" and the
# install-status UI table. Tuple of (phase, marker_name, display_name).
PHASE_ORDER: tuple[tuple[float, str, str], ...] = (
    (0, "defender_exclusion", "defender exclusion"),
    (0.5, "state_dir_ready", "state dir ready"),
    (0.6, "token_staged", "token staged"),
    (1, "agent_ready", "agent ready"),
    (2, "rdprrap_installed", "rdprrap install"),
    (2, "vbs_launchers", "vbs launchers"),
    (2, "oem_runtime_fixes", "oem runtime fixes"),
    (2, "max_sessions", "max sessions"),
    (2, "multi_session_active", "multi-session activate"),
    (3, "install_complete", "install complete"),
)

# Reading C:\winpodx\install-state\ via /exec. ``Get-ChildItem`` on a
# missing dir errors out; ``Test-Path`` first to keep the JSON output
# clean (empty array for "no markers yet").
_LIST_MARKERS_PS1 = (
    r"$dir = 'C:\winpodx\install-state'; "
    r"if (Test-Path $dir) { "
    r"  Get-ChildItem -Path (Join-Path $dir '*.done') -ErrorAction SilentlyContinue | "
    r"  ForEach-Object { $_.Name } | ConvertTo-Json -Compress -Depth 2 "
    r"} else { '[]' }"
)

# install_failure.json is optional; missing file is not an error.
_READ_FAILURE_PS1 = (
    r"$p = 'C:\winpodx\install-state\install_failure.json'; "
    r"if (Test-Path $p) { Get-Content -Raw -Path $p } else { '' }"
)


@dataclass
class GuestInstallStep:
    """One step in the guest install state machine."""

    phase: float
    name: str
    status: StepStatus
    elapsed_seconds: float
    attempt: int = 1


@dataclass
class GuestInstallState:
    """Snapshot of the guest install state machine, agent or cache sourced."""

    session_id: str | None
    overall_status: OverallStatus
    elapsed_seconds: float
    agent_reachable: bool
    marker_state_cached: bool
    steps: list[GuestInstallStep] = field(default_factory=list)
    failure: dict | None = None


def _state_cache_path() -> Path:
    """Return the path to the host-side cached install state file.

    Uses ``$XDG_STATE_HOME`` per the XDG Base Directory spec, falling
    back to ``~/.local/state`` exactly as the spec defines. ``paths.py``
    doesn't yet expose a state_dir() helper; introducing one for one
    caller would be premature, so we resolve inline here.
    """
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "winpodx" / "last_install_state.json"


def _parse_markers_json(raw: str) -> list[str]:
    """Parse the ``ConvertTo-Json -Compress`` output of the marker list.

    PowerShell quirks the parser has to absorb:

    * Empty dir / no markers -> ``"[]"`` (we return that explicitly in
      the PS1) or sometimes ``""`` if the pipeline produces no objects.
    * Single match -> a JSON string, not an array.
    * Multiple matches -> a JSON array of strings.

    Returns a sorted, de-duplicated list of marker filenames. Never
    raises; bad input becomes ``[]`` and the caller falls back through.
    """
    raw = raw.strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        log.debug("install_state: marker list JSON parse failed: %r", raw[:200])
        return []
    if isinstance(parsed, str):
        return [parsed]
    if isinstance(parsed, list):
        return sorted({str(x) for x in parsed if isinstance(x, str)})
    return []


def _markers_to_steps(markers: list[str]) -> tuple[list[GuestInstallStep], OverallStatus]:
    """Map a list of ``.done`` marker filenames onto the canonical step list.

    The "running" step is the first non-``.done`` step in PHASE_ORDER.
    Steps after the running one are "pending". Failure detection is
    layered on by the caller via the install_failure.json payload.
    """
    done_names = {m.removesuffix(".done") for m in markers if m.endswith(".done")}
    steps: list[GuestInstallStep] = []
    saw_running = False
    overall: OverallStatus = "running"

    for phase, name, _label in PHASE_ORDER:
        if name in done_names:
            steps.append(
                GuestInstallStep(phase=phase, name=name, status="done", elapsed_seconds=0.0)
            )
        elif not saw_running:
            steps.append(
                GuestInstallStep(phase=phase, name=name, status="running", elapsed_seconds=0.0)
            )
            saw_running = True
        else:
            steps.append(
                GuestInstallStep(phase=phase, name=name, status="pending", elapsed_seconds=0.0)
            )

    if not saw_running:
        # All markers present -> install complete.
        overall = "done"

    return steps, overall


def _read_cache() -> GuestInstallState | None:
    """Return the cached state from disk, or None if missing / corrupt."""
    path = _state_cache_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        log.debug("install_state: cache read failed: %s", e)
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as e:
        log.debug("install_state: cache JSON parse failed: %s", e)
        return None
    if not isinstance(payload, dict):
        return None
    try:
        steps_raw = payload.get("steps") or []
        steps = [
            GuestInstallStep(
                phase=float(s["phase"]),
                name=str(s["name"]),
                status=s["status"],
                elapsed_seconds=float(s.get("elapsed_seconds", 0.0)),
                attempt=int(s.get("attempt", 1)),
            )
            for s in steps_raw
            if isinstance(s, dict) and "phase" in s and "name" in s and "status" in s
        ]
        return GuestInstallState(
            session_id=payload.get("session_id"),
            overall_status=payload.get("overall_status", "unknown"),
            elapsed_seconds=float(payload.get("elapsed_seconds", 0.0)),
            agent_reachable=bool(payload.get("agent_reachable", False)),
            marker_state_cached=True,
            steps=steps,
            failure=payload.get("failure") if isinstance(payload.get("failure"), dict) else None,
        )
    except (KeyError, TypeError, ValueError) as e:
        log.debug("install_state: cache shape invalid: %s", e)
        return None


def _write_cache(state: GuestInstallState) -> None:
    """Persist ``state`` to the cache file (atomic temp+rename, mode 0600).

    Failures are logged at debug only — caching is best-effort, and a
    write failure should never break the live status fetch.
    """
    path = _state_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.debug("install_state: cache dir mkdir failed: %s", e)
        return

    payload = asdict(state)
    # Cache reflects fresh agent contact -> mirror that on read.
    payload["marker_state_cached"] = False

    try:
        # Same dir as target so rename is atomic on the same filesystem.
        fd, tmp = tempfile.mkstemp(
            prefix=".last_install_state.", suffix=".json", dir=str(path.parent)
        )
        try:
            os.write(fd, json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as e:
        log.debug("install_state: cache write failed: %s", e)
        # Best-effort cleanup of the temp file if rename never happened.
        try:
            if "tmp" in locals() and os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _fetch_via_agent(cfg: Config) -> tuple[list[str], dict | None]:
    """Return (markers, failure_dict) by calling the guest agent.

    Raises any of the agent error types on transport failure — caller
    catches those and falls through to the cache. Script-level non-zero
    rc is treated as "no markers" rather than an error so a partial
    state-dir (e.g. dir doesn't exist yet) parses cleanly.
    """
    # Imported lazily so this module stays importable in environments
    # where the agent client isn't reachable (and to keep the import
    # graph one-way: install_state -> agent, never the reverse).
    from winpodx.core.agent import AgentClient

    client = AgentClient(cfg)

    markers_result = client.exec(_LIST_MARKERS_PS1, timeout=10)
    markers = _parse_markers_json(markers_result.stdout) if markers_result.ok else []

    failure: dict | None = None
    if any(m.endswith(".done") for m in markers) or markers_result.ok:
        # Only attempt the failure read if the agent + state dir are
        # responsive. Saves a round trip on the cold-boot path.
        failure_result = client.exec(_READ_FAILURE_PS1, timeout=10)
        if failure_result.ok and failure_result.stdout.strip():
            try:
                parsed = json.loads(failure_result.stdout)
                if isinstance(parsed, dict):
                    failure = parsed
            except (ValueError, TypeError):
                # Malformed install_failure.json shouldn't poison the
                # whole status fetch; just drop the field.
                log.debug("install_state: install_failure.json parse failed")

    return markers, failure


def fetch_install_state(cfg: Config) -> GuestInstallState:
    """Fetch the current guest install state.

    Tries the guest agent first; on any agent failure (unreachable,
    auth, timeout) falls back to the host-side cache file. Returns a
    ``GuestInstallState`` with ``overall_status="unknown"`` if both
    sources fail. NEVER raises on bad input — the CLI / GUI can render
    the degraded state without try/except wrapping every call.

    Side effect: a successful agent fetch writes the freshly parsed
    state to ``$XDG_STATE_HOME/winpodx/last_install_state.json`` (mode
    0600) so the next agent-down call still has something to render.
    """
    # Import the agent error types here — keeps the top-level import
    # graph free of any agent-module circulars and avoids paying the
    # urllib import cost when callers only want the cached path.
    from winpodx.core.agent import AgentError

    try:
        markers, failure = _fetch_via_agent(cfg)
    except AgentError as e:
        log.debug("install_state: agent unreachable, falling back to cache: %s", e)
        cached = _read_cache()
        if cached is not None:
            cached.agent_reachable = False
            cached.marker_state_cached = True
            return cached
        return GuestInstallState(
            session_id=None,
            overall_status="unknown",
            elapsed_seconds=0.0,
            agent_reachable=False,
            marker_state_cached=False,
            steps=[],
            failure=None,
        )
    except Exception as e:  # noqa: BLE001 — last-resort guard; we promise to never raise
        log.warning("install_state: unexpected error during agent fetch: %s", e)
        cached = _read_cache()
        if cached is not None:
            cached.agent_reachable = False
            cached.marker_state_cached = True
            return cached
        return GuestInstallState(
            session_id=None,
            overall_status="unknown",
            elapsed_seconds=0.0,
            agent_reachable=False,
            marker_state_cached=False,
            steps=[],
            failure=None,
        )

    steps, overall = _markers_to_steps(markers)
    if failure is not None:
        overall = "failed"
        failed_step = failure.get("failed_step") if isinstance(failure, dict) else None
        if failed_step:
            for step in steps:
                if step.name == failed_step:
                    step.status = "failed"
                    if isinstance(failure.get("attempt"), int):
                        step.attempt = failure["attempt"]
                    break

    state = GuestInstallState(
        session_id=None,
        overall_status=overall,
        elapsed_seconds=0.0,
        agent_reachable=True,
        marker_state_cached=False,
        steps=steps,
        failure=failure,
    )
    _write_cache(state)
    return state
