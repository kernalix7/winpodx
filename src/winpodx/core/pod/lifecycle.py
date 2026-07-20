# SPDX-License-Identifier: MIT
"""Pod lifecycle: start_pod / stop_pod."""

from __future__ import annotations

import logging

from winpodx.core.config import Config
from winpodx.core.pod.backend import PodState, PodStatus, get_backend
from winpodx.core.pod.ports import check_host_ports, format_port_conflict_error

log = logging.getLogger(__name__)


def start_pod(cfg: Config) -> PodStatus:
    """Start the Windows pod and wait up to boot_timeout for RDP readiness."""
    backend = get_backend(cfg)

    # #754: probe for host port conflicts (e.g. GNOME Remote Desktop already
    # holding 127.0.0.1:3390 on Ubuntu) before handing off to the backend --
    # otherwise this fails silently inside `podman-compose up` and the user
    # just sees an hour-long boot timeout with no diagnostics. Skip the
    # preflight when the pod is already running/paused: it's the one holding
    # its own ports at that point, not a conflict.
    try:
        already_up = backend.is_running()
    except Exception as e:
        log.error("Failed to probe pod state before start: %s", e)
        return PodStatus(state=PodState.ERROR, error=str(e))

    if not already_up:
        conflicts = check_host_ports(cfg)
        if conflicts:
            msg = format_port_conflict_error(conflicts)
            log.error(msg)
            return PodStatus(state=PodState.ERROR, error=msg)

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
