#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Provenance collection CLI for the Thin AppImage (fail-closed).

Three subcommands, one per build stage:

* ``dpkg``   -- runs INSIDE the pinned Ubuntu 24.04 build container:
                maps every conveyed file to its dpkg owner, version,
                copyright + common-license texts, and the signed-archive
                source URLs; checks the glibc ceiling and the actual
                ``xfreerdp3 --version``; copies license texts into the
                AppDir; writes ``dpkg-provenance.json``.
* ``wheels`` -- runs on the build runner: records the resolved wheel
                closure (URLs + sha256 from pip's JSON report), license
                texts, and exact PyPI sdist URLs; writes
                ``wheels-provenance.json``.
* ``merge``  -- merges both records with the pinned inputs into
                PROVENANCE.json + SOURCE-OFFER.txt, copies them into the
                AppDir and the artifact output directory.

Every stage fails closed: unknown owner/license/text/source blocks the
build instead of shipping an unattributable byte.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from provenance_dpkg import (  # noqa: E402
    build_owner_map,
    owner_of,
    parse_conveyed_manifest,
    parse_dpkg_status,
)
from provenance_elf import (  # noqa: E402
    check_glibc_ceiling,
    parse_freerdp_version,
    parse_glibc_max,
)
from provenance_manifest import build_provenance, write_manifests  # noqa: E402
from provenance_pins import GLIBC_CEILING, ProvenanceError  # noqa: E402
from provenance_pins import as_dict as pins_dict  # noqa: E402
from provenance_sources import collect_dpkg_provenance  # noqa: E402
from provenance_wheels import collect_wheel_provenance  # noqa: E402


def _run(cmd: list[str], cwd: str | None = None) -> str:
    """Run a build-time helper; fail closed on any non-zero exit."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=cwd)
    except OSError as exc:
        raise ProvenanceError(f"cannot run {cmd[0]}: {exc}") from exc
    if proc.returncode != 0:
        raise ProvenanceError(f"{cmd} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def _resolve_sdist(name: str, version: str) -> dict[str, str] | None:
    """Exact sdist URL + sha256 for one wheel, from PyPI's JSON API.

    ``None`` when no sdist is published (HTTP 404 or no sdist file);
    any other network failure raises -- fail closed.
    """
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise ProvenanceError(f"{name} {version}: PyPI JSON query failed: {exc}") from exc
    for entry in data.get("urls", []):
        if entry.get("packagetype") == "sdist":
            sha256 = entry.get("digests", {}).get("sha256")
            if not sha256:
                raise ProvenanceError(f"{name} {version}: sdist has no sha256 digest")
            return {"url": entry["url"], "sha256": sha256}
    return None


def _apply_copy_plan(root: Path, appdir: Path, copy_plan: list[tuple[str, str]]) -> None:
    for src_rel, dst_rel in copy_plan:
        src, dst = root / src_rel, appdir / dst_rel
        if not src.is_file():
            raise ProvenanceError(f"copy-plan source missing: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def cmd_dpkg(args: argparse.Namespace) -> None:
    root = Path(args.root)
    conveyed = parse_conveyed_manifest(Path(args.conveyed).read_text(encoding="utf-8"))
    status = parse_dpkg_status((root / "var/lib/dpkg/status").read_text(encoding="utf-8"))
    owners = build_owner_map(root / "var/lib/dpkg/info")
    source_packages: set[str] = set()
    for conveyed_file in conveyed:
        owner = owner_of(owners, conveyed_file.src)
        if owner not in status:
            raise ProvenanceError(f"conveyed {conveyed_file.src}: owner {owner} not in dpkg status")
        source_packages.add(f"{status[owner]['source']}={status[owner]['source_version']}")
    apt_uris = {
        source.split("=", 1)[0]: _run(["apt-get", "source", "--print-uris", source])
        for source in sorted(source_packages)
    }
    packages, files, copy_plan = collect_dpkg_provenance(root, conveyed, apt_uris)
    glibc_top: tuple[int, int] | None = None
    for conveyed_file in conveyed:
        text = _run(["readelf", "--version-info", conveyed_file.src])
        check_glibc_ceiling(conveyed_file.src, text, GLIBC_CEILING)
        top = parse_glibc_max(text)
        if top is not None and (glibc_top is None or top > glibc_top):
            glibc_top = top
    xfreerdp3_version = parse_freerdp_version(_run([str(root / "usr/bin/xfreerdp3"), "--version"]))
    _apply_copy_plan(root, Path(args.appdir), copy_plan)
    elf_checks = {
        "xfreerdp3_version": xfreerdp3_version,
        "glibc_max": f"{glibc_top[0]}.{glibc_top[1]}" if glibc_top else None,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "dpkg-provenance.json").write_text(
        json.dumps(
            {
                "packages": [package.to_dict() for package in packages.values()],
                "conveyed_files": files,
                "elf_checks": elf_checks,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.download_sources:
        # apt-get source verifies each download against the signed Sources
        # index -- the mirror is checksum-attested by Ubuntu's signing key.
        src_dir = out / "ubuntu-src"
        src_dir.mkdir(parents=True, exist_ok=True)
        for source in sorted(source_packages):
            _run(["apt-get", "source", "--download-only", source], cwd=str(src_dir))
    print(f"[provenance] dpkg: {len(files)} files, {len(packages)} packages -> {out}")


def _pinned_sdists() -> dict[str, dict[str, str]]:
    """Pinned corresponding-source archives for wheels with no PyPI sdist.

    The PySide6 family (incl. shiboken6) publishes no sdists on PyPI; its
    source is the pyside-setup-everywhere-src archive on download.qt.io,
    pinned in provenance_pins.py SOURCE_ARCHIVE_PINS.
    """
    pyside_setup = next(
        (
            archive
            for archive in pins_dict()["source_archives"]
            if archive["name"] == "pyside-setup-everywhere-src"
        ),
        None,
    )
    if pyside_setup is None:
        raise ProvenanceError("pyside-setup-everywhere-src pin missing from provenance_pins")
    fallback = {
        "url": pyside_setup["url"],
        "sha256": pyside_setup["sha256"],
        "source": "pinned:download.qt.io",
    }
    return {
        name: dict(fallback)
        for name in (
            "pyside6",
            "pyside6_addons",
            "pyside6_essentials",
            "pyside6_pdf",
            "pyside6_webengine",
            "shiboken6",
        )
    }


def cmd_wheels(args: argparse.Namespace) -> None:
    report = json.loads(Path(args.pip_report).read_text(encoding="utf-8"))
    vendored = Path(args.vendored) if args.vendored else None
    provenance = collect_wheel_provenance(
        Path(args.site_packages), report, vendored, _resolve_sdist, _pinned_sdists()
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "wheels-provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"[provenance] wheels: {len(provenance['wheels'])} wheels -> {out}")


def cmd_merge(args: argparse.Namespace) -> None:
    provdir = Path(args.provdir)
    dpkg = json.loads((provdir / "dpkg-provenance.json").read_text(encoding="utf-8"))
    wheels = json.loads((provdir / "wheels-provenance.json").read_text(encoding="utf-8"))
    provenance = build_provenance(
        pins_dict(),
        {"packages": dpkg["packages"], "files": dpkg["conveyed_files"]},
        wheels,
        dpkg["elf_checks"],
    )
    mirror = provdir / "source-mirror.json"
    if not mirror.is_file():
        raise ProvenanceError("source-mirror.json missing: run verified source mirror before merge")
    provenance["source_delivery"] = {
        "archive": "winpodx-appimage-sources.tar.gz",
        "method": "adjacent-release-assets-equivalent-network-access",
        "mirrored_inputs": json.loads(mirror.read_text(encoding="utf-8")),
        "static_runtime_alpine_resolution": "reconstructed-not-upstream-locked",
    }
    provenance_path, offer_path = write_manifests(Path(args.out), provenance)
    doc_dir = Path(args.appdir) / "usr/share/doc/winpodx"
    doc_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(provenance_path, doc_dir / provenance_path.name)
    shutil.copy2(offer_path, doc_dir / offer_path.name)
    print(f"[provenance] merge: {provenance_path} + {offer_path} (also inside AppDir)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    dpkg_parser = sub.add_parser("dpkg", help="collect dpkg provenance (in-container)")
    dpkg_parser.add_argument("--root", default="/", help="build-container root")
    dpkg_parser.add_argument("--conveyed", required=True, help="conveyed-files TSV manifest")
    dpkg_parser.add_argument("--appdir", required=True, help="AppDir to copy license texts into")
    dpkg_parser.add_argument("--out", required=True, help="output dir for dpkg-provenance.json")
    dpkg_parser.add_argument(
        "--download-sources",
        action="store_true",
        help="mirror orig+debian source archives (apt-verified) into <out>/ubuntu-src",
    )
    dpkg_parser.set_defaults(func=cmd_dpkg)

    wheels_parser = sub.add_parser("wheels", help="collect wheel provenance (runner)")
    wheels_parser.add_argument("--site-packages", required=True)
    wheels_parser.add_argument("--pip-report", required=True, help="pip --report JSON")
    wheels_parser.add_argument("--vendored", help="packaging/appimage/licenses dir")
    wheels_parser.add_argument("--out", required=True)
    wheels_parser.set_defaults(func=cmd_wheels)

    merge_parser = sub.add_parser("merge", help="emit PROVENANCE.json + SOURCE-OFFER.txt")
    merge_parser.add_argument("--provdir", required=True)
    merge_parser.add_argument("--appdir", required=True)
    merge_parser.add_argument("--out", required=True)
    merge_parser.set_defaults(func=cmd_merge)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ProvenanceError as exc:
        print(f"::error::provenance fail-closed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
