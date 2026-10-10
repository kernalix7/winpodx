# SPDX-License-Identifier: MIT
"""Static runtime notices and full-distribution portable Python metadata."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path

from provenance_pins import PYTHON_FULL_PIN, TYPE2_RUNTIME_COMMIT, ArchivePin, ProvenanceError

RUNTIME_SOURCE_PINS = (
    ArchivePin(
        "alpine-aports",
        "b82a90a26fc0965ffd03cca90dfb1daed17101cc",
        "https://codeload.github.com/alpinelinux/aports/tar.gz/"
        "b82a90a26fc0965ffd03cca90dfb1daed17101cc",
        "2ecefac7515b9bafa84f0f37cd1fc243bc3a48a8f00d91b25ce502c702a4c4e3",
    ),
    ArchivePin(
        "musl",
        "1.2.5",
        "https://musl.libc.org/releases/musl-1.2.5.tar.gz",
        "a9a118bbe84d8764da0ea0d28b3ab3fae8477fc7e4085d90102b8596fc7c75e4",
    ),
    ArchivePin(
        "zstd",
        "1.5.6",
        "https://github.com/facebook/zstd/archive/v1.5.6.tar.gz",
        "30f35f71c1203369dc979ecde0400ffea93c27391bfd2ac5a9715d2173d92ff7",
    ),
    ArchivePin(
        "zlib",
        "1.3.2",
        "https://zlib.net/fossils/zlib-1.3.2.tar.gz",
        "bb329a0a2cd0274d05519d61c667c062e06990d72e125ee2dfa8de64f0119d16",
    ),
    ArchivePin(
        "mimalloc",
        "2.1.7",
        "https://github.com/microsoft/mimalloc/archive/v2.1.7/mimalloc-2.1.7.tar.gz",
        "0eed39319f139afde8515010ff59baf24de9e47ea316a315398e8027d198202d",
    ),
)


def collect_runtime_notices(directory: Path, appdir: Path) -> None:
    """Preserve original runtime/static dependency notices without executing source."""
    from provenance_pins import SOURCE_ARCHIVE_PINS

    pins = [pin for pin in SOURCE_ARCHIVE_PINS if "runtime-src" in pin.name]
    pins.extend(RUNTIME_SOURCE_PINS)
    notices = appdir / "usr/share/doc/winpodx/third-party/type2-runtime"
    notices.mkdir(parents=True, exist_ok=True)
    for pin in pins:
        filename = pin.url.rsplit("/", 1)[-1]
        source = directory / filename
        if not source.is_file():
            raise ProvenanceError(f"runtime source missing: {source}")
        pin.verify_file(source)
        if pin.name == "alpine-aports":
            continue
        count = 0
        with tarfile.open(source) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                relative = Path(*Path(member.name).parts[1:])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ProvenanceError(f"unsafe runtime source member: {member.name}")
                if (
                    not re.match(
                        r"^(LICENSE|LICENCE|COPYING|NOTICE|AUTHORS|COPYRIGHT)([.-].*)?$",
                        relative.name,
                        re.IGNORECASE,
                    )
                    and relative.name not in {"LGPL2.txt", "GPL2.txt"}
                    and relative.name != "zlib.h"
                ):
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise ProvenanceError(f"unreadable notice: {member.name}")
                with stream:
                    payload = stream.read()
                target = notices / pin.name / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                count += 1
        if count == 0:
            raise ProvenanceError(f"{pin.name}: no original runtime dependency notices")
    (notices / "BUILD-NOTES.txt").write_text(
        f"Runtime commit: {TYPE2_RUNTIME_COMMIT}\n"
        "Original Makefile, Dockerfile, Alpine/chroot scripts and libfuse mount.c.diff "
        "are preserved in the adjacent source archive. To relink, apply that patch, "
        "build static libfuse and squashfuse using scripts/common/install-dependencies.sh, "
        "then rebuild src/runtime with its Makefile and replace --runtime-file.\n"
        "Alpine sources/patches are from the 3.21 branch at the runtime release date. "
        "Upstream did not publish the exact resolved apk/image lock. These inputs are "
        "a reconstruction, not proof of byte-identical static dependency versions.\n"
    )


def collect_python_inputs(directory: Path, appdir: Path) -> list[dict[str, str]]:
    """Use exact full distribution metadata, not ldd, for native notice coverage."""
    from provenance_delivery import mirror_archive

    full = mirror_archive(PYTHON_FULL_PIN, directory)
    metadata_bytes = subprocess.run(
        ["tar", "--zstd", "-xOf", str(full), "python/PYTHON.json"],
        check=True,
        capture_output=True,
        timeout=300,
    ).stdout
    metadata = json.loads(metadata_bytes)
    if metadata["python_version"] != "3.11.17":
        raise ProvenanceError("full-distribution Python version mismatch")
    doc = appdir / "usr/share/doc/winpodx/third-party/python-build-standalone"
    doc.mkdir(parents=True, exist_ok=True)
    (doc / "PYTHON.json").write_bytes(metadata_bytes)
    for relative in ("bin/python3.11", "lib/libpython3.11.so.1.0"):
        original = subprocess.run(
            ["tar", "--zstd", "-xOf", str(full), "python/install/" + relative],
            check=True,
            capture_output=True,
            timeout=300,
        ).stdout
        installed = appdir / "opt/python" / relative
        if hashlib.sha256(original).digest() != hashlib.sha256(installed.read_bytes()).digest():
            raise ProvenanceError(f"portable Python full/install-only mismatch: {relative}")
    license_paths = set()
    for variants in metadata["build_info"]["extensions"].values():
        for variant in variants:
            license_paths.update(variant.get("license_paths", []))
    recipe = directory / "20261009"
    with tarfile.open(recipe) as archive:
        stream = archive.extractfile("python-build-standalone-20261009/pythonbuild/downloads.json")
        if stream is None:
            raise ProvenanceError("Python build download locks missing")
        with stream:
            downloads = json.load(stream)
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if not member.isfile() or len(parts) != 2 or not parts[1].startswith("LICENSE"):
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise ProvenanceError(f"Python build recipe notice unreadable: {member.name}")
            with stream:
                (doc / parts[1]).write_bytes(stream.read())
        for path in sorted(license_paths):
            name = "python-build-standalone-20261009/" + Path(path).name
            try:
                stream = archive.extractfile(name)
            except KeyError:
                entries = [
                    entry
                    for entry in downloads.values()
                    if entry.get("license_file") == Path(path).name
                ]
                if len(entries) != 1:
                    raise ProvenanceError(f"Python native notice missing: {path}") from None
                entry = entries[0]
                pin = ArchivePin(Path(path).name, entry["version"], entry["url"], entry["sha256"])
                source = mirror_archive(pin, directory)
                with tarfile.open(source) as upstream:
                    members = [
                        member
                        for member in upstream.getmembers()
                        if member.isfile() and Path(member.name).name == "LICENSE.md"
                    ]
                    if len(members) != 1:
                        raise ProvenanceError(f"Python native notice ambiguous: {path}")
                    stream = upstream.extractfile(members[0])
                    if stream is None:
                        raise ProvenanceError(f"Python native notice unreadable: {path}")
                    with stream:
                        payload = stream.read()
                target = doc / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                continue
            if stream is None:
                raise ProvenanceError(f"Python native notice missing: {path}")
            with stream:
                payload = stream.read()
            target = doc / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    listing = subprocess.run(
        ["tar", "--zstd", "-tf", str(full)], check=True, capture_output=True, text=True, timeout=300
    ).stdout.splitlines()
    notice_members = [
        name for name in listing if name.startswith("python/licenses/") and not name.endswith("/")
    ]
    for name in notice_members:
        content = subprocess.run(
            ["tar", "--zstd", "-xOf", str(full), name], check=True, capture_output=True, timeout=300
        ).stdout
        target = doc / name.removeprefix("python/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    records = []
    required_licenses = {Path(path).name for path in license_paths}
    for name, entry in downloads.items():
        if entry.get("license_file") not in required_licenses:
            continue
        if name.startswith("openssl-") and name != "openssl-3.5":
            continue
        pin = ArchivePin(name, entry["version"], entry["url"], entry["sha256"])
        path = mirror_archive(pin, directory)
        records.append(
            {"name": name, "url": pin.url, "sha256": pin.sha256, "path": "sources/" + path.name}
        )
    (doc / "COVERAGE.txt").write_text(
        "PYTHON.json from the SHA256-pinned full distribution covers built-in and "
        "shared extension links and original native license_paths. Interpreter and "
        "libpython bytes match install_only. Full distribution includes object/static "
        "archives for relinking. Missing full-distribution notices are supplied from "
        "the matching pinned build recipe or its SHA-verified native source "
        "(LICENSE.zlib-ng.txt is the original zlib-ng LICENSE.md). "
        "This does not attest every wheel-vendored native byte.\n"
    )
    return records
