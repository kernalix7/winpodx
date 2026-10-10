# SPDX-License-Identifier: MIT
"""Regression tests: the pure-Python XPM fallback must bound its work (RS-02).

``winpodx.reverse_open.icons._decode_xpm_rgba`` reads the whole XPM, parses the
``W H NCOLORS CPP`` header, checks only that ``w``/``h``/``cpp``/``ncolors`` are
positive and that the declared number of quoted rows exists, then calls
``Image.new("RGBA", (w, h))`` directly — before any size/dimension/pixel check
and without verifying each row is ``w * cpp`` characters long. A 69-byte XPM
can therefore request a multi-gigabyte RGBA allocation (CWE-789 / CWE-400).
This is independent of any Pillow advisory and survives a patched Pillow.

The intended fix introduces module constants ``_MAX_XPM_BYTES`` (1 MiB),
``_MAX_XPM_DIMENSION`` (4096) and ``_MAX_XPM_PIXELS`` (4194304) and rejects
before allocating. They do not exist yet, so tests install them with
``raising=False``. ``Image.new`` is always wrapped in a guard that refuses any
allocation above 8 MiB, so no test can perform a real large allocation — the
guard raises instead, which is itself the RED signal on the current code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pil = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402  (after importorskip)

# Attribute names the intended fix exposes; installed via raising=False so the
# tests stay behavioural (RED) on the current implementation.
_MAX_XPM_BYTES = "_MAX_XPM_BYTES"
_MAX_XPM_DIMENSION = "_MAX_XPM_DIMENSION"
_MAX_XPM_PIXELS = "_MAX_XPM_PIXELS"

# Hard ceiling for the guard — mirrors the task's "never allocate > 8 MiB".
_ALLOC_GUARD_BYTES = 8 * 1024 * 1024

# 69-byte dimension bomb: header claims 1,000,000,000 x 1 pixels with one
# colour and one (short) row. On uncapped code this reaches
# ``Image.new("RGBA", (1000000000, 1))`` = 4,000,000,000 nominal bytes.
_HUGE_DIM_XPM = '/* XPM */\nstatic char * t[] = {\n"1000000000 1 1 2",\n"a c red",\n"a"};\n'

# Valid cpp=2 (veracrypt-class) sample that must keep working.
_VALID_CPP2_XPM = (
    "/* XPM */\n"
    "static char * t[] = {\n"
    '"2 2 2 2",\n'
    '"aa c #FF0000",\n'
    '"bb c #00FF00",\n'
    '"aabb",\n'
    '"bbaa"};\n'
)


def _install_alloc_guard(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[int, int]]]:
    """Replace ``PIL.Image.new`` with a spy that refuses >8 MiB allocations.

    Returns the list of requested ``(mode, (w, h))`` so a test can assert the
    parser rejected *before* it ever asked for an image.
    """
    real_new = Image.new
    calls: list[tuple[str, tuple[int, int]]] = []

    def guard(mode: str, size, *args, **kwargs):
        w, h = int(size[0]), int(size[1])
        calls.append((mode, (w, h)))
        if w * h * 4 > _ALLOC_GUARD_BYTES:
            raise AssertionError(f"refusing oversized Image.new {w}x{h} RGBA")
        return real_new(mode, size, *args, **kwargs)

    monkeypatch.setattr(Image, "new", guard)
    return calls


def _write(tmp_path: Path, text: str, name: str = "icon.xpm") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="latin-1")
    return path


def test_huge_dimension_fixture_rejects_before_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the documented 69-byte XPM asking for 1e9 x 1 RGBA pixels.
    from winpodx.reverse_open import icons

    src = _write(tmp_path, _HUGE_DIM_XPM, "huge.xpm")
    assert len(_HUGE_DIM_XPM.encode("latin-1")) == 69

    # When: the decoder runs with allocation interception in place.
    calls = _install_alloc_guard(monkeypatch)
    result = icons._decode_xpm_rgba(src)

    # Then: it is refused, and no image was ever requested.
    assert result is None
    assert calls == []


def test_over_dimension_fixture_rejects_before_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 5000 px wide exceeds the intended 4096 dimension cap. A full-length row
    # keeps this test about the dimension check, not the short-row check.
    from winpodx.reverse_open import icons

    monkeypatch.setattr(icons, _MAX_XPM_DIMENSION, 4096, raising=False)
    row = "a" * 5000
    src = _write(
        tmp_path,
        'static char *x[] = {\n"5000 1 1 1",\n"a c #000000",\n"' + row + '"};\n',
        "wide.xpm",
    )

    calls = _install_alloc_guard(monkeypatch)
    result = icons._decode_xpm_rgba(src)

    assert result is None
    assert calls == []


def test_pixel_bomb_rejects_before_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Shrink the pixel cap so a tiny 3x3 fixture is already over budget.
    from winpodx.reverse_open import icons

    monkeypatch.setattr(icons, _MAX_XPM_PIXELS, 4, raising=False)
    src = _write(
        tmp_path,
        'static char *x[] = {\n"3 3 1 1",\n"a c #000000",\n"aaa",\n"aaa",\n"aaa"};\n',
        "pixels.xpm",
    )

    calls = _install_alloc_guard(monkeypatch)
    result = icons._decode_xpm_rgba(src)

    assert result is None
    assert calls == []


def test_oversized_file_bytes_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Shrink the byte cap so the valid cpp2 sample is over the limit.
    from winpodx.reverse_open import icons

    monkeypatch.setattr(icons, _MAX_XPM_BYTES, 16, raising=False)
    src = _write(tmp_path, _VALID_CPP2_XPM, "big.xpm")
    assert len(_VALID_CPP2_XPM.encode("latin-1")) > 16

    calls = _install_alloc_guard(monkeypatch)
    result = icons._decode_xpm_rgba(src)

    assert result is None
    assert calls == []


def test_short_row_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Header says 3 px wide but the single row carries only 2 characters.
    from winpodx.reverse_open import icons

    src = _write(
        tmp_path,
        'static char *x[] = {\n"3 1 1 1",\n"a c #000000",\n"ab"};\n',
        "short.xpm",
    )

    calls = _install_alloc_guard(monkeypatch)
    result = icons._decode_xpm_rgba(src)

    assert result is None
    assert calls == []


def test_valid_cpp2_sample_still_decodes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Guards: the fixes must not break the veracrypt-class cpp>=2 case.
    from winpodx.reverse_open import icons

    src = _write(tmp_path, _VALID_CPP2_XPM, "valid.xpm")

    calls = _install_alloc_guard(monkeypatch)
    img = icons._decode_xpm_rgba(src)

    assert img is not None and img.size == (2, 2)
    pixels = img.convert("RGBA").load()
    assert pixels[0, 0] == (255, 0, 0, 255)  # "aa" -> red
    assert pixels[1, 0] == (0, 255, 0, 255)  # "bb" -> green
    assert calls == [("RGBA", (2, 2))]


def test_valid_cpp1_sample_still_decodes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.reverse_open import icons

    src = _write(
        tmp_path,
        'static char *x[] = {\n"2 1 2 1",\n"a c #FF0000",\n"b c #0000FF",\n"ab"};\n',
        "cpp1.xpm",
    )

    calls = _install_alloc_guard(monkeypatch)
    img = icons._decode_xpm_rgba(src)

    assert img is not None and img.size == (2, 1)
    pixels = img.convert("RGBA").load()
    assert pixels[0, 0] == (255, 0, 0, 255)
    assert pixels[1, 0] == (0, 0, 255, 255)
    assert calls == [("RGBA", (2, 1))]
