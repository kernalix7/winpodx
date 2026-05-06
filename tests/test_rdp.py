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


def test_linux_to_unc_outside_home_raises(monkeypatch, tmp_path):
    # Paths outside $HOME and any media share must raise.
    monkeypatch.setattr("winpodx.core.rdp.Path.home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setattr("winpodx.core.rdp._find_media_base", lambda: None)
    with pytest.raises(ValueError, match="outside shared locations"):
        linux_to_unc("/tmp/test.txt")


def test_linux_to_unc_media_path(monkeypatch, tmp_path):
    media = tmp_path / "run_media" / "user"
    media.mkdir(parents=True)
    usb_file = media / "USB" / "report.docx"
    usb_file.parent.mkdir()
    usb_file.touch()

    monkeypatch.setattr("winpodx.core.rdp.Path.home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setattr("winpodx.core.rdp._find_media_base", lambda: media)

    result = linux_to_unc(str(usb_file))
    assert result == "\\\\tsclient\\media\\USB\\report.docx"


def test_launch_app_remoteapp_without_display_raises(monkeypatch, tmp_path):
    # No $DISPLAY -> xfreerdp would die post-detach; launch_app must raise.
    from winpodx.core import rdp as rdp_mod

    monkeypatch.setattr(rdp_mod, "find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp"))
    monkeypatch.setattr(rdp_mod, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
    monkeypatch.delenv("DISPLAY", raising=False)

    with pytest.raises(RuntimeError, match="XWayland"):
        rdp_mod.launch_app(Config(), app_executable="notepad.exe")


def test_find_freerdp_returns_tuple_or_none():
    from winpodx.core.rdp import find_freerdp

    result = find_freerdp()
    assert result is None or (isinstance(result, tuple) and len(result) == 2)


def test_find_existing_session_rejects_non_freerdp_pid(tmp_path, monkeypatch):
    # A non-freerdp process with 'winpodx' in its cmdline must not look like a live RDP session.
    import shutil
    import subprocess

    from winpodx.core.rdp import _find_existing_session

    monkeypatch.setattr("winpodx.core.rdp.runtime_dir", lambda: tmp_path)
    monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)

    sleep = shutil.which("sleep")
    if sleep is None:  # pragma: no cover
        pytest.skip("sleep not available")

    # Any live process whose argv[0] is not a FreeRDP binary must be rejected
    # and its stale .cproc marker reaped (PID-reuse guard).
    child = subprocess.Popen([sleep, "30"])  # noqa: S603
    try:
        pidfile = tmp_path / "notepad.cproc"
        pidfile.write_text(str(child.pid))

        result = _find_existing_session("notepad")
        assert result is None, "must not resurrect a non-freerdp PID"
        assert not pidfile.exists(), "stale cproc marker must be cleaned"
    finally:
        child.kill()
        child.wait(timeout=5)


def test_find_existing_session_cleans_dead_pid(tmp_path, monkeypatch):
    from winpodx.core.rdp import _find_existing_session

    monkeypatch.setattr("winpodx.core.rdp.runtime_dir", lambda: tmp_path)
    monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)

    pidfile = tmp_path / "notepad.cproc"
    pidfile.write_text("99999999")  # almost certainly dead

    assert _find_existing_session("notepad") is None
    assert not pidfile.exists()


def test_is_freerdp_pid_helper_accepts_freerdp_only():
    from winpodx.core import process as proc_mod

    assert proc_mod.is_freerdp_pid(99999999) is False

    from unittest.mock import patch

    class _FakeCmdline:
        def __init__(self, content: bytes) -> None:
            self._content = content

        def read_bytes(self) -> bytes:
            return self._content

    def fake_path_factory(content: bytes):
        return lambda _p: _FakeCmdline(content)

    with (
        patch("winpodx.core.process.os.kill", return_value=None),
        patch(
            "winpodx.core.process.Path",
            side_effect=fake_path_factory(b"/usr/bin/winpodx\0app\0list\0"),
        ),
    ):
        assert proc_mod.is_freerdp_pid(12345) is False

    with (
        patch("winpodx.core.process.os.kill", return_value=None),
        patch(
            "winpodx.core.process.Path",
            side_effect=fake_path_factory(b"/usr/bin/xfreerdp3\0/v:127.0.0.1\0"),
        ),
    ):
        assert proc_mod.is_freerdp_pid(12345) is True


# build_rdp_command tests


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
        assert any("/app:program:notepad.exe" in c for c in cmd)

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

    def test_codec_toggles_pass_filter(self, cfg, monkeypatch):
        """The 2026-05-06 #126 fallout — `-gfx-h264` is the workaround
        for cachyos's experimental WITH_VAAPI_H264_ENCODING build, so the
        filter MUST allow it. Same goes for the rest of the codec/cache
        toggle pack added in this PR — power users should be able to
        disable any single codec without us shipping a per-codec config
        field."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        # +rfx / -rfx aren't real FreeRDP bare toggles (the binary expects
        # `/rfx[:level]`). Only the genuine bare codec toggles.
        toggles = (
            "-gfx-h264 +gfx-h264 "
            "-nsc +nsc -jpeg +jpeg -avc444 +avc444 "
            "-gfx-progressive +gfx-progressive "
            "-gfx-thin-client +gfx-thin-client "
            "-gfx-small-cache +gfx-small-cache "
            "-wallpaper +wallpaper -themes +themes "
            "-decorations +decorations "
            "-grab-keyboard +grab-keyboard -grab-mouse +grab-mouse "
            "-mouse-relative +mouse-relative "
            "-async-update +async-update -async-channels +async-channels "
            "-auto-reconnect +auto-reconnect "
            "-bitmap-cache +bitmap-cache "
            "-offscreen-cache +offscreen-cache "
            "-glyph-cache +glyph-cache"
        )
        cfg.rdp.extra_flags = toggles
        cmd, _ = build_rdp_command(cfg)
        for toggle in toggles.split():
            assert toggle in cmd, f"toggle dropped by filter: {toggle!r}"

    def test_extra_args_kwarg_appended_after_global_extra_flags(self, cfg, monkeypatch):
        """Per-launch `extra_args` (CLI --extra-args / GUI per-launch)
        is appended AFTER `cfg.rdp.extra_flags` so per-launch overrides
        win when FreeRDP ties on duplicate flags. Uses the same
        allowlist filter so the per-launch path can't smuggle anything
        unsafe."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.extra_flags = "+gfx-h264"  # global says: keep H.264
        cmd, _ = build_rdp_command(cfg, extra_args="-gfx-h264 /not-a-real-flag:bad")

        # Both global and per-launch survived the filter.
        assert "+gfx-h264" in cmd
        assert "-gfx-h264" in cmd
        # Per-launch lands AFTER global; this is the override semantics
        # callers rely on.
        assert cmd.index("-gfx-h264") > cmd.index("+gfx-h264")
        # Unsafe flag in extra_args is dropped, same as via cfg.rdp.extra_flags.
        assert "/not-a-real-flag:bad" not in cmd

    def test_extra_args_empty_string_is_noop(self, cfg, monkeypatch):
        """Default (empty extra_args) must not append anything to the
        command. Guards against an accidental sentinel like `""` showing
        up as an empty-string arg in the FreeRDP command."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cmd_no_extra, _ = build_rdp_command(cfg)
        cmd_empty, _ = build_rdp_command(cfg, extra_args="")
        assert cmd_no_extra == cmd_empty
        assert "" not in cmd_empty
