"""Debhelper must preserve the pinned OEM archive without disabling normalization."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Final

import pytest

ROOT: Final = Path(__file__).resolve().parents[1]
ARCHIVE: Final = "rdprrap-0.3.0-windows-x64.zip"


def _normalization_command() -> list[str]:
    lines = (ROOT / "debian/rules").read_text().splitlines()
    target = "override_dh_strip_nondeterminism:"
    if target not in lines:
        return ["dh_strip_nondeterminism"]
    index = lines.index(target) + 1
    commands: list[list[str]] = []
    while index < len(lines) and lines[index].startswith("\t"):
        commands.append(shlex.split(lines[index].strip()))
        index += 1
    assert len(commands) == 1
    return commands[0]


def test_normalization_excludes_only_pinned_archive() -> None:
    # Given / When: parse the executable recipe, not its explanatory prose.
    command = _normalization_command()
    # Then: normal debhelper execution retains exactly one narrow exclusion.
    assert command == ["dh_strip_nondeterminism", f"-X{ARCHIVE}"]


@pytest.mark.skipif(
    shutil.which("dh_strip_nondeterminism") is None,
    reason="real Debian build helper is unavailable",
)
@pytest.mark.parametrize("filename", [ARCHIVE, "ordinary.zip"])
def test_archive_bytes_when_real_helper_runs(tmp_path: Path, filename: str) -> None:
    # Given: an isolated package buildroot with deliberately noncanonical ZIP metadata.
    debian = tmp_path / "debian"
    payload = debian / "winpodx/usr/share/winpodx/config/oem" / filename
    payload.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "debian/control", debian / "control")
    shutil.copyfile(ROOT / "debian/changelog", debian / "changelog")
    (debian / "rules").write_text("#!/usr/bin/make -f\n")
    member = zipfile.ZipInfo("fixture.txt", date_time=(2025, 7, 9, 12, 34, 56))
    member.create_system = 3
    member.external_attr = 0o100644 << 16
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(member, b"normalization control\n")
    original = payload.read_bytes()
    environment = dict(os.environ, SOURCE_DATE_EPOCH="1700000000")

    # When: run the actual installed helper using the production recipe's arguments.
    subprocess.run(
        _normalization_command(),
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        timeout=30,
    )

    # Then: opaque pinned bytes survive; unrelated ZIP metadata still normalizes.
    if filename == ARCHIVE:
        assert payload.read_bytes() == original
    else:
        assert payload.read_bytes() != original
        with zipfile.ZipFile(payload) as archive:
            assert archive.read("fixture.txt") == b"normalization control\n"
