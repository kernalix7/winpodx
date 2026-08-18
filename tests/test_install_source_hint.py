# SPDX-License-Identifier: MIT
"""Tests for utils.install_source's distro probing and the PySide6 hint (#502).

The hint is what a user sees when the GUI can't start, so the rules matter: it
must never suggest a bare ``pip install PySide6`` (PEP 668 externally-managed
Pythons reject it) and must never name an apt package that has no candidate on
this release. ``apt-cache`` / ``/etc/os-release`` are patched at the module's
lookup site so nothing probes the real host.
"""

from __future__ import annotations

import io
import subprocess

import pytest

from winpodx.utils import install_source
from winpodx.utils.install_source import (
    _apt_has_candidate,
    _apt_pyside6_command,
    _distro_id,
    _distro_id_like,
    _pyside6_pkg_command,
    pyside6_install_hint,
)

OS_RELEASE = 'NAME="Ubuntu"\nID=ubuntu\nID_LIKE=debian\nVERSION_ID="24.04"\n'


def _fake_open(content: str | None):
    def opener(path, *a, **k):
        if content is None:
            raise OSError("no os-release")
        return io.StringIO(content)

    return opener


def _apt_policy(candidates: dict[str, str]):
    def run(cmd, **_kw):
        pkg = cmd[-1]
        cand = candidates.get(pkg, "(none)")
        return subprocess.CompletedProcess(
            cmd, 0, stdout=f"{pkg}:\n  Candidate: {cand}\n", stderr=""
        )

    return run


# --- /etc/os-release parsing ---------------------------------------------


def test_distro_id_is_read_and_unquoted(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", _fake_open('ID="fedora"\n'))

    assert _distro_id() == "fedora"


def test_distro_id_like_is_read(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", _fake_open(OS_RELEASE))

    assert _distro_id_like() == "debian"


def test_missing_os_release_yields_empty_ids(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", _fake_open(None))

    assert _distro_id() == ""
    assert _distro_id_like() == ""


def test_os_release_without_the_key_yields_empty(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", _fake_open("NAME=Weird\n"))

    assert _distro_id() == ""
    assert _distro_id_like() == ""


# --- apt candidate probing ------------------------------------------------


def test_candidate_present_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(install_source.subprocess, "run", _apt_policy({"pkg": "6.7.0-1"}))

    assert _apt_has_candidate("pkg") is True


def test_candidate_none_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(install_source.subprocess, "run", _apt_policy({}))

    assert _apt_has_candidate("pkg") is False


def test_apt_cache_failure_is_treated_as_no_candidate(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("apt-cache missing")

    monkeypatch.setattr(install_source.subprocess, "run", boom)

    assert _apt_has_candidate("pkg") is False


def test_apt_cache_timeout_is_treated_as_no_candidate(monkeypatch) -> None:
    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="apt-cache", timeout=5)

    monkeypatch.setattr(install_source.subprocess, "run", slow)

    assert _apt_has_candidate("pkg") is False


# --- apt command selection ------------------------------------------------


def test_split_qt_module_packages_are_preferred(monkeypatch) -> None:
    monkeypatch.setattr(install_source.shutil, "which", lambda _n: "/usr/bin/apt-cache")
    monkeypatch.setattr(
        install_source.subprocess,
        "run",
        _apt_policy({"python3-pyside6.qtwidgets": "6.7", "python3-pyside6.qtsvg": "6.7"}),
    )

    assert _apt_pyside6_command() == (
        "sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtsvg"
    )


def test_metapackage_is_used_when_the_split_packages_are_absent(monkeypatch) -> None:
    monkeypatch.setattr(install_source.shutil, "which", lambda _n: "/usr/bin/apt-cache")
    monkeypatch.setattr(install_source.subprocess, "run", _apt_policy({"python3-pyside6": "6.6"}))

    assert _apt_pyside6_command() == "sudo apt install python3-pyside6"


def test_no_apt_command_when_nothing_is_packaged(monkeypatch) -> None:
    monkeypatch.setattr(install_source.shutil, "which", lambda _n: "/usr/bin/apt-cache")
    monkeypatch.setattr(install_source.subprocess, "run", _apt_policy({}))

    assert _apt_pyside6_command() is None


def test_no_apt_command_without_apt_cache(monkeypatch) -> None:
    monkeypatch.setattr(install_source.shutil, "which", lambda _n: None)

    assert _apt_pyside6_command() is None


# --- per-family package command -------------------------------------------


@pytest.mark.parametrize(
    ("os_release", "expected"),
    [
        ("ID=fedora\n", "sudo dnf install python3-pyside6"),
        ("ID=almalinux\nID_LIKE=rhel\n", "sudo dnf install python3-pyside6"),
        ("ID=arch\n", "sudo pacman -S pyside6"),
        ("ID=cachyos\nID_LIKE=arch\n", "sudo pacman -S pyside6"),
        ("ID=opensuse-tumbleweed\nID_LIKE=suse\n", "sudo zypper install python3-PySide6"),
    ],
)
def test_static_families_get_their_package_manager(monkeypatch, os_release, expected) -> None:
    monkeypatch.setattr("builtins.open", _fake_open(os_release))

    assert _pyside6_pkg_command() == expected


def test_debian_family_is_probed_at_runtime(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", _fake_open(OS_RELEASE))
    monkeypatch.setattr(install_source.shutil, "which", lambda _n: "/usr/bin/apt-cache")
    monkeypatch.setattr(install_source.subprocess, "run", _apt_policy({"python3-pyside6": "6.6"}))

    assert _pyside6_pkg_command() == "sudo apt install python3-pyside6"


def test_unknown_distro_has_no_package_command(monkeypatch) -> None:
    monkeypatch.setattr("builtins.open", _fake_open("ID=plan9\n"))

    assert _pyside6_pkg_command() is None


# --- the user-facing hint -------------------------------------------------


def test_hint_never_suggests_a_bare_pip_install(monkeypatch) -> None:
    monkeypatch.setattr(install_source, "_pyside6_pkg_command", lambda: None)

    hint = pyside6_install_hint()

    assert "pip install PySide6" not in hint
    assert "winpodx[gui]" in hint


def test_hint_leads_with_the_appimage(monkeypatch) -> None:
    monkeypatch.setattr(install_source, "_pyside6_pkg_command", lambda: None)

    assert "AppImage" in pyside6_install_hint().splitlines()[0]


def test_hint_shows_a_distro_command_when_one_exists(monkeypatch) -> None:
    monkeypatch.setattr(install_source, "_pyside6_pkg_command", lambda: "sudo pacman -S pyside6")

    assert "sudo pacman -S pyside6" in pyside6_install_hint()


def test_hint_falls_back_to_the_appimage_when_unpackaged(monkeypatch) -> None:
    monkeypatch.setattr(install_source, "_pyside6_pkg_command", lambda: None)

    hint = pyside6_install_hint()

    assert "may not package PySide6" in hint
    assert "sudo apt install" not in hint
