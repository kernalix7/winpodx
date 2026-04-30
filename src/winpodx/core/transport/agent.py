"""AgentTransport — wraps ``AgentClient`` for the host->guest command channel.

Implements the Transport ABC v1 by delegating to the existing
``AgentClient`` (host-side HTTP client for the in-guest ``agent.ps1``).
``AgentClient.health()`` and ``AgentClient.exec()`` already have the same
shape we need; this module just performs the exception mapping
(``Agent*Error`` → ``Transport*Error``) and shapes the results into the
Transport ABC's frozen dataclasses.

Streaming endpoints (``/apply/{step}``, ``/discover``) aren't on the
agent yet (Phase 4 of the agent-v2 spec); ``stream()`` raises
``TransportUnavailable`` until that lands.
"""

from __future__ import annotations

from collections.abc import Callable

from winpodx.core.agent import (
    AgentAuthError,
    AgentClient,
    AgentError,
    AgentTimeoutError,
    AgentUnavailableError,
)
from winpodx.core.config import Config
from winpodx.core.transport.base import (
    SPEC_VERSION,
    ExecResult,
    HealthStatus,
    Transport,
    TransportAuthError,
    TransportError,
    TransportTimeoutError,
    TransportUnavailable,
)

assert SPEC_VERSION == 1, "AgentTransport built against Transport spec v1"


class AgentTransport(Transport):
    """HTTP transport over the in-guest agent.ps1 listener.

    Health: ``GET /health`` (no auth, ~2s budget per HEALTH_TIMEOUT).
    Exec: ``POST /exec`` with bearer auth and base64-encoded payload.
    Stream: not yet implemented — raises ``TransportUnavailable``.
    """

    name = "agent"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._client = AgentClient(cfg)

    def health(self) -> HealthStatus:
        """Probe the agent's /health endpoint.

        Per Transport ABC rule: must NOT raise on transient state.
        Connection-refused, timeout, malformed JSON all become
        ``available=False`` with a brief detail string.
        """
        try:
            payload = self._client.health()
        except AgentUnavailableError as e:
            return HealthStatus(available=False, detail=str(e))
        except Exception as e:  # noqa: BLE001 — rule: never raise on transient
            return HealthStatus(available=False, detail=f"health probe failed: {e}")

        version = None
        if isinstance(payload, dict):
            v = payload.get("version")
            if isinstance(v, str):
                version = v
        return HealthStatus(available=True, version=version)

    def exec(
        self,
        script: str,
        *,
        timeout: int = 60,
        description: str = "winpodx-exec",
    ) -> ExecResult:
        """POST /exec — bearer-authed PowerShell execution over HTTP.

        ``description`` is unused by the agent (no per-call task name on
        the wire), but kept in the signature for Transport ABC parity
        with FreerdpTransport.
        """
        del description  # unused — agent /exec has no task-name field
        try:
            agent_result = self._client.exec(script, timeout=float(timeout))
        except AgentAuthError as e:
            raise TransportAuthError(str(e)) from e
        except AgentTimeoutError as e:
            raise TransportTimeoutError(str(e)) from e
        except AgentUnavailableError as e:
            raise TransportUnavailable(str(e)) from e
        except AgentError as e:
            raise TransportError(str(e)) from e

        return ExecResult(
            rc=agent_result.rc,
            stdout=agent_result.stdout,
            stderr=agent_result.stderr,
        )

    def stream(
        self,
        script: str,
        on_progress: Callable[[str], None],
        *,
        timeout: int = 600,
        description: str = "winpodx-stream",
    ) -> ExecResult:
        """Stream PowerShell output via SSE.

        Phase 4 of agent-v2: not yet implemented. Raises
        ``TransportUnavailable`` so callers fall back to FreerdpTransport
        for streaming work.
        """
        raise TransportUnavailable(
            "AgentTransport.stream() not implemented — agent SSE endpoints "
            "(/apply/{step}, /discover, /events) are Phase 4 of the agent-v2 "
            "spec. Use FreerdpTransport for streaming until then."
        )
