# SPDX-License-Identifier: MIT
"""Pin the unified backend-selection contract (0.6.0 item E).

``backend/select.choose_backend()`` is the single Python source of truth for
"given what's installed on the host, which container backend should winpodx
use." The setup wizard, the GUI auto-pick, and (via the bash mirror)
``install.sh``'s Automatic-mode picker all walk the same priority order with
the same podman major-version gate.

These tests cover:

* Each branch of :func:`choose_backend` (prefer / auto-podman / podman too old
  → fall through / docker / nothing → recommended podman fallback). libvirt was
  dropped in 0.6.0 (#286), so an explicit ``prefer="libvirt"`` now raises.
* The bash mirror in ``install.sh`` walks the same priority order with the
  same minimum podman major, so the two sources cannot silently drift.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from winpodx.backend.select import (
    AUTO_PRIORITY,
    PODMAN_MIN_MAJOR_VERSION,
    VALID_BACKENDS,
    choose_backend,
)
from winpodx.utils.deps import DepCheck

REPO_ROOT = Path(__file__).resolve().parents[1]


def _deps(*, podman: bool, docker: bool) -> dict[str, DepCheck]:
    """Build a deps dict the way check_all would, for the listed binaries."""
    return {
        "freerdp": DepCheck(name="xfreerdp3", found=True),
        "podman": DepCheck(name="podman", found=podman, note="Podman backend"),
        "docker": DepCheck(name="docker", found=docker, note="Docker backend"),
        "flatpak": DepCheck(name="flatpak", found=False),
        "kvm": DepCheck(name="kvm", found=True, path="/dev/kvm"),
    }


# ---- explicit prefer wins ----


def test_prefer_explicit_returns_unchanged() -> None:
    for backend in ("podman", "docker", "manual"):
        assert choose_backend(prefer=backend, deps=_deps(podman=True, docker=True)) == backend


def test_prefer_libvirt_raises_after_drop() -> None:
    # libvirt was dropped in 0.6.0; an explicit --backend libvirt is now an
    # unknown backend and must fail loudly rather than silently fall through.
    import pytest

    with pytest.raises(ValueError, match="unknown backend"):
        choose_backend(prefer="libvirt", deps=_deps(podman=True, docker=True))


def test_prefer_unknown_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown backend"):
        choose_backend(prefer="qemu", deps=_deps(podman=True, docker=False))


# ---- auto-pick walks AUTO_PRIORITY ----


def test_auto_picks_podman_when_present_and_modern() -> None:
    with patch("winpodx.backend.select.podman_major_version", return_value=5):
        assert choose_backend(deps=_deps(podman=True, docker=True)) == "podman"


def test_auto_skips_podman_too_old_falls_to_docker() -> None:
    # #271: Ubuntu 22.04 podman 3.4. choose_backend treats it as absent and
    # walks down to docker.
    with patch("winpodx.backend.select.podman_major_version", return_value=3):
        assert choose_backend(deps=_deps(podman=True, docker=True)) == "docker"


def test_auto_too_old_podman_no_docker_falls_back_to_podman() -> None:
    # too-old podman + no docker + (libvirt dropped) -> the recommended-podman
    # fallback, so the install path can install a modern podman.
    with patch("winpodx.backend.select.podman_major_version", return_value=3):
        assert choose_backend(deps=_deps(podman=True, docker=False)) == "podman"


def test_auto_picks_docker_when_no_podman() -> None:
    assert choose_backend(deps=_deps(podman=False, docker=True)) == "docker"


def test_auto_falls_back_to_podman_when_nothing_usable() -> None:
    # Install path can then install podman packages; matches install.sh's
    # Recommended fallback behaviour.
    assert choose_backend(deps=_deps(podman=False, docker=False)) == "podman"


def test_podman_major_version_threshold_is_inclusive() -> None:
    # The gate is "major >= min", so PODMAN_MIN_MAJOR_VERSION itself passes.
    with patch(
        "winpodx.backend.select.podman_major_version",
        return_value=PODMAN_MIN_MAJOR_VERSION,
    ):
        assert choose_backend(deps=_deps(podman=True, docker=True)) == "podman"


def test_podman_major_version_unparseable_treated_as_too_old() -> None:
    # podman_major_version returns None when --version output can't be parsed;
    # we fall through to docker rather than choosing a podman we can't verify.
    with patch("winpodx.backend.select.podman_major_version", return_value=None):
        assert choose_backend(deps=_deps(podman=True, docker=True)) == "docker"


def test_deps_defaults_to_check_all_when_omitted() -> None:
    # Smoke: passing no deps must invoke check_all() rather than crashing.
    # We don't assert the result (host-dependent); only that the call returns
    # a member of VALID_BACKENDS.
    result = choose_backend()
    assert result in VALID_BACKENDS


# ---- install.sh bash mirror tracks the Python helper ----


def test_install_sh_mirror_walks_same_priority_order() -> None:
    text = (REPO_ROOT / "install.sh").read_text()
    # `for candidate in podman docker; do` is the Automatic-mode loop; it MUST
    # list AUTO_PRIORITY in the same order so the two sources can't drift on
    # which backend wins on a multi-runtime host.
    match = re.search(
        r"for candidate in (\w+(?:\s+\w+)+); do",
        text,
    )
    assert match, "install.sh Automatic-mode picker loop not found"
    bash_order = tuple(match.group(1).split())
    assert bash_order == AUTO_PRIORITY, (
        f"install.sh walks {bash_order} but Python AUTO_PRIORITY is {AUTO_PRIORITY}; "
        "the bash mirror has drifted"
    )


def test_install_sh_mirror_uses_same_podman_min_major() -> None:
    text = (REPO_ROOT / "install.sh").read_text()
    # The bash gate is `PODMAN_MAJOR < 4`. PODMAN_MIN_MAJOR_VERSION is the
    # exclusive lower bound (3 fails, 4 passes), so the literal in bash is
    # PODMAN_MIN_MAJOR_VERSION itself.
    expected_literal = str(PODMAN_MIN_MAJOR_VERSION)
    match = re.search(r"PODMAN_MAJOR.{0,10}<\s*(\d+)", text)
    assert match, "install.sh podman major-version gate (`PODMAN_MAJOR < N`) not found"
    assert match.group(1) == expected_literal, (
        f"install.sh gates podman major < {match.group(1)} but Python uses "
        f"PODMAN_MIN_MAJOR_VERSION = {expected_literal}; bash mirror has drifted"
    )
