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
    """D1: removing winpodx entry must keep sibling desktop files intact."""
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
    """D1: when winpodx was the ONLY value, the key is removed (not left empty)."""
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
    """No mimeapps.list → no crash, no file created."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    app = AppInfo(name="ghost", full_name="Ghost", executable="C:\\ghost.exe")
    unregister_mime_types(app)  # must not raise
    assert not (tmp_path / "mimeapps.list").exists()


def test_unregister_mime_atomic_write(tmp_path, monkeypatch):
    """D1: write goes through tempfile + os.replace — no partial file left behind."""
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
    """D2: non-ASCII full_name must round-trip even under C/POSIX locale."""
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
    """D2: Japanese full_name should also persist as UTF-8."""
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
    """D4: in source / editable install, data/winpodx-icon.svg must be found."""
    path = bundled_data_path("winpodx-icon.svg")
    assert path is not None, "icon must be discoverable in source layout"
    assert path.exists()
    assert path.name == "winpodx-icon.svg"


def test_bundled_data_path_missing_returns_none(monkeypatch, tmp_path):
    """D4: when all candidate locations miss, returns None (no exception)."""
    # Point sys.prefix and HOME at empty dirs, and pretend the source layout
    # doesn't contain the file by asking for a nonexistent name.
    monkeypatch.setattr("sys.prefix", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    result = bundled_data_path("does-not-exist-" + "x" * 20 + ".svg")
    assert result is None


def test_bundled_data_path_falls_back_to_sys_prefix(monkeypatch, tmp_path):
    """D4: if source layout misses, sys.prefix/share/winpodx/data is searched.

    Simulates the pip-wheel install scenario where the package lives in
    site-packages and `Path(__file__).parent**4 / 'data'` points somewhere
    that doesn't contain our icon. The wheel ships `share/winpodx/data/`
    relative to ``sys.prefix``.
    """
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


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
