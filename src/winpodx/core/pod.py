"""Pod lifecycle management.

A "pod" is the running Windows environment (Docker container, Podman container,
or libvirt VM) that hosts the Windows applications.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from enum import Enum

from winpodx.backend.base import Backend
from winpodx.core.config import Config

log = logging.getLogger(__name__)


class PodState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class PodStatus:
    state: PodState
    ip: str = ""
    uptime: str = ""
    cpu_usage: str = ""
    memory_usage: str = ""
    error: str = ""


def get_backend(cfg: Config) -> Backend:
    """Instantiate the appropriate backend based on config."""
    name = cfg.pod.backend
    if name == "docker":
        from winpodx.backend.docker import DockerBackend

        return DockerBackend(cfg)
    elif name == "podman":
        from winpodx.backend.podman import PodmanBackend

        return PodmanBackend(cfg)
    elif name == "libvirt":
        from winpodx.backend.libvirt import LibvirtBackend

        return LibvirtBackend(cfg)
    elif name == "manual":
        from winpodx.backend.manual import ManualBackend

        return ManualBackend(cfg)
    else:
        raise ValueError(f"Unknown backend: {name}")


def check_rdp_port(ip: str, port: int, timeout: float = 5.0) -> bool:
    """Check if RDP port is open and accepting connections.

    ``port`` is required — the project default is 3390 (not the Microsoft
    standard 3389), so silently defaulting here would mask mis-wired callers.
    Pass ``cfg.rdp.port`` from the caller.
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def start_pod(cfg: Config) -> PodStatus:
    """Start the Windows pod.

    Blocks up to ``cfg.pod.boot_timeout`` seconds waiting for the backend
    to report RDP readiness. Returns ``RUNNING`` if ready, ``STARTING``
    if the timeout elapsed while the pod is still booting.
    """
    backend = get_backend(cfg)
    try:
        backend.start()
    except Exception as e:
        log.error("Failed to start pod: %s", e)
        return PodStatus(state=PodState.ERROR, error=str(e))

    # Wait for the backend to become RDP-ready. Windows boot commonly
    # takes 60-120s; shorter one-shot checks here race the app launcher.
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


def pod_status(cfg: Config) -> PodStatus:
    """Query the current pod status.

    Distinguishes PAUSED from STOPPED/RUNNING so the auto-suspend path
    (daemon.run_idle_monitor → podman pause) actually surfaces to the
    user in CLI, tray, and GUI. Paused containers answer True from
    ``is_running`` (they are alive) so we query ``is_paused`` first.
    """
    backend = get_backend(cfg)
    try:
        running = backend.is_running()
    except Exception as e:
        log.error("Failed to query pod status: %s", e)
        return PodStatus(state=PodState.ERROR, error=str(e))

    if not running:
        return PodStatus(state=PodState.STOPPED)

    try:
        if backend.is_paused():
            return PodStatus(state=PodState.PAUSED, ip=cfg.rdp.ip)
    except Exception as e:  # pragma: no cover - defensive
        log.debug("is_paused probe failed: %s", e)

    rdp_ok = check_rdp_port(cfg.rdp.ip, cfg.rdp.port)
    return PodStatus(
        state=PodState.RUNNING if rdp_ok else PodState.STARTING,
        ip=cfg.rdp.ip,
    )
