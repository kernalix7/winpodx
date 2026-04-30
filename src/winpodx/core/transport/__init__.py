"""Public API for the host->guest Transport layer.

See ``docs/TRANSPORT_ABC.md`` for the full contract. Concrete transports
live in sibling modules (``freerdp``, ``agent``); use ``dispatch()`` to
pick the right one for a given ``Config``.

NOTE: Per spec rule #6, password rotation MUST NOT be routed through a
Transport. Routing rotation through this layer would tempt callers to
use AgentTransport, which would expose the new password to the agent
process and to anyone with read access to its memory. ``core/rotation/``
calls ``windows_exec.run_in_windows`` directly. The rule is enforced by
code review, not by API shape.
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
