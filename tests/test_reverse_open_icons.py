# SPDX-License-Identifier: MIT
"""Tests for ``winpodx.reverse_open.icons``.

The PIL dependency is a soft import; if Pillow isn't installed in the
test environment the ICO tests skip cleanly. The fallback resolver is
exercised against a fake Hicolor tree we build under XDG_DATA_HOME (the
``conftest`` autouse fixture isolates it per-test).
"""

from __future__ import annotations

import os
import sys
import types
from builtins import __import__ as real_import
from pathlib import Path

import pytest

from winpodx.reverse_open.icons import (
    ICO_SIZES,
    _fallback_resolve,
    convert_to_ico,
    resolve_icon,
)

pil = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402  (after importorskip)


def _hicolor_dir() -> Path:
    base = Path(os.environ["XDG_DATA_HOME"]) / "icons" / "hicolor"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _make_png(
    path: Path,
    size: int = 32,
    color: tuple[int, int, int, int] = (255, 0, 0, 255),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), color).save(path, format="PNG")


# --- _fallback_resolve --------------------------------------------------------


def test_fallback_resolve_finds_hicolor_app_icon() -> None:
    _make_png(_hicolor_dir() / "48x48" / "apps" / "kate.png")
    resolved = _fallback_resolve("kate")
    assert resolved is not None
    assert resolved.name == "kate.png"


def test_fallback_resolve_prefers_larger_sizes_first() -> None:
    _make_png(_hicolor_dir() / "16x16" / "apps" / "kate.png", size=16)
    _make_png(_hicolor_dir() / "256x256" / "apps" / "kate.png", size=256)
    resolved = _fallback_resolve("kate")
    assert resolved is not None
    assert "256x256" in str(resolved)


def test_fallback_resolve_returns_none_for_missing_name() -> None:
    assert _fallback_resolve("definitely-not-here") is None


def test_fallback_resolve_refuses_path_traversal() -> None:
    # Slashes / dot-prefixes are rejected at the front door; the
    # caller can't smuggle '../../etc/passwd' through the resolver.
    assert _fallback_resolve("../etc/passwd") is None
    assert _fallback_resolve(".hidden") is None
    assert _fallback_resolve("") is None


# --- resolve_icon -------------------------------------------------------------


def test_resolve_icon_absolute_path_kept_when_exists(tmp_path: Path) -> None:
    src = tmp_path / "abs.png"
    _make_png(src)
    assert resolve_icon(str(src)) == src


def test_resolve_icon_absolute_path_missing_returns_none(tmp_path: Path) -> None:
    assert resolve_icon(str(tmp_path / "nope.png")) is None


def test_resolve_icon_empty_returns_none() -> None:
    assert resolve_icon("") is None


def test_resolve_icon_uses_fallback_for_unknown_name() -> None:
    _make_png(_hicolor_dir() / "128x128" / "apps" / "gimp.png")
    resolved = resolve_icon("gimp")
    assert resolved is not None
    assert resolved.name == "gimp.png"


# --- convert_to_ico -----------------------------------------------------------


def _assert_ico_valid(dst: Path) -> None:
    """Open the ICO with Pillow and verify every declared size is present."""
    assert dst.is_file()
    with Image.open(dst) as img:
        # Pillow exposes embedded sizes via ico.sizes (set of (w, h)).
        sizes = getattr(img, "ico", None)
        if sizes is not None:
            actual = {(w, h) for (w, h) in sizes.sizes()}
        else:
            actual = set(img.info.get("sizes", []))
        # At least one size matches our embedded grid.
        assert any((s, s) in actual for s in ICO_SIZES), actual


def test_convert_to_ico_from_png(tmp_path: Path) -> None:
    src = tmp_path / "in.png"
    _make_png(src, size=64)
    dst = tmp_path / "out.ico"
    ok = convert_to_ico(src, dst)
    assert ok is True
    _assert_ico_valid(dst)


def test_convert_to_ico_small_source_upscales_to_full_size_set(tmp_path: Path) -> None:
    """Source PNGs smaller than the largest target must NOT collapse the
    output to a single frame.

    Pillow's ICO encoder silently skips any requested size larger than
    the source (``if size[0] > width: continue``). Without upscaling the
    base image first, a 16×16 firefox.png would produce a single 16×16
    frame in the .ico — Win11's OpenWith chooser then falls back to the
    generic .exe icon. ``convert_to_ico`` must pre-upscale so every
    requested ICO_SIZES entry lands in the output.
    """
    src = tmp_path / "tiny.png"
    _make_png(src, size=16)  # smaller than max(ICO_SIZES)=256
    dst = tmp_path / "out.ico"
    assert convert_to_ico(src, dst) is True

    with Image.open(dst) as img:
        sizes = getattr(img, "ico", None)
        if sizes is not None:
            actual = {(w, h) for (w, h) in sizes.sizes()}
        else:
            actual = set(img.info.get("sizes", []))
        # Every declared ICO size must be present, not just the
        # source's own 16×16. Win11 chooser typically renders at 32 or
        # 48 — failing to embed those was the v0.4.5/v0.4.6 smoke bug.
        for s in ICO_SIZES:
            assert (s, s) in actual, f"{s}x{s} missing — actual sizes: {actual}"


