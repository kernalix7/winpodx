"""RDP-port liveness probes and pod recovery helpers."""

from __future__ import annotations

import logging
import re
import socket
import subprocess
import time

from winpodx.core.config import Config

log = logging.getLogger(__name__)

# Container name guard reused for the recovery-path subprocess args.
_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def check_rdp_port(ip: str, port: int, timeout: float = 5.0) -> bool:
    """Check if RDP port is open and accepting connections."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


def recover_rdp_if_needed(cfg: Config, *, max_attempts: int = 3) -> bool:
    """Detect "RDP dead but VNC alive" and recover by restarting the container.

    After host suspend / long idle, Windows TermService can hang or the
    virtual NIC can drop into power-save while VNC keeps working (VNC
    talks to the QEMU display, not Windows). The fundamental constraint
    here is that any host-driven Windows-side recovery (TermService
    restart, w32tm resync) needs RDP itself to authenticate via
    ``windows_exec.run_in_windows`` — and RDP is exactly what's broken.

    v0.1.9.5: previous releases tried ``podman exec`` which doesn't
    actually reach the Windows VM (rc=127), so this function has been
    silently no-op'ing since it was added. The honest fix is to restart
    the container — dockur respawns the VM cleanly, OEM hardening
    re-applies on boot, and RDP comes back. Cost is ~30 s pod restart
    vs. some unknown hung-state.

    Returns True if RDP is reachable on return, False if recovery failed.
    Skips silently for libvirt / manual backends.
    """
    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return True

    if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=2.0):
        return True

    # Whole pod sick? Don't try to bandage RDP.
    if not check_rdp_port(cfg.rdp.ip, cfg.pod.vnc_port, timeout=2.0):
        log.warning("RDP and VNC both unreachable; skipping recovery (pod likely down).")
        return False

    container = cfg.pod.container_name
    if not _CONTAINER_NAME_RE.match(container or ""):
        log.warning("Refusing to recover RDP on non-conforming container name: %r", container)
        return False

    log.info(
        "RDP unreachable while VNC is alive; restarting the pod to recover "
        "(no host-driven TermService restart channel exists for an unauthenticated session)."
    )
    runtime = "podman" if backend == "podman" else "docker"
    try:
        subprocess.run(
            [runtime, "restart", "--time", "10", container],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("RDP recovery: pod restart failed: %s", e)
        return False

    backoff = 3.0
    for _attempt in range(max(1, max_attempts)):
        time.sleep(backoff)
        if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=3.0):
            log.info("RDP recovery succeeded after pod restart.")
            return True
        backoff *= 2

    log.warning("RDP recovery exhausted %d attempts; RDP still down.", max_attempts)
    return False
