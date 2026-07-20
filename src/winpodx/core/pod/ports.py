# SPDX-License-Identifier: MIT
"""Host port preflight for pod start (#754).

Ubuntu ships GNOME's built-in Remote Desktop (Settings > Sharing), which by
default binds ``127.0.0.1:3390`` -- the same loopback port winpodx's RDP
config defaults to. When something else already holds a port winpodx's
compose file needs to publish on the host, ``podman-compose`` /
``docker compose`` can't bind it and the pod never comes up; before #754
this only surfaced as an hour-long boot timeout with no diagnostics.

``check_host_ports`` runs a fast bind-test preflight so ``start_pod``
(``winpodx.core.pod.lifecycle``) can fail immediately with an actionable
message instead, and ``winpodx doctor`` can surface the same conflict
before the user ever tries to start.
"""

from __future__ import annotations

import re
import socket
import subprocess
from dataclasses import dataclass

from winpodx.core.agent import AGENT_PORT
from winpodx.core.config import Config
from winpodx.core.guest_disk import SMB_HOST_PORT

# Matches the owning-process name out of `ss -H -tlnp` output, e.g.:
#   LISTEN 0 511 127.0.0.1:3390 0.0.0.0:* users:(("gnome-remote-desktop",pid=1234,fd=7))
_OWNER_RE = re.compile(r'users:\(\("([^"]+)"')


@dataclass(frozen=True)
class PortConflict:
    port: int
    label: str
    owner: str = ""


def _required_ports(cfg: Config) -> list[tuple[int, str]]:
    """Host loopback ports winpodx's compose file needs to publish."""
    return [
        (cfg.rdp.port, "RDP"),
        (cfg.pod.vnc_port, "VNC"),
        (AGENT_PORT, "agent"),
        (SMB_HOST_PORT, "SMB (reverse-open)"),
    ]


def _port_in_use(port: int) -> bool:
    """True if ``127.0.0.1:port`` can't be bound right now.

    Same bind-test idiom as ``core.usbredir``'s relay listener: a plain
    connect-test would miss a port that's bound but not yet accepting, and
    would false-negative against anything not speaking a known protocol.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def _owner_hint(port: int) -> str:
    """Best-effort name of the process holding ``port``, via ``ss -H -tlnp``.

    Empty string when ``ss`` is missing, times out, or the line can't be
    parsed -- this is a diagnostic hint, not load-bearing.
    """
    try:
        result = subprocess.run(
            ["ss", "-H", "-tlnp"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    for line in result.stdout.splitlines():
        if f":{port} " not in line:
            continue
        m = _OWNER_RE.search(line)
        if m:
            return m.group(1)
    return ""


def check_host_ports(cfg: Config) -> list[PortConflict]:
    """Preflight: which of winpodx's required host ports are already taken.

    Only meaningful for container backends (``podman`` / ``docker``) -- the
    ``manual`` backend doesn't publish any ports of its own, so this always
    returns ``[]`` for it.

    Callers must skip this (or accept false positives) when winpodx's own
    pod is already running/paused: a live pod holds these ports itself,
    which is not a conflict.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return []
    conflicts: list[PortConflict] = []
    for port, label in _required_ports(cfg):
        if _port_in_use(port):
            conflicts.append(PortConflict(port=port, label=label, owner=_owner_hint(port)))
    return conflicts


def format_port_conflict_error(conflicts: list[PortConflict]) -> str:
    """Render ``conflicts`` into the multi-line message shown to the user."""
    lines = ["Cannot start pod — the following host port(s) are already in use:"]
    for c in conflicts:
        lines.append(f"  127.0.0.1:{c.port} [{c.label}] ({c.owner or 'unknown process'})")

    affected = {c.label for c in conflicts}
    if "RDP" in affected or "VNC" in affected:
        lines.append(
            "RDP and VNC ports are configurable: "
            "`winpodx config set rdp.port <n>` / `winpodx config set pod.vnc_port <n>`."
        )
    if "RDP" in affected:
        lines.append(
            "On Ubuntu, GNOME's built-in Remote Desktop (Settings > Sharing) commonly "
            "claims 3390 -- turn it off there, or move winpodx's RDP port instead."
        )
    return "\n".join(lines)
