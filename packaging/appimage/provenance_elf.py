# SPDX-License-Identifier: MIT
"""ELF provenance checks for conveyed AppImage binaries.

Two fail-closed checks:

* **glibc symbol-version ceiling** -- every conveyed ELF is scanned with
  ``readelf --version-info`` output; the highest referenced ``GLIBC_x.y``
  symbol version must not exceed the pinned Ubuntu 24.04 base's glibc
  (2.39).  A binary needing a newer glibc would break the AppImage on
  older hosts, so the build blocks instead of shipping it.
* **bundled Qt version** -- extracted from the actual ``libQt6Core.so.6``
  bytes shipped inside the PySide6 wheel, so PROVENANCE records the real
  Qt version instead of assuming it equals the PySide6 version.
"""

from __future__ import annotations

import re
from pathlib import Path

from provenance_pins import ProvenanceError

_GLIBC_RE = re.compile(r"\bGLIBC_(\d+)\.(\d+)\b")
_QT_VERSION_RE = re.compile(rb"(?<![\d.])6\.\d{1,2}\.\d{1,2}(?![\d.])")
_FREERDP_VERSION_RE = re.compile(r"version (\d+\.\d+\.\d+)")


def parse_glibc_max(version_info_text: str) -> tuple[int, int] | None:
    """Highest ``GLIBC_x.y`` symbol version referenced by one ELF.

    ``None`` means the ELF references no glibc symbol versions (e.g. a
    static binary); that is not an error.
    """
    versions = [(int(major), int(minor)) for major, minor in _GLIBC_RE.findall(version_info_text)]
    return max(versions) if versions else None


def check_glibc_ceiling(path: str, version_info_text: str, ceiling: tuple[int, int]) -> str | None:
    """Fail closed if ``path`` references a glibc newer than ``ceiling``.

    Returns the recorded maximum (``"2.38"`` / ``None``) for PROVENANCE.
    """
    top = parse_glibc_max(version_info_text)
    if top is not None and top > ceiling:
        raise ProvenanceError(
            f"{path}: references GLIBC_{top[0]}.{top[1]} but the AppImage "
            f"glibc ceiling is {ceiling[0]}.{ceiling[1]} -- refusing to ship"
        )
    return f"{top[0]}.{top[1]}" if top is not None else None


def qt_version_from_libcore(path: Path) -> str:
    """Exact Qt version embedded in the shipped ``libQt6Core.so.6``.

    Qt bakes its release string into ``.rodata``; scanning the actual
    bytes records what ships instead of guessing from the wheel version.
    Ambiguous or absent version strings block the build.
    """
    data = Path(path).read_bytes()
    candidates = sorted({m.group().decode() for m in _QT_VERSION_RE.finditer(data)})
    if len(candidates) != 1:
        raise ProvenanceError(
            f"cannot determine bundled Qt version from {path}: "
            f"candidates {candidates!r} -- refusing to guess"
        )
    return candidates[0]


def parse_freerdp_version(version_output: str) -> str:
    """Extract the exact FreeRDP version from ``xfreerdp3 --version``.

    Example output: ``This is FreeRDP version 3.32.1 (3.32.1)``.
    """
    match = _FREERDP_VERSION_RE.search(version_output)
    if match is None:
        raise ProvenanceError(
            f"cannot parse FreeRDP version from {version_output!r} -- "
            "refusing to record an unverified version"
        )
    return match.group(1)
