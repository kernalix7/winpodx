"""Tests for winpodx.core.info — the 5-section snapshot consumed by CLI + GUI."""

from __future__ import annotations

import subprocess

from winpodx.core.config import Config
from winpodx.core.info import (
    _bundled_oem_version,
    _bundled_rdprrap_version,
    _read_os_release,
    _read_text_file,
    gather_info,
)


def _cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.rdp.ip = "127.0.0.1"
    cfg.rdp.port = 3390
    cfg.pod.vnc_port = 8007
    cfg.pod.container_name = "winpodx-windows"
    return cfg


# --- _read_text_file ---


def test_read_text_file_returns_none_when_missing(tmp_path):
    assert _read_text_file(tmp_path / "no.txt") is None


def test_read_text_file_returns_none_when_oversized(tmp_path):
    p = tmp_path / "big.txt"
    p.write_bytes(b"x" * 10_000)
    assert _read_text_file(p, max_bytes=4096) is None


def test_read_text_file_strips_trailing_whitespace(tmp_path):
    p = tmp_path / "v.txt"
    p.write_text("0.1.9\n\n", encoding="utf-8")
    assert _read_text_file(p) == "0.1.9"


# --- OS release parsing ---


def test_read_os_release_parses_quoted(tmp_path, monkeypatch):
    fake = tmp_path / "os-release"
    fake.write_text(
        'ID=opensuse-tumbleweed\nVERSION="20260101"\nNAME="openSUSE Tumbleweed"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "winpodx.core.info._read_text_file", lambda p, max_bytes=8192: fake.read_text()
    )
    osr = _read_os_release()
    assert osr["ID"] == "opensuse-tumbleweed"
    assert osr["VERSION"] == "20260101"


def test_read_os_release_returns_empty_when_missing(monkeypatch):
    monkeypatch.setattr("winpodx.core.info._read_text_file", lambda *a, **k: None)
    assert _read_os_release() == {}


# --- bundled version pin readers ---


