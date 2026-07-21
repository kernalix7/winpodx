# SPDX-License-Identifier: MIT
"""dockur networking-mode env tests.

winpodx reaches the Windows guest only over forwarded ports (RDP 3389,
the web viewer 8006, and the agent 8765) -- it never needs the guest on
the host LAN.

Historically the compose pinned ``NETWORK: "user"`` to force user-mode
(passt), a workaround for #269 / #387 where dockur's bridge-NAT path set up
NAT but never forwarded the published ports on to the VM (so the agent port
hung ``pod wait-ready``). dockur **v6.01** (@kroese) rewrote the rootless-Podman
NAT port-forwarding, so 0.10.1 stopped forcing the mode (#735).

That regressed rootless hosts the rewrite did not fully cover: the container
picked bridge NAT, the guest got a NAT-internal ``172.x`` IP, and the host's
forwarded RDP port never reached it (#770). So winpodx now re-forces
``NETWORK: "user"`` on **rootless Podman only** -- rootful Podman and Docker,
where NAT is validated, keep dockur's auto-selection. ``USER_PORTS`` stays
emitted unconditionally as the passt-fallback path (NAT ignores it by design,
forwarding every non-``HOST_PORTS`` port to the VM), so it is harmless when we
do not force user-mode.
"""

from __future__ import annotations

from unittest.mock import patch

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


def test_rootless_podman_forces_network_user():
    # #770: on rootless Podman re-force user-mode (passt); NAT's 172.x guest IP
    # is unreachable for host-forwarded RDP there.
    with patch("winpodx.backend.podman.is_rootless_podman", return_value=True):
        content = _build_compose_content(_cfg())
    assert 'NETWORK: "user"' in content
    assert "slirp" not in content
    assert "USER_PORTS:" in content


def test_rootful_podman_does_not_force_network():
    # Rootful Podman keeps dockur's auto-selection (NAT is validated there).
    with patch("winpodx.backend.podman.is_rootless_podman", return_value=False):
        content = _build_compose_content(_cfg())
    assert "NETWORK:" not in content
    assert "USER_PORTS:" in content


def test_docker_backend_never_forces_network():
    # Docker is excluded entirely -- the rootless detector is podman-specific
    # and must not even be consulted on the docker path.
    cfg = _cfg()
    cfg.pod.backend = "docker"
    with patch("winpodx.backend.podman.is_rootless_podman", return_value=True) as detector:
        content = _build_compose_content(cfg)
    assert "NETWORK:" not in content
    assert "USER_PORTS:" in content
    detector.assert_not_called()


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
