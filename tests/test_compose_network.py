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

That regressed rootless hosts the rewrite did not fully cover, and 0.10.3
re-forced user-mode there (#770). The real cause turned out to be that
podman's rootlessport dials the published port from inside the container's
netns, so it traverses OUTPUT rather than PREROUTING and dockur's DNAT rule
never matched. QEMU base >= 7.37 adds the matching OUTPUT rule, and the image
pinned from 0.10.5 carries it, so the force is gone again -- dockur picks the
mode, and ``pod.network`` is the escape hatch for a host the upstream fix
misses. ``USER_PORTS`` stays emitted unconditionally as the passt-fallback
path (NAT ignores it by design), so it is harmless either way.
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


def test_no_network_forced_by_default():
    # 0.10.5: the pinned image carries the OUTPUT-chain DNAT fix, so dockur
    # picks the mode again on every backend -- rootless included.
    with patch("winpodx.backend.podman.is_rootless_podman", return_value=True):
        content = _build_compose_content(_cfg())
    assert "NETWORK:" not in content
    assert "USER_PORTS:" in content


def test_rootful_podman_does_not_force_network():
    with patch("winpodx.backend.podman.is_rootless_podman", return_value=False):
        content = _build_compose_content(_cfg())
    assert "NETWORK:" not in content
    assert "USER_PORTS:" in content


def test_docker_backend_does_not_force_network():
    cfg = _cfg()
    cfg.pod.backend = "docker"
    content = _build_compose_content(cfg)
    assert "NETWORK:" not in content
    assert "USER_PORTS:" in content


def test_pod_network_user_is_the_escape_hatch():
    # A host the upstream fix misses sets this and gets 0.10.3 behaviour back,
    # instead of a pod whose RDP never comes up and no way to change it.
    cfg = _cfg()
    cfg.pod.network = "user"
    content = _build_compose_content(cfg)
    assert 'NETWORK: "user"' in content
    assert "USER_PORTS:" in content


def test_pod_network_applies_on_docker_too():
    cfg = _cfg()
    cfg.pod.backend = "docker"
    cfg.pod.network = "user"
    content = _build_compose_content(cfg)
    assert 'NETWORK: "user"' in content


def test_pod_network_rejects_unknown_values():
    # The value is interpolated into compose.yaml, so a hand-edited TOML must
    # not be able to put arbitrary text there.
    cfg = _cfg()
    cfg.pod.network = "bridge; rm -rf /"
    cfg.pod.__post_init__()
    assert cfg.pod.network == ""
    assert "NETWORK:" not in _build_compose_content(cfg)


def test_pod_network_normalises_case_and_space():
    cfg = _cfg()
    cfg.pod.network = "  USER  "
    cfg.pod.__post_init__()
    assert cfg.pod.network == "user"


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


# --- #791: install locale detected from the host -----------------------------


def test_default_config_detects_locale_from_the_host(monkeypatch):
    # Goes through PodConfig() rather than assigning after construction: the
    # sanitiser in __post_init__ used to coerce the empty default back to
    # English, which made the autodetect branch unreachable in real use while
    # a test that set the attribute afterwards still passed.
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    cfg = _cfg()

    assert cfg.pod.language == ""  # empty survives __post_init__
    content = _build_compose_content(cfg)

    assert 'LANGUAGE: "Korean"' in content
    assert 'REGION: "ko-KR"' in content
    assert 'KEYBOARD: "ko-KR"' in content


def test_yaml_dangerous_locale_falls_back_to_detection(monkeypatch):
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    cfg = _cfg()
    cfg.pod.language = 'English"\ninjected: "x'
    cfg.pod.__post_init__()

    assert cfg.pod.language == ""
    assert 'LANGUAGE: "German"' in _build_compose_content(cfg)


def test_empty_locale_fields_are_detected_from_the_host(monkeypatch):
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    cfg = _cfg()
    cfg.pod.language = ""
    cfg.pod.region = ""
    cfg.pod.keyboard = ""

    content = _build_compose_content(cfg)

    assert 'LANGUAGE: "Korean"' in content
    assert 'REGION: "ko-KR"' in content
    assert 'KEYBOARD: "ko-KR"' in content


def test_configured_locale_is_not_overridden(monkeypatch):
    # Someone who set these explicitly keeps them whatever the host says.
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    cfg = _cfg()
    cfg.pod.language = "German"
    cfg.pod.region = "de-DE"
    cfg.pod.keyboard = "de-DE"

    content = _build_compose_content(cfg)

    assert 'LANGUAGE: "German"' in content
    assert 'REGION: "de-DE"' in content


def test_partially_configured_locale_fills_only_the_gaps(monkeypatch):
    # A user who picked a keyboard but never touched the language should keep
    # the layout and still get a guest in their own language.
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    cfg = _cfg()
    cfg.pod.language = ""
    cfg.pod.region = ""
    cfg.pod.keyboard = "en-US"

    content = _build_compose_content(cfg)

    assert 'LANGUAGE: "Korean"' in content
    assert 'KEYBOARD: "en-US"' in content
