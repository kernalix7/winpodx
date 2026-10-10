# SPDX-License-Identifier: MIT
"""PROVENANCE.json + SOURCE-OFFER.txt emission for the Thin AppImage.

``PROVENANCE.json`` records the build's declared component scopes:
pinned build inputs, conveyed FreeRDP files' dpkg ownership, wheel closure
with hashes and licenses, and ELF checks.  ``SOURCE-OFFER.txt`` is the
human-readable corresponding-source offer published next to the AppImage
and inside it (``usr/share/doc/winpodx/SOURCE-OFFER.txt``).
"""

from __future__ import annotations

import json
from pathlib import Path

PROVENANCE_SCHEMA = "winpodx-appimage-provenance/1"


def build_provenance(
    pins: dict,
    dpkg: dict,
    wheels: dict,
    elf_checks: dict,
) -> dict:
    """Merge collected provenance into one JSON-able document."""
    return {
        "schema": PROVENANCE_SCHEMA,
        "build_inputs": pins,
        "freerdp": {
            "packages": dpkg["packages"],
            "conveyed_files": dpkg["files"],
            "xfreerdp3_version": elf_checks.get("xfreerdp3_version"),
            "glibc": {
                "ceiling": pins["glibc_ceiling"],
                "max_referenced": elf_checks.get("glibc_max"),
            },
        },
        "python_runtime": {
            "qt_version": wheels.get("qt_version"),
            "wheels": wheels.get("wheels", []),
        },
    }


def _freerdp_section(dpkg: dict) -> list[str]:
    lines = ["Ubuntu FreeRDP packages (exact matching source, incl. Debian patches):"]
    for package in dpkg["packages"]:
        lines.append(f"  {package['package']} {package['version']}")
        for kind in ("orig", "patch", "dsc"):
            archive = package["source_archives"].get(kind)
            if archive:
                lines.append(f"    {kind}: {archive['url']}")
                lines.append(f"      {archive['checksum']}")
    return lines


def _wheels_section(wheels: dict) -> list[str]:
    lines = ["PyPI runtime wheels (exact sdist + wheel, sha256 from PyPI at build time):"]
    for wheel in wheels.get("wheels", []):
        lines.append(
            f"  {wheel['name']} {wheel['version']}  [{wheel['license_spdx'] or 'see license text'}]"
        )
        lines.append(f"    wheel: {wheel['url']}")
        lines.append(f"      sha256 {wheel['sha256']}")
        if wheel.get("sdist"):
            lines.append(f"    sdist: {wheel['sdist']['url']}")
            lines.append(f"      sha256 {wheel['sdist']['sha256']}")
    return lines


def _pins_section(pins: dict) -> list[str]:
    lines = ["Pinned build inputs (each hash verified before use):"]
    lines.append(
        f"  Ubuntu base image: {pins['ubuntu_base']['image']} {pins['ubuntu_base']['digest']}"
    )
    for archive in pins["source_archives"]:
        lines.append(f"  {archive['name']} {archive['version']}")
        lines.append(f"    {archive['url']}")
        lines.append(f"    sha256 {archive['sha256']}")
    lines.append(f"  appimagetool {pins['appimagetool']['version']}: {pins['appimagetool']['url']}")
    lines.append(f"    sha256 {pins['appimagetool']['sha256']}")
    runtime = pins["type2_runtime"]
    lines.append(f"  Type 2 runtime (commit {runtime['commit']}): {runtime['url']}")
    lines.append(f"    sha256 {runtime['sha256']}")
    python = pins["python"]
    lines.append(f"  Portable CPython {python['version']}: {python['url']}")
    lines.append(f"    sha256 {python['sha256']}")
    lines.append(f"    license inventory: {python['license_inventory_url']}")
    return lines


def render_source_offer(provenance: dict) -> str:
    """Render SOURCE-OFFER.txt for the built AppImage."""
    pins = provenance["build_inputs"]
    dpkg = provenance["freerdp"]
    wheels = provenance["python_runtime"]
    freerdp = dpkg["packages"][0]["version"] if dpkg["packages"] else "?"
    lines = [
        "WinPodX Thin AppImage -- corresponding-source delivery index",
        "=" * 70,
        "",
        "This document records the collected source archives and checksums",
        "for the build's declared component scopes (including patch layers).",
        "It is not an attestation of every wheel-vendored native byte. Published",
        "next to the AppImage and inside it at",
        "usr/share/doc/winpodx/SOURCE-OFFER.txt.  The machine-readable record",
        "is PROVENANCE.json (same locations).",
        "",
        "This is an engineering source-provenance record, not legal advice or",
        "a legal guarantee.",
        "",
        "-- Bundled FreeRDP client stack (Ubuntu 24.04) -------------------------",
        *_freerdp_section(dpkg),
        f"  recorded xfreerdp3 --version: {dpkg['xfreerdp3_version']}",
        "",
        "-- Qt / PySide6 (LGPL-3.0 OR GPL-2.0 OR GPL-3.0) -----------------------",
        f"  bundled Qt version (from shipped libQt6Core.so.6): {wheels['qt_version']}",
        "",
        *_wheels_section(wheels),
        "",
        "-- Build inputs --------------------------------------------------------",
        *_pins_section(pins),
        "",
        "-- Replacing / relinking LGPL components ------------------------------",
        "The AppImage is a SquashFS you can unpack and re-pack:",
        "  1. ./winpodx-x86_64.AppImage --appimage-extract   (or set",
        "     APPIMAGE_EXTRACT_AND_RUN=1 to run unpacked)",
        "  2. Replace e.g. squashfs-root/opt/python/lib/python3.11/site-packages/",
        "     PySide6/Qt/lib/libQt6*.so* or PySide6/*.abi3.so, or",
        "     squashfs-root/usr/bin/xfreerdp3, with your own build of the",
        "     corresponding source listed above",
        "  3. Run from squashfs-root/AppRun, or re-pack with appimagetool",
        "     (pass --runtime-file with the pinned Type 2 runtime listed above)",
        "",
        "-- Availability -------------------------------------------------------",
        "Download winpodx-appimage-sources.tar.gz from the same release page",
        "as this binary, with equivalent network access and no extra charge.",
        "It contains mirrored source bytes, patches, build recipes, notices,",
        "input locks and source-mirror.json. SHA256SUMS binds these release",
        "assets to the binary. This document is a source-delivery index,",
        "not a written offer or an unsupported three-year availability promise.",
        "The static runtime's Alpine apk resolution was not locked upstream:",
        "see third-party/type2-runtime/BUILD-NOTES.txt for reconstruction limits.",
        "",
        f"FreeRDP stack version: {freerdp}",
    ]
    return "\n".join(lines) + "\n"


def write_manifests(out_dir: Path, provenance: dict) -> tuple[Path, Path]:
    """Write PROVENANCE.json + SOURCE-OFFER.txt into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    provenance_path = out_dir / "PROVENANCE.json"
    offer_path = out_dir / "SOURCE-OFFER.txt"
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    offer_path.write_text(render_source_offer(provenance), encoding="utf-8")
    return provenance_path, offer_path
