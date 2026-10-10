"""RPM macro regression; native handler tests run inside Fedora buildroots."""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Final

import pytest

SPEC: Final = Path(__file__).resolve().parents[1] / "packaging/rpm/winpodx.spec"
PIN: Final = "22f2c25230d40bfaa3290bc0ab43d66ae4314eb09c9545ed983f3a7e95b1fbb3"
EPOCH: Final = "1776643200"


def test_zip_exclusion_when_fedora_preserves_existing_options() -> None:
    # Given: the production macro definition inside its platform guard.
    spec = SPEC.read_text()
    block = re.search(r"%if 0%\{\?fedora\}\n(.*?)%endif", spec, re.S)
    assert block is not None
    definition = re.search(r"^%global add_determinism_options (.+)$", block[1], re.M)
    assert definition is not None

    # When: RPM's optional existing-options expansion is supplied a distinct value.
    options = shlex.split(definition[1].replace("%{?add_determinism_options}", "-v"))

    # Then: only ZIP is excluded and existing options survive.
    assert options == ["-v", "--handler=-zip"]


@pytest.fixture
def native_fedora() -> int:
    if not os.environ.get("WINPODX_RPM_NATIVE_TESTS"):
        pytest.skip("run with WINPODX_RPM_NATIVE_TESTS=1 inside a Fedora RPM buildroot")
    result = subprocess.run(
        ["rpm", "--eval", "%{fedora}"], check=True, capture_output=True, text=True, timeout=10
    )
    return int(result.stdout.strip())


def _command(spec: str, root: Path) -> list[str]:
    """Expand the real buildroot hook through rpmspec without executing scriptlets."""
    probe = root.parent / "probe.spec"
    probe.write_text(
        spec.split("%changelog", 1)[0]
        + f"\n%global buildroot {root}\n%check\n# RPM_ARCHIVE_PROBE\n"
        + "%{__os_install_post_build_reproducibility}\n"
    )
    result = subprocess.run(
        ["rpmspec", "--define", "add_determinism_options -v", "--parse", str(probe)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    command = shlex.split(result.stdout.split("# RPM_ARCHIVE_PROBE\n", 1)[1].strip())
    assert command[0] in ("/usr/bin/add-determinism", "/usr/bin/add-det")
    assert "--brp" in command
    assert "-v" in command
    assert any(arg.startswith("-j") for arg in command)
    assert command[-1] == str(root)
    return command


def _postprocess(command: list[str], root: Path) -> None:
    """Run the native hook only against this test's temporary buildroot."""
    subprocess.run(
        command,
        check=True,
        env={**os.environ, "RPM_BUILD_ROOT": str(root), "SOURCE_DATE_EPOCH": EPOCH},
        timeout=30,
    )


def test_original_hook_when_zip_is_pinned_breaks_raw_identity(
    tmp_path: Path, native_fedora: int
) -> None:
    # Given: a copy of the original pinned archive, never the original itself.
    assert native_fedora in (42, 43, 44)
    root = tmp_path / "buildroot"
    root.mkdir()
    original = SPEC.parents[2] / "config/oem/rdprrap-0.3.0-windows-x64.zip"
    archive = root / original.name
    shutil.copyfile(original, archive)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == PIN
    with zipfile.ZipFile(archive) as source:
        members = {name: source.read(name) for name in source.namelist()}
    baseline = re.sub(r"^%global add_determinism_options .+\n", "", SPEC.read_text(), flags=re.M)

    # When: the unchanged native BRP hook processes the archive.
    _postprocess(_command(baseline, root), root)

    # Then: raw identity fails although nested contents are identical.
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == (
        "d59832e2d818f671e7aab8695e9060f1c139155c7a46cec6547267042c9b005f"
    )
    with zipfile.ZipFile(archive) as normalized:
        assert {name: normalized.read(name) for name in normalized.namelist()} == members


def test_hook_when_zip_excluded_preserves_pin_and_processes_gzip(
    tmp_path: Path, native_fedora: int
) -> None:
    # Given: a trusted ZIP copy and a benign gzip with a timestamp newer than epoch.
    assert native_fedora in (42, 43, 44)
    root = tmp_path / "buildroot"
    root.mkdir()
    original = SPEC.parents[2] / "config/oem/rdprrap-0.3.0-windows-x64.zip"
    archive = root / original.name
    shutil.copyfile(original, archive)
    compressed = root / "fixture.gz"
    compressed.write_bytes(gzip.compress(b"benign non-ZIP fixture\n", mtime=2000000000))
    before = compressed.read_bytes()

    # When: the repository SPEC's expanded native BRP hook runs.
    _postprocess(_command(SPEC.read_text(), root), root)

    # Then: ZIP still meets its pin; gzip normalization remains active.
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == PIN
    assert compressed.read_bytes() != before
    assert int.from_bytes(compressed.read_bytes()[4:8], "little") == int(EPOCH)
    assert gzip.decompress(compressed.read_bytes()) == b"benign non-ZIP fixture\n"


def test_hook_when_non_fedora_keeps_distribution_options(
    tmp_path: Path, native_fedora: int
) -> None:
    # Given: RPM evaluates the same SPEC for a non-Fedora target.
    assert native_fedora in (42, 43, 44)
    root = tmp_path / "buildroot"
    root.mkdir()
    spec = "%global fedora 0\n" + SPEC.read_text()

    # When: the distribution hook is expanded.
    command = _command(spec, root)

    # Then: the Fedora-only exclusion does not override other targets.
    assert "--handler=-zip" not in command
