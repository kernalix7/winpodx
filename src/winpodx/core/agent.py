r"""HTTP client for the winpodx Windows guest agent (agent.ps1).

Why this module exists
======================

The legacy ``windows_exec`` channel (FreeRDP RemoteApp + PowerShell)
costs roughly 5-10 seconds per call and flashes a brief PS window each
invocation. That's tolerable for a one-shot password sync but painful
for the many small reads the GUI Logs / Apps / Maintenance pages do.

v0.2.2 introduces a Windows-side HTTP server (``agent.ps1``) bound to
``127.0.0.1:8765`` *inside* the Windows VM, exposed to the Linux host
via the dockur ``USER_PORTS`` NAT mapping. Non-secret operations
(``exec`` for read-only commands, streaming logs, multi-step apply,
discovery) move to this faster channel. Sensitive ops — specifically
RDP password rotation — *stay* on FreeRDP RemoteApp via
``windows_exec.run_in_windows``: the agent never sees the new
password and a hostile process listening on 8765 can't intercept it.

Auth
----

Bearer token from a shared secret file:

* host:  ``~/.config/winpodx/agent_token.txt``
* guest: ``C:\OEM\agent_token.txt``

Provisioned by ``install.bat`` / setup at first boot (task #31). This
client only *reads* the host copy — never writes / rotates / passes via
argv or env (which would leak through ``ps``/``/proc``).

Channels
--------

* ``health()`` — fast probe used by the fallback helper to decide if
  the agent is up before each ``exec``.
* ``exec(script)`` — base64-wrapped PowerShell, 60s server-side cap.
* ``stream_events(on_line)`` — long-poll SSE feed used by the Logs page.
* ``post_apply(step)`` — multi-step settings apply with progress lines.
* ``post_discover()`` — Start-Menu discovery with progress lines.

All streaming endpoints parse SSE in the calling thread; callers that
want concurrency (the GUI does) wrap the call in a ``threading.Thread``
and pass a ``threading.Event`` to ``stream_events`` for cancellation.
"""

from __future__ import annotations

import base64
import json
import logging
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from winpodx.core.config import Config
from winpodx.core.windows_exec import WindowsExecResult
from winpodx.utils.paths import config_dir

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_TOKEN_FILENAME = "agent_token.txt"
HEALTH_TIMEOUT = 2.0  # fast probe — agent is local, anything slower means down


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AgentError(RuntimeError):
    """Base class for any AgentClient failure."""


class AgentUnavailableError(AgentError):
    """The agent isn't reachable: connection refused, timeout, 5xx, or no
    token file. Callers that want graceful fallback to FreeRDP catch
    this specifically — see ``run_via_agent_or_freerdp``."""


class AgentAuthError(AgentError):
    """Server returned 401 / 403 — token is missing, wrong, or revoked."""


