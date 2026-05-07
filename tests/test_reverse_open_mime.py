"""Tests for ``winpodx.reverse_open.mime`` (#48 phase 1).

Covers the curated table, the defensive contract (never raises,
returns ``[]`` on bad input), case + whitespace normalisation, the
ambiguous-types decision (first-only for ``mime_to_extensions``,
all for ``mime_to_all_extensions``), and the graceful degradation
when pyxdg isn't installed.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from winpodx.reverse_open import mime as mime_mod
from winpodx.reverse_open.mime import (
    CURATED_MIME_EXT,
    mime_to_all_extensions,
    mime_to_extensions,
)

# ---------------------------------------------------------------------------
# Curated table — happy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mime,expected",
    [
        ("text/plain", [".txt"]),
        ("text/xml", [".xml"]),
        ("text/css", [".css"]),
        ("text/csv", [".csv"]),
        ("text/markdown", [".md"]),
        ("application/json", [".json"]),
        ("application/pdf", [".pdf"]),
        ("application/zip", [".zip"]),
        ("application/x-tar", [".tar"]),
        ("application/gzip", [".gz"]),
        ("application/x-7z-compressed", [".7z"]),
        ("application/rtf", [".rtf"]),
        ("application/msword", [".doc"]),
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            [".docx"],
        ),
        ("application/vnd.ms-excel", [".xls"]),
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            [".xlsx"],
        ),
        ("application/vnd.ms-powerpoint", [".ppt"]),
        (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            [".pptx"],
        ),
        ("application/javascript", [".js"]),
        ("application/x-yaml", [".yaml"]),
        ("application/toml", [".toml"]),
        ("image/png", [".png"]),
        ("image/jpeg", [".jpg"]),
        ("image/gif", [".gif"]),
        ("image/bmp", [".bmp"]),
        ("image/webp", [".webp"]),
        ("image/svg+xml", [".svg"]),
        ("image/tiff", [".tiff"]),
        ("image/x-icon", [".ico"]),
        ("audio/mpeg", [".mp3"]),
        ("audio/wav", [".wav"]),
        ("audio/flac", [".flac"]),
        ("audio/x-m4a", [".m4a"]),
        ("video/mp4", [".mp4"]),
        ("video/x-matroska", [".mkv"]),
        ("video/webm", [".webm"]),
        ("video/quicktime", [".mov"]),
        ("video/x-msvideo", [".avi"]),
        ("font/ttf", [".ttf"]),
        ("font/otf", [".otf"]),
        ("font/woff", [".woff"]),
        ("font/woff2", [".woff2"]),
    ],
)
def test_curated_happy_path(mime, expected):
    assert mime_to_extensions(mime) == expected


def test_curated_table_minimum_size():
    """The design budget is 'top ~80 unambiguous mappings'.

    Don't tie this exactly to 80 (additions are fine), but guard
    the floor so a future cleanup doesn't accidentally gut the table.
    """
    assert len(CURATED_MIME_EXT) >= 60


def test_curated_extensions_all_lowercase_with_dot():
    """Canonical form is leading-dot lowercase: '.txt', not 'txt' or '.TXT'."""
    for mime, exts in CURATED_MIME_EXT.items():
        assert exts, f"{mime!r} has no extensions"
        for ext in exts:
            assert ext.startswith("."), f"{mime}: {ext!r} missing leading dot"
            assert ext == ext.lower(), f"{mime}: {ext!r} not lowercase"


# ---------------------------------------------------------------------------
# Ambiguous-types decision (design #1)
# ---------------------------------------------------------------------------


def test_ambiguous_returns_first_extension_only():
    """text/html → ['.html'] (not ['.html', '.htm'])."""
    assert mime_to_extensions("text/html") == [".html"]
    # image/jpeg is similarly multi-extension in the curated table.
    assert mime_to_extensions("image/jpeg") == [".jpg"]


def test_all_extensions_returns_full_set():
    """mime_to_all_extensions surfaces every known extension."""
    assert mime_to_all_extensions("text/html") == [".html", ".htm"]
    assert mime_to_all_extensions("image/jpeg") == [".jpg", ".jpeg"]
    assert mime_to_all_extensions("image/tiff") == [".tiff", ".tif"]


def test_all_extensions_returns_copy_not_reference():
    """Mutating the returned list must not corrupt the curated table."""
    result = mime_to_all_extensions("text/html")
    result.append(".bogus")
    # Re-fetch and confirm CURATED_MIME_EXT is intact.
    assert mime_to_all_extensions("text/html") == [".html", ".htm"]
    assert CURATED_MIME_EXT["text/html"] == [".html", ".htm"]


# ---------------------------------------------------------------------------
# Defensive contract — never raises, returns []
# ---------------------------------------------------------------------------


def test_unknown_mime_returns_empty_list():
    assert mime_to_extensions("application/x-totally-made-up-by-the-test") == []
    assert mime_to_all_extensions("application/x-totally-made-up-by-the-test") == []


def test_empty_string_returns_empty_list():
    assert mime_to_extensions("") == []
    assert mime_to_all_extensions("") == []


def test_whitespace_only_returns_empty_list():
    assert mime_to_extensions("   ") == []
    assert mime_to_all_extensions("\t\n ") == []


@pytest.mark.parametrize("bad_input", [None, 42, [], {}, object(), 3.14, b"text/plain"])
def test_non_string_input_returns_empty_list(bad_input):
    """Defensive: a malformed .desktop MimeType= field shouldn't crash refresh."""
    assert mime_to_extensions(bad_input) == []  # type: ignore[arg-type]
    assert mime_to_all_extensions(bad_input) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Case + whitespace normalisation
