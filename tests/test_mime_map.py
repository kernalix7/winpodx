# SPDX-License-Identifier: MIT
"""Guest-extension → MIME auto-association (#545)."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from winpodx.core.discovery import (
    DiscoveredApp,
    _backfill_mime_types,
    _entry_to_discovered,
    _render_app_toml,
)
from winpodx.core.mime_map import mime_for_extension, mimes_for_extensions


def test_mime_for_extension():
    assert mime_for_extension(".docx").endswith("wordprocessingml.document")
    assert mime_for_extension("xlsx").endswith("spreadsheetml.sheet")  # leading dot optional
    assert mime_for_extension(".pdf") == "application/pdf"
    assert mime_for_extension(".png") == "image/png"
    assert mime_for_extension(".unknownext") is None


def test_mimes_for_extensions_dedupes_and_skips_unknown():
    out = mimes_for_extensions([".jpg", ".jpeg", ".docx", ".zzz"])
    assert out == [
        "image/jpeg",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]


def test_render_app_toml_maps_extensions_when_enabled():
    app = DiscoveredApp(
        name="word", full_name="Word", executable="C:\\w.exe", extensions=[".doc", ".docx"]
    )
    data = tomllib.loads(_render_app_toml(app, mime_enabled=True))
    assert "application/msword" in data["mime_types"]


def test_render_app_toml_empty_when_disabled():
    app = DiscoveredApp(name="word", full_name="Word", executable="C:\\w.exe", extensions=[".docx"])
    data = tomllib.loads(_render_app_toml(app, mime_enabled=False))
    assert data["mime_types"] == []


def test_backfill_fills_from_extensions(tmp_path):
    p = tmp_path / "app.toml"
    p.write_text('name = "word"\nmime_types = []\n', encoding="utf-8")
    app = DiscoveredApp(name="word", full_name="Word", executable="C:\\w.exe", extensions=[".docx"])
    _backfill_mime_types(p, app, mime_enabled=True)
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["mime_types"] == [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]


def test_backfill_noop_when_disabled(tmp_path):
    p = tmp_path / "app.toml"
    p.write_text('name = "word"\nmime_types = []\n', encoding="utf-8")
    app = DiscoveredApp(name="word", full_name="Word", executable="C:\\w.exe", extensions=[".docx"])
    _backfill_mime_types(p, app, mime_enabled=False)
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["mime_types"] == []


def test_backfill_does_not_clobber_existing(tmp_path):
    p = tmp_path / "app.toml"
    p.write_text('name = "word"\nmime_types = ["text/custom"]\n', encoding="utf-8")
    app = DiscoveredApp(name="word", full_name="Word", executable="C:\\w.exe", extensions=[".docx"])
    _backfill_mime_types(p, app, mime_enabled=True)
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["mime_types"] == ["text/custom"]


def test_entry_to_discovered_parses_and_sanitizes_extensions():
    entry = {
        "name": "Word",
        "path": "C:\\w.exe",
        "extensions": [".DOCX", "xlsx", ".bad ext", "../evil", ".pdf"],
    }
    app = _entry_to_discovered(entry)
    assert app is not None
    # normalised to .ext lowercase; junk ('.bad ext', '../evil') dropped
    assert app.extensions == [".docx", ".xlsx", ".pdf"]
