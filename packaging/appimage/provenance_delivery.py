# SPDX-License-Identifier: MIT
"""Verified corresponding-source mirror and adjacent public delivery archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from provenance_pins import PYTHON_FULL_PIN, SOURCE_ARCHIVE_PINS, ArchivePin, ProvenanceError
from provenance_runtime import RUNTIME_SOURCE_PINS, collect_python_inputs, collect_runtime_notices


def mirror_archive(pin: ArchivePin, directory: Path) -> Path:
    """Reuse only verified cache bytes; download bounded, then verify SHA256."""
    directory.mkdir(parents=True, exist_ok=True)
    filename = unquote(urlsplit(pin.url).path.rsplit("/", 1)[-1])
    if not filename or filename in (".", "..") or "/" in filename:
        raise ProvenanceError(f"{pin.name}: unsafe archive filename")
    target = directory / filename
    if not target.is_file():
        partial = target.with_name(target.name + ".partial")
        subprocess.run(
            [
                "curl",
                "-fL",
                "--retry",
                "2",
                "--connect-timeout",
                "30",
                "--max-time",
                "600",
                pin.url,
                "-o",
                str(partial),
            ],
            check=True,
            timeout=1900,
        )
        pin.verify_file(partial)
        partial.replace(target)
    else:
        pin.verify_file(target)
    return target


def mirror_sources(provdir: Path, appdir: Path) -> None:
    """Mirror all declared source locks and validate signed-index Ubuntu bytes."""
    directory = provdir / "sources"
    records = []
    for pin in (*SOURCE_ARCHIVE_PINS, *RUNTIME_SOURCE_PINS, PYTHON_FULL_PIN):
        path = mirror_archive(pin, directory)
        records.append(
            {
                "name": pin.name,
                "url": pin.url,
                "sha256": pin.sha256,
                "path": str(path.relative_to(provdir)),
            }
        )
    wheels = json.loads((provdir / "wheels-provenance.json").read_text())
    site = appdir / "opt/python/lib/python3.11/site-packages"
    notices = appdir / "usr/share/doc/winpodx/third-party/wheels"
    for wheel in wheels["wheels"]:
        normalized = re.sub(r"[-_.]+", "_", wheel["name"]).lower()
        info = site / f"{normalized}-{wheel['version']}.dist-info"
        for relative in wheel["license_texts"]:
            if relative.startswith("vendored:"):
                continue
            source = info / relative
            if not source.is_file():
                raise ProvenanceError(f"wheel notice missing: {source}")
            target = notices / info.name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if (info / "licenses").is_dir():
            shutil.copytree(info / "licenses", notices / info.name / "licenses", dirs_exist_ok=True)
        sdist = wheel.get("sdist")
        if sdist:
            pin = ArchivePin(wheel["name"], wheel["version"], sdist["url"], sdist["sha256"])
            path = mirror_archive(pin, directory)
            records.append(
                {
                    "name": pin.name,
                    "url": pin.url,
                    "sha256": pin.sha256,
                    "path": str(path.relative_to(provdir)),
                }
            )
    for info in sorted(site.glob("*.dist-info")):
        if info.name.startswith(("pip-", "setuptools-")) and (info / "licenses").is_dir():
            shutil.copytree(info / "licenses", notices / info.name / "licenses", dirs_exist_ok=True)
    dpkg = json.loads((provdir / "dpkg-provenance.json").read_text())
    for package in dpkg["packages"]:
        for archive in package["source_archives"].values():
            path = provdir / "ubuntu-src" / archive["name"]
            if not path.is_file():
                raise ProvenanceError(f"Ubuntu source missing: {path}")
            algorithm, expected = archive["checksum"].split(":", 1)
            digest = hashlib.new(algorithm.lower(), path.read_bytes()).hexdigest()
            if digest != expected:
                raise ProvenanceError(f"Ubuntu source hash mismatch: {path}")
    records.extend(collect_python_inputs(directory, appdir))
    collect_runtime_notices(directory, appdir)
    (provdir / "source-mirror.json").write_text(json.dumps(records, indent=2) + "\n")
    source_files = sorted(
        path
        for root in (directory, provdir / "ubuntu-src")
        for path in root.iterdir()
        if path.is_file()
    )
    hashes = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(provdir)}"
        for path in source_files
    ]
    (provdir / "SOURCE-SHA256SUMS").write_text("\n".join(hashes) + "\n")


def bundle_sources(provdir: Path, recipe: Path) -> Path:
    """Package source bytes, exact recipes, input locks and notice inventories."""
    required = (
        "sources",
        "ubuntu-src",
        "source-mirror.json",
        "SOURCE-SHA256SUMS",
        "PROVENANCE.json",
        "SOURCE-OFFER.txt",
        "dpkg-provenance.json",
        "wheels-provenance.json",
        "pip-report.json",
        "conveyed-files.tsv",
        "license-inventory",
    )
    for name in required:
        if not (provdir / name).exists():
            raise ProvenanceError(f"public source bundle input missing: {name}")
    target = provdir / "winpodx-appimage-sources.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        for name in required:
            archive.add(provdir / name, arcname=name)
        archive.add(
            recipe,
            arcname="build-inputs/packaging/appimage",
            filter=lambda entry: None if "__pycache__" in entry.name else entry,
        )
        workflow = recipe.parents[1] / ".github/workflows/appimage-publish.yml"
        archive.add(workflow, arcname="build-inputs/appimage-publish.yml")
    return target


def write_release_hashes(provdir: Path, binary: Path) -> None:
    """Bind the actual AppImage to its adjacent source, notice and lock archive."""
    names = (
        binary,
        provdir / "winpodx-appimage-sources.tar.gz",
        provdir / "PROVENANCE.json",
        provdir / "SOURCE-OFFER.txt",
    )
    lines = []
    for path in names:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    (provdir / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("mirror", "bundle", "hashes"))
    parser.add_argument("--provdir", type=Path, required=True)
    parser.add_argument("--appdir", type=Path)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    if args.stage == "mirror":
        if args.appdir is None:
            parser.error("mirror requires --appdir")
        mirror_sources(args.provdir, args.appdir)
        notices = args.appdir / "usr/share/doc/winpodx/third-party"
        shutil.copytree(notices, args.provdir / "license-inventory", dirs_exist_ok=True)
    elif args.stage == "bundle":
        bundle_sources(args.provdir, Path(__file__).resolve().parent)
    else:
        if args.binary is None:
            parser.error("hashes requires --binary")
        write_release_hashes(args.provdir, args.binary)


if __name__ == "__main__":
    main()