def test_bundled_oem_version_falls_back_to_unknown(monkeypatch, tmp_path):
    """When neither the .txt stamp NOR the install.bat fallback is available,
    the helper must return ``(unknown)`` rather than raising or returning ''.

    The implementation reads:
      1. ``config/oem/oem_version.txt`` via _read_text_file
      2. ``config/oem/install.bat`` via path.open (streaming, not _read_text_file)

    Both sources need to be redirected to a nonexistent path for the test to
    actually exercise the fallback path. Patching only _read_text_file leaves
    install.bat reachable on a dev checkout and the helper returns the real
    version.
    """
    monkeypatch.setattr("winpodx.core.info._read_text_file", lambda *a, **k: None)
    # Redirect __file__'s parent walk to a tmp path so install.bat candidates
    # don't resolve to the real repo file.
    monkeypatch.setattr("winpodx.core.info.Path.home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr("winpodx.core.info.__file__", str(tmp_path / "info.py"), raising=False)
    assert _bundled_oem_version() == "(unknown)"


def test_bundled_rdprrap_version_keeps_first_line_only(monkeypatch, tmp_path):
    pin = tmp_path / "rdprrap_version.txt"
    pin.write_text("0.1.3\nsha256:deadbeef...\n", encoding="utf-8")
    monkeypatch.setattr(
        "winpodx.core.info._read_text_file",
        lambda p, max_bytes=128: pin.read_text() if "rdprrap" in str(p) else None,
    )
    assert _bundled_rdprrap_version() == "0.1.3"


# --- gather_info: end-to-end shape ---


def _stub_probes(monkeypatch, *, rdp_alive: bool, vnc_alive: bool):
    """Override the network probes deterministically."""

    def probe(ip, port, timeout=5.0):
        if port == 8007:
            return vnc_alive
        return rdp_alive

    monkeypatch.setattr("winpodx.core.info.check_rdp_port", probe)


def _stub_pod_status(monkeypatch, state_value: str):
    from winpodx.core.pod import PodState, PodStatus

    state = PodState(state_value) if state_value in (s.value for s in PodState) else PodState.ERROR
    monkeypatch.setattr("winpodx.core.info.pod_status", lambda cfg: PodStatus(state=state))


def _stub_uptime(monkeypatch, value: str = ""):
    """Stub _container_uptime directly so we don't accidentally monkeypatch subprocess
    globally (display.scaling also reads from subprocess.run for kreadconfig6 etc.)."""
    monkeypatch.setattr("winpodx.core.info._container_uptime", lambda cfg: value)


def _stub_display(monkeypatch):
    monkeypatch.setattr(
        "winpodx.core.info._display_section",
        lambda: {
            "session_type": "wayland",
            "desktop_environment": "kde",
            "wayland_freerdp": "True",
            "raw_scale": "1.00",
            "rdp_scale": "100%",
        },
    )


def _stub_dependencies(monkeypatch):
    monkeypatch.setattr(
        "winpodx.core.info._dependencies_section",
        lambda: {"python3": {"found": "true", "path": "/usr/bin/python3"}},
    )


def _stub_active_sessions(monkeypatch, count: int = 0):
    monkeypatch.setattr("winpodx.core.info._active_session_count", lambda: count)


def _stub_all(monkeypatch, *, rdp=True, vnc=True, state="running", uptime="", sessions=0):
    """Compose all the stubs gather_info needs. Each test customizes via kwargs."""
    _stub_probes(monkeypatch, rdp_alive=rdp, vnc_alive=vnc)
    _stub_pod_status(monkeypatch, state)
    _stub_uptime(monkeypatch, uptime)
    _stub_display(monkeypatch)
    _stub_dependencies(monkeypatch)
    _stub_active_sessions(monkeypatch, sessions)


def test_gather_info_returns_all_five_sections(monkeypatch):
    cfg = _cfg()
    _stub_all(monkeypatch, uptime="2026-04-25T00:00:00Z")
    info = gather_info(cfg)
    assert set(info.keys()) == {"system", "display", "dependencies", "pod", "config"}


def test_gather_info_pod_section_when_running(monkeypatch):
    cfg = _cfg()
    _stub_all(monkeypatch, uptime="2026-04-25T00:00:00Z")
    info = gather_info(cfg)
    pod = info["pod"]
    assert pod["state"] == "running"
    assert pod["rdp_reachable"] is True
    assert pod["vnc_reachable"] is True
    assert pod["uptime"] == "2026-04-25T00:00:00Z"


def test_gather_info_pod_section_when_rdp_dead_vnc_alive(monkeypatch):
    """Bug B's exact scenario — info should reflect the asymmetry honestly."""
    cfg = _cfg()
    _stub_all(monkeypatch, rdp=False, vnc=True, state="starting", uptime="2026-04-25T00:00:00Z")
    info = gather_info(cfg)
    assert info["pod"]["rdp_reachable"] is False
    assert info["pod"]["vnc_reachable"] is True


def test_gather_info_pod_section_when_pod_down(monkeypatch):
    cfg = _cfg()
    _stub_all(monkeypatch, rdp=False, vnc=False, state="stopped", uptime="")
    info = gather_info(cfg)
    assert info["pod"]["uptime"] == ""
    assert info["pod"]["rdp_reachable"] is False
    assert info["pod"]["vnc_reachable"] is False


def test_gather_info_pod_section_libvirt_uptime_empty(monkeypatch):
    cfg = _cfg()
    cfg.pod.backend = "libvirt"
    _stub_all(monkeypatch)  # _container_uptime stubbed to "" anyway, libvirt path also returns ""
    info = gather_info(cfg)
    assert info["pod"]["uptime"] == ""


def test_gather_info_config_section_includes_budget_when_oversubscribed(monkeypatch):
    cfg = _cfg()
    cfg.pod.max_sessions = 50
    cfg.pod.ram_gb = 4
    _stub_all(monkeypatch)
    info = gather_info(cfg)
    assert info["config"]["budget_warning"]
    assert "ram_gb" in info["config"]["budget_warning"]


def test_gather_info_config_section_silent_on_default(monkeypatch):
    cfg = _cfg()  # default 10 sessions / 4 GB
    _stub_all(monkeypatch)
    info = gather_info(cfg)
    assert info["config"]["budget_warning"] == ""


def test_gather_info_active_sessions_count_propagates(monkeypatch):
    cfg = _cfg()
    _stub_all(monkeypatch, sessions=3)
    info = gather_info(cfg)
    assert info["pod"]["active_sessions"] == 3


def test_active_session_count_returns_zero_on_helper_failure(monkeypatch):
    """The real _active_session_count must swallow exceptions and return 0."""
    from winpodx.core.info import _active_session_count

    def boom():
        raise RuntimeError("simulated process tracking failure")

    monkeypatch.setattr("winpodx.core.process.list_active_sessions", boom)
    assert _active_session_count() == 0


def test_container_uptime_returns_empty_on_subprocess_timeout(monkeypatch):
    """The real _container_uptime must swallow TimeoutExpired."""
    from winpodx.core.info import _container_uptime

    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr("winpodx.core.info.subprocess.run", boom)
    cfg = _cfg()
    assert _container_uptime(cfg) == ""
