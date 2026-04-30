"""AgentTransport — STUB for Sprint 2 of feat/redesign.

This is a placeholder so the dispatcher and future callers can import
``AgentTransport`` immediately. Every method raises
``TransportUnavailable`` so dispatch() falls through to FreerdpTransport
in the meantime.

The real implementation lands in Sprint 3 once the agent-v2 Phase 2
``/exec`` endpoint stabilises and ``AgentClient.exec()`` is exercised
by callers. The wrapping pattern will mirror FreerdpTransport: delegate
to ``AgentClient``, map ``Agent*Error`` to the matching
``Transport*Error`` subclass, and use ``AgentClient.health()`` for the
health probe.
"""

from __future__ import annotations

from collections.abc import Callable

from winpodx.core.config import Config
from winpodx.core.transport.base import (
    SPEC_VERSION,
    ExecResult,
    HealthStatus,
    Transport,
    TransportUnavailable,
)

assert SPEC_VERSION == 1, "AgentTransport built against Transport spec v1"

_NOT_IMPLEMENTED_DETAIL = "AgentTransport not implemented in Sprint 2 (stub)"


class AgentTransport(Transport):
    """Stub. Real implementation arrives in Sprint 3."""

    name = "agent"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def health(self) -> HealthStatus:
        # Spec rule: health() must not raise on transient state. The
        # stub is "unavailable" by design, not a transient failure, so
        # returning available=False is the right shape for dispatch()
        # to fall through cleanly.
        return HealthStatus(available=False, detail=_NOT_IMPLEMENTED_DETAIL)

    def exec(
        self,
        script: str,
        *,
        timeout: int = 60,
        description: str = "winpodx-exec",
    ) -> ExecResult:
        raise TransportUnavailable(_NOT_IMPLEMENTED_DETAIL)

    def stream(
        self,
        script: str,
        on_progress: Callable[[str], None],
        *,
        timeout: int = 600,
        description: str = "winpodx-stream",
    ) -> ExecResult:
        raise TransportUnavailable(_NOT_IMPLEMENTED_DETAIL)
