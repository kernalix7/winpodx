# SPDX-License-Identifier: MIT
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


def check_tcp_port(ip: str, port: int, timeout: float = 5.0) -> bool:
    """Plain TCP-accept check. True if a connection can be established
    to (ip, port), regardless of what protocol (if any) is spoken.

    Used by ``recover_rdp_if_needed`` to probe the VNC port -- the
    intent there is "is the container's QEMU process still alive at
    all", and VNC doesn't speak RDP so we can't use the X.224-handshake
    flavor of ``check_rdp_port`` for that. Generally NOT what you want
    for "is RDP up" -- prefer ``check_rdp_port`` so QEMU's slirp
    accepting forwards-with-no-guest doesn't surface as a false
    positive.
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, TimeoutError, OSError):
        return False


# Minimal X.224 Connection Request packet wrapped in a TPKT header.
# The first three bytes (`03 00 00`) are TPKT version + reserved; byte
# 4 is the TPKT length (0x13 = 19). Bytes 5-19 are an X.224 CR TPDU
# carrying an empty cookie. Any RFC-compliant RDP server (Windows
# TermService, FreeRDP server, xrdp) will respond to this with a TPKT
# whose first two bytes are also `03 00` -- the X.224 Connection
# Confirm or a Negotiation Failure. We don't care which kind of
# response, just that the bytes look like a real RDP server speaking,
# not QEMU slirp accepting a TCP connection it can't actually serve.
_RDP_HANDSHAKE_PROBE = (
    b"\x03\x00\x00\x13\x0e\xe0\x00\x00\x00\x00\x00\x01\x00\x08\x00\x03\x00\x00\x00"
)


def check_rdp_port(ip: str, port: int, timeout: float = 5.0) -> bool:
    """Check that an RDP server is actually answering on (ip, port).

    Pre-PR-XX this did just ``socket.create_connection``. dockur's QEMU
    slirp accepts TCP forwards on the host's mapped port before the
    Windows guest's TermService is up -- so a fresh install in mid-ISO-
    download (or any pod that has booted QEMU but hasn't yet brought
    Windows to the RDP listener) would report "RDP port open" the
    instant the QEMU process started. install.sh's ``wait-ready`` then
    skipped past phase 2 in 0 seconds, hit phase 3 (which after
    PR #91 returns True with a warning when /health misses), and ran
    migrate's apply chain against a Windows that wasn't there --
    surfacing as a cascade of FreeRDP rc=147 / "Connection reset by
    peer" failures.

    The fix sends a minimal X.224 Connection Request and reads back 2
    bytes. A real RDP server responds with a TPKT (first byte 0x03,
    second byte 0x00). QEMU-forwarding-with-no-guest gets us the SYN-
    ACK from slirp's TCP stack but the recv either times out or
    returns EOF when slirp can't reach a listener inside the guest --
    distinguishable from the real-server case in ~1 second.

    Backward-compat note: this can return False on RDP servers that
    don't speak the X.224 prelude (rare, mostly homebrew). If that
    bites someone, opt out via the existing ``timeout=0`` callers OR
    we add a fallback. Until then the false-positive case (QEMU mid-
    install) is much more painful than the false-negative case.
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(max(1.0, timeout))
            try:
                s.sendall(_RDP_HANDSHAKE_PROBE)
            except OSError:
                return False
            try:
                data = s.recv(2)
            except (TimeoutError, OSError):
                return False
            return len(data) == 2 and data[0:1] == b"\x03" and data[1:2] == b"\x00"
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

    # Whole pod sick? Don't try to bandage RDP. VNC isn't RDP -- use
    # the plain TCP-accept probe so we don't get false negatives from
    # check_rdp_port's X.224 handshake (which would naturally fail
    # against a VNC server).
    if not check_tcp_port(cfg.rdp.ip, cfg.pod.vnc_port, timeout=2.0):
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
    # Thin AppImage (#357 / #363 root-cause fix, 0.6.0 item A): the container
    # stack is no longer bundled, so standard PATH resolution finds the host
    # runtime directly. ``host_env()`` still strips ``${APPDIR}`` from
    # ``LD_LIBRARY_PATH`` so the host runtime + the host helpers it spawns
    # load HOST libs. ``host_env()`` is a no-op outside an AppImage.
    from winpodx.backend._hostenv import host_env

    runtime = "podman" if backend == "podman" else "docker"
    try:
        subprocess.run(
            [runtime, "restart", "--time", "10", container],
            capture_output=True,
            text=True,
            timeout=60,
            env=host_env(),
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
