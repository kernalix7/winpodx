"""dispatch() — pick the best available Transport for cfg.

Default policy: prefer the agent (fast HTTP) when it answers /health,
fall back to FreeRDP (slow but always available once RDP works).

Per spec rule #1, every Transport.health() returns
``HealthStatus(available=False)`` rather than raising on transient
state, so this function avoids try/except chains for the happy path.
Configuration errors (FreeRDP missing) DO bubble up as
TransportUnavailable so the user sees them rather than getting silent
fallback to a transport that also can't work.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from winpodx.core.config import Config
from winpodx.core.transport.agent import AgentTransport
from winpodx.core.transport.base import (
    SPEC_VERSION,
    Transport,
    TransportUnavailable,
)
from winpodx.core.transport.freerdp import FreerdpTransport

assert SPEC_VERSION == 1, "dispatch() built against Transport spec v1"

log = logging.getLogger(__name__)

PreferKind = Literal["agent", "freerdp"]


def dispatch(cfg: Config, *, prefer: Optional[PreferKind] = None) -> Transport:
    """Pick the best available transport for ``cfg``.

    Default policy:
      1. If AgentTransport.health().available, use it.
      2. Else fall back to FreerdpTransport.

    ``prefer="freerdp"`` forces FreerdpTransport (used for password
    rotation paths that explicitly opt out of the agent — see
    ``docs/TRANSPORT_ABC.md`` rule #6, "NEVER use Transport for
    password rotation"; rotation should call ``run_in_windows``
    directly, but if a code path *must* go through dispatch and avoid
    the agent, this is the escape hatch).

    ``prefer="agent"`` raises ``TransportUnavailable`` if the agent
    isn't up rather than silently falling back — useful for callers
    that need the streaming/SSE features only AgentTransport provides.

    The dispatcher does NOT cache instances; each call returns a fresh
    Transport so state stays on cfg, not on the dispatcher.
    """
    if prefer == "freerdp":
        return FreerdpTransport(cfg)

    if prefer == "agent":
        agent = AgentTransport(cfg)
        status = agent.health()
        if not status.available:
            raise TransportUnavailable(
                f"agent transport explicitly requested but unavailable: {status.detail}"
            )
        return agent

    if prefer is not None:
        raise ValueError(f"unknown prefer kind: {prefer!r}")

    # Default policy: try agent first.
    agent = AgentTransport(cfg)
    try:
        status = agent.health()
    except Exception as e:  # noqa: BLE001 — health() shouldn't raise here, but if it does we degrade
        log.debug("agent health probe raised, falling back to FreeRDP: %s", e)
        return FreerdpTransport(cfg)
    if status.available:
        return agent

    log.debug("agent unavailable (%s), falling back to FreeRDP", status.detail)
    return FreerdpTransport(cfg)
