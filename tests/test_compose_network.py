# SPDX-License-Identifier: MIT
"""dockur networking-mode env tests.

winpodx reaches the Windows guest only over forwarded ports (RDP 3389,
the web viewer 8006, and the agent 8765) -- it never needs the guest on
the host LAN.

Historically the compose pinned ``NETWORK: "user"`` to force user-mode
(passt), a workaround for #269 / #387 where dockur's bridge-NAT path set up
NAT but never forwarded the published ports on to the VM (so the agent port
hung ``pod wait-ready``). dockur **v6.01** (@kroese) rewrote the rootless-Podman
NAT port-forwarding to fix exactly that, so we no longer force the mode (#735):
the container picks NAT when it can and falls back to passt itself. ``USER_PORTS``
stays as the passt-fallback path (NAT ignores it by design, forwarding every
non-``HOST_PORTS`` port to the VM).
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


def test_compose_does_not_force_network_mode():
    # #735: don't force NETWORK=user -- let dockur v6.01 pick NAT (with its
    # rootless port-forwarding fix) and fall back to passt on its own.
    content = _build_compose_content(_cfg())
    assert "NETWORK:" not in content


def test_compose_user_ports_present():
    # USER_PORTS stays as the passt-fallback path (ignored under NAT by design).
    content = _build_compose_content(_cfg())
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
