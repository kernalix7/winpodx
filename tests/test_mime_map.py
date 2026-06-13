# SPDX-License-Identifier: MIT
"""System-derived extension → MIME auto-association (#545)."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

import winpodx.core.mime_map as mime_map
from winpodx.core.discovery import (
    DiscoveredApp,
    _backfill_mime_types,
    _entry_to_discovered,
    _render_app_toml,
)
from winpodx.core.mime_map import mime_for_extension, mimes_for_extensions


def _use_globs(monkeypatch, tmp_path, content: str, name: str = "globs"):
    """Point the MIME db at a temp globs file and reset the cache."""
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    monkeypatch.setattr(mime_map, "_GLOBS_FILES", (str(f),))
    mime_map._ext_mime_db.cache_clear()


_GLOBS = (
    "# comment line\n"
    "application/msword:*.doc\n"
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document:*.docx\n"
    "text/plain:*.txt\n"
    "image/jpeg:*.jpg\n"
    "image/jpeg:*.jpeg\n"
    "application/x-something:*.*\n"  # wildcard pattern must be ignored
)

_GLOBS2 = (
    "50:application/pdf:*.pdf\n"
    "60:text/markdown:*.md\n"
    "80:image/png:*.png:cs\n"  # trailing flag field tolerated
)


def test_parses_plain_globs(monkeypatch, tmp_path):
    _use_globs(monkeypatch, tmp_path, _GLOBS)
    assert mime_for_extension(".doc") == "application/msword"
    assert mime_for_extension("docx").endswith("wordprocessingml.document")  # leading dot optional
    assert mime_for_extension(".txt") == "text/plain"
    # the "*.*" wildcard line must not leak in
    assert mime_for_extension(".x") != "application/x-something"


def test_parses_globs2_weighted(monkeypatch, tmp_path):
    _use_globs(monkeypatch, tmp_path, _GLOBS2, name="globs2")
    assert mime_for_extension(".pdf") == "application/pdf"
    assert mime_for_extension(".md") == "text/markdown"
    assert mime_for_extension(".png") == "image/png"  # trailing :cs ignored


def test_falls_back_to_mimetypes_when_db_missing(monkeypatch, tmp_path):
    # No globs file → empty db → Python mimetypes covers common types.
    monkeypatch.setattr(mime_map, "_GLOBS_FILES", (str(tmp_path / "nope"),))
    mime_map._ext_mime_db.cache_clear()
    assert mime_for_extension(".txt") == "text/plain"
    assert mime_for_extension(".zzqq") is None


def test_mimes_for_extensions_dedupes(monkeypatch, tmp_path):
    _use_globs(monkeypatch, tmp_path, _GLOBS)
    out = mimes_for_extensions([".jpg", ".jpeg", ".doc", ".zzz"])
    assert out == ["image/jpeg", "application/msword"]  # jpeg deduped, zzz skipped


def test_render_app_toml_maps_extensions(monkeypatch, tmp_path):
    _use_globs(monkeypatch, tmp_path, _GLOBS)
    app = DiscoveredApp(name="word", full_name="Word", executable="C:\\w.exe", extensions=[".docx"])
    data = tomllib.loads(_render_app_toml(app, mime_enabled=True))
    assert data["mime_types"] == [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]
    # disabled -> empty regardless of extensions
    assert tomllib.loads(_render_app_toml(app, mime_enabled=False))["mime_types"] == []


def test_backfill_fills_and_respects_toggle(monkeypatch, tmp_path):
    _use_globs(monkeypatch, tmp_path, _GLOBS)
    app = DiscoveredApp(name="word", full_name="Word", executable="C:\\w.exe", extensions=[".doc"])

    p = tmp_path / "on.toml"
    p.write_text('name = "word"\nmime_types = []\n', encoding="utf-8")
    _backfill_mime_types(p, app, mime_enabled=True)
    assert tomllib.loads(p.read_text(encoding="utf-8"))["mime_types"] == ["application/msword"]

    p2 = tmp_path / "off.toml"
    p2.write_text('name = "word"\nmime_types = []\n', encoding="utf-8")
    _backfill_mime_types(p2, app, mime_enabled=False)
    assert tomllib.loads(p2.read_text(encoding="utf-8"))["mime_types"] == []


def test_entry_to_discovered_sanitizes_extensions():
    entry = {
        "name": "Word",
        "path": "C:\\w.exe",
        "extensions": [".DOCX", "xlsx", ".bad ext", "../evil", ".pdf"],
    }
    app = _entry_to_discovered(entry)
    assert app is not None
    assert app.extensions == [".docx", ".xlsx", ".pdf"]  # normalised; junk dropped
