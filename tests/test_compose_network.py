# SPDX-License-Identifier: MIT
"""dockur networking-mode env tests.

winpodx reaches the Windows guest only over forwarded ports (RDP 3389,
the web viewer 8006, and the agent 8765) -- it never needs the guest on
the host LAN. So the compose always pins ``NETWORK: "user"`` (dockur's
user-mode / passt networking), which forwards every entry in ``USER_PORTS``.

This closes #269 / #387: on hosts where dockur's default bridge-NAT path
succeeds (typically rootful), that path silently ignores ``USER_PORTS``,
so the agent port 8765 is never forwarded to the guest and ``pod
wait-ready`` hangs forever. Pinning user-mode routes around the bridge
path so ``USER_PORTS`` is always honoured. (On rootless hosts dockur
already falls back to passt, so this is a no-op there.)
"""

from __future__ import annotations

from winpodx.core.config import Config
from winpodx.core.pod.compose import _build_compose_content


def _cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.rdp.user = "User"
    cfg.rdp.password = "TestPassword1!"
    cfg.rdp.port = 3390
    cfg.pod.vnc_port = 8007
    cfg.pod.container_name = "winpodx-windows"
    cfg.pod.tuning_profile = "off"
    return cfg


def test_compose_pins_user_mode_networking():
    content = _build_compose_content(_cfg())
    # user-mode networking is what makes USER_PORTS (the agent port) forward.
    assert 'NETWORK: "user"' in content


def test_compose_user_ports_present_alongside_network():
    content = _build_compose_content(_cfg())
    # NETWORK + USER_PORTS must coexist: USER_PORTS is only honoured under
    # user-mode, so both lines together are what forwards the agent port.
    assert 'NETWORK: "user"' in content
    assert "USER_PORTS:" in content


def test_compose_ballooning_off_unconditional():
    # dockur v6.00 promotes memory ballooning to a first-class env. winpodx
    # deliberately runs the VM with ballooning OFF for stability, so
    # BALLOONING: "N" is emitted unconditionally (independent of tuning).
    content = _build_compose_content(_cfg())
    assert 'BALLOONING: "N"' in content


def test_compose_never_sets_disk_io_iouring():
    # DISK_IO: "io_uring" is deliberately NOT wired: the container backend's
    # default seccomp blocks io_uring_setup (ENOSYS), so QEMU falls back to
    # the thread pool and only logs an error. Keep the guest on dockur's
    # default DISK_IO. (See the v6.00 roll-forward follow-up.)
    content = _build_compose_content(_cfg())
    assert "DISK_IO:" not in content