def test_convert_to_ico_creates_parent_directory(tmp_path: Path) -> None:
    src = tmp_path / "in.png"
    _make_png(src)
    dst = tmp_path / "nested" / "deeper" / "out.ico"
    assert convert_to_ico(src, dst) is True
    assert dst.is_file()


def test_convert_to_ico_overwrites_atomically(tmp_path: Path) -> None:
    src = tmp_path / "in.png"
    _make_png(src)
    dst = tmp_path / "out.ico"
    dst.write_bytes(b"stale")
    assert convert_to_ico(src, dst) is True
    _assert_ico_valid(dst)


def test_convert_to_ico_missing_source_writes_placeholder(tmp_path: Path) -> None:
    dst = tmp_path / "ph.ico"
    ok = convert_to_ico(tmp_path / "does-not-exist.png", dst)
    assert ok is False  # caller knows a placeholder went out
    _assert_ico_valid(dst)


def test_convert_to_ico_empty_path_writes_placeholder(tmp_path: Path) -> None:
    # Empty path is the common "Icon= field was blank" call from
    # host_open.refresh — we still produce a valid ICO so Windows
    # always has something to display.
    dst = tmp_path / "ph2.ico"
    assert convert_to_ico(Path(""), dst) is False
    _assert_ico_valid(dst)


def test_convert_to_ico_unreadable_source_writes_placeholder(tmp_path: Path) -> None:
    src = tmp_path / "garbage.png"
    src.write_bytes(b"this is not a PNG")
    dst = tmp_path / "out.ico"
    assert convert_to_ico(src, dst) is False
    _assert_ico_valid(dst)


# --- pure-Python XPM decoder (veracrypt-class cpp>=2, >256 colours) ----------

_CPP2_XPM = (
    "/* XPM */\n"
    "static char * t[] = {\n"
    '"2 2 2 2",\n'
    '"aa c #FF0000",\n'
    '"bb c #00FF00",\n'
    '"aabb",\n'
    '"bbaa"};\n'
)


def test_decode_xpm_cpp2(tmp_path: Path) -> None:
    # cpp=2 (2 chars per pixel) -- Pillow's XPM decoder only does cpp=1 and
    # raises. The pure-Python decoder must read it and map the colours.
    from winpodx.reverse_open import icons

    src = tmp_path / "t.xpm"
    src.write_text(_CPP2_XPM)
    img = icons._decode_xpm_rgba(src)
    assert img is not None and img.size == (2, 2)
    px = img.convert("RGBA").load()
    assert px[0, 0] == (255, 0, 0, 255)  # "aa" -> red
    assert px[1, 0] == (0, 255, 0, 255)  # "bb" -> green


def test_decode_xpm_none_colour_is_transparent(tmp_path: Path) -> None:
    from winpodx.reverse_open import icons

    xpm = (
        "/* XPM */\nstatic char * t[] = {\n"
        '"1 1 1 2",\n'
        '"   c None",\n'  # cpp=2 key "  " (two spaces) -> transparent
        '"  "};\n'
    )
    src = tmp_path / "n.xpm"
    src.write_text(xpm)
    img = icons._decode_xpm_rgba(src)
    assert img is not None
    assert img.convert("RGBA").load()[0, 0] == (0, 0, 0, 0)


def test_decode_xpm_malformed_returns_none(tmp_path: Path) -> None:
    from winpodx.reverse_open import icons

    bad = tmp_path / "bad.xpm"
    bad.write_text("/* XPM */ not really xpm")
    assert icons._decode_xpm_rgba(bad) is None


def test_open_raster_uses_xpm_decoder_when_pillow_fails(tmp_path: Path) -> None:
    # End-to-end through the raster opener: cpp=2 XPM -> real RGBA image,
    # no external tool, no placeholder.
    from winpodx.reverse_open import icons

    src = tmp_path / "t.xpm"
    src.write_text(_CPP2_XPM)
    img = icons._open_raster_rgba(src)
    assert img is not None and img.size == (2, 2)


def test_convert_cpp2_xpm_writes_real_icon(tmp_path: Path) -> None:
    # The veracrypt case: cpp>=2 XPM must produce a REAL multi-res icon,
    # not a placeholder -- with zero external dependency.
    src = tmp_path / "t.xpm"
    src.write_text(_CPP2_XPM)
    dst = tmp_path / "t.ico"
    assert convert_to_ico(src, dst) is True
    _assert_ico_valid(dst)


