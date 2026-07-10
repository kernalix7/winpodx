# SPDX-License-Identifier: MIT
"""Tests for desktop entry, MIME, and icon handling."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from winpodx.core.app import AppInfo
from winpodx.desktop import entry as entry_mod
from winpodx.desktop.entry import DESKTOP_TEMPLATE, install_desktop_entry
from winpodx.desktop.icons import bundled_data_path
from winpodx.desktop.mime import register_mime_types, unregister_mime_types


def test_desktop_template():
    app = AppInfo(
        name="word",
        full_name="Microsoft Word",
        executable="C:\\Program Files\\Office\\WINWORD.EXE",
        categories=["Office", "WordProcessor"],
        mime_types=["application/msword"],
    )

    content = DESKTOP_TEMPLATE.format(
        winpodx_exe="winpodx",
        full_name=app.full_name,
        name=app.name,
        comment="Word processor",
        icon_name=f"winpodx-{app.name}",
        categories=";".join(app.categories) + ";",
        mime_types=";".join(app.mime_types) + ";",
        wm_class="winword",
        folder_line="",
    )

    assert "Name=Microsoft Word" in content
    assert "Comment=Word processor" in content
    assert "Exec=winpodx app run word %u" in content
    assert "Icon=winpodx-word" in content
    assert "Categories=Office;WordProcessor;" in content
    assert "MimeType=application/msword;" in content
    assert "StartupWMClass=winword" in content


def test_register_mime_excludes_http_https_from_default_grab(tmp_path, monkeypatch):
    """Security: a discovered (semi-trusted) guest app must never seize the host
    http/https default handler. register_mime_types runs `xdg-mime default` for
    file mimes + mailto/vendor schemes, but NOT for http/https (#421/#694
    trust-boundary hardening)."""
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()
    monkeypatch.setattr("winpodx.desktop.mime.applications_dir", lambda: apps_dir)
    (apps_dir / "winpodx-edge.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")

    registered: list[str] = []

    class _R:
        returncode = 0
        stderr = ""

    def _fake_run(argv, **kw):
        # argv = ["xdg-mime", "default", "<desktop>", "<mime>"]
        registered.append(argv[-1])
        return _R()

    monkeypatch.setattr("winpodx.desktop.mime.subprocess.run", _fake_run)

    app = AppInfo(
        name="edge",
        full_name="Microsoft Edge",
        executable="C:\\edge.exe",
        mime_types=["text/html"],
        url_schemes=["http", "https", "mailto", "webcal"],
    )
    register_mime_types(app)

    assert "x-scheme-handler/http" not in registered
    assert "x-scheme-handler/https" not in registered
    # mailto + vendor schemes still auto-default (the #421 use case); file mimes too.
    assert "x-scheme-handler/mailto" in registered
    assert "x-scheme-handler/webcal" in registered
    assert "text/html" in registered


# D1: unregister_mime_types must not destroy mimeapps.list structure


def _write_mimeapps(tmp_path: Path, content: str) -> Path:
    """Create a fake $XDG_CONFIG_HOME/mimeapps.list at tmp_path/mimeapps.list."""
    mimeapps = tmp_path / "mimeapps.list"
    mimeapps.write_text(content, encoding="utf-8")
    return mimeapps


def test_unregister_mime_preserves_other_apps(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mimeapps = _write_mimeapps(
        tmp_path,
        "[Default Applications]\n"
        "text/plain=gedit.desktop;winpodx-notepad.desktop;\n"
        "application/pdf=evince.desktop;\n"
        "image/png=gimp.desktop;winpodx-paint.desktop;eog.desktop;\n"
        "\n"
        "[Added Associations]\n"
        "text/plain=winpodx-notepad.desktop;kate.desktop;\n",
    )

    app = AppInfo(name="notepad", full_name="Notepad", executable="C:\\notepad.exe")
    unregister_mime_types(app)

    parser = configparser.RawConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(mimeapps, encoding="utf-8")

    assert parser.get("Default Applications", "text/plain") == "gedit.desktop;"
    assert parser.get("Default Applications", "application/pdf") == "evince.desktop;"
    assert (
        parser.get("Default Applications", "image/png")
        == "gimp.desktop;winpodx-paint.desktop;eog.desktop;"
    )
    assert parser.get("Added Associations", "text/plain") == "kate.desktop;"


def test_unregister_mime_drops_empty_keys(tmp_path, monkeypatch):
    # When winpodx was the only value, the key is removed (not left empty).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    mimeapps = _write_mimeapps(
        tmp_path,
        "[Default Applications]\n"
        "application/x-foo=winpodx-foo.desktop;\n"
        "text/plain=gedit.desktop;\n",
    )

    app = AppInfo(name="foo", full_name="Foo", executable="C:\\foo.exe")
    unregister_mime_types(app)

    parser = configparser.RawConfigParser(strict=False, delimiters=("=",))
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(mimeapps, encoding="utf-8")

    assert not parser.has_option("Default Applications", "application/x-foo")
    assert parser.get("Default Applications", "text/plain") == "gedit.desktop;"


def test_unregister_mime_noop_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = AppInfo(name="ghost", full_name="Ghost", executable="C:\\ghost.exe")
    unregister_mime_types(app)  # must not raise
    assert not (tmp_path / "mimeapps.list").exists()


def test_unregister_mime_atomic_write(tmp_path, monkeypatch):
    # Write goes through tempfile + os.replace, no partial file left behind.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    _write_mimeapps(
        tmp_path,
        "[Default Applications]\ntext/plain=winpodx-notepad.desktop;gedit.desktop;\n",
    )

    app = AppInfo(name="notepad", full_name="Notepad", executable="C:\\notepad.exe")
    unregister_mime_types(app)

    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "mimeapps.list"]
    assert leftovers == [], f"stray tempfiles: {leftovers}"


# D2: install_desktop_entry must write UTF-8 explicitly


def test_install_desktop_entry_utf8_korean(tmp_path, monkeypatch):
    # Non-ASCII full_name must round-trip under C/POSIX locale.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    app = AppInfo(
        name="hangul",
        full_name="\ud55c\uae00 \uba54\ubaa8\uc7a5",  # "한글 메모장"
        executable="C:\\notepad.exe",
        categories=["Utility"],
        mime_types=["text/plain"],
    )

    monkeypatch.setattr(entry_mod, "_install_icon", lambda _app: "winpodx")
    # Pin the Exec prefix so the assertion is independent of whether winpodx is
    # resolvable on the test runner's PATH (install_desktop_entry now embeds the
    # absolute path from shutil.which).
    monkeypatch.setattr(entry_mod, "_winpodx_exe", lambda: "winpodx")

    desktop_path = install_desktop_entry(app)
    assert desktop_path.exists()

    content = desktop_path.read_text(encoding="utf-8")
    assert "Name=\ud55c\uae00 \uba54\ubaa8\uc7a5" in content
    assert "Exec=winpodx app run hangul %u" in content

    raw = desktop_path.read_bytes()
    assert "\ud55c\uae00 \uba54\ubaa8\uc7a5".encode("utf-8") in raw


def test_desktop_entry_emits_scheme_handler(tmp_path, monkeypatch):
    # #421/#694: url_schemes become x-scheme-handler/<scheme> MIME entries
    # alongside the file MIME types.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(entry_mod, "_install_icon", lambda _app: "winpodx")
    monkeypatch.setattr(entry_mod, "_winpodx_exe", lambda: "winpodx")

    app = AppInfo(
        name="outlook",
        full_name="Outlook",
        executable="C:\\o.exe",
        mime_types=["application/pdf"],
        url_schemes=["mailto", "slack"],
    )
    content = install_desktop_entry(app).read_text(encoding="utf-8")
    mime_line = next(ln for ln in content.splitlines() if ln.startswith("MimeType="))
    assert "application/pdf" in mime_line
    assert "x-scheme-handler/mailto" in mime_line
    assert "x-scheme-handler/slack" in mime_line


def test_scheme_only_app_triggers_db_update(tmp_path, monkeypatch):
    # An app with URL schemes but NO file MIME types must still rebuild the
    # desktop database (the old gate only checked app.mime_types).
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(entry_mod, "_install_icon", lambda _app: "winpodx")
    monkeypatch.setattr(entry_mod, "_winpodx_exe", lambda: "winpodx")
    called: list = []
    monkeypatch.setattr(entry_mod, "update_desktop_database", lambda: called.append(1))

    app = AppInfo(name="mailer", full_name="Mailer", executable="C:\\m.exe", url_schemes=["mailto"])
    install_desktop_entry(app)
    assert called == [1]


def test_install_desktop_entry_utf8_japanese(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    app = AppInfo(
        name="notepad-jp",
        full_name="\u30e1\u30e2\u5e33",  # "メモ帳"
        executable="C:\\notepad.exe",
    )
    monkeypatch.setattr(entry_mod, "_install_icon", lambda _app: "winpodx")

    desktop_path = install_desktop_entry(app)
    content = desktop_path.read_text(encoding="utf-8")
    assert "Name=\u30e1\u30e2\u5e33" in content


def test_install_desktop_entry_strips_newlines_in_full_name(tmp_path, monkeypatch):
    # A hostile/compromised guest could embed a newline in the discovered app
    # name to inject arbitrary .desktop keys (Exec=, Hidden=, ...) into the
    # launcher spec via Name=. The newline must be collapsed so no extra
    # key line is produced.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    app = AppInfo(
        name="evil",
        full_name='Word\nExec=/bin/sh -c "touch /tmp/pwned"',
        executable="C:\\word.exe",
    )
    monkeypatch.setattr(entry_mod, "_install_icon", lambda _app: "winpodx")
    monkeypatch.setattr(entry_mod, "_winpodx_exe", lambda: "winpodx")

    desktop_path = install_desktop_entry(app)
    content = desktop_path.read_text(encoding="utf-8")

    # The only Exec= line is winpodx's own; the injected one must be gone.
    exec_lines = [ln for ln in content.splitlines() if ln.startswith("Exec=")]
    assert exec_lines == ["Exec=winpodx app run evil %u"]
    # Name= stays on a single line with the newline collapsed to a space.
    name_lines = [ln for ln in content.splitlines() if ln.startswith("Name=")]
    assert len(name_lines) == 1
    assert "touch /tmp/pwned" in name_lines[0]  # payload neutralised into the name text


# D4: bundled_data_path resolves icon from source/wheel/user data dirs


def test_bundled_data_path_source_layout():
    path = bundled_data_path("winpodx-icon.svg")
    assert path is not None, "icon must be discoverable in source layout"
    assert path.exists()
    assert path.name == "winpodx-icon.svg"


def test_bundled_data_path_missing_returns_none():
    assert bundled_data_path("does-not-exist-" + "x" * 20 + ".svg") is None


# Audit Issue 12: update_icon_cache must enforce timeout


def test_update_icon_cache_gtk_timeout(monkeypatch, tmp_path, caplog):
    # gtk-update-icon-cache hang must be bounded by timeout=30.
    import logging
    import subprocess

    from winpodx.desktop import icons as icons_mod

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    seen_timeouts: list[int | None] = []

    def fake_run(cmd, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        if cmd[0] == "gtk-update-icon-cache":
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(icons_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(icons_mod.shutil, "which", lambda _c: None)

    with caplog.at_level(logging.WARNING, logger="winpodx.desktop.icons"):
        icons_mod.update_icon_cache()

    assert 30 in seen_timeouts, f"expected timeout=30 in calls, got {seen_timeouts}"
    assert any("timed out" in rec.message for rec in caplog.records)


def test_update_icon_cache_xdg_timeout(monkeypatch, tmp_path):
    # xdg-icon-resource forceupdate is also bounded.
    import subprocess

    from winpodx.desktop import icons as icons_mod

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    observed_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        observed_cmds.append(list(cmd))
        assert kwargs.get("timeout") is not None, f"missing timeout for {cmd[0]}"
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(icons_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(icons_mod.shutil, "which", lambda _c: None)

    icons_mod.update_icon_cache()

    called = [c[0] for c in observed_cmds]
    assert "xdg-icon-resource" in called


# Audit Issue 13: notify-send must enforce timeout


def test_notify_send_has_timeout(monkeypatch):
    # send_notification must pass timeout= to subprocess.run.
    import subprocess

    from winpodx.desktop import notify as notify_mod

    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(notify_mod.subprocess, "run", fake_run)

    notify_mod.send_notification("Title", "Body")

    # cmd[0] is now the resolved absolute path (shutil.which) so it survives a
    # stripped-PATH transient unit (#675) — bare name when which() returns None.
    assert captured["cmd"][0] == "notify-send" or captured["cmd"][0].endswith("/notify-send")
    assert captured["timeout"] == 5


def test_notify_send_timeout_swallowed(monkeypatch, caplog):
    # TimeoutExpired must not propagate out of send_notification.
    import logging
    import subprocess

    from winpodx.desktop import notify as notify_mod

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(notify_mod.subprocess, "run", fake_run)

    with caplog.at_level(logging.DEBUG, logger="winpodx.desktop.notify"):
        notify_mod.send_notification("t", "b")

    assert any("timed out" in rec.message for rec in caplog.records)


# Audit Issue 14: remove_desktop_entry must clean scalable/apps/


def test_remove_desktop_entry_cleans_scalable_apps(tmp_path, monkeypatch):
    # Icons installed to scalable/apps/ must be removed.
    from winpodx.desktop.entry import remove_desktop_entry

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    scalable = tmp_path / "icons" / "hicolor" / "scalable" / "apps"
    scalable.mkdir(parents=True)
    svg = scalable / "winpodx-notepad.svg"
    svg.write_text("<svg/>", encoding="utf-8")

    sized = tmp_path / "icons" / "hicolor" / "48x48" / "apps"
    sized.mkdir(parents=True)
    png = sized / "winpodx-notepad.png"
    png.write_bytes(b"\x89PNG")

    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "winpodx-notepad.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")

    remove_desktop_entry("notepad")

    assert not svg.exists(), "scalable/apps/ SVG should be removed"
    assert not png.exists(), "sized PNG should also be removed"
    assert not (apps / "winpodx-notepad.desktop").exists()


# Audit Issue 16: non-SVG icons must not land in scalable/apps/


def test_install_icon_rejects_non_svg(tmp_path, monkeypatch, caplog):
    # .ico in scalable/apps/ is ignored by icon cache; must fall back.
    import logging

    from winpodx.desktop import entry as entry_mod

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    ico = tmp_path / "foo.ico"
    ico.write_bytes(b"\x00\x00\x01\x00")

    app = AppInfo(
        name="foo",
        full_name="Foo",
        executable="C:\\foo.exe",
        icon_path=str(ico),
    )

    with caplog.at_level(logging.WARNING, logger="winpodx.desktop.entry"):
        result = entry_mod._install_icon(app)

    assert result == "winpodx", "non-SVG should fall back to default icon"
    assert any("not SVG" in rec.message for rec in caplog.records)
    scalable = tmp_path / "icons" / "hicolor" / "scalable" / "apps"
    assert not scalable.exists() or not any(scalable.iterdir())


def test_install_icon_accepts_svg(tmp_path, monkeypatch):
    from winpodx.desktop import entry as entry_mod

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    svg = tmp_path / "foo.svg"
    svg.write_text("<svg/>", encoding="utf-8")

    app = AppInfo(
        name="foo",
        full_name="Foo",
        executable="C:\\foo.exe",
        icon_path=str(svg),
    )

    result = entry_mod._install_icon(app)

    assert result == "winpodx-foo"
    dest = tmp_path / "icons" / "hicolor" / "scalable" / "apps" / "winpodx-foo.svg"
    assert dest.exists()


# Audit Issue 18: bundled_data_path must refuse symlink escapes


def test_bundled_data_path_rejects_symlink_escape(tmp_path, monkeypatch):
    # A symlink pointing outside the data dir must not be returned.
    from winpodx.desktop import icons as icons_mod

    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")

    data = tmp_path / "bundle" / "data"
    data.mkdir(parents=True)
    (data / "winpodx-icon.svg").symlink_to(secret)

    monkeypatch.setattr(icons_mod, "bundle_dir", lambda: tmp_path / "bundle")

    result = icons_mod.bundled_data_path("winpodx-icon.svg")
    assert result is None, "symlink escape must be rejected, got %r" % (result,)


def test_install_winpodx_icon_refuses_symlink_source(tmp_path, monkeypatch):
    # install_winpodx_icon must refuse symlink sources.
    from winpodx.desktop import icons as icons_mod

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    secret = tmp_path / "secret.key"
    secret.write_text("SECRET", encoding="utf-8")
    symlink = tmp_path / "symlink.svg"
    symlink.symlink_to(secret)

    monkeypatch.setattr(icons_mod, "bundled_data_path", lambda *_p: symlink)

    ok = icons_mod.install_winpodx_icon()
    assert ok is False

    dest = tmp_path / "share" / "icons" / "hicolor" / "scalable" / "apps" / "winpodx.svg"
    assert not dest.exists(), "must not copy symlink target contents"


class TestInstallGuiLauncherDesktop:
    """G7 (#255): `winpodx setup` registers the GUI launcher .desktop file."""

    def test_copies_bundled_desktop_to_user_applications(self, tmp_path, monkeypatch):
        from winpodx.desktop import icons as icons_mod

        # Sandbox HOME and bundle a fake .desktop file.
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        bundled = tmp_path / "bundle" / "winpodx.desktop"
        bundled.parent.mkdir(parents=True)
        bundled.write_text("[Desktop Entry]\nExec=winpodx gui\n", encoding="utf-8")
        monkeypatch.setattr(icons_mod, "bundled_data_path", lambda *_p: bundled)
        # No system copy on this sandbox.
        monkeypatch.setattr(
            "pathlib.Path.is_file",
            lambda self: (
                str(self) != "/usr/share/applications/winpodx.desktop"
                and Path.__dict__["is_file"].__wrapped__(self)
                if False
                else (str(self) != "/usr/share/applications/winpodx.desktop" and self.exists())
            ),
        )

        result = icons_mod.install_gui_launcher_desktop()

        assert result is True
        dest = tmp_path / ".local" / "share" / "applications" / "winpodx.desktop"
        assert dest.is_file()
        assert "Exec=winpodx gui" in dest.read_text()

    def test_skips_when_system_copy_exists(self, tmp_path, monkeypatch):
        from winpodx.desktop import icons as icons_mod

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Pretend /usr/share/applications/winpodx.desktop exists.
        monkeypatch.setattr(
            "pathlib.Path.is_file",
            lambda self: str(self) == "/usr/share/applications/winpodx.desktop",
        )

        result = icons_mod.install_gui_launcher_desktop()

        assert result is False
        dest = tmp_path / ".local" / "share" / "applications" / "winpodx.desktop"
        assert not dest.exists()

    def test_refuses_symlink_source(self, tmp_path, monkeypatch):
        from winpodx.desktop import icons as icons_mod

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        secret = tmp_path / "secret.key"
        secret.write_text("SECRET", encoding="utf-8")
        symlink = tmp_path / "winpodx.desktop"
        symlink.symlink_to(secret)
        monkeypatch.setattr(icons_mod, "bundled_data_path", lambda *_p: symlink)
        # No system copy.
        monkeypatch.setattr(
            "pathlib.Path.is_file",
            lambda self: (
                False
                if str(self) == "/usr/share/applications/winpodx.desktop"
                else self.exists() and not self.is_symlink()
            ),
        )

        result = icons_mod.install_gui_launcher_desktop()

        assert result is False
        dest = tmp_path / ".local" / "share" / "applications" / "winpodx.desktop"
        assert not dest.exists()


def test_bundled_data_path_accepts_regular_file(tmp_path, monkeypatch):
    # Regression: ordinary files in the data dir still work.
    from winpodx.desktop import icons as icons_mod

    data = tmp_path / "bundle" / "data"
    data.mkdir(parents=True)
    (data / "winpodx-icon.svg").write_text("<svg/>", encoding="utf-8")

    monkeypatch.setattr(icons_mod, "bundle_dir", lambda: tmp_path / "bundle")

    result = icons_mod.bundled_data_path("winpodx-icon.svg")
    assert result is not None
    assert result.name == "winpodx-icon.svg"


# Audit Issue 20: save_app_profile must write UTF-8


def test_save_app_profile_utf8_korean(tmp_path, monkeypatch):
    # TOML with Korean full_name must save under C locale.
    pytest.importorskip("PySide6")

    from winpodx.gui.app_dialog import save_app_profile

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    data = {
        "name": "hangul",
        "full_name": "\ud55c\uae00 \uba54\ubaa8\uc7a5",
        "executable": "C:\\notepad.exe",
        "categories": [],
        "mime_types": [],
    }

    toml_path = save_app_profile(data)
    assert toml_path.exists()

    content = toml_path.read_text(encoding="utf-8")
    assert "\ud55c\uae00 \uba54\ubaa8\uc7a5" in content
    raw = toml_path.read_bytes()
    assert "\ud55c\uae00 \uba54\ubaa8\uc7a5".encode("utf-8") in raw


def test_install_desktop_entry_uses_app_description(tmp_path, monkeypatch):
    """When AppInfo.description is set, the .desktop Comment= line should
    use it instead of the generic 'Windows application via winpodx' stamp."""
    monkeypatch.setattr("winpodx.desktop.entry.applications_dir", lambda: tmp_path)
    monkeypatch.setattr("winpodx.desktop.entry.icons_dir", lambda: tmp_path / "icons")
    app = AppInfo(
        name="edge",
        full_name="Microsoft Edge",
        executable="C:\\Program Files\\Edge\\msedge.exe",
        description="Browse the web with Microsoft Edge",
    )
    desktop_path = install_desktop_entry(app)
    raw = desktop_path.read_text(encoding="utf-8")
    assert "Comment=Browse the web with Microsoft Edge" in raw
    assert "Comment=Windows application via WinPodX" not in raw


def test_install_desktop_entry_falls_back_when_description_blank(tmp_path, monkeypatch):
    """Apps without a discovered description still get the generic stamp."""
    monkeypatch.setattr("winpodx.desktop.entry.applications_dir", lambda: tmp_path)
    monkeypatch.setattr("winpodx.desktop.entry.icons_dir", lambda: tmp_path / "icons")
    app = AppInfo(
        name="legacy",
        full_name="Legacy App",
        executable="C:\\legacy.exe",
        # description omitted intentionally
    )
    desktop_path = install_desktop_entry(app)
    raw = desktop_path.read_text(encoding="utf-8")
    assert "Comment=Windows application via WinPodX" in raw


def test_install_desktop_entry_strips_newlines_in_description(tmp_path, monkeypatch):
    """Multi-line / tab-laden descriptions from the guest must not
    corrupt later .desktop keys (each key is line-terminated)."""
    monkeypatch.setattr("winpodx.desktop.entry.applications_dir", lambda: tmp_path)
    monkeypatch.setattr("winpodx.desktop.entry.icons_dir", lambda: tmp_path / "icons")
    monkeypatch.setattr(entry_mod, "_winpodx_exe", lambda: "winpodx")
    app = AppInfo(
        name="messy",
        full_name="Messy App",
        executable="C:\\messy.exe",
        description="Line one\nLine two\twith tab\rcarriage",
    )
    desktop_path = install_desktop_entry(app)
    raw = desktop_path.read_text(encoding="utf-8")
    comment_line = next(line for line in raw.splitlines() if line.startswith("Comment="))
    assert "\n" not in comment_line
    assert "\t" not in comment_line
    assert "\r" not in comment_line
    # Following keys (Exec, Icon, …) must still be intact.
    assert "Exec=winpodx app run messy %u" in raw


def test_install_desktop_entry_uses_absolute_exec_path(tmp_path, monkeypatch):
    # Desktop environments that launch apps as systemd transient units (e.g.
    # Deepin's dde-application-manager) run with a stripped PATH that doesn't
    # include ~/.local/bin, so a bare ``Exec=winpodx`` fails with
    # "exec: winpodx: not found". install_desktop_entry must embed the absolute
    # path resolved at install time.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(entry_mod, "_install_icon", lambda _app: "winpodx")
    monkeypatch.setattr(entry_mod.shutil, "which", lambda name: f"/home/u/.local/bin/{name}")

    app = AppInfo(
        name="notepad",
        full_name="Notepad",
        executable="C:\\notepad.exe",
    )
    desktop_path = install_desktop_entry(app)
    content = desktop_path.read_text(encoding="utf-8")
    assert "Exec=/home/u/.local/bin/winpodx app run notepad %u" in content


def test_winpodx_exe_falls_back_to_bare_name(monkeypatch):
    # shutil.which returns None when winpodx isn't on PATH (e.g. running from a
    # checkout); the bare name keeps the .desktop entry valid for PATH-based
    # launchers rather than emitting ``Exec=None``.
    monkeypatch.setattr(entry_mod.shutil, "which", lambda _name: None)
    assert entry_mod._winpodx_exe() == "winpodx"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])


