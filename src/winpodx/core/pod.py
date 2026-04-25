"""Pod lifecycle management."""

from __future__ import annotations

import logging
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from enum import Enum

from winpodx.backend.base import Backend
from winpodx.core.config import Config

log = logging.getLogger(__name__)

# Container name guard reused for the recovery-path subprocess args.
_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


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
    """Check if RDP port is open and accepting connections."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


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


def recover_rdp_if_needed(cfg: Config, *, max_attempts: int = 3) -> bool:
    """Bug B: kick TermService when RDP is dead but VNC is alive.

    After host suspend / long idle, Windows TermService can hang and
    the virtual NIC can drop into power-save and stop accepting RDP
    traffic — VNC keeps working because it talks to the QEMU display
    directly. We detect the asymmetry and force a TermService restart
    plus a w32tm resync, then re-probe RDP up to ``max_attempts`` times
    with simple linear backoff.

    Returns True if RDP is reachable when this function returns (either
    because it was already up, or recovery succeeded). Returns False if
    recovery couldn't bring it back, or if VNC was also dead (whole pod
    is sick — caller should surface a different error). Backends other
    than podman / docker skip silently with True so libvirt + manual
    users aren't blocked by a no-op.
    """
    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return True

    if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=2.0):
        return True

    # Whole pod sick? Don't try to bandage RDP.
    if not check_rdp_port(cfg.rdp.ip, cfg.pod.vnc_port, timeout=2.0):
        log.warning(
            "RDP and VNC both unreachable; skipping TermService recovery (pod likely down)."
        )
        return False

    container = cfg.pod.container_name
    if not _CONTAINER_NAME_RE.match(container or ""):
        log.warning("Refusing to recover RDP on non-conforming container name: %r", container)
        return False

    runtime = "podman" if backend == "podman" else "docker"
    ps_recover = (
        "Restart-Service -Force TermService; "
        "Start-Sleep -Seconds 2; "
        "w32tm /resync /force | Out-Null"
    )
    cmd = [
        runtime,
        "exec",
        container,
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        ps_recover,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("RDP recovery: TermService restart command failed: %s", e)
        return False

    if result.returncode != 0:
        log.warning("RDP recovery: rc=%d stderr=%s", result.returncode, result.stderr.strip())
        # Even on rc!=0 retry the probe — TermService may already be
        # back from a Windows-side `sc.exe failure` action.

    backoff = 2.0
    for _attempt in range(max(1, max_attempts)):
        time.sleep(backoff)
        if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=3.0):
            log.info("RDP recovery succeeded after TermService restart.")
            return True
        backoff *= 2

    log.warning("RDP recovery exhausted %d attempts; RDP still down.", max_attempts)
    return False


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
    return PodStatus(
        state=PodState.RUNNING if rdp_ok else PodState.STARTING,
        ip=cfg.rdp.ip,
    )
