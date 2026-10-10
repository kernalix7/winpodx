# SPDX-License-Identifier: MIT
"""Fail-closed runtime-wheel provenance (pip report + dist-info licenses).

Every wheel installed into the bundled interpreter is recorded from
pip's JSON install report: exact name, version, download URL, and
sha256.  Every wheel must also have locatable license texts -- either
inside its ``*.dist-info`` (which travels with the AppImage) or in the
in-repo vendored fallback (PySide6/shiboken6 wheels ship no license
texts).  A wheel with no license text anywhere BLOCKS the build.

Copyleft-licensed (or license-unknown) wheels additionally require an
exact sdist URL, resolved via PyPI's JSON API at build time -- a
homepage is not a source offer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from provenance_elf import qt_version_from_libcore
from provenance_pins import ProvenanceError

_LICENSE_FILE_RE = re.compile(r"^(LICENSE|LICENCE|COPYING|NOTICE)([.-].*)?$", re.IGNORECASE)
_COPYLEFT_RE = re.compile(r"\b(L)?GPL", re.IGNORECASE)


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


@dataclass(frozen=True)
class WheelRecord:
    """One resolved wheel from pip's install report."""

    name: str
    version: str
    url: str
    sha256: str
    license_spdx: str | None
    license_texts: tuple[str, ...]  # in-tree dist-info rel paths, or vendored: paths
    sdist: dict[str, str] | None  # {"url", "sha256"} from PyPI JSON


def parse_pip_report(report: dict) -> list[tuple[str, str, str, str]]:
    """Extract (name, version, url, sha256) per installed wheel.

    Fail closed on any entry without a URL or a sha256 archive hash.
    """
    if not isinstance(report, dict) or not report.get("install"):
        raise ProvenanceError("pip report has no 'install' list -- nothing was installed")
    wheels: list[tuple[str, str, str, str]] = []
    for entry in report["install"]:
        meta = entry.get("metadata", {})
        name, version = meta.get("name"), meta.get("version")
        download = entry.get("download_info", {})
        url = download.get("url")
        archive = download.get("archive_info", {})
        digest = archive.get("hash", "")
        if not name or not version or not url:
            raise ProvenanceError(f"pip report entry missing name/version/url: {entry}")
        if not re.match(r"^sha256=[0-9a-f]{64}$", digest):
            raise ProvenanceError(
                f"{name} {version}: pip report has no sha256 archive hash -- "
                "refusing an unverified wheel"
            )
        wheels.append((name, version, url, digest.split("=", 1)[1]))
    return wheels


def dist_license_files(dist_info: Path) -> tuple[str, ...]:
    """License/notice files inside a ``*.dist-info`` (PEP 639 layout)."""
    found: list[str] = []
    for item in sorted(dist_info.iterdir()):
        if item.is_file() and _LICENSE_FILE_RE.match(item.name):
            found.append(item.name)
    licenses_dir = dist_info / "licenses"
    if licenses_dir.is_dir():
        found.extend(
            sorted(f"licenses/{item.name}" for item in licenses_dir.iterdir() if item.is_file())
        )
    return tuple(found)


def _metadata_license(dist_info: Path) -> str | None:
    metadata = dist_info / "METADATA"
    if not metadata.is_file():
        return None
    for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
        for field in ("License:", "License-Expression:"):
            if line.startswith(field):
                value = line[len(field) :].strip()
                if value and value.upper() != "UNKNOWN":
                    return value
    return None


def _vendored_texts(name: str, vendored_dir: Path | None) -> tuple[str, ...] | None:
    """Vendored license texts for wheels that ship none (PySide6 family)."""
    if vendored_dir is None:
        return None
    norm = _normalize(name)
    subdir = "pyside6" if norm == "shiboken6" or norm.startswith("pyside6") else norm
    candidate = vendored_dir / subdir
    if not candidate.is_dir():
        return None
    texts = tuple(sorted(item.name for item in candidate.iterdir() if item.is_file()))
    return (
        tuple(f"vendored:packaging/appimage/licenses/{subdir}/{name}" for name in texts)
        if texts
        else None
    )


def collect_wheel_provenance(
    site_packages: Path,
    pip_report: dict,
    vendored_dir: Path | None,
    resolve_sdist: Callable[[str, str], dict[str, str] | None],
    pinned_sdists: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Fail-closed wheel provenance for the whole runtime closure.

    ``resolve_sdist(name, version)`` returns ``{"url", "sha256"}`` for the
    exact PyPI sdist or ``None`` when none is published.  ``pinned_sdists``
    maps normalized wheel names to a pinned corresponding-source archive
    for wheels that publish no PyPI sdist (the PySide6 family ships its
    source as pyside-setup-everywhere-src on download.qt.io, pinned in
    provenance_pins.py).  A copyleft/unknown-license wheel with NEITHER
    still blocks the build.
    """
    records: list[WheelRecord] = []
    for name, version, url, sha256 in parse_pip_report(pip_report):
        dist_info = site_packages / f"{_normalize(name)}-{version}.dist-info"
        if dist_info.is_dir():
            texts: tuple[str, ...] | None = dist_license_files(dist_info)
            spdx = _metadata_license(dist_info)
        else:
            texts, spdx = None, None
        if not texts:
            texts = _vendored_texts(name, vendored_dir)
        if not texts:
            raise ProvenanceError(
                f"{name} {version}: no license text in dist-info and no vendored "
                "fallback -- unknown license must block the build"
            )
        sdist = resolve_sdist(name, version) or (pinned_sdists or {}).get(_normalize(name))
        if sdist is None and (spdx is None or _COPYLEFT_RE.search(spdx)):
            raise ProvenanceError(
                f"{name} {version}: copyleft/unknown license ({spdx!r}) has no "
                "exact sdist or pinned source URL -- cannot offer corresponding source"
            )
        records.append(WheelRecord(name, version, url, sha256, spdx, texts, sdist))
    qt_lib = site_packages / "PySide6" / "Qt" / "lib" / "libQt6Core.so.6"
    qt_version = qt_version_from_libcore(qt_lib) if qt_lib.is_file() else None
    return {
        "qt_version": qt_version,
        "wheels": [
            {
                "name": w.name,
                "version": w.version,
                "url": w.url,
                "sha256": w.sha256,
                "license_spdx": w.license_spdx,
                "license_texts": list(w.license_texts),
                "sdist": w.sdist,
            }
            for w in records
        ],
    }
