# SPDX-License-Identifier: MIT
"""Fail-closed source-archive provenance for conveyed dpkg packages.

Parses ``apt-get source --print-uris`` output (orig + patch layer, both
source formats, SHA256/SHA512 checksums from Ubuntu's signed Sources
index), builds per-package records (copyright + referenced common
license texts + exact source archives), and orchestrates the
fail-closed collection over the conveyed files: unknown owner, missing
copyright text, or a missing source archive BLOCKS the collection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from provenance_dpkg import (
    REQUIRED_CONVEYED,
    ConveyedFile,
    CopyrightInfo,
    build_owner_map,
    owner_of,
    parse_copyright,
    parse_dpkg_status,
)
from provenance_pins import ProvenanceError

_APT_URI_RE = re.compile(
    r"^'(?P<url>https?://[^']+)' (?P<name>\S+) (?P<size>\d+) "
    r"(?P<hash>(?:SHA512:[0-9a-f]{128}|SHA256:[0-9a-f]{64}))$"
)


def parse_apt_source_uris(source_package: str, text: str) -> dict[str, dict[str, str]]:
    """Parse ``apt-get source --print-uris`` output for one source package.

    Requires the orig tarball AND the patch layer -- ``.debian.tar.*``
    (source format 3.0) or ``.diff.*`` (old format 1.0).  A source offer
    without the exact matching patches is incomplete.  Detached
    signature files (``.asc``/``.sig``) are skipped so they cannot
    shadow the archive they sign.
    """
    archives: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        match = _APT_URI_RE.match(line.strip())
        if match is None:
            continue
        name = match.group("name")
        if name.endswith((".asc", ".sig")):
            continue
        if name.endswith(".dsc"):
            kind = "dsc"
        elif ".orig.tar." in name:
            kind = "orig"
        elif ".debian.tar." in name or ".diff." in name:
            kind = "patch"
        else:
            continue
        archives[kind] = {
            "url": match.group("url"),
            "name": name,
            "size": match.group("size"),
            "checksum": match.group("hash"),
        }
    for kind in ("orig", "patch"):
        if kind not in archives:
            raise ProvenanceError(
                f"{source_package}: no {kind} source archive in apt --print-uris output; "
                "cannot offer corresponding source"
            )
    return archives


@dataclass(frozen=True)
class PackageRecord:
    package: str
    version: str
    source_package: str
    source_version: str
    copyright_info: CopyrightInfo
    source_archives: dict[str, dict[str, str]]

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "version": self.version,
            "source_package": self.source_package,
            "source_version": self.source_version,
            "upstream_source": self.copyright_info.upstream_source,
            "licenses": list(self.copyright_info.license_names),
            "source_archives": self.source_archives,
        }


def _package_record(
    root: Path, package: str, status: dict[str, dict[str, str]], apt_uris: dict[str, str]
) -> PackageRecord:
    if package not in status:
        raise ProvenanceError(f"{package}: owner missing from dpkg status database")
    entry = status[package]
    copyright_path = root / "usr/share/doc" / package / "copyright"
    if not copyright_path.is_file():
        raise ProvenanceError(
            f"{package}: no /usr/share/doc/{package}/copyright in build root -- "
            "cannot convey license text"
        )
    info = parse_copyright(package, copyright_path.read_text(encoding="utf-8"))
    for common in info.common_licenses:
        if not (root / common.lstrip("/")).is_file():
            raise ProvenanceError(
                f"{package}: copyright references {common} but that text is missing"
            )
    source_package = entry["source"]
    if source_package not in apt_uris:
        raise ProvenanceError(
            f"{package}: no apt source URIs for source package {source_package!r}"
        )
    return PackageRecord(
        package=package,
        version=entry["version"],
        source_package=source_package,
        source_version=entry["source_version"],
        copyright_info=info,
        source_archives=parse_apt_source_uris(source_package, apt_uris[source_package]),
    )


def collect_dpkg_provenance(
    root: Path, conveyed: list[ConveyedFile], apt_uris: dict[str, str]
) -> tuple[dict[str, PackageRecord], list[dict[str, str]], list[tuple[str, str]]]:
    """Fail-closed collection over the conveyed files.

    Returns (packages, per-file records, copy plan).  The copy plan is a
    list of (absolute source path in ``root``, AppDir-relative destination)
    for copyright + referenced common-license texts.
    """
    status = parse_dpkg_status((root / "var/lib/dpkg/status").read_text(encoding="utf-8"))
    owners = build_owner_map(root / "var/lib/dpkg/info")
    packages: dict[str, PackageRecord] = {}
    files: list[dict[str, str]] = []
    copy_plan: list[tuple[str, str]] = []
    for conveyed_file in conveyed:
        if not (root / conveyed_file.src.lstrip("/")).is_file():
            raise ProvenanceError(f"conveyed {conveyed_file.src}: missing from build root")
        owner = owner_of(owners, conveyed_file.src)
        if owner not in packages:
            packages[owner] = _package_record(root, owner, status, apt_uris)
            record = packages[owner]
            copy_plan.append(
                (
                    f"usr/share/doc/{owner}/copyright",
                    f"usr/share/doc/winpodx/third-party/{owner}/copyright",
                )
            )
            for common in record.copyright_info.common_licenses:
                copy_plan.append(
                    (
                        common.lstrip("/"),
                        f"usr/share/doc/winpodx/third-party/{owner}/"
                        f"common-licenses/{Path(common).name}",
                    )
                )
        files.append(
            {
                "path": conveyed_file.dst,
                "sha256": conveyed_file.sha256,
                "owner": owner,
                "version": packages[owner].version,
            }
        )
    for required in REQUIRED_CONVEYED:
        if not any(f["path"] == required for f in files):
            raise ProvenanceError(
                f"core FreeRDP client {required} was not conveyed -- refusing to ship"
            )
    return packages, files, copy_plan
