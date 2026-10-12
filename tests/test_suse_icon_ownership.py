"""Executable RPM dependency regression with opt-in native SUSE ownership proof."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Final

import pytest

SPEC: Final = Path(__file__).resolve().parents[1] / "packaging/rpm/winpodx.spec"
OWNER: Final = "hicolor-icon-theme"
DIRECTORIES: Final = (
    "/usr/share/icons/hicolor",
    "/usr/share/icons/hicolor/scalable",
    "/usr/share/icons/hicolor/scalable/apps",
)


def _rpm(arguments: list[str]) -> frozenset[str]:
    """Query bounded native metadata without installing or running scriptlets."""
    result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=15)
    return frozenset(result.stdout.splitlines())


@pytest.mark.parametrize("suse_version", (1500, 1560, 1600, 1699))
@pytest.mark.parametrize("query", ("--buildrequires", "--requires"))
def test_icon_owner_dependency_when_suse(suse_version: int, query: str) -> None:
    # Given: the actual SPEC and each supported SUSE macro branch.
    if shutil.which("rpmspec") is None:
        pytest.skip("rpmspec is required for executable RPM metadata regression")
    arguments = [
        "rpmspec",
        "--undefine",
        "fedora",
        "--undefine",
        "rhel",
        "--define",
        f"suse_version {suse_version}",
        "-q",
        query,
        str(SPEC),
    ]

    # When: RPM evaluates the executable package metadata.
    dependencies = _rpm(arguments)

    # Then: both buildroot ownership and installed-system ownership are required.
    assert OWNER in dependencies


@pytest.mark.parametrize("target", ("fedora 44", "rhel 9", "rhel 10"))
@pytest.mark.parametrize("query", ("--buildrequires", "--requires"))
def test_suse_owner_dependency_when_other_platform_is_absent(target: str, query: str) -> None:
    # Given: RPM evaluates the same SPEC outside the SUSE guard.
    if shutil.which("rpmspec") is None:
        pytest.skip("rpmspec is required for executable RPM metadata regression")
    arguments = [
        "rpmspec",
        "--undefine",
        "suse_version",
        "--undefine",
        "fedora",
        "--undefine",
        "rhel",
        "--define",
        target,
        "-q",
        query,
        str(SPEC),
    ]

    # When: native RPM parses the platform dependencies.
    dependencies = _rpm(arguments)

    # Then: the correction does not change Fedora or RHEL dependency policy.
    assert OWNER not in dependencies


def test_native_icon_directory_owner_when_suse() -> None:
    # Given: explicit opt-in inside an isolated native SUSE build container.
    if not os.environ.get("WINPODX_SUSE_NATIVE_TESTS"):
        pytest.skip("set WINPODX_SUSE_NATIVE_TESTS=1 inside a native SUSE buildroot")
    assert int(next(iter(_rpm(["rpm", "--eval", "%{suse_version}"])))) >= 1500

    # When: the actual RPM database resolves the three rejected directories.
    owners = _rpm(["rpm", "-qf", "--qf", "%{NAME}\n", *DIRECTORIES])
    owner_files = _rpm(["rpm", "-ql", OWNER])

    # Then: the dependency is the native directory owner, not arbitrary co-ownership.
    assert owners == frozenset({OWNER})
    assert set(DIRECTORIES) <= owner_files
