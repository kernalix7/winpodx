# SPDX-License-Identifier: MIT
"""Pinned build inputs for the Thin AppImage (fail-closed provenance).

Every external byte that can end up inside the shipped AppImage is pinned
here: the Ubuntu base-image digest, the exact dpkg versions of the bundled
FreeRDP stack, the appimagetool + Type 2 runtime archives, the portable
CPython tarball, the runtime pip wheel closure, and the corresponding-source
archives for the LGPL/GPL components.

Every hash below was verified against the upstream asset at pin time
(evidence: ``.rtrt/tmp/release-0.12.0/appimage-fix/result.md``).  The build
re-verifies each pinned hash before use and fails closed on mismatch,
unknown input, or missing provenance record.  Nothing is inferred, guessed,
or silently defaulted.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProvenanceError(RuntimeError):
    """A build input failed closed: unknown, unpinned, or mismatched."""


@dataclass(frozen=True)
class ArchivePin:
    """A downloadable archive pinned by exact URL + SHA256."""

    name: str
    version: str
    url: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.url.startswith("https://"):
            raise ProvenanceError(f"{self.name}: pin URL must be https: {self.url}")
        if not _SHA256_RE.match(self.sha256):
            raise ProvenanceError(f"{self.name}: pin sha256 malformed: {self.sha256}")

    def verify_file(self, path: Path) -> None:
        """Fail closed unless ``path`` hashes to the pinned sha256."""
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if digest != self.sha256:
            raise ProvenanceError(
                f"{self.name} {self.version}: sha256 mismatch for {path}: "
                f"expected {self.sha256}, got {digest}"
            )


@dataclass(frozen=True)
class DpkgPin:
    """An Ubuntu package pinned to an exact dpkg version."""

    package: str
    version: str


UBUNTU_IMAGE = "docker.io/library/ubuntu:24.04"
UBUNTU_DIGEST = "sha256:f610ab94648195aa356059f5b41d6085c9d4d903c072430cdd1af7bdb646106b"

# FreeRDP 3 client stack from Ubuntu 24.04 (noble updates/security).
# Verified: all five candidates resolve to this exact version.
FREERDP_DPKG_PINS = (
    DpkgPin("freerdp3-x11", "3.32.1+dfsg-0ubuntu0.24.04.1"),
    DpkgPin("freerdp3-wayland", "3.32.1+dfsg-0ubuntu0.24.04.1"),
    DpkgPin("libfreerdp3-3", "3.32.1+dfsg-0ubuntu0.24.04.1"),
    DpkgPin("libfreerdp-client3-3", "3.32.1+dfsg-0ubuntu0.24.04.1"),
    DpkgPin("libwinpr3-3", "3.32.1+dfsg-0ubuntu0.24.04.1"),
)

# The pinned base retains OpenSSL .15, whose source is no longer indexed.
# Install .16 explicitly so conveyed binaries match the available exact source.
SUPPORT_DPKG_PINS = (DpkgPin("libssl3t64", "3.0.13-0ubuntu3.16"),)

APPIMAGETOOL_PIN = ArchivePin(
    name="appimagetool",
    version="1.9.1",
    url=(
        "https://github.com/AppImage/appimagetool/releases/download/1.9.1/"
        "appimagetool-x86_64.AppImage"
    ),
    sha256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0",
)

# Type 2 runtime: the `continuous` release whose target commit is pinned.
# appimagetool is driven with --runtime-file so it never downloads a
# floating runtime itself.
TYPE2_RUNTIME_COMMIT = "8f39b89e2ac31e1640b3d3f7e9a5108e6ce805fa"
TYPE2_RUNTIME_PIN = ArchivePin(
    name="type2-runtime-x86_64",
    version=f"continuous@{TYPE2_RUNTIME_COMMIT}",
    url=("https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"),
    sha256="156f4bdbde9c52d01814600013e0a273f0118dc2de98975f3c8c63427ec79074",
)

PYTHON_PIN = ArchivePin(
    name="cpython-install-only",
    version="3.11.17+20261009",
    url=(
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        "20261009/cpython-3.11.17%2B20261009-x86_64-unknown-linux-gnu-"
        "install_only.tar.gz"
    ),
    sha256="035b0ccfe7f3d38ab3ddab505290d200c6a265b95fbd5fac76c358751d6bb6a8",
)
# Portable-Python license inventory: the python-build-standalone repo at the
# release tag carries LICENSE plus one LICENSE.<dep>.txt per linked third-party
# library.  Copied verbatim by relative path -- never collapsed into one file.
PYTHON_LICENSE_INVENTORY_TAG = "20261009"
PYTHON_LICENSE_INVENTORY_URL = (
    "https://github.com/astral-sh/python-build-standalone/tree/" + PYTHON_LICENSE_INVENTORY_TAG
)

# Runtime pip closure, pinned to the parent QA-verified resolution
# (PySide6 6.12.0 / Pillow 12.3.0 / CairoSVG 2.9.1 + transitives).  The build
# installs with these exact constraints and records the resolved closure
# (URLs + hashes) from pip's JSON report.
WHEEL_PINS = {
    "PySide6": "6.12.0",
    "shiboken6": "6.12.0",
    "Pillow": "12.3.0",
    "CairoSVG": "2.9.1",
    "pyxdg": "0.28",
    "cairocffi": "1.7.1",
    "cffi": "2.1.1",
    "cssselect2": "0.10.1",
    "defusedxml": "0.7.1",
    "pycparser": "3.11",
    "tinycss2": "1.5.1",
    "webencodings": "0.6.1",
}

# Conveyed ELFs must not require a newer glibc than the pinned Ubuntu 24.04
# base provides (glibc 2.39) -- the AppImage must run on older hosts too.
GLIBC_CEILING = (2, 39)

# Corresponding-source archives for redistributed components (LGPL/GPL
# delivery + general provenance).  Hashes verified once at pin time; the
# build downloads and verifies these archives before emitting the source bundle.
SOURCE_ARCHIVE_PINS = (
    ArchivePin(
        name="qt-everywhere-src",
        version="6.12.0",
        url=(
            "https://download.qt.io/official_releases/qt/6.12/6.12.0/single/"
            "qt-everywhere-src-6.12.0.tar.xz"
        ),
        sha256="98ff4f44bac6ec3e1e62ee2a4316ae0e3d15badb015d753cc0268a20db52f165",
    ),
    ArchivePin(
        name="pyside-setup-everywhere-src",
        version="6.12.0",
        url=(
            "https://download.qt.io/official_releases/QtForPython/pyside6/"
            "PySide6-6.12.0-src/pyside-setup-everywhere-src-6.12.0.tar.xz"
        ),
        sha256="099c1a597cf33c1b000e11cf68a0b82d216fb802bf81726efc720394f10c7751",
    ),
    ArchivePin(
        name="cpython-src",
        version="3.11.17",
        url="https://www.python.org/ftp/python/3.11.17/Python-3.11.17.tgz",
        sha256="53cdee63ac4bf12387b7b33a53d3b1f8f4941cad73807a7b4fe91bb001ef004a",
    ),
    ArchivePin(
        name="appimagetool-src",
        version="1.9.1",
        url="https://github.com/AppImage/appimagetool/archive/refs/tags/1.9.1.tar.gz",
        sha256="01c86a11057129ce5b5f821436b217d99bcf6345bd2b210a2b773c3c39fc340e",
    ),
    ArchivePin(
        name="type2-runtime-src",
        version=f"continuous@{TYPE2_RUNTIME_COMMIT}",
        url=(
            "https://github.com/AppImage/type2-runtime/archive/" + TYPE2_RUNTIME_COMMIT + ".tar.gz"
        ),
        sha256="c449eb42c2190820872631f5e22f8e15b1e985a36dc4a9e2de3585bb7536c785",
    ),
    ArchivePin(
        "libfuse-runtime-src",
        "3.15.0",
        "https://github.com/libfuse/libfuse/releases/download/fuse-3.15.0/fuse-3.15.0.tar.xz",
        "70589cfd5e1cff7ccd6ac91c86c01be340b227285c5e200baa284e401eea2ca0",
    ),
    ArchivePin(
        "squashfuse-runtime-src",
        "0.5.2",
        "https://github.com/vasi/squashfuse/archive/0.5.2.tar.gz",
        "db0238c5981dabbd80ee09ae15387f390091668ca060a7bc38047912491443d3",
    ),
    ArchivePin(
        "python-build-standalone-src",
        "20261009",
        "https://codeload.github.com/astral-sh/python-build-standalone/tar.gz/refs/tags/20261009",
        "7c7a5d3948913c6b777bc4c45befc1d4014c1f63902863af207a65070ab6b57d",
    ),
)

PYTHON_FULL_PIN = ArchivePin(
    "python-full",
    "3.11.17+20261009",
    "https://github.com/astral-sh/python-build-standalone/releases/download/20261009/"
    "cpython-3.11.17%2B20261009-x86_64-unknown-linux-gnu-pgo%2Blto-full.tar.zst",
    "2a0aff9762cb07823631cafb041c19411215341c122e3fcaadd2d44880f1dc7c",
)


def wheel_constraints() -> str:
    """pip constraint lines (``name==version``) for the runtime closure."""
    return "\n".join(f"{name}=={version}" for name, version in WHEEL_PINS.items())


def as_dict() -> dict:
    """JSON-able snapshot of every pin (goes into PROVENANCE.json)."""
    return {
        "ubuntu_base": {"image": UBUNTU_IMAGE, "digest": UBUNTU_DIGEST},
        "freerdp_dpkg": [{"package": p.package, "version": p.version} for p in FREERDP_DPKG_PINS],
        "support_dpkg": [{"package": p.package, "version": p.version} for p in SUPPORT_DPKG_PINS],
        "appimagetool": {
            "version": APPIMAGETOOL_PIN.version,
            "url": APPIMAGETOOL_PIN.url,
            "sha256": APPIMAGETOOL_PIN.sha256,
        },
        "type2_runtime": {
            "commit": TYPE2_RUNTIME_COMMIT,
            "url": TYPE2_RUNTIME_PIN.url,
            "sha256": TYPE2_RUNTIME_PIN.sha256,
        },
        "python": {
            "version": PYTHON_PIN.version,
            "url": PYTHON_PIN.url,
            "sha256": PYTHON_PIN.sha256,
            "license_inventory_url": PYTHON_LICENSE_INVENTORY_URL,
        },
        "wheel_pins": dict(WHEEL_PINS),
        "glibc_ceiling": ".".join(map(str, GLIBC_CEILING)),
        "source_archives": [
            {"name": a.name, "version": a.version, "url": a.url, "sha256": a.sha256}
            for a in SOURCE_ARCHIVE_PINS
        ],
    }
