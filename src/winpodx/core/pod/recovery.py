# SPDX-License-Identifier: MIT
"""Auto-recovery for `PodState.UNRESPONSIVE` — restart Windows RDP service.

When `pod_status()` reports ``UNRESPONSIVE`` (container running long enough
that an RDP-port miss can't be confused with a fresh boot), this module
tries to bring the guest back without a full container restart: the agent
transport is asked to cycle ``TermService`` in-guest, then we re-probe the
RDP port for a short window.

Failure modes — by design, *not* exceptions:

* Agent unreachable. We report ``RecoveryAction.AGENT_UNREACHABLE`` so the
  caller (tray / GUI) can surface the "needs manual restart" notification
  instead of silently retrying forever. This is the common case: the same
  guest stall that breaks RDP usually breaks the agent transport too.
* Agent reachable but RDP still down after the recovery window. Reported
  as ``RecoveryAction.RDP_STILL_DOWN``. TermService cycled but the guest
  state didn't clear (NIC asleep, Modern Standby, etc.) — operator
  needs to ``winpodx pod restart``.
* Recovered. ``RecoveryAction.RESTARTED_TERMSERVICE`` and `success=True`.
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass

from winpodx.core.config import Config
from winpodx.core.pod.health import check_rdp_port

log = logging.getLogger(__name__)


class RecoveryAction(enum.Enum):
    RESTARTED_TERMSERVICE = "restarted_termservice"
    AGENT_UNREACHABLE = "agent_unreachable"
    RDP_STILL_DOWN = "rdp_still_down"


@dataclass
class RecoveryResult:
    success: bool
    action: RecoveryAction
    detail: str = ""


# How long to wait for the RDP port to come back after the TermService
# cycle. Matches the post-restart settling window we observed during
# manual recovery: typically 5-15 s on a warm guest, occasionally 25 s
# when the guest is also waking from Modern Standby.
_RDP_RECHECK_WINDOW_SECS = 30
_RDP_RECHECK_INTERVAL_SECS = 2

# Single PowerShell line — TermService has no dependencies that need
# explicit start, and `-Force` skips the "are you sure" prompt for
# dependent services.
_RESTART_TERMSERVICE = "Restart-Service -Force TermService"


def try_recover_rdp(cfg: Config) -> RecoveryResult:
    """Cycle Windows TermService via agent and re-probe RDP."""
    from winpodx.core.transport.agent import AgentTransport
    from winpodx.core.transport.base import (
        TransportError,
        TransportTimeoutError,
        TransportUnavailable,
    )

    transport = AgentTransport(cfg)
    health = transport.health()
    if not health.available:
        return RecoveryResult(
            success=False,
            action=RecoveryAction.AGENT_UNREACHABLE,
            detail=health.detail or "agent /health probe failed",
        )

    try:
        result = transport.exec(_RESTART_TERMSERVICE, timeout=20)
    except (TransportUnavailable, TransportTimeoutError) as e:
        return RecoveryResult(
            success=False,
            action=RecoveryAction.AGENT_UNREACHABLE,
            detail=f"agent /exec failed: {e}",
        )
    except TransportError as e:
        return RecoveryResult(
            success=False,
            action=RecoveryAction.AGENT_UNREACHABLE,
            detail=f"agent error: {e}",
        )

    if result.rc != 0:
        log.warning(
            "TermService restart exited rc=%s stderr=%r",
            result.rc,
            result.stderr.strip(),
        )

    deadline = time.monotonic() + _RDP_RECHECK_WINDOW_SECS
    while time.monotonic() < deadline:
        if check_rdp_port(cfg.rdp.ip, cfg.rdp.port):
            return RecoveryResult(
                success=True,
                action=RecoveryAction.RESTARTED_TERMSERVICE,
            )
        time.sleep(_RDP_RECHECK_INTERVAL_SECS)

    return RecoveryResult(
        success=False,
        action=RecoveryAction.RDP_STILL_DOWN,
        detail=(
            f"RDP port {cfg.rdp.port} still unreachable "
            f"{_RDP_RECHECK_WINDOW_SECS}s after TermService restart"
        ),
    )
