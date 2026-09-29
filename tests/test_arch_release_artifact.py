# SPDX-License-Identifier: MIT
"""Contract for the versioned Arch release asset and its checksum sidecar."""

from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/build_arch_package.sh"
WORKFLOW = ROOT / ".github/workflows/arch-publish.yml"
TEMPLATE = ROOT / "packaging/aur/PKGBUILD"
INSTALL = ROOT / "packaging/aur/winpodx.install"
PACKAGE = "winpodx-0.11.0-1-x86_64.pkg.tar.zst"


@pytest.fixture
def build_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, mode="w:gz") as source:
        for name, content in (
            ("PKGBUILD", TEMPLATE.read_bytes()),
            ("winpodx.install", INSTALL.read_bytes()),
        ):
            entry = tarfile.TarInfo(f"winpodx-0.11.0/packaging/aur/{name}")
            entry.size = len(content)
            source.addfile(entry, io.BytesIO(content))
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        'if [ "${FAKE_CURL_FAIL:-0}" = 1 ]; then exit 22; fi\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = -o ]; then shift; output="$1"; fi\n'
        "  shift\n"
        "done\n"
        'cp "$FAKE_SOURCE" "$output"\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)
    makepkg = bin_dir / "makepkg"
    makepkg.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        "test -f winpodx.install\ncat PKGBUILD\n"
        'if [ "${FAKE_PACKAGE_COUNT:-1}" = 0 ]; then exit 0; fi\n'
        "printf 'package payload' > winpodx-0.11.0-1-x86_64.pkg.tar.zst\n"
        'if [ "${FAKE_PACKAGE_COUNT:-1}" = 2 ]; then\n'
        "  printf 'other' > unexpected.pkg.tar.zst\n"
        "fi\n",
        encoding="utf-8",
    )
    makepkg.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_SOURCE": str(archive)}
    return tmp_path, env


