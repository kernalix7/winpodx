"""Tests for desktop entry, MIME, and icon handling."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from winpodx.core.app import AppInfo
from winpodx.desktop import entry as entry_mod
from winpodx.desktop.entry import DESKTOP_TEMPLATE, install_desktop_entry
from winpodx.desktop.icons import bundled_data_path
from winpodx.desktop.mime import unregister_mime_types


def test_desktop_template():
    app = AppInfo(
        name="word",
        full_name="Microsoft Word",
        executable="C:\\Program Files\\Office\\WINWORD.EXE",
        categories=["Office", "WordProcessor"],
        mime_types=["application/msword"],
    )

    content = DESKTOP_TEMPLATE.format(
        full_name=app.full_name,
        name=app.name,
        icon_name=f"winpodx-{app.name}",
        categories=";".join(app.categories) + ";",
        mime_types=";".join(app.mime_types) + ";",
        wm_class="winword",
    )

    assert "Name=Microsoft Word" in content
    assert "Exec=winpodx app run word %F" in content
    assert "Icon=winpodx-word" in content
    assert "Categories=Office;WordProcessor;" in content
    assert "MimeType=application/msword;" in content
    assert "StartupWMClass=winword" in content


# ─────────────────────────────────────────────────────────────────────────────
# D1: unregister_mime_types must not destroy mimeapps.list structure
# ─────────────────────────────────────────────────────────────────────────────


def _write_mimeapps(tmp_path: Path, content: str) -> Path:
    """Create a fake $XDG_CONFIG_HOME/mimeapps.list at tmp_path/mimeapps.list.

    ``unregister_mime_types`` reads ``config_dir().parent / 'mimeapps.list'``
    which with ``XDG_CONFIG_HOME=<tmp_path>`` resolves to
    ``<tmp_path>/mimeapps.list`` (config_dir() is ``<tmp_path>/winpodx``).
    """
    mimeapps = tmp_path / "mimeapps.list"
    mimeapps.write_text(content, encoding="utf-8")
    return mimeapps


def test_unregister_mime_preserves_other_apps(tmp_path, monkeypatch):
    # D1: removing winpodx entry must keep sibling desktop files intact.
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

    # text/plain MUST still map to gedit — the whole point of D1
    assert parser.get("Default Applications", "text/plain") == "gedit.desktop;"
    # Unrelated entries untouched
    assert parser.get("Default Applications", "application/pdf") == "evince.desktop;"
    # Other winpodx-* entries elsewhere in the file also untouched
    assert (
        parser.get("Default Applications", "image/png")
        == "gimp.desktop;winpodx-paint.desktop;eog.desktop;"
    )
    # Multi-section: [Added Associations] text/plain keeps kate
    assert parser.get("Added Associations", "text/plain") == "kate.desktop;"


def test_unregister_mime_drops_empty_keys(tmp_path, monkeypatch):
    # D1: when winpodx was the ONLY value, the key is removed (not left empty).
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
    # D1: write goes through tempfile + os.replace — no partial file left behind.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    _write_mimeapps(
        tmp_path,
        "[Default Applications]\ntext/plain=winpodx-notepad.desktop;gedit.desktop;\n",
    )

    app = AppInfo(name="notepad", full_name="Notepad", executable="C:\\notepad.exe")
    unregister_mime_types(app)

    # Only mimeapps.list should remain — no leftover .tmp files
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "mimeapps.list"]
    assert leftovers == [], f"stray tempfiles: {leftovers}"


# ─────────────────────────────────────────────────────────────────────────────
# D2: install_desktop_entry must write UTF-8 explicitly
# ─────────────────────────────────────────────────────────────────────────────


def test_install_desktop_entry_utf8_korean(tmp_path, monkeypatch):
    # D2: non-ASCII full_name must round-trip even under C/POSIX locale.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    app = AppInfo(
        name="hangul",
        full_name="\ud55c\uae00 \uba54\ubaa8\uc7a5",  # "한글 메모장"
        executable="C:\\notepad.exe",
        categories=["Utility"],
        mime_types=["text/plain"],
    )

    # Stub icon installer so we don't touch the real icon theme.
    monkeypatch.setattr(entry_mod, "_install_icon", lambda _app: "winpodx")

    desktop_path = install_desktop_entry(app)
    assert desktop_path.exists()

    # Read back as UTF-8: the Korean name must survive round-trip.
    content = desktop_path.read_text(encoding="utf-8")
    assert "Name=\ud55c\uae00 \uba54\ubaa8\uc7a5" in content
    assert "Exec=winpodx app run hangul %F" in content

    # Binary-level check: no mojibake / replacement bytes written.
    raw = desktop_path.read_bytes()
    assert "\ud55c\uae00 \uba54\ubaa8\uc7a5".encode("utf-8") in raw


def test_install_desktop_entry_utf8_japanese(tmp_path, monkeypatch):
    # D2: Japanese full_name should also persist as UTF-8.
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


# ─────────────────────────────────────────────────────────────────────────────
# D4: bundled_data_path resolves icon from source/wheel/user data dirs
# ─────────────────────────────────────────────────────────────────────────────


def test_bundled_data_path_source_layout():
    # D4: in source / editable install, data/winpodx-icon.svg must be found.
    path = bundled_data_path("winpodx-icon.svg")
    assert path is not None, "icon must be discoverable in source layout"
    assert path.exists()
    assert path.name == "winpodx-icon.svg"


def test_bundled_data_path_missing_returns_none(monkeypatch, tmp_path):
    # D4: when all candidate locations miss, returns None (no exception).
    # Point sys.prefix and HOME at empty dirs, and pretend the source layout
    # doesn't contain the file by asking for a nonexistent name.
    monkeypatch.setattr("sys.prefix", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    result = bundled_data_path("does-not-exist-" + "x" * 20 + ".svg")
    assert result is None


def test_bundled_data_path_falls_back_to_sys_prefix(monkeypatch, tmp_path):
    # D4: if source layout misses, sys.prefix/share/winpodx/data is searched.
    # Simulates the pip-wheel install scenario where the package lives in
    # site-packages and `Path(__file__).parent**4 / 'data'` points somewhere
    # that doesn't contain our icon. The wheel ships `share/winpodx/data/`
    # relative to ``sys.prefix``.
    # Create a fake prefix with the expected shared-data layout
    prefix = tmp_path / "prefix"
    share = prefix / "share" / "winpodx" / "data"
    share.mkdir(parents=True)
    fake_icon = share / "fake-wheel-asset.svg"
    fake_icon.write_text("<svg/>", encoding="utf-8")

    monkeypatch.setattr("sys.prefix", str(prefix))
    # Make sure ~/.local/share fallback doesn't accidentally hit.
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))

    # Must not resolve from source layout (that file isn't in repo data/),
    # must find it under sys.prefix instead.
    resolved = bundled_data_path("fake-wheel-asset.svg")
    assert resolved == fake_icon


# ─────────────────────────────────────────────────────────────────────────────
# Audit Issue 12: update_icon_cache must enforce timeout
# ─────────────────────────────────────────────────────────────────────────────


def test_update_icon_cache_gtk_timeout(monkeypatch, tmp_path, caplog):
    # Issue 12: gtk-update-icon-cache hang must be bounded by timeout=30.
    import logging
    import subprocess

    from winpodx.desktop import icons as icons_mod

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    seen_timeouts: list[int | None] = []

    def fake_run(cmd, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        if cmd[0] == "gtk-update-icon-cache":
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))
        # xdg-icon-resource, kbuildsycoca — return success
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(icons_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(icons_mod.shutil, "which", lambda _c: None)

    with caplog.at_level(logging.WARNING, logger="winpodx.desktop.icons"):
        icons_mod.update_icon_cache()  # must not hang, must not raise

    # gtk-update-icon-cache was invoked with a finite timeout
    assert 30 in seen_timeouts, f"expected timeout=30 in calls, got {seen_timeouts}"
    # And the timeout path logged a warning rather than silently swallowing
    assert any("timed out" in rec.message for rec in caplog.records)


def test_update_icon_cache_xdg_timeout(monkeypatch, tmp_path):
    # Issue 12: xdg-icon-resource forceupdate is also bounded.
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


# ─────────────────────────────────────────────────────────────────────────────
# Audit Issue 13: notify-send must enforce timeout
# ─────────────────────────────────────────────────────────────────────────────


def test_notify_send_has_timeout(monkeypatch):
    # Issue 13: send_notification must pass timeout= to subprocess.run.
    import subprocess

    from winpodx.desktop import notify as notify_mod

    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(notify_mod.subprocess, "run", fake_run)

    notify_mod.send_notification("Title", "Body")

    assert captured["cmd"][0] == "notify-send"
    assert captured["timeout"] == 5


def test_notify_send_timeout_swallowed(monkeypatch, caplog):
    # Issue 13: TimeoutExpired must not propagate out of send_notification.
    import logging
    import subprocess

    from winpodx.desktop import notify as notify_mod

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(notify_mod.subprocess, "run", fake_run)

    with caplog.at_level(logging.DEBUG, logger="winpodx.desktop.notify"):
        notify_mod.send_notification("t", "b")  # must not raise

    assert any("timed out" in rec.message for rec in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# Audit Issue 14: remove_desktop_entry must clean scalable/apps/
# ─────────────────────────────────────────────────────────────────────────────


def test_remove_desktop_entry_cleans_scalable_apps(tmp_path, monkeypatch):
    # Issue 14: icons installed to scalable/apps/ were left behind on remove.
    from winpodx.desktop.entry import remove_desktop_entry

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    scalable = tmp_path / "icons" / "hicolor" / "scalable" / "apps"
    scalable.mkdir(parents=True)
    svg = scalable / "winpodx-notepad.svg"
    svg.write_text("<svg/>", encoding="utf-8")

    # Also place a PNG in a sized dir to prove the old sweep still runs
    sized = tmp_path / "icons" / "hicolor" / "48x48" / "apps"
    sized.mkdir(parents=True)
    png = sized / "winpodx-notepad.png"
    png.write_bytes(b"\x89PNG")

    # And a desktop file in applications/
    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "winpodx-notepad.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")

    remove_desktop_entry("notepad")

    assert not svg.exists(), "scalable/apps/ SVG should be removed"
    assert not png.exists(), "sized PNG should also be removed"
    assert not (apps / "winpodx-notepad.desktop").exists()


# ─────────────────────────────────────────────────────────────────────────────
# Audit Issue 16: non-SVG icons must not land in scalable/apps/
# ─────────────────────────────────────────────────────────────────────────────


def test_install_icon_rejects_non_svg(tmp_path, monkeypatch, caplog):
    # Issue 16: .ico in scalable/apps/ is ignored by icon cache; fall back.
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
    # Nothing copied into scalable/apps/
    scalable = tmp_path / "icons" / "hicolor" / "scalable" / "apps"
    assert not scalable.exists() or not any(scalable.iterdir())


def test_install_icon_accepts_svg(tmp_path, monkeypatch):
    # Issue 16 regression guard: SVG icons still install normally.
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


# ─────────────────────────────────────────────────────────────────────────────
# Audit Issue 18: bundled_data_path must refuse symlink escapes
# ─────────────────────────────────────────────────────────────────────────────


def test_bundled_data_path_rejects_symlink_escape(tmp_path, monkeypatch):
    # Issue 18: a symlink pointing outside the data dir must not be returned.
    # Real-world exploit: attacker writes
    # ~/.local/share/winpodx/data/winpodx-icon.svg as a symlink to
    # /etc/shadow (or ~/.ssh/id_rsa). Without path validation,
    # shutil.copy2 would then copy the target's contents into the user's
    # hicolor icon directory, leaking secrets.
    from winpodx.desktop import icons as icons_mod

    # Secret target outside the data directory
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")

    # Fake user-install data dir with a malicious symlink
    home = tmp_path / "home"
    data = home / ".local" / "share" / "winpodx" / "data"
    data.mkdir(parents=True)
    malicious = data / "winpodx-icon.svg"
    malicious.symlink_to(secret)

    # Point all candidate bases away from the real repo so the user-install
    # dir is the only hit.
    empty_prefix = tmp_path / "empty-prefix"
    empty_prefix.mkdir()
    monkeypatch.setattr("sys.prefix", str(empty_prefix))
    monkeypatch.setenv("HOME", str(home))
    # Also blind the source-layout candidate by patching __file__ base.
    monkeypatch.setattr(
        icons_mod,
        "__file__",
        str(empty_prefix / "unused" / "a" / "b" / "c.py"),
    )

    result = icons_mod.bundled_data_path("winpodx-icon.svg")
    assert result is None, "symlink escape must be rejected, got %r" % (result,)


def test_install_winpodx_icon_refuses_symlink_source(tmp_path, monkeypatch):
    # Issue 18: install_winpodx_icon must refuse symlink sources defensively.
    from winpodx.desktop import icons as icons_mod

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

    secret = tmp_path / "secret.key"
    secret.write_text("SECRET", encoding="utf-8")
    symlink = tmp_path / "symlink.svg"
    symlink.symlink_to(secret)

    # Bypass bundled_data_path guard to test the defense-in-depth layer.
    monkeypatch.setattr(icons_mod, "bundled_data_path", lambda *_p: symlink)

    ok = icons_mod.install_winpodx_icon()
    assert ok is False

    dest = tmp_path / "share" / "icons" / "hicolor" / "scalable" / "apps" / "winpodx.svg"
    assert not dest.exists(), "must not copy symlink target contents"


def test_bundled_data_path_accepts_regular_file(tmp_path, monkeypatch):
    # Issue 18 regression: ordinary files in the data dir still work.
    from winpodx.desktop import icons as icons_mod

    home = tmp_path / "home"
    data = home / ".local" / "share" / "winpodx" / "data"
    data.mkdir(parents=True)
    (data / "winpodx-icon.svg").write_text("<svg/>", encoding="utf-8")

    empty_prefix = tmp_path / "empty-prefix"
    empty_prefix.mkdir()
    monkeypatch.setattr("sys.prefix", str(empty_prefix))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        icons_mod,
        "__file__",
        str(empty_prefix / "unused" / "a" / "b" / "c.py"),
    )

    result = icons_mod.bundled_data_path("winpodx-icon.svg")
    assert result is not None
    assert result.name == "winpodx-icon.svg"


# ─────────────────────────────────────────────────────────────────────────────
# Audit Issue 20: save_app_profile must write UTF-8
# ─────────────────────────────────────────────────────────────────────────────


def test_save_app_profile_utf8_korean(tmp_path, monkeypatch):
    # Issue 20: TOML with Korean full_name must save under C locale.
    # GUI module imports PySide6 at module load — skip cleanly if unavailable.
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


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
