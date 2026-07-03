# SPDX-License-Identifier: MIT
"""Tests for RDP session management."""

from __future__ import annotations

import pytest

from winpodx.core.config import Config
from winpodx.core.rdp import _auto_kbd_flag, build_rdp_command, linux_to_unc


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


def test_find_media_base_prefers_user_then_persistent_parent(monkeypatch):
    # A live media parent is preferred over the placeholder so a USB inserted
    # AFTER the session starts shows on refresh. Per-user dir wins; the
    # persistent parent is the fallback for the not-mounted-yet-at-launch case.
    from winpodx.core import rdp

    monkeypatch.setenv("USER", "alice")
    present: set[str] = set()
    monkeypatch.setattr(rdp.Path, "is_dir", lambda self: str(self) in present)

    # Nothing mounted, no media subsystem dir -> None (caller uses placeholder).
    assert rdp._find_media_base() is None

    # USB inserted while no per-user dir existed yet: the persistent parent
    # exists, so we redirect it (USB shows at \\media\alice\<LABEL> on F5).
    present.add("/run/media")
    assert str(rdp._find_media_base()) == "/run/media"

    # Once udisks has made the per-user dir, prefer it (\\media\<LABEL>).
    present.add("/run/media/alice")
    assert str(rdp._find_media_base()) == "/run/media/alice"