class AgentTimeoutError(AgentError):
    """Server accepted the request but didn't finish in time. Distinct
    from ``AgentUnavailableError`` (transport-level) so callers can
    decide whether to retry vs fall back."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ExecResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AgentClient:
    """Synchronous HTTP client for the Windows guest agent.

    Thread-safe for distinct method calls (urllib opens a fresh
    connection per call); a single streaming call is bound to whatever
    thread invoked it.
    """

    def __init__(
        self,
        cfg: Config,
        *,
        base_url: str | None = None,
        token: str | None = None,
        default_timeout: float = 30.0,
    ) -> None:
        self.cfg = cfg
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.default_timeout = float(default_timeout)
        # Token resolution is lazy on purpose: a caller that only ever
        # invokes ``health()`` shouldn't crash on a missing token file.
        self._token_override = token
        self._token_cached: str | None = None

    # -- token / URL plumbing (split out for unit-test mocking) --

    @staticmethod
    def _token_path() -> Path:
        return config_dir() / DEFAULT_TOKEN_FILENAME

    def _token(self) -> str:
        """Read the bearer token from arg-override or the on-disk file.

        Raises ``AgentUnavailableError`` if neither source yields one —
        we treat 'no token' the same as 'agent down' so callers using
        the fallback helper degrade gracefully instead of crashing.
        """
        if self._token_override is not None:
            return self._token_override
        if self._token_cached is not None:
            return self._token_cached
        path = self._token_path()
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as e:
            raise AgentUnavailableError(
                f"no agent token at {path} — agent likely not provisioned yet"
            ) from e
        except OSError as e:
            raise AgentUnavailableError(f"cannot read agent token: {e}") from e
        if not raw:
            raise AgentUnavailableError(f"agent token at {path} is empty")
        self._token_cached = raw
        return raw

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _build_request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        with_auth: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> urllib.request.Request:
        headers: dict[str, str] = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if with_auth:
            headers["Authorization"] = f"Bearer {self._token()}"
        if extra_headers:
            headers.update(extra_headers)
        return urllib.request.Request(
            self._url(path),
            data=body,
            method=method,
            headers=headers,
        )

    # -- public API --

    def health(self) -> dict[str, Any]:
        """GET /health. No auth. Returns the parsed JSON.

        Raises ``AgentUnavailableError`` for connection failures, DNS
        errors, timeouts, or 5xx. 4xx (other than 401/403) is treated
        as malformed agent and also surfaces as unavailable.
        """
        req = self._build_request("/health", with_auth=False)
        try:
            with urllib.request.urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if 500 <= e.code < 600:
                raise AgentUnavailableError(f"/health returned {e.code}") from e
            raise AgentUnavailableError(f"/health unexpected status {e.code}") from e
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            raise AgentUnavailableError(f"agent unreachable: {e}") from e
        except json.JSONDecodeError as e:
            raise AgentUnavailableError(f"/health returned non-JSON: {e}") from e

    def exec(self, script: str, *, timeout: float | None = None) -> ExecResult:
        """POST /exec. The script is base64-encoded so binary-ish or
        multi-line PowerShell traverses the JSON body cleanly without
        escape-hell.

        Returns an ``ExecResult`` even when rc != 0 — the wrapped
        script's own failure is *not* an agent failure. Only transport
        / auth / timeout / malformed-response errors raise.
        """
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        body = json.dumps({"script": encoded}).encode("utf-8")
        req = self._build_request("/exec", method="POST", body=body)
        eff_timeout = self.default_timeout if timeout is None else float(timeout)
        try:
            with urllib.request.urlopen(req, timeout=eff_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AgentAuthError(f"/exec auth failed ({e.code})") from e
            if 500 <= e.code < 600:
                raise AgentUnavailableError(f"/exec returned {e.code}") from e
            raise AgentError(f"/exec unexpected status {e.code}") from e
        except socket.timeout as e:
            raise AgentTimeoutError(f"/exec timed out after {eff_timeout}s") from e
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            # urllib wraps socket.timeout in URLError on some Python
            # versions — sniff the inner reason so callers still get a
            # ``AgentTimeoutError`` they can distinguish.
            inner = getattr(e, "reason", None)
            if isinstance(inner, socket.timeout):
                raise AgentTimeoutError(f"/exec timed out after {eff_timeout}s") from e
            raise AgentUnavailableError(f"agent unreachable: {e}") from e

        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise AgentError(f"/exec returned non-JSON: {e}") from e
        return ExecResult(
            rc=int(data.get("rc", 0)),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
        )

    def stream_events(
        self,
        on_line: Callable[[dict], None],
        *,
        stop: threading.Event | None = None,
    ) -> None:
        """Connect to GET /events (SSE) and dispatch each parsed
        ``data: {...}`` line to ``on_line``.

        Returns when ``stop`` is set, the connection drops, or
        ``on_line`` raises (the exception propagates so the caller
        thread sees it). ``stop`` is checked between lines — a long
        idle period without server activity may briefly delay shutdown
        while urllib waits on the socket.
        """
        # Long timeout: SSE is meant to be open. Caller controls
        # cancellation via ``stop``.
        req = self._build_request(
            "/events",
            extra_headers={"Accept": "text/event-stream"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.default_timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AgentAuthError(f"/events auth failed ({e.code})") from e
            raise AgentUnavailableError(f"/events returned {e.code}") from e
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            raise AgentUnavailableError(f"agent unreachable: {e}") from e

        try:
            self._consume_sse(
                resp,
                stop=stop,
                on_data=lambda data, _event: self._dispatch_event_line(data, on_line),
            )
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass

    @staticmethod
    def _dispatch_event_line(data: str, on_line: Callable[[dict], None]) -> None:
        if not data:
            return
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            log.debug("stream_events: dropping non-JSON data line: %r", data[:200])
            return
        if isinstance(parsed, dict):
            on_line(parsed)
        else:
            log.debug("stream_events: dropping non-dict payload: %r", data[:200])

    def post_apply(
        self,
        step: str,
        on_progress: Callable[[str], None] | None = None,
    ) -> int:
        """POST /apply/{step}. Streams progress lines via ``on_progress``
        and returns the rc embedded in the trailing ``event: done``
        payload. Steps the server understands: ``max_sessions``,
        ``rdp_timeouts``, ``oem``, ``multi_session``.
        """
        return self._stream_until_done(
            f"/apply/{step}",
            on_progress=on_progress,
        )

    def post_discover(
        self,
        on_progress: Callable[[str], None] | None = None,
    ) -> dict:
        """POST /discover. Returns the full ``done`` payload, e.g.
        ``{"rc": 0, "json_file": "C:\\OEM\\discover.json"}``.
        """
        return self._stream_until_done(
            "/discover",
            on_progress=on_progress,
            return_payload=True,
        )

    # -- shared SSE plumbing --

    def _stream_until_done(
        self,
        path: str,
        *,
        on_progress: Callable[[str], None] | None,
        return_payload: bool = False,
    ) -> Any:
        """Open ``path`` as POST + SSE, dispatch ``data:`` lines (other
        than the terminal ``event: done``) to ``on_progress``, and
        return either the final rc (``return_payload=False``) or the
        full done-payload dict.
        """
        req = self._build_request(
            path,
            method="POST",
            body=b"",
            extra_headers={"Accept": "text/event-stream"},
        )
        try:
            resp = urllib.request.urlopen(req, timeout=self.default_timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise AgentAuthError(f"{path} auth failed ({e.code})") from e
            raise AgentUnavailableError(f"{path} returned {e.code}") from e
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            raise AgentUnavailableError(f"agent unreachable: {e}") from e

        captured: dict[str, Any] = {}
        sentinel: list[dict[str, Any]] = []

        def _handle(data: str, event: str) -> None:
            if event == "done":
                try:
                    sentinel.append(json.loads(data) if data else {})
                except json.JSONDecodeError:
                    sentinel.append({"rc": 0})
                return
            if on_progress is not None and data:
                try:
                    on_progress(data)
                except Exception:  # noqa: BLE001
                    log.debug("on_progress raised", exc_info=True)

        try:
            self._consume_sse(resp, stop=None, on_data=_handle)
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

        if not sentinel:
            raise AgentError(f"{path} stream ended without a 'done' event")
        captured = sentinel[-1]
        if return_payload:
            return captured
        return int(captured.get("rc", 0))

    @staticmethod
    def _consume_sse(
        resp: Any,
        *,
        stop: threading.Event | None,
        on_data: Callable[[str, str], None],
    ) -> None:
        """Minimal SSE parser.

        Reads line-by-line, accumulating ``event:`` and ``data:`` fields
        until a blank line, then dispatches ``(joined_data, event_name)``
        to ``on_data``. Multiple ``data:`` lines per event are joined
        with ``\\n`` per the SSE spec.

        Stops when:
          * the response stream EOFs
          * ``stop`` (if provided) is set between events
          * ``on_data`` raises (propagated)
        """
        event_name = ""
        data_parts: list[str] = []
        for raw in resp:
            if stop is not None and stop.is_set():
                break
            # urllib gives us bytes; SSE is utf-8 by spec.
            try:
                line = raw.decode("utf-8", errors="replace")
            except AttributeError:
                line = raw  # already a str (test stubs)
            line = line.rstrip("\r\n")
            if line == "":
                # Blank line = dispatch boundary.
                if data_parts or event_name:
                    on_data("\n".join(data_parts), event_name)
                event_name = ""
                data_parts = []
                continue
            if line.startswith(":"):
                # SSE comment / heartbeat — ignore.
                continue
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_parts.append(line[len("data:") :].lstrip())
            # Other fields (id:, retry:) intentionally ignored — agent
            # doesn't use them.
        # Flush any trailing event without a closing blank line.
        if data_parts or event_name:
            on_data("\n".join(data_parts), event_name)


# ---------------------------------------------------------------------------
# Module-level fallback helper
# ---------------------------------------------------------------------------


def run_via_agent_or_freerdp(
    cfg: Config,
    script: str,
    *,
    description: str = "winpodx-exec",
    timeout: int = 60,
) -> WindowsExecResult:
    """Run ``script`` via the fast HTTP agent if available, else fall
    back to the slow-but-always-there FreeRDP RemoteApp channel.

    The return type is normalised to ``WindowsExecResult`` so callers
    don't branch on which channel actually ran. Sensitive operations
    (password rotation in particular) MUST NOT use this helper — they
    have to go through ``windows_exec.run_in_windows`` directly so the
    secret never crosses the agent boundary.

    Fallback triggers:
      * no token file (agent not provisioned yet)
      * connection refused / timeout / 5xx on /health or /exec
    Auth errors and timeouts on /exec do *not* fall back: they indicate
    the agent is up and broken in a way FreeRDP wouldn't fix, and
    silently retrying through a slower channel just hides the problem.
    """
    # Local import to avoid a cycle: windows_exec already imports from
    # core.config / core.rdp; pulling it in at module load would only
    # be a problem if a future refactor makes it import agent.py too,
    # but the lazy import is cheap insurance.
    from winpodx.core.windows_exec import run_in_windows

    client = AgentClient(cfg)
    try:
        # Probe first so a totally-down agent doesn't waste 30s on the
        # /exec timeout. ``health()`` itself has a 2s budget.
        client.health()
        result = client.exec(script, timeout=float(timeout))
    except AgentUnavailableError as e:
        log.info("agent unavailable, falling back to FreeRDP: %s", e)
        return run_in_windows(cfg, script, timeout=timeout, description=description)
    return WindowsExecResult(rc=result.rc, stdout=result.stdout, stderr=result.stderr)


# Map _self_heal_apply / apply_windows_runtime_fixes step names to the
# agent's /apply/{step} endpoint slugs. The host names (max_sessions,
# rdp_timeouts, oem_runtime_fixes, multi_session) come from
# provisioner.py; the agent uses shorter labels.
_APPLY_STEP_TO_AGENT = {
    "max_sessions": "max_sessions",
    "rdp_timeouts": "rdp_timeouts",
    "oem_runtime_fixes": "oem",
    "multi_session": "multi_session",
}


def run_apply_via_agent_or_freerdp(
    cfg: Config,
    step: str,
    freerdp_fallback: Callable[[Config], None],
    *,
    on_progress: Callable[[str], None] | None = None,
) -> None:
    """Run an apply step via the HTTP guest agent, falling back to the
    existing FreeRDP RemoteApp PowerShell payload (``freerdp_fallback``)
    when the agent isn't reachable.

    ``step`` is the host-side name (``max_sessions``, ``rdp_timeouts``,
    ``oem_runtime_fixes``, ``multi_session``). The agent slug mapping
    happens internally so callers don't track two naming conventions.

    The agent path streams progress via ``on_progress`` and returns
    when ``event: done`` arrives. A non-zero rc raises ``AgentError``
    so the caller's existing exception flow (used by
    ``apply_windows_runtime_fixes`` to populate the per-helper result
    map) keeps working unchanged.
    """
    agent_slug = _APPLY_STEP_TO_AGENT.get(step)
    if agent_slug is None:
        # Unknown step name — refuse silently and use the FreeRDP path.
        log.warning("apply step %r unknown to agent map; using FreeRDP", step)
        freerdp_fallback(cfg)
        return

    client = AgentClient(cfg)
    try:
        client.health()
    except AgentUnavailableError as e:
        log.info("agent unavailable, applying %s via FreeRDP: %s", step, e)
        freerdp_fallback(cfg)
        return

    log.info("applying %s via guest agent", step)
    try:
        rc = client.post_apply(agent_slug, on_progress=on_progress)
    except AgentUnavailableError as e:
        log.info("agent dropped during /apply/%s, falling back: %s", agent_slug, e)
        freerdp_fallback(cfg)
        return
    if rc != 0:
        raise AgentError(f"agent /apply/{agent_slug} returned rc={rc}")


# NOTE: discovery is intentionally NOT migrated to the agent in v0.2.2.
# The agent's /discover endpoint writes its JSON output to C:\OEM\agent-
# runs\<timestamp>.json *inside* the Windows VM. Reading that file back
# from the host requires either a C:\ -> host-volume path translation
# (dockur volume layout) or a follow-up GET endpoint that returns the
# file content. Both are deferred to v0.2.3. discover_apps continues
# to use the existing FreeRDP RemoteApp channel via windows_exec.
