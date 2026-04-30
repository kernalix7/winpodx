"""Host-side HTTP client for the guest agent (agent-v2).

Complements ``config/oem/agent/agent.ps1`` running inside the Windows VM.
The guest binds an HTTP listener on ``127.0.0.1:8765``; both forwarding
chain legs (QEMU hostfwd via dockur ``USER_PORTS``, plus the compose
``ports:`` mapping) make that listener reachable from the host on the
same loopback port.

Phase 1 implements only ``GET /health`` (no auth) — the readiness
signal that gates everything downstream. ``health()`` responding is the
single, definitive proof that ``install.bat`` finished, ``rdprrap``
activated, and the agent could bind its listener.

Later phases will add ``/exec``, ``/events``, ``/apply``, ``/discover``.
The Phase 2+ surface area is sketched as helpers/exception types in
this module so callers don't churn between phases.

See ``docs/AGENT_V2_DESIGN.md`` for the full design.
"""

from __future__ import annotations

import base64
import json
import logging
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from winpodx.core.config import Config
from winpodx.utils.agent_token import token_path

log = logging.getLogger(__name__)


class AgentError(RuntimeError):
    """Base class for all AgentClient failures."""


class AgentUnavailableError(AgentError):
    """Agent is unreachable: connection refused, timeout, 5xx, no token, etc.

    The host should treat this as "still booting" or "guest agent not yet
    up" and avoid firing speculative FreeRDP probes (anti-goal #3).
    """


class AgentAuthError(AgentError):
    """Agent rejected the request with 401/403 — token mismatch or missing.

    Phase 1's ``/health`` is unauthenticated, so this is reserved for
    Phase 2+ endpoints. Defined now so callers don't need to change
    their except-clauses between phases.
    """


class AgentTimeoutError(AgentError):
    """Server accepted the request but didn't finish before the deadline.

    Distinct from ``AgentUnavailableError``'s connect-timeout case: the
    listener was up and replied with headers but the work itself
    exceeded the per-request budget.
    """


@dataclass(frozen=True)
class ExecResult:
    """Outcome of a guest-side script execution via ``/exec``.

    Fields mirror the agent's JSON response (``rc``/``stdout``/``stderr``).
    Transport-level failures (channel down, auth, timeout) raise the
    matching ``Agent*Error`` instead of returning here.
    """

    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


