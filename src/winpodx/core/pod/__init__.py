"""Pod lifecycle management.

This package was split out of the former ``winpodx.core.pod`` module. The
public surface is preserved: existing ``from winpodx.core.pod import X``
imports continue to work via the re-exports below.

Submodules:
- ``backend``: PodState / PodStatus, the backend factory, and pod_status.
- ``health``: RDP-port liveness probes and recover_rdp_if_needed.
- ``lifecycle``: start_pod / stop_pod.
- ``compose``: (pending) compose template generation.
"""

from __future__ import annotations

from winpodx.core.pod.backend import (
    PodState,
    PodStatus,
    get_backend,
    pod_status,
)
from winpodx.core.pod.health import check_rdp_port, recover_rdp_if_needed
from winpodx.core.pod.lifecycle import start_pod, stop_pod

__all__ = [
    "PodState",
    "PodStatus",
    "check_rdp_port",
    "get_backend",
    "pod_status",
    "recover_rdp_if_needed",
    "start_pod",
    "stop_pod",
]
