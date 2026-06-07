# SPDX-License-Identifier: MIT
"""GUI missing-PySide6 install hint (#502): distro-aware, apt-candidate-probed."""

from __future__ import annotations

from unittest.mock import patch

from winpodx.utils import install_source as src


class _Proc:
    def __init__(self, stdout: str):
        self.stdout = stdout


def _apt_policy(candidate: str | None) -> str:
    """Render an `apt-cache policy` stdout with the given Candidate value."""
    cand = candidate if candidate is not None else "(none)"
    return f"python3-pyside6.qtwidgets:\n  Installed: (none)\n  Candidate: {cand}\n"


def test_apt_has_candidate_true_and_false():
    with patch.object(src.subprocess, "run", return_value=_Proc(_apt_policy("6.6.0-1"))):
        assert src._apt_has_candidate("python3-pyside6.qtwidgets") is True
    with patch.object(src.subprocess, "run", return_value=_Proc(_apt_policy(None))):
        assert src._apt_has_candidate("python3-pyside6.qtwidgets") is False
    # Unknown package — apt-cache prints nothing for it.
    with patch.object(src.subprocess, "run", return_value=_Proc("")):
        assert src._apt_has_candidate("nope") is False


def test_apt_has_candidate_survives_subprocess_error():
    with patch.object(src.subprocess, "run", side_effect=OSError):
        assert src._apt_has_candidate("python3-pyside6.qtwidgets") is False


def test_apt_command_split_packages_when_available():
    with (
        patch.object(src.shutil, "which", return_value="/usr/bin/apt-cache"),
        patch.object(src, "_apt_has_candidate", return_value=True),
    ):
        cmd = src._apt_pyside6_command()
    assert cmd == "sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg"


def test_apt_command_falls_back_to_metapackage():
    def has(pkg: str) -> bool:
        return pkg == "python3-pyside6"  # only the metapackage exists

    with (
        patch.object(src.shutil, "which", return_value="/usr/bin/apt-cache"),
        patch.object(src, "_apt_has_candidate", side_effect=has),
    ):
        assert src._apt_pyside6_command() == "sudo apt install python3-pyside6"


def test_apt_command_none_when_archive_lacks_pyside6():
    # The Ubuntu 24.04 LTS case: no candidate for any name.
    with (
        patch.object(src.shutil, "which", return_value="/usr/bin/apt-cache"),
        patch.object(src, "_apt_has_candidate", return_value=False),
    ):
        assert src._apt_pyside6_command() is None


def test_apt_command_none_without_apt_cache():
    with patch.object(src.shutil, "which", return_value=None):
        assert src._apt_pyside6_command() is None


def test_pkg_command_static_families():
    with (
        patch.object(src, "_distro_id", return_value="fedora"),
        patch.object(src, "_distro_id_like", return_value=""),
    ):
        assert src._pyside6_pkg_command() == "sudo dnf install python3-pyside6"
    with (
        patch.object(src, "_distro_id", return_value="arch"),
        patch.object(src, "_distro_id_like", return_value=""),
    ):
        assert src._pyside6_pkg_command() == "sudo pacman -S pyside6"


def test_pkg_command_unknown_distro_is_none():
    with (
        patch.object(src, "_distro_id", return_value="weirdos"),
        patch.object(src, "_distro_id_like", return_value=""),
    ):
        assert src._pyside6_pkg_command() is None


def test_hint_leads_with_appimage_and_never_bare_pip():
    with patch.object(src, "_pyside6_pkg_command", return_value=None):
        hint = src.pyside6_install_hint()
    first = hint.splitlines()[0]
    assert "AppImage" in first
    # The old failing advice must be gone.
    assert "pip install PySide6" not in hint
    # When no distro package, the explicit fallback note is shown.
    assert "may not package PySide6" in hint


def test_hint_shows_distro_package_when_available():
    with patch.object(src, "_pyside6_pkg_command", return_value="sudo apt install python3-pyside6"):
        hint = src.pyside6_install_hint()
    assert "sudo apt install python3-pyside6" in hint
    assert "may not package PySide6" not in hint
