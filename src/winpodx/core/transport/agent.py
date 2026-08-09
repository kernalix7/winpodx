# SPDX-License-Identifier: MIT
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

import logging
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

log = logging.getLogger(__name__)


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
            # /health is intentionally unauthenticated, but this Transport is
            # only usable for authenticated /exec calls. If the host token is
            # missing, report the agent transport as unavailable so default
            # dispatch can fall back to FreeRDP instead of selecting agent and
            # failing later on the first /exec.
            auth_ready, auth_detail = self._client.auth_ready()
            if not auth_ready:
                return HealthStatus(available=False, detail=auth_detail)
        except (AgentTimeoutError, AgentUnavailableError) as e:
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
        from winpodx.core.agent_resync import heal_generation, heal_token_once

        generation = heal_generation()
        try:
            return self._exec_once(script, timeout)
        except TransportAuthError:
            # A 401 here is token drift, not a transient blip: a non-purge
            # reinstall regenerates the host token while the guest disk (and
            # its baked copy) survives. /health is unauthenticated so it keeps
            # returning OK, which is why this used to look like a dead feature
            # rather than an auth problem — apply-fixes, discovery,
            # reverse-open sync and the keepalive all stayed broken until
            # someone happened to run `winpodx doctor`, the one caller that
            # healed (#730). Heal here instead, once, then retry.
            ok, detail = heal_token_once(self.cfg, seen_generation=generation)
            if not ok:
                log.warning("agent auth failed and token heal did not run: %s", detail)
                raise
            # resync_token may have minted a fresh host token, so drop the
            # client holding the old cached bearer.
            self._client = AgentClient(self.cfg)

        # Outside the handler on purpose: a second 401 propagates as-is
        # instead of re-entering the heal path.
        return self._exec_once(script, timeout)

    def _exec_once(self, script: str, timeout: int) -> ExecResult:
        """One /exec round trip with the Agent -> Transport exception mapping."""
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
