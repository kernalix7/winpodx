# SPDX-License-Identifier: MIT
"""Bounded, lossless PNG-in-SVG export for desktop icon compatibility.

The wrapper retains raster pixels; it does not create vector detail. Private
icons remain untouched, and only marked wrappers belong to this exporter.
"""

from __future__ import annotations

import base64
import os
import tempfile
import zlib
from pathlib import Path
from typing import Final

_MAX_PNG_BYTES: Final = 1 << 20
_MAX_PNG_DIMENSION: Final = 1024
_PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
_WRAPPER_MARKER: Final = b"<!-- winpodx:png-svg-wrapper:v1 -->\n"
_PNG_FORMATS: Final = frozenset(
    (color, depth)
    for color, depths in (
        (0, (1, 2, 4, 8, 16)),
        (2, (8, 16)),
        (3, (1, 2, 4, 8)),
        (4, (8, 16)),
        (6, (8, 16)),
    )
    for depth in depths
)


def _validated_png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Validate bounded PNG chunk structure without decoding image data."""
    if not 33 <= len(data) <= _MAX_PNG_BYTES or not data.startswith(_PNG_SIGNATURE):
        return None
    if data[8:16] != b"\0\0\0\rIHDR":
        return None
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if not (0 < width <= _MAX_PNG_DIMENSION and 0 < height <= _MAX_PNG_DIMENSION):
        return None
    depth, color, compression, filtering, interlace = data[24:29]
    if (
        (color, depth) not in _PNG_FORMATS
        or compression != 0
        or filtering != 0
        or interlace not in (0, 1)
    ):
        return None

    offset = 8
    palette_seen = False
    idat_seen = False
    idat_closed = False
    idat_bytes = 0
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset : offset + 4], "big")
        kind = data[offset + 4 : offset + 8]
        end = offset + 8 + size
        if end + 4 > len(data) or not kind.isalpha() or kind[2] & 0x20:
            return None
        checksum = int.from_bytes(data[end : end + 4], "big")
        if zlib.crc32(data[offset + 4 : end]) != checksum:
            return None

        if kind == b"IHDR":
            if offset != 8:
                return None
        elif kind == b"PLTE":
            if (
                palette_seen
                or idat_seen
                or color in (0, 4)
                or not 0 < size <= 768
                or size % 3
                or (color == 3 and size // 3 > 1 << depth)
            ):
                return None
            palette_seen = True
        elif kind == b"IDAT":
            if idat_closed or (color == 3 and not palette_seen):
                return None
            idat_seen = True
            idat_bytes += size
        elif kind == b"IEND":
            if size == 0 and idat_bytes > 0 and end + 4 == len(data):
                return width, height
            return None
        elif not kind[0] & 0x20:  # Unknown critical chunks cannot be rendered safely.
            return None

        if idat_seen and kind != b"IDAT":
            idat_closed = True
        offset = end + 4
    return None


def _export_png_svg(source: Path, dest: Path) -> None:
    """Refresh only our wrapper, leaving native SVGs and failed writes intact.

    ``source`` is the installed PNG, so the wrapper embeds the same bytes as the
    sized fallback. I/O errors propagate to the caller's best-effort boundary;
    they must not be mistaken for an unsuitable PNG and delete a working SVG.
    """
    owned = False
    try:
        with dest.open("rb") as existing:
            owned = existing.read(len(_WRAPPER_MARKER)) == _WRAPPER_MARKER
    except FileNotFoundError:
        # A dangling link has unknown ownership; never replace it speculatively.
        if dest.is_symlink():
            return
    else:
        if not owned:
            return

    if source.is_symlink():
        return
    with source.open("rb") as png_file:
        data = png_file.read(_MAX_PNG_BYTES + 1)
    dimensions = _validated_png_dimensions(data)
    if dimensions is None:
        if owned:
            dest.unlink(missing_ok=True)
        return

    width, height = dimensions
    encoded = base64.b64encode(data).decode("ascii")
    svg = _WRAPPER_MARKER + (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        'preserveAspectRatio="xMidYMid meet">\n'
        f'  <image width="{width}" height="{height}" preserveAspectRatio="xMidYMid meet" '
        f'xlink:href="data:image/png;base64,{encoded}"/>\n'
        "</svg>\n"
    ).encode("ascii")

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(svg)
            target.flush()
            os.fsync(target.fileno())
        tmp.chmod(0o644)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