# ---------------------------------------------------------------------------


def test_case_insensitive_lookup():
    assert mime_to_extensions("TEXT/PLAIN") == [".txt"]
    assert mime_to_extensions("Text/Plain") == [".txt"]
    assert mime_to_extensions("APPLICATION/PDF") == [".pdf"]
    assert mime_to_all_extensions("TEXT/HTML") == [".html", ".htm"]


def test_leading_trailing_whitespace_stripped():
    assert mime_to_extensions("  text/plain  ") == [".txt"]
    assert mime_to_extensions("\ttext/plain\n") == [".txt"]
    assert mime_to_all_extensions("  text/html\n") == [".html", ".htm"]


def test_combined_whitespace_and_case():
    assert mime_to_extensions("  TEXT/Plain  ") == [".txt"]


# ---------------------------------------------------------------------------
# pyxdg soft-dep behaviour
# ---------------------------------------------------------------------------


def test_works_without_xdg_module(monkeypatch):
    """Simulate pyxdg not installed; non-curated lookups must still
    not raise. Either the stdlib XML fallback finds it (on a
    typical Linux dev box) or we return [].
    """
    # Reset the import-tried sentinel and force the import to fail.
    monkeypatch.setattr(mime_mod, "_XDG_IMPORT_TRIED", False)
    monkeypatch.setattr(mime_mod, "_XDG_MIME_MODULE", None)
    monkeypatch.setitem(sys.modules, "xdg", None)  # forces ImportError

    # Curated still works regardless of pyxdg state.
    assert mime_to_extensions("text/plain") == [".txt"]

    # Unknown still returns []; never raises.
    assert mime_to_extensions("application/x-totally-fake") == []


def test_xdg_import_attempted_only_once(monkeypatch):
    """The 'pyxdg not installed' INFO log fires once per process."""
    monkeypatch.setattr(mime_mod, "_XDG_IMPORT_TRIED", False)
    monkeypatch.setattr(mime_mod, "_XDG_MIME_MODULE", None)
    monkeypatch.setitem(sys.modules, "xdg", None)

    # First call attempts import.
    mime_mod._try_import_xdg()
    assert mime_mod._XDG_IMPORT_TRIED is True

    # Second call is a no-op; the sentinel short-circuits.
    # We can't easily count log calls, but we can verify the flag
    # stays True and the function still returns the cached None.
    assert mime_mod._try_import_xdg() is None
    assert mime_mod._XDG_IMPORT_TRIED is True


