"""Public API for the host->guest Transport layer.

See ``docs/TRANSPORT_ABC.md`` for the full contract. Concrete transports
live in sibling modules (``freerdp``, ``agent``); use ``dispatch()`` to
pick the right one for a given ``Config``.
"""

from __future__ import annotations

from winpodx.core.transport.agent import AgentTransport
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
from winpodx.core.transport.dispatch import PreferKind, dispatch
from winpodx.core.transport.freerdp import FreerdpTransport

__all__ = [
    "SPEC_VERSION",
    "AgentTransport",
    "ExecResult",
    "FreerdpTransport",
    "HealthStatus",
    "PreferKind",
    "Transport",
    "TransportAuthError",
    "TransportError",
    "TransportTimeoutError",
    "TransportUnavailable",
    "dispatch",
]
