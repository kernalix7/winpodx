# SPDX-License-Identifier: MIT
"""Release payload contracts for the 0.12.0 wheel, sdist, and source-copy routes.

Locks the shipping-audit repairs so a regression cannot silently re-drop the
QEMU disguise recipe from installed formats, lose the aggregate notices on the
local source-copy route, or re-ship dev/CI helpers and shim rebuild sources as
wheel runtime payload:

- the five QEMU disguise recipe sources ship in the wheel (never a patched
  QEMU binary or container image) so ``winpodx disguise build-image`` works
  from an installed copy, while every rebuild source stays in the sdist;
- guest scripts ship without ``scripts/ci`` / ``gen_web_i18n.py``; the shim's
  prebuilt binaries and every notice keep shipping while its Rust rebuild
  sources stay wheel-excluded;
- ``install.sh``'s local source-copy route keeps ``THIRD_PARTY_LICENSES.md``
  and the recipe (only the recipe subdir of ``packaging/``, not every
  packaging channel);
- dependency floors match the 0.12.0 resolution (Pillow 12.3+, CairoSVG 2.9+,
  pytest 9.0.3+) with the Python 3.10 floor and stdlib-only core untouched;
- the Nix wrapper drops the removed libvirt backend and the raw ``cp`` that
  reintroduced wheel-excluded data, relying on the wheel's shared data.

Set ``WINPODX_PAYLOAD_WHEEL=/path/to/winpodx-*.whl`` to additionally verify a
real built wheel's inventory and the recipe resolver from a controlled prefix
outside this checkout (skipped otherwise; CI runs the static contracts).
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / "install.sh"
FLAKE_NIX = ROOT / "flake.nix"

RECIPE_FILES = ("Dockerfile", "README.md", "patch-strings.sh", "ssdt-sensors.asl", "wsmt.asl")
# Never bundled in any shipped format: the disguise is built locally from the
# recipe, so a patched QEMU or container image must not appear anywhere.
RECIPE_FORBIDDEN_SUFFIXES = {".exe", ".img", ".qcow2", ".iso", ".tar", ".gz", ".zip", ".xz"}
SHIM = "config/oem/reverse-open/shim"
SHIM_REBUILD_SOURCES = (
    f"{SHIM}/.gitignore",
    f"{SHIM}/Cargo.toml",
    f"{SHIM}/Cargo.lock",
    f"{SHIM}/build.rs",
    f"{SHIM}/src/main.rs",
)
DEV_SCRIPTS = (
    "scripts/ci/build_arch_package.sh",
    "scripts/ci/obs_check_shipping.py",
    "scripts/ci/validate_discover_dryrun.py",
    "scripts/ci/verify_versions.py",
    "scripts/gen_web_i18n.py",
)
WHEEL = os.environ.get("WINPODX_PAYLOAD_WHEEL")


def _toml() -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return _toml()


@pytest.fixture(scope="module")
def shared_data(pyproject: dict) -> dict[str, str]:
    return pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["shared-data"]


@pytest.fixture(scope="module")
def wheel_excludes(pyproject: dict) -> list[str]:
    return pyproject["tool"]["hatch"]["build"]["targets"]["wheel"].get("exclude", [])


def _excluded(target: str, excludes: list[str]) -> bool:
    """Gitignore-style match of a wheel exclude pattern against a destination
    path, mirroring how hatchling matches shared-data files (by destination,
    i.e. ``share/winpodx/...``)."""
    for pattern in excludes:
        pat = pattern.rstrip("/")
        if pat.endswith("/**"):
            if target.startswith(pat[:-2] + "/"):
                return True
        elif target == pat or target.startswith(pat + "/"):
            return True
    return False


def _ships(rel: str, shared_data: dict[str, str], excludes: list[str]) -> bool:
    """True when a repo-relative file lands in the wheel via shared-data.

    Mirrors hatchling's shared-data selection: a mapped source ships the file
    unless a wheel exclude matches the destination path."""
    for source, dest in shared_data.items():
        if rel == source:
            target = dest
        elif rel.startswith(source.rstrip("/") + "/"):
            target = f"{dest}/{rel[len(source.rstrip('/')) + 1 :]}"
        else:
            continue
        if not _excluded(target, excludes):
            return True
    return False


def _repo_files(subdir: str) -> list[str]:
    base = ROOT / subdir
    return sorted(str(p.relative_to(ROOT)) for p in base.rglob("*") if p.is_file())


# ---- wheel payload: QEMU disguise recipe ----------------------------------


def test_wheel_maps_qemu_disguise_recipe(shared_data: dict[str, str]) -> None:
    # cli/disguise.py:_recipe_dir looks up bundle_dir()/packaging/qemu-disguise;
    # without this mapping the advertised local build capability needs a
    # source checkout (shipping-audit gap 1).
    assert shared_data.get("packaging/qemu-disguise") == "share/winpodx/packaging/qemu-disguise"


def test_qemu_recipe_payload_is_five_source_files() -> None:
    # The recipe is exactly the five build-recipe sources — never a patched
    # QEMU binary or container image in any shipped format.
    recipe = ROOT / "packaging" / "qemu-disguise"
    files = sorted(str(p.relative_to(recipe)) for p in recipe.rglob("*") if p.is_file())
    assert files == sorted(RECIPE_FILES)
    for path in recipe.rglob("*"):
        if path.is_file():
            assert path.suffix not in RECIPE_FORBIDDEN_SUFFIXES, f"binary/image in recipe: {path}"


# ---- wheel payload: guest scripts vs dev helpers --------------------------


def test_wheel_ships_guest_scripts_without_dev_helpers(
    shared_data: dict[str, str], wheel_excludes: list[str]
) -> None:
    # Every guest script must reach the wheel; the CI/website helpers are
    # rebuild/dev tooling and must stay out of the runtime payload.
    guest = _repo_files("scripts/windows")
    assert guest, "scripts/windows unexpectedly empty"
    for rel in guest:
        assert _ships(rel, shared_data, wheel_excludes), f"guest script dropped from wheel: {rel}"
    for rel in DEV_SCRIPTS:
        assert not _ships(rel, shared_data, wheel_excludes), (
            f"dev/CI helper must stay out of the wheel: {rel}"
        )


# ---- wheel payload: shim binaries/notices vs rebuild sources --------------


def test_wheel_keeps_shim_binaries_and_notices(
    shared_data: dict[str, str], wheel_excludes: list[str]
) -> None:
    # The prebuilt shim + rcedit and every adjacent notice (LICENSE*, NOTICE*,
    # build-provenance*) must keep shipping; unknown future notice files must
    # not be caught by the rebuild-source exclusions.
    shipped = _repo_files(SHIM)
    assert shipped, "shim payload unexpectedly empty"
    names = {Path(rel).name for rel in shipped}
    assert "winpodx-reverse-open-shim.exe" in names
    assert "rcedit.exe" in names
    for rel in shipped:
        top = Path(rel).name
        keeps = rel.startswith(f"{SHIM}/bin/") or top.startswith(
            ("LICENSE", "NOTICE", "build-provenance")
        )
        if keeps:
            assert _ships(rel, shared_data, wheel_excludes), (
                f"shim payload dropped from wheel: {rel}"
            )


def test_wheel_excludes_shim_rebuild_sources(
    shared_data: dict[str, str], wheel_excludes: list[str]
) -> None:
    # The Rust rebuild sources stay sdist-only; the wheel ships the prebuilt
    # binary under shim/bin/ instead (shipping-audit runtime-bloat finding).
    for rel in SHIM_REBUILD_SOURCES:
        assert not _ships(rel, shared_data, wheel_excludes), (
            f"shim rebuild source must stay out of the wheel: {rel}"
        )


# ---- sdist: every rebuild source stays -------------------------------------


def test_sdist_keeps_every_rebuild_source(pyproject: dict) -> None:
    # No global or sdist-target selection may drop the shim rebuild sources,
    # CI helpers, or the recipe: the sdist is the rebuild material.
    build_cfg = pyproject["tool"]["hatch"]["build"]
    targets = build_cfg["targets"]
    for cfg in (build_cfg, targets.get("sdist", {})):
        for key in ("exclude", "include", "only-include", "skip-excluded-dirs"):
            assert not cfg.get(key), f"{key} would drop sdist rebuild sources: {cfg.get(key)}"


# ---- install.sh local source-copy route -----------------------------------


def _copy_from_local_body() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r"^copy_from_local\(\) \{.*?^\}", text, re.DOTALL | re.MULTILINE)
    assert match, "copy_from_local() not found in install.sh"
    return match.group(0)


def test_copy_from_local_keeps_notices_and_recipe() -> None:
    # The --source / repo-rerun route must keep the aggregate notices and the
    # QEMU recipe; without them the locally built wheel silently loses both
    # (shipping-audit gap 2).
    body = _copy_from_local_body()
    assert "THIRD_PARTY_LICENSES.md" in body
    assert "packaging/qemu-disguise" in body


def test_copy_from_local_copies_only_the_recipe_subdir() -> None:
    # Only the qemu-disguise recipe comes across from packaging/ — the OBS/AUR/
    # RPM/AppImage channels are not runtime payload — and it lands at
    # packaging/qemu-disguise so the layout matches the wheel's shared data.
    body = _copy_from_local_body()
    items = re.search(r"for item in ([^;]+); do", body)
    assert items, "copy_from_local item loop not found"
    listed = [i.strip().strip('"') for i in items.group(1).split()]
    assert "packaging" not in listed, "copy_from_local must not copy all of packaging/"
    assert 'mkdir -p "$WORK_DIR/packaging"' in body
    assert 'cp -r "$src/packaging/qemu-disguise" "$WORK_DIR/packaging/"' in body


# ---- dependency floors -----------------------------------------------------


def _find_requirement(raw: list[str], name: str) -> Requirement:
    for entry in raw:
        req = Requirement(entry)
        if req.name.lower() == name.lower():
            return req
    pytest.fail(f"{name} not found in {raw}")


def test_dependency_floors_match_0_12_resolution(pyproject: dict) -> None:
    extras = pyproject["project"]["optional-dependencies"]
    for extra in ("reverse-open", "dev"):
        pillow = _find_requirement(extras[extra], "Pillow")
        assert pillow.specifier == SpecifierSet(">=12.3,<13"), f"{extra}: {pillow}"
    cairosvg = _find_requirement(extras["reverse-open"], "cairosvg")
    assert cairosvg.specifier == SpecifierSet(">=2.9,<3"), f"reverse-open: {cairosvg}"
    pytest_req = _find_requirement(extras["dev"], "pytest")
    assert pytest_req.specifier == SpecifierSet(">=9.0.3,<10"), f"dev: {pytest_req}"


def test_python_floor_and_stdlib_core_unchanged(pyproject: dict) -> None:
    # Python 3.10 stays the floor and the core stays stdlib-only (tomli is the
    # sole dependency, marker-gated to Python < 3.11).
    assert pyproject["project"]["requires-python"] == ">=3.10"
    core = pyproject["project"]["dependencies"]
    assert len(core) == 1, f"core must stay stdlib-only: {core}"
    req = Requirement(core[0])
    assert req.name == "tomli"
    assert req.marker is not None
    marker = str(req.marker)
    assert "python_version" in marker and "3.11" in marker


# ---- flake.nix wrapper -----------------------------------------------------


def test_flake_drops_removed_libvirt_backend() -> None:
    # The libvirt backend was removed (podman/docker/manual only); the Nix
    # python dependency on it was dead closure weight (shipping-audit finding).
    assert "libvirt" not in FLAKE_NIX.read_text(encoding="utf-8")


def test_flake_relies_on_wheel_shared_data() -> None:
    # The raw postInstall `cp -r scripts config data` reintroduced the
    # wheel-excluded dev/CI payload; the wheel's shared data already installs
    # share/winpodx. The wrapper bundle path and runtime tools stay.
    text = FLAKE_NIX.read_text(encoding="utf-8")
    assert not re.search(r"^\s*postInstall\s*=", text, re.MULTILINE)
    assert "cp -r scripts config data" not in text
    assert "WINPODX_BUNDLE_DIR" in text
    assert '${placeholder "out"}/share/winpodx' in text
    for tool in ("freerdp", "iproute2", "libnotify", "podman", "podman-compose"):
        assert f"pkgs.{tool}" in text, f"runtime tool dropped from wrapper PATH: {tool}"


# ---- optional: real built wheel inventory + resolver ----------------------


def _wheel_share_names() -> list[str]:
    """share/winpodx/... paths from the built wheel's data payload."""
    with zipfile.ZipFile(WHEEL) as zf:
        names = []
        for member in zf.namelist():
            if ".data/data/" in member:
                names.append(member.split(".data/data/", 1)[1])
        return names


