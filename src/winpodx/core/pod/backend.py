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
# classified as ``UNRESPONSIVE`` rather than ``STARTING``. First-boot
# Sysprep + OEM apply is driven by ``install.sh`` separately
# (``[1-3/3] Waiting for ...`` checkpoints) so the GUI / tray are
# expected to be closed during that window. By the time the GUI is
# usable to the human, the container is past boot — three minutes is
# plenty of cushion for an in-place ``winpodx pod restart`` to come
# back without the user briefly seeing UNRESPONSIVE flicker. Lowered
# from 600 s after the post-#219 smoke showed the tray still stuck on
# ``starting`` past the 10-minute mark on a known-stalled pod.
_UNRESPONSIVE_UPTIME_FLOOR_SECS = 180


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
    # guest has stalled — pre-#219 this was misreported as STARTING
    # forever, leaving the GUI / tray frozen on "starting" until the
    # user noticed). Probe the backend's container uptime: under the
    # floor → STARTING, past it → UNRESPONSIVE.
    #
    # Unknown-uptime fallback (post-smoke v0.5.5): treat ``None`` as
    # UNRESPONSIVE on container backends. The dockur first-boot window
    # is owned by ``install.sh`` (`[1-3/3]` checkpoints, no GUI / tray
    # active), so by the time a probe returns ``None`` from the running
    # GUI, the container has clearly been up — defaulting to STARTING
    # was the bug we just hit on real-Windows smoke: uptime parse
    # failure → STARTING → user thinks pod is still booting after
    # 50 min. Fall back to STARTING only on backends that don't
    # implement uptime_secs at all (libvirt / manual return None
    # because they override nothing; container backends return a real
    # value or None only on probe failure).
    uptime = None
    try:
        uptime = backend.uptime_secs()
    except Exception as e:  # pragma: no cover - defensive
        log.debug("uptime_secs probe failed: %s", e)
    if uptime is not None and uptime >= _UNRESPONSIVE_UPTIME_FLOOR_SECS:
        return PodStatus(state=PodState.UNRESPONSIVE, ip=cfg.rdp.ip)
    if uptime is None and cfg.pod.backend in ("podman", "docker"):
        log.warning(
            "Container backend %r did not return an uptime; classifying as "
            "UNRESPONSIVE on the assumption that an RDP miss without uptime "
            "data is a stalled long-running pod, not a fresh boot.",
            cfg.pod.backend,
        )
        return PodStatus(state=PodState.UNRESPONSIVE, ip=cfg.rdp.ip)
    return PodStatus(state=PodState.STARTING, ip=cfg.rdp.ip)