# Menu consolidation: all winpodx apps land under one "winpodx" folder
# (Wine-style), via a custom X-winpodx category + a .directory/.menu fragment.


def _menu_paths(root):
    """(directory_file, menu_fragment) under an XDG root used as both DATA+CONFIG."""
    directory = root / "desktop-directories" / "winpodx-windows.directory"
    fragment = root / "menus" / "applications-merged" / "winpodx.menu"
    return directory, fragment


def test_install_entry_consolidates_category(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = AppInfo(
        name="word",
        full_name="Microsoft Word",
        executable="C:\\Office\\WINWORD.EXE",
        categories=["Office", "WordProcessor"],
    )

    desktop_path = install_desktop_entry(app)
    content = desktop_path.read_text(encoding="utf-8")

    # Natural categories are replaced by the single grouping category, so the
    # app shows ONLY in the winpodx folder, not scattered across Office/etc.
    assert "Categories=X-winpodx;" in content
    assert "Office" not in content.split("Categories=")[1].splitlines()[0]
    # Search still finds it.
    assert "winpodx;" in content  # Keywords line


def test_install_entry_creates_menu_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = AppInfo(name="notepad", full_name="Notepad", executable="C:\\notepad.exe")

    install_desktop_entry(app)

    directory, fragment = _menu_paths(tmp_path)
    assert directory.exists()
    assert "Type=Directory" in directory.read_text(encoding="utf-8")
    assert fragment.exists()
    frag = fragment.read_text(encoding="utf-8")
    assert "<Category>X-winpodx</Category>" in frag
    assert "winpodx-windows.directory" in frag


def test_remove_last_app_tears_down_menu_folder(tmp_path, monkeypatch):
    from winpodx.desktop.entry import remove_desktop_entry

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    a = AppInfo(name="word", full_name="Word", executable="C:\\word.exe")
    b = AppInfo(name="excel", full_name="Excel", executable="C:\\excel.exe")
    install_desktop_entry(a)
    install_desktop_entry(b)

    directory, fragment = _menu_paths(tmp_path)
    assert directory.exists() and fragment.exists()

    # Removing one of two apps keeps the folder.
    remove_desktop_entry("word")
    assert directory.exists() and fragment.exists()

    # Removing the last app tears it down.
    remove_desktop_entry("excel")
    assert not directory.exists()
    assert not fragment.exists()


# #581 Goal 2: Start Menu folder hierarchy mirrored into nested winpodx submenus


def test_foldered_app_carries_leaf_category_and_folder_key(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = AppInfo(
        name="word",
        full_name="Microsoft Word",
        executable="C:\\Office\\WINWORD.EXE",
        start_menu_folder="Microsoft Office/Tools",
    )
    content = install_desktop_entry(app).read_text(encoding="utf-8")
    # Leaf category only -- not the bare root -- so it lands in the nested node.
    cat_line = next(ln for ln in content.splitlines() if ln.startswith("Categories="))
    assert cat_line == "Categories=X-winpodx-microsoft-office-tools;"
    assert "X-Winpodx-Folder=Microsoft Office/Tools" in content


def test_top_level_app_keeps_root_category_no_folder_key(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = AppInfo(name="notepad", full_name="Notepad", executable="C:\\notepad.exe")
    content = install_desktop_entry(app).read_text(encoding="utf-8")
    assert "Categories=X-winpodx;" in content
    assert "X-Winpodx-Folder=" not in content


def test_nested_menu_tree_and_directory_files(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    install_desktop_entry(
        AppInfo(
            name="word",
            full_name="Word",
            executable="C:\\w.exe",
            start_menu_folder="Microsoft Office",
        )
    )
    install_desktop_entry(
        AppInfo(
            name="solver",
            full_name="Solver",
            executable="C:\\s.exe",
            start_menu_folder="Microsoft Office/Tools",
        )
    )

    _, fragment = _menu_paths(tmp_path)
    frag = fragment.read_text(encoding="utf-8")
    # Nested: Tools submenu lives under Microsoft Office.
    assert "<Category>X-winpodx-microsoft-office</Category>" in frag
    assert "<Category>X-winpodx-microsoft-office-tools</Category>" in frag
    office_idx = frag.index("microsoft-office.directory")
    tools_idx = frag.index("microsoft-office-tools.directory")
    assert office_idx < tools_idx  # parent declared before child

    dirs = tmp_path / "desktop-directories"
    assert (dirs / "winpodx-folder-microsoft-office.directory").exists()
    tools_dir = (dirs / "winpodx-folder-microsoft-office-tools.directory").read_text(
        encoding="utf-8"
    )
    assert "Name=Tools" in tools_dir  # display name = leaf component, not slug


def test_ampersand_folder_does_not_break_menu_xml(tmp_path, monkeypatch):
    # A folder like "Games & Stuff" must not emit a raw & into the .menu XML;
    # the node name/category use the slug, the display name lives in .directory.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    install_desktop_entry(
        AppInfo(
            name="game",
            full_name="Game",
            executable="C:\\g.exe",
            start_menu_folder="Games & Stuff",
        )
    )
    _, fragment = _menu_paths(tmp_path)
    frag = fragment.read_text(encoding="utf-8")
    assert "Games & Stuff" not in frag  # raw & never enters the XML
    assert "<Category>X-winpodx-games-stuff</Category>" in frag
    dirs = tmp_path / "desktop-directories"
    disp = (dirs / "winpodx-folder-games-stuff.directory").read_text(encoding="utf-8")
    assert "Name=Games & Stuff" in disp  # literal in the Desktop Entry .directory


def test_emptied_subfolder_directory_is_pruned(tmp_path, monkeypatch):
    from winpodx.desktop.entry import remove_desktop_entry

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    install_desktop_entry(AppInfo(name="top", full_name="Top", executable="C:\\t.exe"))
    install_desktop_entry(
        AppInfo(
            name="word",
            full_name="Word",
            executable="C:\\w.exe",
            start_menu_folder="Microsoft Office",
        )
    )
    dirs = tmp_path / "desktop-directories"
    office_dir = dirs / "winpodx-folder-microsoft-office.directory"
    assert office_dir.exists()

    # Removing the only app in "Microsoft Office" prunes its .directory while the
    # top-level app keeps the root folder alive.
    remove_desktop_entry("word")
    assert not office_dir.exists()
    assert (dirs / "winpodx-windows.directory").exists()


def test_top_level_windows_folder_does_not_clobber_root(tmp_path, monkeypatch):
    # A Start Menu folder named exactly "Windows" slugs to "windows"; its
    # per-folder .directory must be namespaced (winpodx-folder-windows...) so it
    # can't overwrite the root winpodx-windows.directory label/icon.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    install_desktop_entry(
        AppInfo(
            name="wt",
            full_name="Windows Terminal",
            executable="C:\\wt.exe",
            start_menu_folder="Windows",
        )
    )
    dirs = tmp_path / "desktop-directories"
    root = (dirs / "winpodx-windows.directory").read_text(encoding="utf-8")
    assert "Name=WinPodX (Windows Apps)" in root
    assert "Icon=winpodx" in root
    # The "Windows" subfolder lives under the namespaced name with its own label.
    sub = (dirs / "winpodx-folder-windows.directory").read_text(encoding="utf-8")
    assert "Name=Windows" in sub
