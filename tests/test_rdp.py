# SPDX-License-Identifier: MIT
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

    with pytest.raises(RuntimeError, match="XWayland"):
        rdp_mod.launch_app(Config(), app_executable="notepad.exe")


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
    from winpodx.core.rdp import find_freerdp

    _patch_freerdp_probes(monkeypatch, native=True, flatpak=True)
    assert find_freerdp("auto") == ("flatpak run com.freerdp.FreeRDP", "flatpak")


def test_find_freerdp_auto_falls_back_to_native(monkeypatch):
    from winpodx.core.rdp import find_freerdp

    _patch_freerdp_probes(monkeypatch, native=True, flatpak=False)
    assert find_freerdp("auto") == ("/usr/bin/xfreerdp3", "xfreerdp")


def test_find_freerdp_native_forced_ignores_flatpak(monkeypatch):
    from winpodx.core.rdp import find_freerdp

    _patch_freerdp_probes(monkeypatch, native=True, flatpak=True)
    assert find_freerdp("native") == ("/usr/bin/xfreerdp3", "xfreerdp")


def test_find_freerdp_native_forced_falls_back_to_flatpak(monkeypatch):
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

    def test_dpi_flag_omitted_when_zero(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.dpi = 0
        cmd, _ = build_rdp_command(cfg)
        assert not any(c.startswith("/scale-desktop:") for c in cmd)

    def test_no_password_no_stdin(self, cfg, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.password = ""
        cmd, password = build_rdp_command(cfg)
        assert password == ""
        assert "/from-stdin:force" not in cmd

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
        """Genuine BOOL toggles still pass — gfx-progressive,
        gfx-thin-client, gfx-small-cache, wallpaper, themes, decorations,
        grab-*, async-*, auto-reconnect, *-cache. Without these in
        _BARE_FLAGS they were silently dropped before reaching
        xfreerdp3."""
        monkeypatch.setattr(
            "winpodx.core.rdp.find_freerdp",
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        # FreeRDP 3.x cmdline split:
        #   - BOOL flags (`+/-name`): the genuine bare toggles below.
        #   - OPTIONAL/REQUIRED (`/name:value` only): rfx, gfx-h264, nsc,
        #     jpeg, avc444 — these were attempted as bare in the original
        #     v0.4.3 patch and FreeRDP itself rejected them with
        #     "Unexpected keyword" (xiyeming's 2026-05-06/07 test in
        #     #126). Stripped from the allowlist to surface the parse
        #     failure at the filter layer instead of producing a confusing
        #     stderr from xfreerdp3.
        toggles = (
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
            lambda *a, **k: ("/usr/bin/xfreerdp3", "xfreerdp"),
        )
        cfg.rdp.extra_flags = "+gfx-progressive"  # global says: enable
        cmd, _ = build_rdp_command(cfg, extra_args="-gfx-progressive /not-a-real-flag:bad")

        # Both global and per-launch survived the filter.
        assert "+gfx-progressive" in cmd
        assert "-gfx-progressive" in cmd
        # Per-launch lands AFTER global; this is the override semantics
        # callers rely on.
        assert cmd.index("-gfx-progressive") > cmd.index("+gfx-progressive")
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