# ---------------------------------------------------------------------------
# stdlib XML fallback — synthetic database
# ---------------------------------------------------------------------------


def test_stdlib_xml_fallback_finds_unknown_type(monkeypatch, tmp_path):
    """Drop a synthetic freedesktop.org.xml in place and confirm
    the stdlib parser path picks up a non-curated type.
    """
    fake_db = tmp_path / "freedesktop.org.xml"
    fake_db.write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0" encoding="utf-8"?>
            <mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
              <mime-type type="application/x-winpodx-fake">
                <glob pattern="*.wpx"/>
                <glob pattern="*.winpodx"/>
              </mime-type>
            </mime-info>
            """
        )
    )
    monkeypatch.setattr(mime_mod, "_FDO_MIME_XML", Path(fake_db))
    # Ensure pyxdg path is bypassed.
    monkeypatch.setattr(mime_mod, "_XDG_IMPORT_TRIED", True)
    monkeypatch.setattr(mime_mod, "_XDG_MIME_MODULE", None)

    assert mime_to_extensions("application/x-winpodx-fake") == [".wpx"]
    assert mime_to_all_extensions("application/x-winpodx-fake") == [".wpx", ".winpodx"]


def test_stdlib_xml_fallback_skips_complex_globs(monkeypatch, tmp_path):
    """Multi-dot patterns (*.tar.gz) and literal filenames (Makefile)
    don't map cleanly to a Windows extension — they must be skipped.
    """
    fake_db = tmp_path / "freedesktop.org.xml"
    fake_db.write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0" encoding="utf-8"?>
            <mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
              <mime-type type="application/x-winpodx-multidot">
                <glob pattern="*.tar.gz"/>
                <glob pattern="Makefile"/>
                <glob pattern="*.wpd2"/>
              </mime-type>
            </mime-info>
            """
        )
    )
    monkeypatch.setattr(mime_mod, "_FDO_MIME_XML", Path(fake_db))
    monkeypatch.setattr(mime_mod, "_XDG_IMPORT_TRIED", True)
    monkeypatch.setattr(mime_mod, "_XDG_MIME_MODULE", None)

    # Only .wpd2 survives the filter.
    assert mime_to_all_extensions("application/x-winpodx-multidot") == [".wpd2"]
    assert mime_to_extensions("application/x-winpodx-multidot") == [".wpd2"]


def test_stdlib_xml_fallback_handles_aliases(monkeypatch, tmp_path):
    """An alias should resolve to the canonical type's globs."""
    fake_db = tmp_path / "freedesktop.org.xml"
    fake_db.write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0" encoding="utf-8"?>
            <mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
              <mime-type type="application/x-winpodx-canonical">
                <alias type="application/x-winpodx-old"/>
                <glob pattern="*.wpc"/>
              </mime-type>
            </mime-info>
            """
        )
    )
    monkeypatch.setattr(mime_mod, "_FDO_MIME_XML", Path(fake_db))
    monkeypatch.setattr(mime_mod, "_XDG_IMPORT_TRIED", True)
    monkeypatch.setattr(mime_mod, "_XDG_MIME_MODULE", None)

    # Canonical resolves directly.
    assert mime_to_extensions("application/x-winpodx-canonical") == [".wpc"]
    # Alias resolves via the alias-walk in the parser.
    assert mime_to_extensions("application/x-winpodx-old") == [".wpc"]


def test_stdlib_xml_fallback_missing_db_returns_empty(monkeypatch, tmp_path):
    """If the freedesktop.org.xml file doesn't exist, lookups for
    non-curated types return [] — they must not raise.
    """
    nonexistent = tmp_path / "nope-not-here.xml"
    monkeypatch.setattr(mime_mod, "_FDO_MIME_XML", Path(nonexistent))
    monkeypatch.setattr(mime_mod, "_XDG_IMPORT_TRIED", True)
    monkeypatch.setattr(mime_mod, "_XDG_MIME_MODULE", None)

    assert mime_to_extensions("application/x-not-in-curated-or-anywhere") == []
    assert mime_to_all_extensions("application/x-not-in-curated-or-anywhere") == []
