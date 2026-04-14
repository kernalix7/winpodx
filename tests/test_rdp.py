"""Tests for RDP session management."""

from __future__ import annotations

import pytest

from winpodx.core.config import Config
from winpodx.core.rdp import build_rdp_command, linux_to_unc


def test_linux_to_unc_home(monkeypatch, tmp_path):
    monkeypatch.setattr("winpodx.core.rdp.Path.home", staticmethod(lambda: tmp_path))
    doc = tmp_path / "Documents" / "test.docx"
    doc.parent.mkdir()
    doc.touch()
    result = linux_to_unc(str(doc))
    assert result == "\\\\tsclient\\home\\Documents\\test.docx"


def test_linux_to_unc_outside_home(monkeypatch, tmp_path):
    monkeypatch.setattr("winpodx.core.rdp.Path.home", staticmethod(lambda: tmp_path / "home"))
    result = linux_to_unc("/tmp/test.txt")
    assert result == "\\\\tsclient\\tmp\\test.txt"


def test_find_freerdp_returns_tuple_or_none():
    from winpodx.core.rdp import find_freerdp

    result = find_freerdp()
    # May or may not find freerdp — just check the type
    assert result is None or (isinstance(result, tuple) and len(result) == 2)


# --- build_rdp_command tests ---


class TestBuildRdpCommand:
    @pytest.fixture()
    def cfg(self):
        c = Config()
        c.rdp.ip = "127.0.0.1"
        c.rdp.port = 3390
        c.rdp.user = "TestUser"
        c.rdp.password = "secret"
        c.rdp.scale = 100
        c.rdp.dpi = 0
        c.rdp.extra_flags = ""
        c.pod.backend = "manual"
        return c

    def test_raises_without_freerdp(self, cfg, monkeypatch):
        monkeypatch.setattr("winpodx.core.rdp.find_freerdp", lambda: None)
        with pytest.raises(RuntimeError, match="FreeRDP 3\\+ not found"):
            build_rdp_command(cfg)

    def test_basic_command_structure(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cmd, password = build_rdp_command(cfg)
        assert password == ""  # password embedded in /p: flag
        assert any(c.startswith("/p:") for c in cmd)
        assert "/v:127.0.0.1:3390" in cmd
        assert "/u:TestUser" in cmd
        assert "/cert:ignore" in cmd
        assert "/scale:100" in cmd
        assert "/from-stdin:force" not in cmd

    def test_remote_ip_uses_tofu(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.ip = "192.168.1.100"
        cmd, _ = build_rdp_command(cfg)
        assert "/cert:tofu" in cmd
        assert "/cert:ignore" not in cmd

    def test_app_executable_added(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cmd, _ = build_rdp_command(cfg, app_executable="notepad.exe")
        assert "/shell:notepad.exe" in cmd

    def test_dpi_flag_when_set(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.dpi = 150
        cmd, _ = build_rdp_command(cfg)
        assert "/scale-desktop:150" in cmd

    def test_dpi_flag_omitted_when_zero(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.dpi = 0
        cmd, _ = build_rdp_command(cfg)
        assert not any(c.startswith("/scale-desktop:") for c in cmd)

    def test_no_password_no_stdin(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.password = ""
        cmd, password = build_rdp_command(cfg)
        assert password == ""
        assert "/from-stdin:force" not in cmd

    def test_domain_flag(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.domain = "WORKGROUP"
        cmd, _ = build_rdp_command(cfg)
        assert "/d:WORKGROUP" in cmd

    def test_extra_flags_filtered(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.extra_flags = "/sound:sys:alsa /exec:evil"
        cmd, _ = build_rdp_command(cfg)
        assert "/sound:sys:alsa" in cmd
        assert "/exec:evil" not in cmd