def _build(
    tmp_path: Path, env: dict[str, str], tag: str = "v0.11.0"
) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.is_file(), "Arch release build script must exist"
    return subprocess.run(
        ["bash", str(SCRIPT), tag, str(tmp_path / "dist")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_recipe_renders_x86_64_and_preserves_aur_template(build_fixture):
    tmp_path, env = build_fixture
    template_before = TEMPLATE.read_bytes()
    install_before = INSTALL.read_bytes()
    # Given a version tag and a known source archive; when the build runs.
    result = _build(tmp_path, env)
    # Then only the staged recipe changes, never either tracked AUR template.
    assert result.returncode == 0, result.stderr
    assert TEMPLATE.read_bytes() == template_before
    assert INSTALL.read_bytes() == install_before
    assert "pkgver=__PKGVER__" in template_before.decode()
    assert "sha256sums=('__SHA256__')" in template_before.decode()
    assert "arch=('any')" in template_before.decode()
    assert (tmp_path / "dist" / PACKAGE).is_file()


def test_source_checksum_and_arch_are_stamped_in_the_build_recipe(build_fixture):
    tmp_path, env = build_fixture
    # Given a source archive; when the staged recipe is built.
    result = _build(tmp_path, env)
    # Then makepkg received the actual source checksum and x86_64 metadata.
    assert result.returncode == 0, result.stderr
    recipe = result.stdout
    assert "pkgver=0.11.0" in recipe
    assert "arch=('x86_64')" in recipe
    source_digest = hashlib.sha256(Path(env["FAKE_SOURCE"]).read_bytes()).hexdigest()
    assert f"sha256sums=('{source_digest}')" in recipe


def test_older_tag_uses_its_archived_pkgbuild_not_current_template(build_fixture):
    tmp_path, env = build_fixture
    archive = Path(env["FAKE_SOURCE"])
    old_recipe = TEMPLATE.read_bytes().replace(
        b"data/org.winpodx.WinPodX.metainfo.xml", b"data/previous-release-file.xml"
    )
    with tarfile.open(archive, mode="w:gz") as source:
        for name, content in (("PKGBUILD", old_recipe), ("winpodx.install", INSTALL.read_bytes())):
            entry = tarfile.TarInfo(f"winpodx-0.11.0/packaging/aur/{name}")
            entry.size = len(content)
            source.addfile(entry, io.BytesIO(content))
    # Given a tag whose packaging files differ from HEAD; when the build runs.
    result = _build(tmp_path, env)
    # Then its recipe matches the tagged source, not a newer incompatible template.
    assert result.returncode == 0, result.stderr
    assert "data/previous-release-file.xml" in result.stdout
    assert "data/org.winpodx.WinPodX.metainfo.xml" not in result.stdout


def test_sidecar_uses_actual_package_digest_and_basename(build_fixture):
    tmp_path, env = build_fixture
    # Given a completed package; when the build writes the sidecar.
    result = _build(tmp_path, env)
    # Then sha256sum can verify it in the artifact directory, not only in staging.
    assert result.returncode == 0, result.stderr
    dist = tmp_path / "dist"
    package = dist / PACKAGE
    checksum = dist / f"{PACKAGE}.sha256"
    expected_digest = hashlib.sha256(package.read_bytes()).hexdigest()
    assert checksum.read_text() == f"{expected_digest}  {PACKAGE}\n"
    verified = subprocess.run(
        ["sha256sum", "-c", checksum.name], cwd=dist, capture_output=True, text=True, check=False
    )
    assert verified.returncode == 0, verified.stderr
    package.write_bytes(b"tampered")
    tampered = subprocess.run(
        ["sha256sum", "-c", checksum.name], cwd=dist, capture_output=True, text=True, check=False
    )
    assert tampered.returncode != 0


@pytest.mark.parametrize("tag", ["REL-v0.11.0", "v0.11.0;touch BAD", "v0.11.0-RTM1", "main"])
def test_invalid_tag_fails_before_download(build_fixture, tag: str):
    tmp_path, env = build_fixture
    # Given an unsupported/untrusted ref; when the helper starts.
    result = _build(tmp_path, env, tag)
    # Then it rejects the input without producing a package.
    assert result.returncode != 0
    assert "tag" in result.stderr.lower()
    assert not (tmp_path / "dist" / PACKAGE).exists()


@pytest.mark.parametrize("failure", ["empty", "http"])
def test_download_failure_cannot_produce_a_package(build_fixture, failure: str):
    tmp_path, env = build_fixture
    if failure == "empty":
        Path(env["FAKE_SOURCE"]).write_bytes(b"")
    else:
        env["FAKE_CURL_FAIL"] = "1"
    # Given a failed source fetch; when the helper starts.
    result = _build(tmp_path, env)
    # Then no package or sidecar is created.
    assert result.returncode != 0
    assert not list((tmp_path / "dist").glob("*.pkg.tar.zst*"))


@pytest.mark.parametrize("count", ["0", "2"])
def test_missing_or_ambiguous_build_output_fails(build_fixture, count: str):
    tmp_path, env = build_fixture
    env["FAKE_PACKAGE_COUNT"] = count
    # Given zero or multiple package outputs; when collection runs.
    result = _build(tmp_path, env)
    # Then an arbitrary/stale package cannot be attached.
    assert result.returncode != 0
    assert not list((tmp_path / "dist").glob("*.pkg.tar.zst*"))


def test_workflow_builds_on_version_tags_not_release_marker():
    assert WORKFLOW.is_file(), "Arch publish workflow must exist"
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r'on:\s*\n\s*push:\s*\n\s*tags:\s*\n\s*- "v\*\.\*\.\*"', text)
    assert "workflow_dispatch:" in text
    assert "actions/checkout@v5" in text
    default_ref = "github.event.repository.default_branch"
    assert f"github.event_name == 'push' && github.ref || {default_ref}" in text
    assert "container: archlinux:latest" in text
    assert "AUR_SSH_PRIVATE_KEY" not in text


def test_workflow_retains_both_assets_and_only_attaches_to_existing_release():
    assert WORKFLOW.is_file(), "Arch publish workflow must exist"
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/upload-artifact@v4" in text
    assert "dist/*.pkg.tar.zst" in text
    assert "dist/*.pkg.tar.zst.sha256" in text
    assert "if-no-files-found: error" in text
    assert "gh release view" in text
    assert "gh release upload" in text
    assert "--clobber" in text
    assert "gh release create" not in text
    assert "softprops/action-gh-release" not in text
    assert "48" in text and "sleep 15" in text