@pytest.mark.skipif(not WHEEL, reason="set WINPODX_PAYLOAD_WHEEL to a built wheel")
class TestBuiltWheelInventory:
    def test_recipe_and_notices_ship(self) -> None:
        names = _wheel_share_names()
        for name in RECIPE_FILES:
            assert f"share/winpodx/packaging/qemu-disguise/{name}" in names
        assert "share/winpodx/THIRD_PARTY_LICENSES.md" in names

    def test_dev_helpers_and_shim_rebuild_sources_absent(self) -> None:
        names = _wheel_share_names()
        for rel in DEV_SCRIPTS:
            assert f"share/winpodx/{rel}" not in names, rel
        for rel in SHIM_REBUILD_SOURCES:
            assert f"share/winpodx/{rel}" not in names, rel
        assert not [n for n in names if n.startswith("share/winpodx/scripts/ci/")]

    def test_guest_scripts_and_shim_binaries_present(self) -> None:
        names = _wheel_share_names()
        for rel in _repo_files("scripts/windows"):
            assert f"share/winpodx/{rel}" in names, rel
        for rel in _repo_files(SHIM):
            if rel.startswith(f"{SHIM}/bin/"):
                assert f"share/winpodx/{rel}" in names, rel

    def test_recipe_resolver_from_controlled_prefix(self, monkeypatch, tmp_path) -> None:
        # Stage the wheel's share data at a controlled prefix and point bundle
        # resolution at it. The module's own ancestry is blanked to a fictional
        # root that cannot be a checkout ancestor — pytest's tmp_path can live
        # inside a real checkout (it did during the audit), and an ancestor's
        # packaging/qemu-disguise would leak in and mask a missing wheel
        # payload. _recipe_dir() must resolve the recipe from the installed
        # payload alone.
        import shutil

        from winpodx.cli import disguise
        from winpodx.utils import paths

        prefix = tmp_path / "controlled-prefix"
        extract = tmp_path / "wheel-extract"
        with zipfile.ZipFile(WHEEL) as zf:
            zf.extractall(extract)
        data_root = next(extract.glob("*.data/data/share/winpodx"))
        shutil.copytree(data_root, prefix / "share" / "winpodx")

        ctrl_lib = Path("/winpodx-payload-contract/lib/python/winpodx")
        monkeypatch.delenv("WINPODX_BUNDLE_DIR", raising=False)
        monkeypatch.setattr(paths, "__file__", str(ctrl_lib / "utils" / "paths.py"))
        monkeypatch.setattr(paths.sys, "prefix", str(prefix))
        monkeypatch.setattr(disguise, "__file__", str(ctrl_lib / "cli" / "disguise.py"))

        recipe = disguise._recipe_dir()
        assert recipe == prefix / "share" / "winpodx" / "packaging" / "qemu-disguise"
        assert (recipe / "Dockerfile").is_file()
