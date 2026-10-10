# SPDX-License-Identifier: MIT
"""Fail-closed provenance tests for the Thin AppImage build pipeline.

Loads the collector modules from ``packaging/appimage/`` (not a package)
via ``sys.path``, drives them against toy dpkg / wheel / runtime
fixtures, and asserts the fail-closed contract: an unknown owner,
license, text, or source BLOCKS the collection instead of being
invented.  Also locks the workflow's build inputs in lockstep with
``provenance_pins.py`` and guards the tag-only release attach.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APPIMAGE_DIR = REPO_ROOT / "packaging" / "appimage"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "appimage-publish.yml"
BUNDLE_SCRIPT = APPIMAGE_DIR / "bundle-system-bins.sh"

sys.path.insert(0, str(APPIMAGE_DIR))

import provenance_dpkg as dpkg  # noqa: E402
import provenance_elf as elf  # noqa: E402
import provenance_manifest as manifest  # noqa: E402
import provenance_pins as pins  # noqa: E402
import provenance_sources as sources  # noqa: E402
import provenance_wheels as wheels  # noqa: E402

TOY_COPYRIGHT = (
    "Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/\n"
    "Upstream-Name: FreeRDP\n"
    "Source: https://github.com/FreeRDP/FreeRDP\n"
    "Files: *\n"
    "Copyright: 2024 FreeRDP contributors\n"
    "License: Apache-2.0\n"
    " On Debian systems, the full text can be found in\n"
    " /usr/share/common-licenses/Apache-2.0\n"
)

APT_URIS = (
    "\n".join(
        [
            "'http://archive.ubuntu.com/ubuntu/pool/main/f/freerdp3/"
            "freerdp3_3.32.1%2bdfsg.orig.tar.xz' freerdp3_3.32.1+dfsg.orig.tar.xz "
            "5377792 SHA512:" + "a" * 128,
            "'http://archive.ubuntu.com/ubuntu/pool/main/f/freerdp3/"
            "freerdp3_3.32.1%2bdfsg-0ubuntu0.24.04.1.debian.tar.xz' "
            "freerdp3_3.32.1+dfsg-0ubuntu0.24.04.1.debian.tar.xz 48168 SHA512:" + "b" * 128,
            "'http://archive.ubuntu.com/ubuntu/pool/main/f/freerdp3/"
            "freerdp3_3.32.1%2bdfsg-0ubuntu0.24.04.1.dsc' "
            "freerdp3_3.32.1+dfsg-0ubuntu0.24.04.1.dsc 3686 SHA512:" + "c" * 128,
        ]
    )
    + "\n"
)

READELF_OK = (
    "Version needs section '.gnu.version_r' contains 1 entries:\n"
    "  0x0010: Name: GLIBC_2.2.5  Flags: none\n"
    "  0x0020: Name: GLIBC_2.38  Flags: none\n"
)
READELF_OVER = (
    "Version needs section '.gnu.version_r' contains 1 entries:\n"
    "  0x0010: Name: GLIBC_2.2.5  Flags: none\n"
    "  0x0020: Name: GLIBC_2.40  Flags: none\n"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_dpkg_root(
    tmp_path: Path,
    *,
    copyright_text: str = TOY_COPYRIGHT,
    with_common: bool = True,
    with_copyright: bool = True,
) -> Path:
    root = tmp_path / "root"
    (root / "var/lib/dpkg/info").mkdir(parents=True)
    (root / "usr/share/common-licenses").mkdir(parents=True)
    (root / "var/lib/dpkg/status").write_text(
        "Package: freerdp3-x11\n"
        "Status: install ok installed\n"
        "Version: 3.32.1+dfsg-0ubuntu0.24.04.1\n"
        "Source: freerdp3\n\n"
        "Package: removed-pkg\n"
        "Status: deinstall ok config-files\n"
        "Version: 1.0\n",
        encoding="utf-8",
    )
    (root / "var/lib/dpkg/info/freerdp3-x11.list").write_text(
        "/usr/bin/xfreerdp3\n/usr/lib/x86_64-linux-gnu/libfreerdp3-3.so.3\n",
        encoding="utf-8",
    )
    doc = root / "usr/share/doc/freerdp3-x11"
    doc.mkdir(parents=True)
    if with_copyright:
        (doc / "copyright").write_text(copyright_text, encoding="utf-8")
    if with_common:
        (root / "usr/share/common-licenses/Apache-2.0").write_text(
            "Apache License 2.0 full text\n", encoding="utf-8"
        )
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/lib/x86_64-linux-gnu").mkdir(parents=True)
    (root / "usr/bin/xfreerdp3").write_bytes(b"\x7fELF-toy-bin")
    (root / "usr/lib/x86_64-linux-gnu/libfreerdp3-3.so.3").write_bytes(b"\x7fELF-toy-lib")
    return root


def toy_conveyed(root: Path) -> str:
    lines = []
    for src, dst in [
        ("/usr/bin/xfreerdp3", "usr/bin/xfreerdp3"),
        ("/usr/lib/x86_64-linux-gnu/libfreerdp3-3.so.3", "usr/lib/libfreerdp3-3.so.3"),
    ]:
        digest = _sha((root / src.lstrip("/")).read_bytes())
        lines.append(f"{digest}\t{src}\t{dst}")
    return "\n".join(lines) + "\n"


def make_site_packages(tmp_path: Path) -> Path:
    site = tmp_path / "site-packages"
    pyside = site / "pyside6-6.12.0.dist-info"
    pyside.mkdir(parents=True)
    (pyside / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: PySide6\nVersion: 6.12.0\n"
        "License: LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only\n",
        encoding="utf-8",
    )
    pillow = site / "pillow-12.3.0.dist-info"
    pillow.mkdir()
    (pillow / "METADATA").write_text(
        "Metadata-Version: 2.4\nName: pillow\nVersion: 12.3.0\nLicense-Expression: MIT-CMU\n",
        encoding="utf-8",
    )
    (pillow / "licenses").mkdir()
    (pillow / "licenses" / "LICENSE.txt").write_text("MIT-CMU text\n", encoding="utf-8")
    qt_lib = site / "PySide6" / "Qt" / "lib"
    qt_lib.mkdir(parents=True)
    (qt_lib / "libQt6Core.so.6").write_bytes(b"Qt runtime\x006.12.0\x00")
    return site


def toy_pip_report() -> dict:
    return {
        "install": [
            {
                "metadata": {"name": "PySide6", "version": "6.12.0"},
                "download_info": {
                    "url": "https://files.pythonhosted.org/pyside6-6.12.0.whl",
                    "archive_info": {"hash": "sha256=" + "1" * 64},
                },
            },
            {
                "metadata": {"name": "pillow", "version": "12.3.0"},
                "download_info": {
                    "url": "https://files.pythonhosted.org/pillow-12.3.0.whl",
                    "archive_info": {"hash": "sha256=" + "2" * 64},
                },
            },
        ]
    }


TOY_SDIST = {"url": "https://files.pythonhosted.org/pkg.tar.gz", "sha256": "3" * 64}


def _resolve_all(name: str, version: str) -> dict[str, str]:
    return dict(TOY_SDIST)


# --- conveyed manifest ---------------------------------------------------


def test_parse_conveyed_manifest_accepts_valid_tsv(tmp_path: Path):
    root = make_dpkg_root(tmp_path)
    conveyed = dpkg.parse_conveyed_manifest(toy_conveyed(root))
    assert [c.dst for c in conveyed] == ["usr/bin/xfreerdp3", "usr/lib/libfreerdp3-3.so.3"]
    assert all(len(c.sha256) == 64 for c in conveyed)


def test_parse_conveyed_manifest_blocks_malformed_line():
    with pytest.raises(pins.ProvenanceError, match="3 TSV fields"):
        dpkg.parse_conveyed_manifest("no\ttabs\there\textra\n")
    with pytest.raises(pins.ProvenanceError, match="bad sha256"):
        dpkg.parse_conveyed_manifest("zz\t/usr/bin/x\tusr/bin/x\n")


def test_parse_conveyed_manifest_blocks_empty_manifest():
    with pytest.raises(pins.ProvenanceError, match="empty"):
        dpkg.parse_conveyed_manifest("\n\n")


# --- dpkg status / owner map ---------------------------------------------


def test_parse_dpkg_status_extracts_installed_packages_only():
    status = dpkg.parse_dpkg_status(
        "Package: freerdp3-x11\n"
        "Status: install ok installed\n"
        "Version: 3.32.1+dfsg-0ubuntu0.24.04.1\n"
        "Source: freerdp3\n\n"
        "Package: removed-pkg\n"
        "Status: deinstall ok config-files\n"
        "Version: 1.0\n"
    )
    assert set(status) == {"freerdp3-x11"}
    assert status["freerdp3-x11"] == {
        "version": "3.32.1+dfsg-0ubuntu0.24.04.1",
        "source": "freerdp3",
        "source_version": "3.32.1+dfsg-0ubuntu0.24.04.1",
    }


def test_parse_dpkg_status_blocks_installed_package_without_version():
    with pytest.raises(pins.ProvenanceError, match="no Version"):
        dpkg.parse_dpkg_status("Package: broken\nStatus: install ok installed\n")


def test_owner_of_blocks_dual_ownership_but_allows_shared_dirs(tmp_path: Path):
    info = tmp_path / "info"
    info.mkdir()
    (info / "pkg-a.list").write_text("/.\n/usr\n/usr/bin\n/usr/bin/shared\n", encoding="utf-8")
    (info / "pkg-b.list").write_text("/usr\n/usr/bin\n/usr/bin/shared\n", encoding="utf-8")
    owners = dpkg.build_owner_map(info)
    assert "/usr/bin" in owners  # shared directory entry: not a conflict
    with pytest.raises(pins.ProvenanceError, match="refusing to guess"):
        dpkg.owner_of(owners, "/usr/bin/shared")
    with pytest.raises(pins.ProvenanceError, match="no dpkg owner"):
        dpkg.owner_of(owners, "/usr/bin/unclaimed")


# --- copyright / source URIs ---------------------------------------------


def test_parse_copyright_extracts_licenses_and_common_refs():
    info = dpkg.parse_copyright("freerdp3-x11", TOY_COPYRIGHT)
    assert "Apache-2.0" in info.license_names
    assert info.common_licenses == ("/usr/share/common-licenses/Apache-2.0",)
    assert info.upstream_source == "https://github.com/FreeRDP/FreeRDP"


def test_parse_copyright_blocks_empty_and_keeps_old_style_honest():
    with pytest.raises(pins.ProvenanceError, match="copyright file is empty"):
        dpkg.parse_copyright("pkg", "   \n")
    old_style = (
        "This package was debianized by someone.\n"
        "License of the library:\n"
        " terms of the GNU Lesser General Public License.\n"
        "On Debian systems, the complete text can be found in\n"
        " /usr/share/common-licenses/LGPL-2.1.\n"
    )
    info = dpkg.parse_copyright("libusb-1.0-0", old_style)
    assert info.license_names == ()  # no invented name
    assert info.common_licenses == ("/usr/share/common-licenses/LGPL-2.1",)


def test_parse_apt_source_uris_requires_orig_and_patch():
    archives = sources.parse_apt_source_uris("freerdp3", APT_URIS)
    assert set(archives) == {"orig", "patch", "dsc"}
    assert archives["orig"]["checksum"] == "SHA512:" + "a" * 128
    assert archives["patch"]["url"].endswith(".debian.tar.xz")


def test_parse_apt_source_uris_accepts_old_format_diff_gz():
    old_format = (
        "'http://archive.ubuntu.com/ubuntu/pool/main/libx/libxkbfile/"
        "libxkbfile_1.1.0.orig.tar.gz' libxkbfile_1.1.0.orig.tar.gz 441021 "
        "SHA512:" + "d" * 128 + "\n"
        "'http://archive.ubuntu.com/ubuntu/pool/main/libx/libxkbfile/"
        "libxkbfile_1.1.0.orig.tar.gz.asc' libxkbfile_1.1.0.orig.tar.gz.asc 801 "
        "SHA512:" + "e" * 128 + "\n"
        "'http://archive.ubuntu.com/ubuntu/pool/main/libx/libxkbfile/"
        "libxkbfile_1.1.0-1build4.diff.gz' libxkbfile_1.1.0-1build4.diff.gz 10342 "
        "SHA512:" + "f" * 128 + "\n"
    )
    archives = sources.parse_apt_source_uris("libxkbfile", old_format)
    assert set(archives) == {"orig", "patch"}
    assert archives["patch"]["url"].endswith(".diff.gz")
    assert archives["orig"]["name"] == "libxkbfile_1.1.0.orig.tar.gz"  # .asc skipped


def test_parse_apt_source_uris_blocks_missing_patch_archive():
    orig_only = APT_URIS.splitlines()[0] + "\n"
    with pytest.raises(pins.ProvenanceError, match="patch source archive"):
        sources.parse_apt_source_uris("freerdp3", orig_only)


# --- dpkg collection (fail-closed) ----------------------------------------


def test_collect_dpkg_provenance_maps_every_conveyed_file(tmp_path: Path):
    root = make_dpkg_root(tmp_path)
    conveyed = dpkg.parse_conveyed_manifest(toy_conveyed(root))
    packages, files, copy_plan = sources.collect_dpkg_provenance(
        root, conveyed, {"freerdp3": APT_URIS}
    )
    assert set(packages) == {"freerdp3-x11"}
    record = packages["freerdp3-x11"]
    assert record.version == "3.32.1+dfsg-0ubuntu0.24.04.1"
    assert "Apache-2.0" in record.copyright_info.license_names
    assert record.source_archives["orig"]["checksum"] == "SHA512:" + "a" * 128
    assert len(files) == 2
    assert all(entry["owner"] == "freerdp3-x11" for entry in files)
    assert (
        "usr/share/doc/freerdp3-x11/copyright",
        "usr/share/doc/winpodx/third-party/freerdp3-x11/copyright",
    ) in copy_plan
    assert (
        "usr/share/common-licenses/Apache-2.0",
        "usr/share/doc/winpodx/third-party/freerdp3-x11/common-licenses/Apache-2.0",
    ) in copy_plan


def test_collect_dpkg_provenance_blocks_unknown_owner(tmp_path: Path):
    root = make_dpkg_root(tmp_path)
    ghost = root / "usr/bin/ghost"
    ghost.write_bytes(b"\x7fELF-ghost")
    manifest = toy_conveyed(root) + f"{_sha(ghost.read_bytes())}\t/usr/bin/ghost\tusr/bin/ghost\n"
    conveyed = dpkg.parse_conveyed_manifest(manifest)
    with pytest.raises(pins.ProvenanceError, match="no dpkg owner"):
        sources.collect_dpkg_provenance(root, conveyed, {"freerdp3": APT_URIS})


def test_collect_dpkg_provenance_blocks_missing_conveyed_source(tmp_path: Path):
    root = make_dpkg_root(tmp_path)
    manifest = toy_conveyed(root) + f"{_sha(b'x')}\t/usr/bin/vanished\tusr/bin/vanished\n"
    conveyed = dpkg.parse_conveyed_manifest(manifest)
    with pytest.raises(pins.ProvenanceError, match="missing from build root"):
        sources.collect_dpkg_provenance(root, conveyed, {"freerdp3": APT_URIS})


def test_collect_dpkg_provenance_blocks_missing_copyright(tmp_path: Path):
    root = make_dpkg_root(tmp_path, with_copyright=False)
    conveyed = dpkg.parse_conveyed_manifest(toy_conveyed(root))
    with pytest.raises(pins.ProvenanceError, match="copyright"):
        sources.collect_dpkg_provenance(root, conveyed, {"freerdp3": APT_URIS})


def test_collect_dpkg_provenance_blocks_missing_common_license_text(tmp_path: Path):
    root = make_dpkg_root(tmp_path, with_common=False)
    conveyed = dpkg.parse_conveyed_manifest(toy_conveyed(root))
    with pytest.raises(pins.ProvenanceError, match="common-licenses"):
        sources.collect_dpkg_provenance(root, conveyed, {"freerdp3": APT_URIS})


def test_collect_dpkg_provenance_blocks_missing_source_uris(tmp_path: Path):
    root = make_dpkg_root(tmp_path)
    conveyed = dpkg.parse_conveyed_manifest(toy_conveyed(root))
    with pytest.raises(pins.ProvenanceError, match="apt source URIs"):
        sources.collect_dpkg_provenance(root, conveyed, {})


def test_collect_dpkg_provenance_blocks_missing_core_client(tmp_path: Path):
    root = make_dpkg_root(tmp_path)
    lib_only = toy_conveyed(root).splitlines()[1] + "\n"
    conveyed = dpkg.parse_conveyed_manifest(lib_only)
    with pytest.raises(pins.ProvenanceError, match="usr/bin/xfreerdp3"):
        sources.collect_dpkg_provenance(root, conveyed, {"freerdp3": APT_URIS})


# --- ELF checks -----------------------------------------------------------


def test_parse_glibc_max_finds_highest_reference():
    assert elf.parse_glibc_max(READELF_OK) == (2, 38)
    assert elf.parse_glibc_max("no glibc references") is None


def test_check_glibc_ceiling_blocks_above_and_passes_below():
    assert elf.check_glibc_ceiling("bin", READELF_OK, (2, 39)) == "2.38"
    with pytest.raises(pins.ProvenanceError, match="GLIBC_2.40"):
        elf.check_glibc_ceiling("bin", READELF_OVER, (2, 39))


def test_qt_version_from_libcore_reads_embedded_version(tmp_path: Path):
    lib = tmp_path / "libQt6Core.so.6"
    lib.write_bytes(b"Qt runtime\x006.12.0\x00")
    assert elf.qt_version_from_libcore(lib) == "6.12.0"


def test_qt_version_from_libcore_blocks_ambiguous(tmp_path: Path):
    lib = tmp_path / "libQt6Core.so.6"
    lib.write_bytes(b"Qt runtime\x006.12.0\x006.9.1\x00")
    with pytest.raises(pins.ProvenanceError, match="refusing to guess"):
        elf.qt_version_from_libcore(lib)


def test_parse_freerdp_version_extracts_and_blocks():
    assert elf.parse_freerdp_version("This is FreeRDP version 3.32.1 (3.32.1)\n") == "3.32.1"
    with pytest.raises(pins.ProvenanceError, match="unverified version"):
        elf.parse_freerdp_version("no version here")


# --- wheels ---------------------------------------------------------------


def test_parse_pip_report_requires_sha256_per_wheel():
    report = toy_pip_report()
    del report["install"][0]["download_info"]["archive_info"]["hash"]
    with pytest.raises(pins.ProvenanceError, match="no sha256 archive hash"):
        wheels.parse_pip_report(report)


def test_collect_wheel_provenance_records_closure(tmp_path: Path):
    site = make_site_packages(tmp_path)
    result = wheels.collect_wheel_provenance(
        site, toy_pip_report(), APPIMAGE_DIR / "licenses", _resolve_all
    )
    assert result["qt_version"] == "6.12.0"
    by_name = {w["name"]: w for w in result["wheels"]}
    assert by_name["PySide6"]["license_spdx"] == ("LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only")
    assert by_name["PySide6"]["sdist"] == TOY_SDIST
    assert by_name["pillow"]["license_texts"] == ["licenses/LICENSE.txt"]
    assert by_name["pillow"]["sha256"] == "2" * 64


def test_collect_wheel_provenance_uses_vendored_fallback_for_pyside6(tmp_path: Path):
    site = make_site_packages(tmp_path)
    result = wheels.collect_wheel_provenance(
        site, toy_pip_report(), APPIMAGE_DIR / "licenses", _resolve_all
    )
    pyside = next(w for w in result["wheels"] if w["name"] == "PySide6")
    assert any(
        text.startswith("vendored:packaging/appimage/licenses/pyside6/")
        for text in pyside["license_texts"]
    )
    assert any("LGPL" in text for text in pyside["license_texts"])


def test_collect_wheel_provenance_blocks_wheel_without_license_texts(tmp_path: Path):
    site = make_site_packages(tmp_path)
    report = toy_pip_report()
    report["install"].append(
        {
            "metadata": {"name": "mystery", "version": "1.0"},
            "download_info": {
                "url": "https://files.pythonhosted.org/mystery-1.0.whl",
                "archive_info": {"hash": "sha256=" + "4" * 64},
            },
        }
    )
    with pytest.raises(pins.ProvenanceError, match="no license text"):
        wheels.collect_wheel_provenance(site, report, APPIMAGE_DIR / "licenses", _resolve_all)


def test_collect_wheel_provenance_blocks_copyleft_without_sdist(tmp_path: Path):
    site = make_site_packages(tmp_path)
    with pytest.raises(pins.ProvenanceError, match="no exact sdist or pinned source"):
        wheels.collect_wheel_provenance(
            site, toy_pip_report(), APPIMAGE_DIR / "licenses", lambda name, version: None
        )


def test_collect_wheel_provenance_pinned_sdist_fallback_for_pyside6(tmp_path: Path):
    site = make_site_packages(tmp_path)
    pinned = {
        "pyside6": {
            "url": "https://download.qt.io/pyside-setup-everywhere-src-6.12.0.tar.xz",
            "sha256": "5" * 64,
            "source": "pinned:download.qt.io",
        }
    }
    result = wheels.collect_wheel_provenance(
        site,
        toy_pip_report(),
        APPIMAGE_DIR / "licenses",
        lambda name, version: None,
        pinned,
    )
    pyside = next(w for w in result["wheels"] if w["name"] == "PySide6")
    assert pyside["sdist"]["url"].endswith("pyside-setup-everywhere-src-6.12.0.tar.xz")
    assert pyside["sdist"]["sha256"] == "5" * 64


def test_collect_wheel_provenance_allows_permissive_without_sdist(tmp_path: Path):
    site = make_site_packages(tmp_path)
    report = {"install": [toy_pip_report()["install"][1]]}
    result = wheels.collect_wheel_provenance(
        site, report, APPIMAGE_DIR / "licenses", lambda name, version: None
    )
    assert result["wheels"][0]["name"] == "pillow"
    assert result["wheels"][0]["sdist"] is None


# --- pins ------------------------------------------------------------------


def test_archive_pin_blocks_malformed_sha256():
    with pytest.raises(pins.ProvenanceError, match="malformed"):
        pins.ArchivePin("bad", "1.0", "https://example.com/x", "not-a-hash")


def test_archive_pin_verify_file_blocks_mismatch(tmp_path: Path):
    target = tmp_path / "asset"
    target.write_bytes(b"payload")
    good = pins.ArchivePin("asset", "1.0", "https://example.com/x", _sha(b"payload"))
    good.verify_file(target)
    bad = pins.ArchivePin("asset", "1.0", "https://example.com/x", _sha(b"other"))
    with pytest.raises(pins.ProvenanceError, match="sha256 mismatch"):
        bad.verify_file(target)


def test_pins_hold_the_verified_values():
    assert pins.UBUNTU_DIGEST == (
        "sha256:f610ab94648195aa356059f5b41d6085c9d4d903c072430cdd1af7bdb646106b"
    )
    assert all(p.version == "3.32.1+dfsg-0ubuntu0.24.04.1" for p in pins.FREERDP_DPKG_PINS)
    assert pins.APPIMAGETOOL_PIN.version == "1.9.1"
    assert pins.TYPE2_RUNTIME_COMMIT == "8f39b89e2ac31e1640b3d3f7e9a5108e6ce805fa"
    assert pins.PYTHON_PIN.version == "3.11.17+20261009"
    assert pins.WHEEL_PINS == {
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
    assert pins.GLIBC_CEILING == (2, 39)


def test_workflow_build_inputs_are_lockstep_with_pins():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert pins.UBUNTU_DIGEST in text
    assert pins.APPIMAGETOOL_PIN.sha256 in text
    assert pins.TYPE2_RUNTIME_PIN.sha256 in text
    assert pins.PYTHON_PIN.sha256 in text
    for pin in pins.FREERDP_DPKG_PINS:
        assert f"{pin.package}={pin.version}" in text
    for name, version in pins.WHEEL_PINS.items():
        assert f"{name}=={version}" in text
    assert "fedora:41" not in text
    assert "releases/download/continuous/appimagetool" not in text


def test_workflow_repack_uses_pinned_runtime_file():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "appimagetool --runtime-file /tmp/runtime-x86_64 AppDir" in text


def test_workflow_release_attach_is_tag_push_only():
    text = WORKFLOW.read_text(encoding="utf-8")
    attach = text.split("Attach AppImage + provenance to GitHub Release", 1)[1]
    assert "if: github.event_name == 'push'" in attach


def test_workflow_emits_provenance_artifacts():
    text = WORKFLOW.read_text(encoding="utf-8")
    for artifact in (
        "PROVENANCE.json",
        "SOURCE-OFFER.txt",
        "conveyed-files.tsv",
        "dpkg-provenance.json",
        "wheels-provenance.json",
        "pip-report.json",
    ):
        assert artifact in text, f"workflow no longer emits {artifact}"
    assert "winpodx-appimage-provenance" in text
    assert "winpodx-appimage-source-archives" in text


def test_bundle_script_records_conveyed_manifest():
    text = BUNDLE_SCRIPT.read_text(encoding="utf-8")
    assert "conveyed-files.tsv" in text
    assert "sha256sum" in text
    assert "record" in text


# --- manifest emission ------------------------------------------------------


def toy_provenance() -> dict:
    dpkg_data = {
        "packages": [
            {
                "package": "freerdp3-x11",
                "version": "3.32.1+dfsg-0ubuntu0.24.04.1",
                "source_package": "freerdp3",
                "upstream_source": "https://github.com/FreeRDP/FreeRDP",
                "licenses": ["Apache-2.0"],
                "source_archives": sources.parse_apt_source_uris("freerdp3", APT_URIS),
            }
        ],
        "files": [
            {
                "path": "usr/bin/xfreerdp3",
                "sha256": "1" * 64,
                "owner": "freerdp3-x11",
                "version": "3.32.1+dfsg-0ubuntu0.24.04.1",
            }
        ],
    }
    wheels_data = {
        "qt_version": "6.12.0",
        "wheels": [
            {
                "name": "PySide6",
                "version": "6.12.0",
                "url": "https://files.pythonhosted.org/pyside6-6.12.0.whl",
                "sha256": "2" * 64,
                "license_spdx": "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only",
                "license_texts": ["vendored:packaging/appimage/licenses/pyside6/LGPL-3.0-only.txt"],
                "sdist": TOY_SDIST,
            }
        ],
    }
    return manifest.build_provenance(
        pins.as_dict(),
        dpkg_data,
        wheels_data,
        {"xfreerdp3_version": "3.32.1", "glibc_max": "2.38"},
    )


def test_build_provenance_merges_all_sections():
    provenance = toy_provenance()
    assert provenance["schema"] == "winpodx-appimage-provenance/1"
    assert provenance["freerdp"]["conveyed_files"][0]["owner"] == "freerdp3-x11"
    assert provenance["freerdp"]["xfreerdp3_version"] == "3.32.1"
    assert provenance["freerdp"]["glibc"] == {"ceiling": "2.39", "max_referenced": "2.38"}
    assert provenance["python_runtime"]["qt_version"] == "6.12.0"
    assert provenance["build_inputs"]["ubuntu_base"]["digest"] == pins.UBUNTU_DIGEST


def test_render_source_offer_lists_urls_checksums_and_replacement():
    offer = manifest.render_source_offer(toy_provenance())
    assert "freerdp3_3.32.1%2bdfsg.orig.tar.xz" in offer
    assert "SHA512:" + "a" * 128 in offer
    assert "qt-everywhere-src-6.12.0.tar.xz" in offer
    assert "pyside-setup-everywhere-src-6.12.0.tar.xz" in offer
    assert "--appimage-extract" in offer
    assert "--runtime-file" in offer
    assert "not legal advice" in offer


def test_write_manifests_writes_both_files(tmp_path: Path):
    provenance_path, offer_path = manifest.write_manifests(tmp_path, toy_provenance())
    assert json.loads(provenance_path.read_text(encoding="utf-8"))["schema"] == (
        "winpodx-appimage-provenance/1"
    )
    assert offer_path.name == "SOURCE-OFFER.txt"


# --- module size guard ------------------------------------------------------


def test_provenance_modules_stay_under_250_pure_loc():
    for module in sorted(APPIMAGE_DIR.glob("provenance*.py")):
        text = module.read_text(encoding="utf-8")
        pure = sum(
            1 for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
        )
        assert pure < 250, f"{module.name} grew past 250 pure LOC ({pure})"


def test_release_upload_requires_public_source_bundle():
    # Given / When: inspect the executable release-upload step.
    attach = WORKFLOW.read_text().split("gh release upload", 1)[1]
    # Then: source bytes, input locks and hashes travel beside the binary.
    assert '"provenance-out/winpodx-appimage-sources.tar.gz"' in attach
    assert '"provenance-out/SHA256SUMS"' in attach


def test_source_mirror_rejects_corrupt_cache(tmp_path: Path):
    # Given: a cached archive with bytes different from the trusted lock.
    import provenance_delivery as delivery

    pin = pins.ArchivePin("toy", "1", "https://example.org/toy.tar.gz", _sha(b"correct"))
    (tmp_path / "toy.tar.gz").write_bytes(b"corrupt")
    # When / Then: reject it rather than recording an unchecked source URL.
    with pytest.raises(pins.ProvenanceError, match="sha256 mismatch"):
        delivery.mirror_archive(pin, tmp_path)


def test_runtime_notice_collection_requires_dependency_sources(tmp_path: Path):
    # Given: an empty source mirror.
    import provenance_runtime as runtime

    # When / Then: an appended static runtime cannot omit its source/license closure.
    with pytest.raises(pins.ProvenanceError, match="runtime source missing"):
        runtime.collect_runtime_notices(tmp_path, tmp_path / "AppDir")


def test_runtime_notices_preserve_full_texts_when_libfuse_names_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: only a pinned synthetic libfuse source, with distinct original bytes.
    import provenance_runtime as runtime

    payloads = {
        "LICENSE": b"See LGPL2.txt and GPL2.txt.\r\n",
        "AUTHORS": b"Synthetic authors\n",
        "LGPL2.txt": b"Synthetic LGPL full text\r\n\xff\n",
        "GPL2.txt": b"Synthetic GPL full text\n\xfe",
        "nested/LGPL2.txt": b"Nested synthetic LGPL\r\n",
        "nested/GPL2.txt": b"Nested synthetic GPL\n",
    }
    archive = tmp_path / "fuse.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for relative, payload in payloads.items():
            member = tarfile.TarInfo("fuse-3.15.0/" + relative)
            member.size = len(payload)
            output.addfile(member, io.BytesIO(payload))
    pin = pins.ArchivePin(
        "libfuse-runtime-src",
        "3.15.0",
        "https://example.org/fuse.tar.gz",
        _sha(archive.read_bytes()),
    )
    monkeypatch.setattr(pins, "SOURCE_ARCHIVE_PINS", (pin,))
    monkeypatch.setattr(runtime, "RUNTIME_SOURCE_PINS", ())
    appdir = tmp_path / "AppDir"

    # When: collect notices from the source without executing or downloading it.
    runtime.collect_runtime_notices(tmp_path, appdir)

    # Then: preserve every original filename, relative path and byte verbatim.
    notices = appdir / "usr/share/doc/winpodx/third-party/type2-runtime/libfuse-runtime-src"
    assert {
        path.relative_to(notices).as_posix(): path.read_bytes()
        for path in notices.rglob("*")
        if path.is_file()
    } == payloads


def test_public_bundle_rejects_missing_inputs(tmp_path: Path):
    # Given: no mirrored source bytes or locks.
    import provenance_delivery as delivery

    # When / Then: refuse to create a publicly attachable empty source bundle.
    with pytest.raises(pins.ProvenanceError, match="public source bundle input missing"):
        delivery.bundle_sources(tmp_path, APPIMAGE_DIR)


def test_source_version_uses_explicit_dpkg_source_version():
    # Given: a binNMU package whose source version differs from its binary version.
    status = (
        "Package: sample\nStatus: install ok installed\nVersion: 1.0+b1\nSource: upstream (1.0)\n"
    )
    # When: parse the dpkg source identity.
    result = dpkg.parse_dpkg_status(status)
    # Then: apt must fetch the matching source, not the current archive candidate.
    assert result["sample"]["source"] == "upstream"
    assert result["sample"]["source_version"] == "1.0"


def test_runtime_notices_reject_missing_static_dependency(tmp_path: Path, monkeypatch):
    # Given: runtime source exists, but its static libfuse dependency does not.
    import provenance_runtime as runtime

    license_file = tmp_path / "LICENSE"
    license_file.write_bytes(b"original MIT text")
    archive = tmp_path / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(license_file, arcname="runtime/LICENSE")
    runtime_pin = pins.ArchivePin(
        "type2-runtime-src", "1", "https://example.org/runtime.tar.gz", _sha(archive.read_bytes())
    )
    fuse_pin = pins.ArchivePin(
        "libfuse-runtime-src", "1", "https://example.org/fuse.tar.gz", "1" * 64
    )
    monkeypatch.setattr(pins, "SOURCE_ARCHIVE_PINS", (runtime_pin, fuse_pin))
    # When / Then: the runtime's own MIT notice cannot hide a missing LGPL source.
    with pytest.raises(pins.ProvenanceError, match="runtime source missing: .*fuse.tar.gz"):
        runtime.collect_runtime_notices(tmp_path, tmp_path / "AppDir")
