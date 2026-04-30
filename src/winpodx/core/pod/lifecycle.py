"""Pod lifecycle: start_pod / stop_pod."""

from __future__ import annotations

import logging

from winpodx.core.config import Config
from winpodx.core.pod.backend import PodState, PodStatus, get_backend

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