def test_launch_app_remoteapp_without_display_raises(monkeypatch, tmp_path):
    # No $DISPLAY -> xfreerdp would die post-detach; launch_app must raise.
    from winpodx.core import rdp as rdp_mod

    monkeypatch.setattr(rdp_mod, "find_freerdp", lambda *a, **k: ("/usr/bin/xfreerdp", "xfreerdp"))
    monkeypatch.setattr(rdp_mod, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
    monkeypatch.delenv("DISPLAY", raising=False)

    # A real launch has credentials; set them so we reach the display/XWayland
    # guard rather than the empty-credential guards (#569).
    cfg = Config()
    cfg.rdp.user = "TestUser"
    cfg.rdp.password = "secret"
    with pytest.raises(RuntimeError, match="XWayland"):
        rdp_mod.launch_app(cfg, app_executable="notepad.exe")


def test_build_rdp_command_empty_user_raises_clear_error(monkeypatch):
    # #569: an empty username made xfreerdp fall back to an interactive prompt
    # that died with "Inappropriate ioctl for device" under a GUI launch. Fail
    # fast with an actionable message instead.
    from winpodx.core import rdp as rdp_mod

    monkeypatch.setattr(rdp_mod, "find_freerdp", lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"))
    cfg = Config()
    cfg.rdp.user = ""
    with pytest.raises(RuntimeError, match="credentials are not configured"):
        rdp_mod.build_rdp_command(cfg)


def test_build_rdp_command_empty_password_and_no_askpass_raises_clear_error(monkeypatch):
    # Empty password with no askpass makes xfreerdp prompt interactively, which
    # dies under a GUI launch with the same "Inappropriate ioctl for device" /
    # ERRCONNECT_CONNECT_CANCELLED error as an empty username.
    from winpodx.core import rdp as rdp_mod

    monkeypatch.setattr(rdp_mod, "find_freerdp", lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"))
    cfg = Config()
    cfg.rdp.user = "TestUser"
    cfg.rdp.password = ""
    cfg.rdp.askpass = ""
    with pytest.raises(RuntimeError, match="password is not configured"):
        rdp_mod.build_rdp_command(cfg)


def test_find_freerdp_returns_tuple_or_none():
    from winpodx.core.rdp import find_freerdp

    result = find_freerdp()
    assert result is None or (isinstance(result, tuple) and len(result) == 2)


def _patch_freerdp_probes(monkeypatch, *, native, flatpak):
    """Stub the native + Flatpak FreeRDP probes independently."""
    import winpodx.core.rdp as rdp_mod

    rdp_mod._FREERDP_CACHE.clear()
    monkeypatch.setattr(
        rdp_mod,
        "_find_native_freerdp",
        lambda: ("/usr/bin/xfreerdp3", "xfreerdp") if native else None,
    )
    monkeypatch.setattr(
        rdp_mod,
        "_find_flatpak_freerdp",
        lambda: ("flatpak run com.freerdp.FreeRDP", "flatpak") if flatpak else None,
    )


def test_find_freerdp_auto_prefers_flatpak_when_both_present(monkeypatch):
    # The Flatpak ships a self-contained FreeRDP 3+ (no host package skew) and
    # its RAIL multi-display rough edges are handled by cfg.rdp.multimon=span,
    # so auto prefers the Flatpak when both are installed.
    from winpodx.core.rdp import find_freerdp

    _patch_freerdp_probes(monkeypatch, native=True, flatpak=True)
    assert find_freerdp("auto") == ("flatpak run com.freerdp.FreeRDP", "flatpak")


def test_find_freerdp_auto_falls_back_to_native(monkeypatch):
    # No Flatpak -> the native client is the fallback under auto.
    from winpodx.core.rdp import find_freerdp

    _patch_freerdp_probes(monkeypatch, native=True, flatpak=False)
    assert find_freerdp("auto") == ("/usr/bin/xfreerdp3", "xfreerdp")


def test_find_freerdp_auto_uses_flatpak_when_only_flatpak(monkeypatch):
    # Only the Flatpak present -> auto uses it.
    from winpodx.core.rdp import find_freerdp

    _patch_freerdp_probes(monkeypatch, native=False, flatpak=True)
    assert find_freerdp("auto") == ("flatpak run com.freerdp.FreeRDP", "flatpak")


def test_find_freerdp_native_prefers_native(monkeypatch):
    from winpodx.core.rdp import find_freerdp

    _patch_freerdp_probes(monkeypatch, native=True, flatpak=True)
    assert find_freerdp("native") == ("/usr/bin/xfreerdp3", "xfreerdp")


def test_find_freerdp_native_falls_back_to_flatpak(monkeypatch):
    from winpodx.core.rdp import find_freerdp

    _patch_freerdp_probes(monkeypatch, native=False, flatpak=True)
    assert find_freerdp("native") == ("flatpak run com.freerdp.FreeRDP", "flatpak")


def test_find_freerdp_flatpak_forced(monkeypatch):
    from winpodx.core.rdp import find_freerdp

    _patch_freerdp_probes(monkeypatch, native=True, flatpak=True)
    assert find_freerdp("flatpak") == ("flatpak run com.freerdp.FreeRDP", "flatpak")


def test_flatpak_invocation_forces_xfreerdp_and_grants_perms():
    # The Flatpak default command is the SDL client (no RAIL) -> a RemoteApp
    # launch would open the full desktop. We must force xfreerdp and open the
    # sandbox holes winpodx's RDP flags need.
    from winpodx.core.rdp import _FLATPAK_FREERDP_CMD

    assert _FLATPAK_FREERDP_CMD.startswith("flatpak run ")
    assert "--command=xfreerdp" in _FLATPAK_FREERDP_CMD  # RAIL-capable client
    assert _FLATPAK_FREERDP_CMD.endswith(" com.freerdp.FreeRDP")  # app id last
    for perm in (
        "--share=network",  # /v: localhost RDP
        "--socket=x11",  # RAIL + clipboard
        "--socket=wayland",
        "--socket=pulseaudio",  # /sound
        "--socket=cups",  # /printer
        "--filesystem=home",  # \\tsclient\home + drive redirect
        "--filesystem=/run/media",  # \\tsclient\media (USB)
    ):
        assert perm in _FLATPAK_FREERDP_CMD, f"missing sandbox permission: {perm}"


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
        monkeypatch.setattr("winpodx.core.rdp.find_freerdp", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="FreeRDP 3\\+ not found"):
            build_rdp_command(cfg)

    def test_basic_command_structure(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
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
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.ip = "192.168.1.100"
        cmd, _ = build_rdp_command(cfg)
        assert "/cert:tofu" in cmd
        assert "/cert:ignore" not in cmd

    def test_app_executable_freerdp3_uses_combined_syntax(self, cfg, monkeypatch):
        """On FreeRDP 3, Win32 RemoteApp must use the combined
        ``/app:program:X,name:Y[,cmd:Z]`` syntax. FreeRDP 3 parses
        ``/app:`` as ``<key>:<value>,...`` so bare ``/app:PATH`` is
        rejected with "Unexpected keyword" at the path's drive prefix.
        Regression test for the smoke failure on Tumbleweed FreeRDP
        3.24.1 (2026-05-14)."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        monkeypatch.setattr("winpodx.core.rdp.freerdp_major_version", lambda: 3)
        cmd, _ = build_rdp_command(cfg, app_executable="notepad.exe")
        assert "/app:program:notepad.exe,name:notepad" in cmd
        # Separate /app-name: / /app-cmd: flags MUST NOT appear — they
        # double up with the combined form's name: / cmd: sub-keys.
        assert not any(c.startswith("/app-name:") for c in cmd)
        assert not any(c.startswith("/app-cmd:") for c in cmd)

    def test_app_executable_freerdp2_uses_separate_flags(self, cfg, monkeypatch):
        """On FreeRDP 2, Win32 RemoteApp must use the separate
        ``/app:PATH`` + ``/app-name:NAME`` + ``/app-cmd:CMD`` flag
        form. FreeRDP 2 parses the combined ``program:X,name:Y,cmd:Z``
        string as the literal program path and falls back to the
        Microsoft Store handler (#158, reported by @poetman)."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp", "xfreerdp"),
        )
        monkeypatch.setattr("winpodx.core.rdp.freerdp_major_version", lambda: 2)
        cmd, _ = build_rdp_command(cfg, app_executable="notepad.exe")
        assert "/app:notepad.exe" in cmd
        assert any(c.startswith("/app-name:") for c in cmd)
        # Combined form must NOT appear on FreeRDP 2.
        assert not any(c.startswith("/app:program:") for c in cmd)

    def test_app_cmd_freerdp3_inlined_into_app_arg(self, cfg, monkeypatch):
        """FreeRDP 3 combined form bundles ``cmd:`` into the same
        ``/app:`` arg, with comma-to-space sanitisation."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        monkeypatch.setattr("winpodx.core.rdp.freerdp_major_version", lambda: 3)
        cmd, _ = build_rdp_command(
            cfg,
            app_executable="explorer.exe",
            default_args="shell:Desktop",
        )
        assert any(",cmd:shell:Desktop" in c for c in cmd)
        assert not any(c.startswith("/app-cmd:") for c in cmd)

    def test_app_cmd_freerdp2_uses_separate_flag(self, cfg, monkeypatch):
        """FreeRDP 2 puts ``default_args`` on its own ``/app-cmd:``
        flag — commas inside the value are safe because each flag is
        its own argv entry."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp", "xfreerdp"),
        )
        monkeypatch.setattr("winpodx.core.rdp.freerdp_major_version", lambda: 2)
        cmd, _ = build_rdp_command(
            cfg,
            app_executable="explorer.exe",
            default_args="shell:Desktop",
        )
        assert "/app-cmd:shell:Desktop" in cmd

    def test_file_path_with_space_is_quoted_freerdp3(self, cfg, monkeypatch, tmp_path):
        """#473: a file path containing a space must reach the guest quoted,
        or the RAIL command line splits and the app gets 'path not found'."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        monkeypatch.setattr("winpodx.core.rdp.freerdp_major_version", lambda: 3)
        monkeypatch.setattr("winpodx.core.rdp.Path.home", staticmethod(lambda: tmp_path))
        f = tmp_path / "BRMP Rawa" / "01 KK.xlsx"
        f.parent.mkdir(parents=True)
        f.touch()
        cmd, _ = build_rdp_command(cfg, app_executable="excel.exe", file_path=str(f))
        assert any(',cmd:"\\\\tsclient\\home\\BRMP Rawa\\01 KK.xlsx"' in c for c in cmd)

    def test_file_path_with_space_is_quoted_freerdp2(self, cfg, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp", "xfreerdp"),
        )
        monkeypatch.setattr("winpodx.core.rdp.freerdp_major_version", lambda: 2)
        monkeypatch.setattr("winpodx.core.rdp.Path.home", staticmethod(lambda: tmp_path))
        f = tmp_path / "BRMP Rawa" / "01 KK.xlsx"
        f.parent.mkdir(parents=True)
        f.touch()
        cmd, _ = build_rdp_command(cfg, app_executable="excel.exe", file_path=str(f))
        assert '/app-cmd:"\\\\tsclient\\home\\BRMP Rawa\\01 KK.xlsx"' in cmd

    def test_dpi_flag_when_set(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.dpi = 150
        cmd, _ = build_rdp_command(cfg)
        assert "/scale-desktop:150" in cmd

    def test_dynamic_resolution_flag(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cmd, _ = build_rdp_command(cfg)
        assert "/dynamic-resolution" in cmd

    def test_dynamic_resolution_not_in_app_launch(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cmd, _ = build_rdp_command(
            cfg,
            app_executable="explorer.exe",
            default_args="shell:Desktop",
        )
        assert any(c.startswith("/app:") for c in cmd)
        assert "/dynamic-resolution" not in cmd

    def test_span_added_to_app_launch_uniform_scale(self, cfg, monkeypatch):
        # multimon defaults to "span": with uniform monitor scales a RAIL app
        # launch spans the host monitor bounding box so a window dragged to a
        # second monitor keeps input mapping (clicks would otherwise miss).
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        monkeypatch.setattr("winpodx.display.layout.has_mixed_scale", lambda: False)
        cmd, _ = build_rdp_command(cfg, app_executable="notepad.exe")
        assert "/span" in cmd
        assert "/multimon" not in cmd

    def test_span_omitted_on_mixed_scale(self, cfg, monkeypatch):
        # Different per-monitor scales -> pin to the primary monitor (no /span):
        # FreeRDP RAIL can't span mixed-scale monitors without freezing.
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        monkeypatch.setattr("winpodx.display.layout.has_mixed_scale", lambda: True)
        cmd, _ = build_rdp_command(cfg, app_executable="notepad.exe")
        assert "/span" not in cmd
        assert "/multimon" not in cmd

    def test_span_not_in_full_desktop_launch(self, cfg, monkeypatch):
        # The full-desktop path keeps /dynamic-resolution and must not span.
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cmd, _ = build_rdp_command(cfg)
        assert "/span" not in cmd

    def test_multimon_off_omits_span(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.multimon = "off"
        cmd, _ = build_rdp_command(cfg, app_executable="notepad.exe")
        assert "/span" not in cmd
        assert "/multimon" not in cmd

    def test_multimon_explicit_uses_multimon_flag(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.multimon = "multimon"
        cmd, _ = build_rdp_command(cfg, app_executable="notepad.exe")
        assert "/multimon" in cmd
        assert "/span" not in cmd

    def test_dpi_flag_omitted_when_zero(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.dpi = 0
        cmd, _ = build_rdp_command(cfg)
        assert not any(c.startswith("/scale-desktop:") for c in cmd)

    def test_no_password_with_askpass_uses_askpass_not_stdin(self, cfg, monkeypatch):
        # When the config password is empty but askpass is set, the password is
        # resolved from askpass and embedded in /p:; no /from-stdin:force.
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        monkeypatch.setattr(
            "winpodx.core.rdp._resolve_password",
            lambda _cfg: "askpass-secret",
        )
        cfg.rdp.password = ""
        cfg.rdp.askpass = "my-askpass"
        cmd, password = build_rdp_command(cfg)
        assert password == ""
        assert "/from-stdin:force" not in cmd
        assert any(c.startswith("/p:askpass-secret") for c in cmd)

    def test_domain_flag(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.domain = "WORKGROUP"
        cmd, _ = build_rdp_command(cfg)
        assert "/d:WORKGROUP" in cmd

    def test_extra_flags_filtered(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.extra_flags = "/sound:sys:alsa /exec:evil"
        cmd, _ = build_rdp_command(cfg)
        assert "/sound:sys:alsa" in cmd
        assert "/exec:evil" not in cmd

    def test_optional_codec_flags_rejected_as_bare(self, cfg, monkeypatch):
        """Regression for the v0.4.3 → #126 follow-up: FreeRDP 3.x flags
        of type ``COMMAND_LINE_VALUE_OPTIONAL`` (gfx-h264, rfx, nsc,
        jpeg, avc444) are NOT bare toggles. Including them in
        _BARE_FLAGS lets `--extra-args="-gfx-h264"` pass our filter only
        for FreeRDP itself to reject with "Unexpected keyword" —
        confusing stderr for the user. Reject at the filter so the
        misleading flag never reaches xfreerdp3."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.extra_flags = "-gfx-h264 +gfx-h264 -rfx +rfx -nsc +nsc -jpeg +jpeg -avc444 +avc444"
        cmd, _ = build_rdp_command(cfg)
        for bad in cfg.rdp.extra_flags.split():
            assert bad not in cmd, f"OPTIONAL-typed flag leaked through: {bad!r}"

    def test_codec_toggles_pass_filter(self, cfg, monkeypatch):
        """Genuine BOOL toggles still pass — wallpaper, themes, decorations,
        grab-*, async-*, auto-reconnect, *-cache. Without these in
        _BARE_FLAGS they were silently dropped before reaching xfreerdp3.

        (gfx-progressive / gfx-thin-client / gfx-small-cache were removed in
        #380 — they are `/gfx:` sub-options, not bare toggles; see
        ``test_gfx_suboptions_and_window_position``. The bare cache toggles
        -bitmap-cache / -offscreen-cache / -glyph-cache were likewise removed
        when #380 reopened — they are `/cache:<sub>:on|off` sub-options; see
        ``test_cache_suboptions``.)"""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        toggles = (
            "-wallpaper +wallpaper -themes +themes "
            "-decorations +decorations "
            "-grab-keyboard +grab-keyboard -grab-mouse +grab-mouse "
            "-mouse-relative +mouse-relative "
            "-async-update +async-update -async-channels +async-channels "
            "-auto-reconnect +auto-reconnect"
        )
        cfg.rdp.extra_flags = toggles
        cmd, _ = build_rdp_command(cfg)
        for toggle in toggles.split():
            assert toggle in cmd, f"toggle dropped by filter: {toggle!r}"

    def test_cache_suboptions(self, cfg, monkeypatch):
        """#380 (reopened, FreeRDP 3.26): cache toggles use `/cache:<sub>:on|off`,
        not bare `+/-{bitmap,offscreen,glyph}-cache`."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.extra_flags = (
            "/cache:bitmap:on /cache:offscreen:off /cache:glyph:on "
            "+bitmap-cache -glyph-cache"  # stale bare forms must be dropped
        )
        cmd, _ = build_rdp_command(cfg)
        assert "/cache:bitmap:on" in cmd
        assert "/cache:offscreen:off" in cmd
        assert "/cache:glyph:on" in cmd
        assert "+bitmap-cache" not in cmd
        assert "-glyph-cache" not in cmd

    def test_gfx_suboptions_and_window_position(self, cfg, monkeypatch):
        """#380 (notnotno, FreeRDP 3.26): the gfx sub-options and
        window-position use `/name:value` syntax, not bare `+/-` toggles.

        - `+gfx-progressive` etc. + `+/-window-position` must be REJECTED
          (they passed the old allowlist only for xfreerdp to reject them).
        - `/gfx:progressive:on|off`, `/gfx:thin-client:on`,
          `/gfx:small-cache:off`, and `/window-position:<x>x<y>` must PASS."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        bad = (
            "+gfx-progressive -gfx-progressive "
            "+gfx-thin-client -gfx-thin-client "
            "+gfx-small-cache -gfx-small-cache "
            "+window-position -window-position"
        )
        cfg.rdp.extra_flags = bad
        cmd, _ = build_rdp_command(cfg)
        for flag in bad.split():
            assert flag not in cmd, f"stale FreeRDP-2 flag leaked: {flag!r}"

        good = (
            "/gfx:progressive:on /gfx:thin-client:on /gfx:small-cache:off /window-position:100x200"
        )
        cfg.rdp.extra_flags = good
        cmd, _ = build_rdp_command(cfg)
        for flag in good.split():
            assert flag in cmd, f"correct FreeRDP-3 flag dropped: {flag!r}"

    def test_extra_args_kwarg_appended_after_global_extra_flags(self, cfg, monkeypatch):
        """Per-launch `extra_args` (CLI --extra-args / GUI per-launch)
        is appended AFTER `cfg.rdp.extra_flags` so per-launch overrides
        win when FreeRDP ties on duplicate flags. Uses the same
        allowlist filter so the per-launch path can't smuggle anything
        unsafe."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        # `aero` is not in the default command, so the only occurrences come
        # from global (+) then per-launch (-) — clean ordering check.
        cfg.rdp.extra_flags = "+aero"  # global says: enable
        cmd, _ = build_rdp_command(cfg, extra_args="-aero /not-a-real-flag:bad")

        # Both global and per-launch survived the filter.
        assert "+aero" in cmd
        assert "-aero" in cmd
        # Per-launch lands AFTER global; this is the override semantics
        # callers rely on.
        assert cmd.index("-aero") > cmd.index("+aero")
        # Unsafe flag in extra_args is dropped, same as via cfg.rdp.extra_flags.
        assert "/not-a-real-flag:bad" not in cmd

    def test_extra_args_empty_string_is_noop(self, cfg, monkeypatch):
        """Default (empty extra_args) must not append anything to the
        command. Guards against an accidental sentinel like `""` showing
        up as an empty-string arg in the FreeRDP command."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cmd_no_extra, _ = build_rdp_command(cfg)
        cmd_empty, _ = build_rdp_command(cfg, extra_args="")
        assert cmd_no_extra == cmd_empty
        assert "" not in cmd_empty

    def test_kbd_layout_flag_passes_filter(self, cfg, monkeypatch):
        """/kbd layout flag must survive the allowlist so users can work
        around FreeRDP's 'keycode 0x08 no rdp scancode found' warning and
        keyboard-layout mismatches (e.g. /kbd:layout:us)."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.extra_flags = "/kbd:layout:us"
        cmd, _ = build_rdp_command(cfg)
        assert "/kbd:layout:us" in cmd


def test_linux_to_unc_home_symlink_atomic(monkeypatch, tmp_path):
    # Fedora Atomic / Silverblue / Kinoite: /home is a symlink to /var/home.
    # Path.home() stays the symlink path; the file resolves to the target.
    # Both sides must resolve so the prefix check matches (#418).
    real_home = tmp_path / "var_home" / "me"
    real_home.mkdir(parents=True)
    home_link = tmp_path / "home" / "me"
    home_link.parent.mkdir(parents=True)
    home_link.symlink_to(real_home)  # /home/me -> /var/home/me

    doc = real_home / "Desktop" / "temp.doc"
    doc.parent.mkdir()
    doc.touch()

    monkeypatch.setattr("winpodx.core.rdp.Path.home", staticmethod(lambda: home_link))
    monkeypatch.setattr("winpodx.core.rdp._find_media_base", lambda: None)

    # File passed through the symlinked home path, as `app run` would.
    result = linux_to_unc(str(home_link / "Desktop" / "temp.doc"))
    assert result == "\\\\tsclient\\home\\Desktop\\temp.doc"


def test_linux_to_unc_symlinked_subdir_under_home(monkeypatch, tmp_path):
    # #547: a subdir under $HOME is itself a symlink pointing out of home
    # (~/Documents -> /mnt/store/Documents). The file is still reachable as
    # \\tsclient\home\Documents\... because FreeRDP serves $HOME and follows
    # symlinks within it; resolving the file path would wrongly reject it.
    home = tmp_path / "home" / "me"
    home.mkdir(parents=True)
    external = tmp_path / "mnt" / "store" / "Documents"
    external.mkdir(parents=True)
    (home / "Documents").symlink_to(external)
    doc = external / "book.xlsx"
    doc.touch()

    monkeypatch.setattr("winpodx.core.rdp.Path.home", staticmethod(lambda: home))
    monkeypatch.setattr("winpodx.core.rdp._find_media_base", lambda: None)

    result = linux_to_unc(str(home / "Documents" / "book.xlsx"))
    assert result == "\\\\tsclient\\home\\Documents\\book.xlsx"


def test_linux_to_unc_dotdot_traversal_still_blocked(monkeypatch, tmp_path):
    # '..' is collapsed lexically, so a crafted path can't escape $HOME even
    # though the file itself is no longer resolve()'d (#547 fix must not weaken
    # the containment check).
    home = tmp_path / "home" / "me"
    home.mkdir(parents=True)
    monkeypatch.setattr("winpodx.core.rdp.Path.home", staticmethod(lambda: home))
    monkeypatch.setattr("winpodx.core.rdp._find_media_base", lambda: None)

    with pytest.raises(ValueError, match="outside shared locations"):
        linux_to_unc(str(home / ".." / ".." / "etc" / "passwd"))


class TestResolveWmClass:
    """resolve_wm_class() is the single source of truth shared by FreeRDP's
    /wm-class and the .desktop StartupWMClass (taskbar window matching)."""

    def test_exe_stem_default(self):
        from winpodx.core.rdp import resolve_wm_class

        assert resolve_wm_class("C:\\Program Files\\App\\notepad.exe") == "notepad"

    def test_exe_hint_overrides_stem(self):
        from winpodx.core.rdp import resolve_wm_class

        assert resolve_wm_class("C:\\x\\app.exe", "MyApp") == "myapp"

    def test_exe_unsafe_hint_falls_back_to_stem(self):
        from winpodx.core.rdp import resolve_wm_class

        # A hint with disallowed chars must not produce an unsafe token.
        assert resolve_wm_class("C:\\x\\app.exe", "bad name!@#") == "app"

    def test_uwp_uses_aumid_slug_not_exe_stem(self):
        # Regression: a UWP AUMID's exe-stem is useless ("microsoft" from
        # "Microsoft.WindowsCalculator_...!App") and never matched the
        # StartupWMClass, so Calculator showed up unmatched in the taskbar.
        from winpodx.core.rdp import _uwp_fallback_wm_class, resolve_wm_class

        aumid = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
        got = resolve_wm_class(None, None, aumid)
        assert got == _uwp_fallback_wm_class(aumid)
        assert got != "microsoft"
        assert got.startswith("winpodx-uwp-")

    def test_uwp_valid_hint_wins(self):
        from winpodx.core.rdp import resolve_wm_class

        aumid = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
        assert resolve_wm_class(None, "calc", aumid) == "calc"

    def test_desktop_startupwmclass_matches_wm_class(self):
        # The .desktop StartupWMClass must equal what FreeRDP gets, for both a
        # UWP app and a plain exe.
        from winpodx.core.app import AppInfo
        from winpodx.core.rdp import resolve_wm_class

        uwp = AppInfo(
            name="calc",
            full_name="Calculator",
            executable="Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
            launch_uri="Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
        )
        token = resolve_wm_class(uwp.executable, uwp.wm_class_hint or None, uwp.launch_uri or None)
        assert token.startswith("winpodx-uwp-")
        assert token != "microsoft"


class _FakeRun:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.returncode = 0


def test_relist_uwp_taskbar_clears_skip_states(monkeypatch):
    import time

    import winpodx.core.rdp as rdp

    monkeypatch.setattr(rdp.shutil, "which", lambda _name: "/usr/bin/wmctrl")
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    lx = (
        "0x0140003f  0 RAIL.winpodx-uwp-foo-bar  host Calculator\n"
        "0x02000010  0 RAIL.other-app  host Notepad\n"
    )
    calls: list[list[str]] = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return _FakeRun(lx if cmd[1] == "-lx" else "")

    monkeypatch.setattr(rdp.subprocess, "run", fake_run)
    rdp._relist_uwp_taskbar("winpodx-uwp-foo-bar")

    removes = [c for c in calls if c[-1] == "remove,skip_taskbar,skip_pager"]
    assert removes, "expected a wmctrl remove for the matching RAIL window"
    # Only the matching window id is re-listed, never the unrelated RAIL app.
    assert all("0x0140003f" in c for c in removes)
    assert not any("0x02000010" in c for c in removes)


def test_relist_uwp_taskbar_noop_without_wmctrl(monkeypatch):
    import winpodx.core.rdp as rdp

    monkeypatch.setattr(rdp.shutil, "which", lambda _name: None)
    calls: list = []
    monkeypatch.setattr(rdp.subprocess, "run", lambda *a, **k: calls.append(a))
    rdp._relist_uwp_taskbar("winpodx-uwp-foo-bar")
    assert calls == []


class _FakeProc:
    returncode = 0

    def poll(self):
        return None  # alive


def _patch_launch_to_spawn(monkeypatch, tmp_path, cmd):
    """Stub launch_app's surroundings so it reaches the spawn/early-exit path."""
    from winpodx.core import rdp as rdp_mod

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(rdp_mod, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(rdp_mod, "_find_existing_session", lambda _name: None)
    monkeypatch.setattr(rdp_mod, "find_freerdp", lambda *a, **k: ("/usr/bin/xfreerdp", "xfreerdp"))
    monkeypatch.setattr(rdp_mod, "build_rdp_command", lambda *a, **k: (list(cmd), ""))
    monkeypatch.setattr(rdp_mod, "_reaper_thread", lambda _s: None)

    spawned: list[list[str]] = []

    def fake_spawn(session, spawn_cmd):
        spawned.append(list(spawn_cmd))
        session.process = _FakeProc()
        return session

    monkeypatch.setattr(rdp_mod, "_spawn_detached", fake_spawn)
    return rdp_mod, spawned


_PRECONNECT_ERR = "ERRCONNECT_PRE_CONNECT_FAILED [0x00020001]\nfreerdp_pre_connect failed"


def test_launch_app_retries_single_monitor_on_span_preconnect_fail(monkeypatch, tmp_path):
    # A spanned launch that FreeRDP rejects at pre_connect (mixed-DPI host
    # monitors) must auto-retry once with /span dropped, and succeed. With no
    # readable X-screen extent (single monitor / no xrandr) the retry is plain
    # single-monitor -- no /size added.
    rdp_mod, spawned = _patch_launch_to_spawn(
        monkeypatch, tmp_path, ["xfreerdp", "/v:127.0.0.1", "/span", "notepad"]
    )
    monkeypatch.setattr("winpodx.display.layout.detect_x_screen_extent", lambda: None)

    seen = {"n": 0}

    def fake_early(_session, **_kw):
        seen["n"] += 1
        return _PRECONNECT_ERR if seen["n"] == 1 else None  # fail once, then OK

    monkeypatch.setattr(rdp_mod, "_early_exit_stderr", fake_early)

    cfg = Config()
    cfg.pod.backend = "manual"  # skip the interactive-session wait
    session = rdp_mod.launch_app(cfg, app_executable="notepad.exe")

    assert len(spawned) == 2
    assert "/span" in spawned[0]
    assert "/span" not in spawned[1]  # retry dropped the span
    assert not any(f.startswith("/size:") for f in spawned[1])  # no extent -> single
    assert session.process is not None


def test_launch_app_retries_geometry_size_on_span_preconnect_fail(monkeypatch, tmp_path):
    # When the X-screen extent is known, the retry hands FreeRDP an explicit
    # /size desktop spanning both monitors instead of falling to single-monitor.
    rdp_mod, spawned = _patch_launch_to_spawn(
        monkeypatch, tmp_path, ["xfreerdp", "/v:127.0.0.1", "/span", "notepad"]
    )
    monkeypatch.setattr("winpodx.display.layout.detect_x_screen_extent", lambda: (5334, 1600))

    seen = {"n": 0}

    def fake_early(_session, **_kw):
        seen["n"] += 1
        return _PRECONNECT_ERR if seen["n"] == 1 else None

    monkeypatch.setattr(rdp_mod, "_early_exit_stderr", fake_early)

    cfg = Config()
    cfg.pod.backend = "manual"
    session = rdp_mod.launch_app(cfg, app_executable="notepad.exe")

    assert len(spawned) == 2
    assert "/span" not in spawned[1]
    assert "/size:5334x1600" in spawned[1]  # explicit both-monitor desktop
    assert session.process is not None


def test_launch_app_no_retry_when_no_span(monkeypatch, tmp_path):
    # The same pre_connect failure without a span flag is a real error -- no
    # retry, raise straight away.
    rdp_mod, spawned = _patch_launch_to_spawn(
        monkeypatch, tmp_path, ["xfreerdp", "/v:127.0.0.1", "notepad"]
    )
    monkeypatch.setattr(rdp_mod, "_early_exit_stderr", lambda _s, **_k: _PRECONNECT_ERR)

    cfg = Config()
    cfg.pod.backend = "manual"
    with pytest.raises(RuntimeError, match="exited immediately"):
        rdp_mod.launch_app(cfg, app_executable="notepad.exe")

    assert len(spawned) == 1  # no retry


def test_launch_app_healthy_spawn_starts_reaper_no_retry(monkeypatch, tmp_path):
    # A clean launch (no early exit) spawns once and returns the live session.
    rdp_mod, spawned = _patch_launch_to_spawn(
        monkeypatch, tmp_path, ["xfreerdp", "/v:127.0.0.1", "/span", "notepad"]
    )
    monkeypatch.setattr(rdp_mod, "_early_exit_stderr", lambda _s, **_k: None)

    cfg = Config()
    cfg.pod.backend = "manual"
    session = rdp_mod.launch_app(cfg, app_executable="notepad.exe")

    assert len(spawned) == 1
    assert "/span" in spawned[0]
    assert session.process is not None


def test_launch_app_existing_session_with_file_spawns_fresh_window(monkeypatch, tmp_path):
    # #680/#2: opening a file while the app is already running no longer attempts
    # a (futile under multi-session, ~30s) warm delivery -- it falls straight
    # through to a fresh RAIL spawn carrying the file. Prove build_rdp_command is
    # reached rather than an early return of the existing session.
    from winpodx.core import rdp as rdp_mod
    from winpodx.core.rdp import RDPSession

    live = RDPSession(app_name="winword")
    monkeypatch.setattr(rdp_mod, "_find_existing_session", lambda _name: live)
    monkeypatch.setattr(rdp_mod, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(rdp_mod, "linux_to_unc", lambda _p: "\\\\tsclient\\home\\2.docx")

    class _ReachedCold(RuntimeError):
        pass

    def _boom(*a, **k):
        raise _ReachedCold()

    monkeypatch.setattr(rdp_mod, "build_rdp_command", _boom)

    cfg = Config()
    cfg.pod.backend = "manual"
    with pytest.raises(_ReachedCold):
        rdp_mod.launch_app(
            cfg,
            app_executable="C:\\WINWORD.EXE",
            file_path=str(tmp_path / "2.docx"),
        )


def test_launch_app_existing_session_without_file_returns_existing(monkeypatch, tmp_path):
    # Without a file_path (plain re-launch), the existing session is returned
    # immediately -- no fresh spawn.
    from winpodx.core import rdp as rdp_mod
    from winpodx.core.rdp import RDPSession

    live = RDPSession(app_name="winword")
    monkeypatch.setattr(rdp_mod, "_find_existing_session", lambda _name: live)
    monkeypatch.setattr(rdp_mod, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(
        rdp_mod, "build_rdp_command", lambda *a, **k: pytest.fail("must not spawn without a file")
    )

    cfg = Config()
    cfg.pod.backend = "manual"
    result = rdp_mod.launch_app(cfg, app_executable="C:\\WINWORD.EXE", file_path=None)

    assert result is live


def test_launch_app_warm_session_unmappable_file_notifies_not_silent(monkeypatch, tmp_path):
    # #675: a dropped file outside the shared home must surface an error toast
    # (parity with the cold path) and NOT spawn a fresh window -- the warm branch
    # validates the path up front and notifies instead of falling through.
    from winpodx.core import rdp as rdp_mod
    from winpodx.core.rdp import RDPSession

    live = RDPSession(app_name="winword")
    monkeypatch.setattr(rdp_mod, "_find_existing_session", lambda _name: live)
    monkeypatch.setattr(rdp_mod, "runtime_dir", lambda: tmp_path)

    def _raise_value(_p):
        raise ValueError("Cannot open file: outside shared locations")

    monkeypatch.setattr(rdp_mod, "linux_to_unc", _raise_value)
    monkeypatch.setattr(
        rdp_mod, "build_rdp_command", lambda *a, **k: pytest.fail("should not spawn a fresh window")
    )

    notified: list[str] = []
    import winpodx.desktop.notify as notify_mod

    monkeypatch.setattr(notify_mod, "notify_error", lambda msg: notified.append(msg))

    cfg = Config()
    cfg.pod.backend = "manual"
    result = rdp_mod.launch_app(cfg, app_executable="C:\\WINWORD.EXE", file_path="/etc/passwd")

    assert result is live
    assert notified and "outside shared locations" in notified[0]


def test_launch_app_cold_path_uses_longer_interactive_timeout(monkeypatch, tmp_path):
    # #675 v2: the cold RemoteApp path waits _INTERACTIVE_WAIT_TIMEOUT (bumped
    # from 20s) for the guest session to become interactive before creating the
    # RAIL window, so a slow guest finishing autologon doesn't get a stale-logon
    # framebuffer painted over the app.
    from winpodx.core.rdp import _INTERACTIVE_WAIT_TIMEOUT

    assert _INTERACTIVE_WAIT_TIMEOUT >= 45

    rdp_mod, spawned = _patch_launch_to_spawn(
        monkeypatch, tmp_path, ["xfreerdp", "/v:127.0.0.1", "notepad"]
    )
    monkeypatch.setattr(rdp_mod, "_early_exit_stderr", lambda *a, **k: None)

    seen: list = []

    def _record_wait(cfg, *, timeout=20):
        seen.append(timeout)
        return True

    monkeypatch.setattr(rdp_mod, "_wait_session_interactive", _record_wait)

    cfg = Config()
    cfg.pod.backend = "podman"  # so the cold-path interactive wait actually fires
    rdp_mod.launch_app(cfg, app_executable="notepad.exe")

    assert seen == [_INTERACTIVE_WAIT_TIMEOUT]
    assert len(spawned) == 1


def test_spawn_detached_sets_wlog_filter_env(monkeypatch, tmp_path):
    # #680 nit: the FreeRDP subprocess runs with WLOG_FILTER raising the
    # commandline WLog tag to FATAL, silencing the cosmetic get_next_comma
    # warning at every launch -- without touching the delivered argv or the
    # process-group setup kill_session() relies on.
    from winpodx.core import rdp as rdp_mod
    from winpodx.core.rdp import RDPSession

    monkeypatch.setattr(rdp_mod, "runtime_dir", lambda: tmp_path)

    captured: dict = {}

    class _FakeProc:
        pid = 4321

    def fake_popen(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(rdp_mod.subprocess, "Popen", fake_popen)

    session = rdp_mod._spawn_detached(RDPSession(app_name="excel"), ["xfreerdp", "/v:x"])

    assert session.process is not None
    env = captured.get("env") or {}
    assert env.get("WLOG_FILTER") == "com.winpr.commandline:FATAL"
    assert captured.get("start_new_session") is True  # PGID preserved for kill_session


def test_count_rail_windows_matches_res_class(monkeypatch):
    # #680: RAIL windows are res_name RAIL + res_class == the app_name slug, so
    # `wmctrl -lx` column 3 is "RAIL.<app>". Count only exact-class matches.
    from winpodx.core import rdp as rdp_mod

    out = (
        "0x01 0 RAIL.excel host Book1\n"
        "0x02 0 RAIL.excel host Book2\n"
        "0x03 0 RAIL.winword host Doc\n"
        "0x04 0 firefox.Firefox host web\n"
    )
    monkeypatch.setattr(rdp_mod.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": out})())
    assert rdp_mod._count_rail_windows("wmctrl", "RAIL.excel") == 2
    assert rdp_mod._count_rail_windows("wmctrl", "RAIL.winword") == 1
    assert rdp_mod._count_rail_windows("wmctrl", "RAIL.none") == 0


def test_count_rail_windows_none_on_scan_error(monkeypatch):
    from winpodx.core import rdp as rdp_mod

    def _boom(*a, **k):
        raise OSError("wmctrl gone")

    monkeypatch.setattr(rdp_mod.subprocess, "run", _boom)
    assert rdp_mod._count_rail_windows("wmctrl", "RAIL.excel") is None


class _AliveProc:
    def poll(self):
        return None  # never exits on its own -- the watcher must reap it


def _fast_reaper_tuning(monkeypatch, rdp_mod):
    monkeypatch.setattr(rdp_mod, "_WINDOW_REAP_POLL", 0.001)
    monkeypatch.setattr(rdp_mod, "_WINDOW_REAP_APPEAR_TIMEOUT", 0.4)
    monkeypatch.setattr(rdp_mod, "_WINDOW_REAP_DEBOUNCE", 0.005)
    monkeypatch.setattr(rdp_mod.shutil, "which", lambda _n: "/usr/bin/wmctrl")


def test_window_reaper_reaps_after_windows_close(monkeypatch):
    # A window appears (arms the watcher), then all windows close -> after the
    # debounce the session is reaped via kill_session even though the process
    # never exited on its own (#680).
    from winpodx.core import rdp as rdp_mod
    from winpodx.core.rdp import RDPSession

    _fast_reaper_tuning(monkeypatch, rdp_mod)
    # present for the first couple scans, gone forever after.
    seq = iter([1, 1])
    monkeypatch.setattr(rdp_mod, "_count_rail_windows", lambda *a: next(seq, 0))

    killed: list[str] = []
    monkeypatch.setattr("winpodx.core.process.kill_session", lambda name: killed.append(name))

    session = RDPSession(app_name="excel")
    session.process = _AliveProc()
    rdp_mod._window_reaper(session, "excel")

    assert killed == ["excel"]


def test_window_reaper_never_reaps_if_no_window_appears(monkeypatch):
    # Safe-by-omission: an app whose RAIL window never maps is left entirely to
    # the process-reaper -- the watcher must NOT kill it.
    from winpodx.core import rdp as rdp_mod
    from winpodx.core.rdp import RDPSession

    _fast_reaper_tuning(monkeypatch, rdp_mod)
    monkeypatch.setattr(rdp_mod, "_count_rail_windows", lambda *a: 0)  # never appears

    killed: list[str] = []
    monkeypatch.setattr("winpodx.core.process.kill_session", lambda name: killed.append(name))

    session = RDPSession(app_name="excel")
    session.process = _AliveProc()
    rdp_mod._window_reaper(session, "excel")

    assert killed == []


def test_window_reaper_noop_without_wmctrl(monkeypatch):
    from winpodx.core import rdp as rdp_mod
    from winpodx.core.rdp import RDPSession

    monkeypatch.setattr(rdp_mod.shutil, "which", lambda _n: None)
    scanned: list = []
    monkeypatch.setattr(rdp_mod, "_count_rail_windows", lambda *a: scanned.append(a))
    killed: list[str] = []
    monkeypatch.setattr("winpodx.core.process.kill_session", lambda name: killed.append(name))

    session = RDPSession(app_name="excel")
    session.process = _AliveProc()
    rdp_mod._window_reaper(session, "excel")

    assert scanned == [] and killed == []


def test_session_state_probe_is_session_scoped(monkeypatch):
    # #680/#5: the LOCKED probe must compare LogonUI/explorer by SessionId so a
    # lock screen in ANOTHER session (console / stale disconnected RAIL) doesn't
    # flip an interactive app session to LOCKED. Guard the exact shape of the PS.
    from winpodx.core.rdp import _SESSION_STATE_PS

    assert "SessionId" in _SESSION_STATE_PS
    assert "-notcontains" in _SESSION_STATE_PS
    # all three states still emitted
    for state in ("'READY'", "'LOCKED'", "'NOSHELL'"):
        assert state in _SESSION_STATE_PS
    # the naive machine-wide "any LogonUI -> LOCKED" form is gone
    assert "if (Get-Process LogonUI" not in _SESSION_STATE_PS


def test_redact_cmd_for_log_masks_password():
    # The FreeRDP argv carries the Windows password as /p: (and gateway pw as
    # /gp:); the launch log must not print either in cleartext.
    from winpodx.core.rdp import _redact_cmd_for_log

    out = _redact_cmd_for_log(
        ["xfreerdp3", "/v:127.0.0.1:3390", "/u:User", "/p:s3cr3t", "/gp:gwpass", "/cert:ignore"]
    )
    assert "s3cr3t" not in out
    assert "gwpass" not in out
    assert "/p:***" in out
    assert "/gp:***" in out
    assert "/u:User" in out  # non-secret tokens are preserved
    assert "/v:127.0.0.1:3390" in out


# #660: cfg.pod.keyboard -> FreeRDP /kbd:layout propagation


class TestKeyboardLayoutPropagation:
    def test_auto_kbd_flag_default_en_us_is_none(self):
        c = Config()
        c.pod.keyboard = "en-US"
        assert _auto_kbd_flag(c) is None

    def test_auto_kbd_flag_empty_is_none(self):
        c = Config()
        c.pod.keyboard = ""
        assert _auto_kbd_flag(c) is None

    def test_auto_kbd_flag_full_culture(self):
        c = Config()
        c.pod.keyboard = "de-DE"
        assert _auto_kbd_flag(c) == "/kbd:layout:0x00000407"

    def test_auto_kbd_flag_bare_language_fallback(self):
        c = Config()
        c.pod.keyboard = "hu"
        assert _auto_kbd_flag(c) == "/kbd:layout:0x0000040e"

    def test_auto_kbd_flag_culture_prefix_fallback(self):
        # Unknown region but known language prefix -> falls back to the language.
        c = Config()
        c.pod.keyboard = "fr-BE"
        assert _auto_kbd_flag(c) == "/kbd:layout:0x0000040c"

    def test_auto_kbd_flag_unmapped_is_none(self):
        c = Config()
        c.pod.keyboard = "xx-YY"
        assert _auto_kbd_flag(c) is None

    def _cfg(self):
        c = Config()
        c.rdp.ip = "127.0.0.1"
        c.rdp.port = 3390
        c.rdp.user = "TestUser"
        c.rdp.password = "secret"
        c.rdp.extra_flags = ""
        c.pod.backend = "manual"
        return c

    def test_non_default_keyboard_appends_kbd(self, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg = self._cfg()
        cfg.pod.keyboard = "hu-HU"
        cmd, _ = build_rdp_command(cfg)
        assert "/kbd:layout:0x0000040e" in cmd

    def test_default_keyboard_does_not_append_kbd(self, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg = self._cfg()  # default en-US
        cmd, _ = build_rdp_command(cfg)
        assert not any(c.startswith("/kbd") for c in cmd)

    def test_user_extra_flags_kbd_wins(self, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg = self._cfg()
        cfg.pod.keyboard = "hu-HU"  # would auto-map to 0x040e
        cfg.rdp.extra_flags = "/kbd:layout:0x00000409"  # user forces US
        cmd, _ = build_rdp_command(cfg)
        kbd_flags = [c for c in cmd if c.startswith("/kbd")]
        assert kbd_flags == ["/kbd:layout:0x00000409"]  # only the user's, no auto