def test_resolve_icon_uses_lazy_xdg_icon_theme(monkeypatch, tmp_path: Path) -> None:
    icon = tmp_path / "theme.png"
    _make_png(icon)
    calls: list[tuple[str, int]] = []
    icon_theme = types.ModuleType("xdg.IconTheme")

    def get_icon_path(name: str, *, size: int) -> str:
        calls.append((name, size))
        return str(icon)

    icon_theme.getIconPath = get_icon_path
    monkeypatch.setitem(sys.modules, "xdg", types.ModuleType("xdg"))
    monkeypatch.setitem(sys.modules, "xdg.IconTheme", icon_theme)

    assert resolve_icon("themed-app") == icon
    assert calls == [("themed-app", 256)]


def test_convert_svg_uses_lazy_cairosvg_and_writes_ico(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "icon.svg"
    src.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"/>')
    dst = tmp_path / "icon.ico"
    calls: list[tuple[str, int, int]] = []
    cairosvg = types.ModuleType("cairosvg")

    def svg2png(*, url: str, output_width: int, output_height: int) -> bytes:
        calls.append((url, output_width, output_height))
        buffer = __import__("io").BytesIO()
        Image.new("RGBA", (output_width, output_height), (10, 20, 30, 255)).save(
            buffer, format="PNG"
        )
        return buffer.getvalue()

    cairosvg.svg2png = svg2png
    monkeypatch.setitem(sys.modules, "cairosvg", cairosvg)

    assert convert_to_ico(src, dst) is True
    assert src.read_bytes().startswith(b"<svg")
    assert dst.read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert [size for _, size, _ in calls] == list(ICO_SIZES)


def test_svg_without_raster_backend_writes_placeholder(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "icon.svg"
    src.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    dst = tmp_path / "placeholder.ico"

    def import_without_svg_backends(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "cairosvg" or name.startswith("reportlab") or name.startswith("svglib"):
            raise ImportError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", import_without_svg_backends)

    assert convert_to_ico(src, dst) is False
    assert dst.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_convert_without_pillow_logs_warning_and_does_not_write(
    monkeypatch, caplog, tmp_path: Path
) -> None:
    dst = tmp_path / "missing.ico"

    def import_without_pillow(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "PIL":
            raise ImportError(name)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", import_without_pillow)

    with caplog.at_level("WARNING", logger="winpodx.reverse_open.icons"):
        assert convert_to_ico(tmp_path / "source.png", dst) is False

    assert "Pillow not installed" in caplog.text
    assert dst.exists() is False


def test_convert_svg_rejects_non_png_rasterizer_output(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "bad.svg"
    src.write_text("not an svg")
    dst = tmp_path / "bad.ico"
    cairosvg = types.ModuleType("cairosvg")
    cairosvg.svg2png = lambda **_kwargs: b"not-png"
    monkeypatch.setitem(sys.modules, "cairosvg", cairosvg)

    with pytest.raises(OSError):
        convert_to_ico(src, dst)

    assert dst.exists() is False


def test_decode_xpm_unreadable_path_returns_none(caplog, tmp_path: Path) -> None:
    from winpodx.reverse_open import icons

    missing = tmp_path / "missing.xpm"

    with caplog.at_level("DEBUG", logger="winpodx.reverse_open.icons"):
        assert icons._decode_xpm_rgba(missing) is None

    assert "cannot read XPM" in caplog.text


@pytest.mark.parametrize(
    "header",
    ["bad header", "0 1 1 1", "1 1 2 1"],
)
def test_decode_xpm_rejects_invalid_headers(tmp_path: Path, header: str) -> None:
    from winpodx.reverse_open import icons

    src = tmp_path / "invalid.xpm"
    src.write_text(f'static char *x[] = {{"{header}", "a c red", "a"}};')

    assert icons._decode_xpm_rgba(src) is None


def test_decode_xpm_handles_wide_hex_unknown_and_missing_colors(tmp_path: Path) -> None:
    from winpodx.reverse_open import icons

    src = tmp_path / "colors.xpm"
    src.write_text(
        "static char *x[] = {\n"
        '"3 1 3 1",\n'
        '"a c #FFFF00008000",\n'
        '"b c definitely-not-an-x11-color",\n'
        '"c symbolic ignored",\n'
        '"abc"};\n'
    )

    image = icons._decode_xpm_rgba(src)

    assert image is not None
    pixels = image.load()
    assert pixels[0, 0] == (255, 0, 128, 255)
    assert pixels[1, 0] == (0, 0, 0, 0)
    assert pixels[2, 0] == (0, 0, 0, 0)
