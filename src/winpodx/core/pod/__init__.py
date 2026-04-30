"""Pod lifecycle management.

This package was split out of the former ``winpodx.core.pod`` module. The
public surface is preserved: existing ``from winpodx.core.pod import X``
imports continue to work via the re-exports below.

Submodules:
- ``backend``: PodState / PodStatus, the backend factory, and pod_status.
- ``health``: RDP-port liveness probes and recover_rdp_if_needed.
- ``lifecycle``: (pending) start_pod / stop_pod.
- ``compose``: (pending) compose template generation.
"""

from __future__ import annotations

import logging

from winpodx.core.config import Config
from winpodx.core.pod.backend import (
    PodState,
    PodStatus,
    get_backend,
    pod_status,
)
from winpodx.core.pod.health import check_rdp_port, recover_rdp_if_needed

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

log = logging.getLogger(__name__)


def start_pod(cfg: Config) -> PodStatus:
    """Start the Windows pod and wait up to boot_timeout for RDP readiness."""
    backend = get_backend(cfg)
    try:
        backend.start()
    except Exception as e:
        log.error("Failed to start pod: %s", e)
        return PodStatus(state=PodState.ERROR, error=str(e))

    try:
        ready = backend.wait_for_ready(timeout=cfg.pod.boot_timeout)
    except Exception as e:
        log.error("wait_for_ready failed: %s", e)
        return PodStatus(state=PodState.ERROR, error=str(e))

    if ready:
        return PodStatus(state=PodState.RUNNING, ip=cfg.rdp.ip)
    return PodStatus(state=PodState.STARTING, ip=cfg.rdp.ip)


def stop_pod(cfg: Config) -> PodStatus:
    """Stop the Windows pod."""
    backend = get_backend(cfg)
    try:
        backend.stop()
    except Exception as e:
        log.error("Failed to stop pod: %s", e)
        return PodStatus(state=PodState.ERROR, error=str(e))
    return PodStatus(state=PodState.STOPPED)
