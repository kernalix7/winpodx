"""Pod state types, backend factory, and status query."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from winpodx.backend.base import Backend
from winpodx.core.config import Config
from winpodx.core.pod.health import check_rdp_port

log = logging.getLogger(__name__)


class PodState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    # Container is alive (running, not paused) but the Windows guest has
    # stopped answering on the RDP port long enough that this can't be
    # confused with a fresh boot. Surfaces idle-induced Modern Standby
    # entries, TermService stalls, and similar guest-side regressions
    # that legacy pod_status() used to misreport as ``STARTING`` forever.
    UNRESPONSIVE = "unresponsive"
    ERROR = "error"


# Container must be running this long before an RDP-port miss can be
# classified as ``UNRESPONSIVE`` rather than ``STARTING``. Default
# matches the dockur first-boot floor (Sysprep + OEM apply); legitimate
# late-boot misses on slower hosts may extend past this — in which case
# the caller's auto-recovery flow is a no-op (the agent isn't up yet
# either) and the next poll picks up the real RUNNING state.
_UNRESPONSIVE_UPTIME_FLOOR_SECS = 600


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


def pod_status(cfg: Config) -> PodStatus:
    """Query the current pod status."""
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
    if rdp_ok:
        return PodStatus(state=PodState.RUNNING, ip=cfg.rdp.ip)

    # RDP unreachable. Discriminate STARTING (boot in progress, expected
    # to clear) from UNRESPONSIVE (long-running container whose Windows
    # guest has stalled — pre-#TBD this was misreported as STARTING
    # forever, leaving the GUI / tray frozen on "starting" until the
    # user noticed). Probe the backend's container uptime: under the
    # floor → STARTING, past it → UNRESPONSIVE. Backends that don't
    # expose uptime (libvirt, manual) fall back to the legacy STARTING
    # answer.
    uptime = None
    try:
        uptime = backend.uptime_secs()
    except Exception as e:  # pragma: no cover - defensive
        log.debug("uptime_secs probe failed: %s", e)
    if uptime is not None and uptime >= _UNRESPONSIVE_UPTIME_FLOOR_SECS:
        return PodStatus(state=PodState.UNRESPONSIVE, ip=cfg.rdp.ip)
    return PodStatus(state=PodState.STARTING, ip=cfg.rdp.ip)
