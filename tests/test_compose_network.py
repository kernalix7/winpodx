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


def test_compose_disk_io_absent_when_tuning_off():
    # With tuning_profile "off" the profile never asks for io_uring, so no
    # DISK_IO env is written (dockur keeps its own default).
    content = _build_compose_content(_cfg())
    assert "DISK_IO:" not in content


def test_compose_disk_io_iouring_when_profile_enables_it(monkeypatch):
    # When the host tuning profile enables io_uring, it is wired through to
    # the guest as the v6.00 dedicated DISK_IO env (not a raw QEMU flag).
    from winpodx.utils import specs

    # A real TuningProfile so every other tuning consumer (virtio-rng, etc.)
    # still finds its fields; only io_uring is turned on.
    prof = specs.TuningProfile(
        name="manual",
        apply_invtsc=False,
        apply_io_uring=True,
        apply_hugepages=False,
        apply_cpu_pinning=False,
        apply_platform_tick=False,
        apply_no_balloon=False,
    )
    monkeypatch.setattr(specs, "recommend_tuning_profile", lambda *a, **k: prof)
    content = _build_compose_content(_cfg())
    assert 'DISK_IO: "io_uring"' in content