class AgentClient:
    """HTTP client for the guest agent on ``127.0.0.1:8765``."""

    DEFAULT_BASE_URL = "http://127.0.0.1:8765"
    HEALTH_TIMEOUT = 2.0

    def __init__(
        self,
        cfg: Config,
        *,
        base_url: str | None = None,
        token: str | None = None,
        default_timeout: float = 30.0,
    ) -> None:
        self.cfg = cfg
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.default_timeout = default_timeout
        self._cached_token = token

    def _token(self) -> str:
        """Return the bearer token, lazily loaded from the host token file.

        Raises ``AgentUnavailableError`` if the token file is missing or
        empty — without it the client cannot authenticate Phase 2+
        requests, so the agent is functionally unavailable.

        ``health()`` does not call this — Phase 1 ``/health`` is
        unauthenticated. Plumbed now for Phase 2.
        """
        if self._cached_token:
            return self._cached_token
        path = token_path()
        try:
            content = path.read_text(encoding="ascii").strip()
        except FileNotFoundError as e:
            raise AgentUnavailableError(f"agent token file missing: {path}") from e
        except OSError as e:
            raise AgentUnavailableError(f"cannot read agent token: {e}") from e
        if not content:
            raise AgentUnavailableError(f"agent token file is empty: {path}")
        self._cached_token = content
        return content

    def _build_request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        with_auth: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> urllib_request.Request:
        """Build a urllib Request for ``path`` against the configured base URL."""
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if with_auth:
            headers["Authorization"] = f"Bearer {self._token()}"
        if extra_headers:
            headers.update(extra_headers)
        return urllib_request.Request(url, data=body, headers=headers, method=method)

    def health(self) -> dict[str, Any]:
        """GET /health — the readiness signal. No auth, 2s timeout.

        Returns the parsed JSON status payload on 200. Raises
        ``AgentUnavailableError`` on connection-refused, timeout, 5xx,
        or non-JSON body. Callers should treat any exception as "agent
        not ready, do not fire FreeRDP probes" (anti-goal #3).
        """
        req = self._build_request("/health", method="GET", with_auth=False)
        try:
            with urllib_request.urlopen(req, timeout=self.HEALTH_TIMEOUT) as resp:
                status = resp.status
                raw = resp.read()
        except urllib_error.HTTPError as e:
            # 4xx (other than auth) and 5xx come back here.
            if e.code in (401, 403):
                raise AgentAuthError(f"/health returned {e.code}") from e
            raise AgentUnavailableError(f"/health returned HTTP {e.code}") from e
        except urllib_error.URLError as e:
            raise AgentUnavailableError(f"/health unreachable: {e.reason}") from e
        except TimeoutError as e:
            raise AgentUnavailableError(f"/health timed out after {self.HEALTH_TIMEOUT}s") from e
        except OSError as e:
            raise AgentUnavailableError(f"/health socket error: {e}") from e

        if status >= 500:
            raise AgentUnavailableError(f"/health returned HTTP {status}")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise AgentUnavailableError(f"/health returned non-JSON body: {e}") from e

    def exec(self, script: str, *, timeout: int = 60) -> ExecResult:
        """POST /exec — run ``script`` as PowerShell on the guest.

        ``script`` is the raw PowerShell source; this method base64-encodes
        it before sending. ``timeout`` is the per-call client budget in
        seconds; we pass ``timeout-1`` to urlopen so the server-side cap
        has room to fire and respond before the client gives up. A
        client-side socket timeout becomes ``AgentTimeoutError``.

        Returns ``ExecResult`` even when the script's rc is non-zero —
        that's a script-level outcome, not a transport-level error.

        Raises:
            AgentAuthError: 401/403 from the agent (token mismatch).
            AgentTimeoutError: client-side socket timeout while waiting.
            AgentUnavailableError: connect refused / 5xx / network error.
            AgentError: 200 with a body the client can't parse.
        """
        encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
        body = json.dumps({"script": encoded, "timeout_sec": timeout}).encode("utf-8")
        req = self._build_request(
            "/exec",
            method="POST",
            body=body,
            with_auth=True,
            extra_headers={"Content-Type": "application/json"},
        )
        urlopen_timeout = float(max(1, timeout - 1))
        try:
            with urllib_request.urlopen(req, timeout=urlopen_timeout) as resp:
                status = resp.status
                raw = resp.read()
        except urllib_error.HTTPError as e:
            if e.code in (401, 403):
                raise AgentAuthError(f"/exec returned {e.code}") from e
            raise AgentUnavailableError(f"/exec returned HTTP {e.code}") from e
        except socket.timeout as e:
            raise AgentTimeoutError(f"/exec timed out after {urlopen_timeout}s") from e
        except urllib_error.URLError as e:
            # urllib wraps socket.timeout as URLError(reason=socket.timeout) on
            # some Python versions — disambiguate before falling through.
            if isinstance(e.reason, socket.timeout):
                raise AgentTimeoutError(f"/exec timed out after {urlopen_timeout}s") from e
            raise AgentUnavailableError(f"/exec unreachable: {e.reason}") from e
        except TimeoutError as e:
            raise AgentTimeoutError(f"/exec timed out after {urlopen_timeout}s") from e
        except OSError as e:
            raise AgentUnavailableError(f"/exec socket error: {e}") from e

        if status >= 500:
            raise AgentUnavailableError(f"/exec returned HTTP {status}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise AgentError(f"/exec returned non-JSON body: {e}") from e

        try:
            return ExecResult(
                rc=int(payload["rc"]),
                stdout=str(payload.get("stdout", "")),
                stderr=str(payload.get("stderr", "")),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise AgentError(f"/exec response missing required fields: {e}") from e


def run_via_agent_or_freerdp(
    cfg: Config,
    script: str,
    *,
    description: str = "winpodx-exec",
    timeout: int = 60,
) -> Any:
    """Run ``script`` (PowerShell source) in the Windows guest.

    Phase 1: always falls back to the FreeRDP RemoteApp channel via
    ``windows_exec.run_in_windows``. The agent's ``/exec`` endpoint
    arrives in Phase 2; this helper lets callers commit to a stable
    API now and benefit from the faster path automatically once the
    agent route lights up.

    Returns whatever ``run_in_windows`` returns
    (``WindowsExecResult``); callers should treat the result as
    opaque and rely on its ``.ok`` / ``.rc`` / ``.stdout`` /
    ``.stderr`` attributes.
    """
    from winpodx.core.windows_exec import run_in_windows

    return run_in_windows(cfg, script, timeout=timeout, description=description)
