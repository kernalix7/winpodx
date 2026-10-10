# SPDX-License-Identifier: MIT
"""Every channel must ship the three files that make winpodx clickable.

A package that installs only `/usr/bin/winpodx` leaves the user with nothing to
click: no menu entry (while the RPM %post banner tells them to look for one) and
no software-center listing, so the only way in is a terminal. The wheel's
shared-data copy under `/usr/share/winpodx/data/` does not count -- no desktop
environment scans that path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

METAINFO_NAME = "org.winpodx.WinPodX.metainfo.xml"
METAINFO_SRC = ROOT / "data" / METAINFO_NAME
DESKTOP_SRC = ROOT / "data" / "winpodx.desktop"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class TestMetainfoSource:
    def test_it_exists_and_declares_the_expected_component(self):
        text = METAINFO_SRC.read_text(encoding="utf-8")

        assert "<id>org.winpodx.WinPodX</id>" in text
        assert 'type="desktop-application"' in text
        assert '<launchable type="desktop-id">winpodx.desktop</launchable>' in text

    def test_the_launchable_matches_the_shipped_desktop_file(self):
        assert DESKTOP_SRC.is_file()

    def test_the_desktop_icon_key_matches_the_installed_icon_basename(self):
        # Icon=winpodx resolves only if the file lands as winpodx.svg; the
        # source is named winpodx-icon.svg, so every channel has to rename it.
        icon_line = next(
            line
            for line in DESKTOP_SRC.read_text(encoding="utf-8").splitlines()
            if line.startswith("Icon=")
        )

        assert icon_line == "Icon=winpodx"

    def test_the_gui_entry_does_not_open_a_terminal(self):
        text = DESKTOP_SRC.read_text(encoding="utf-8")

        assert "Terminal=false" in text
        assert "Exec=winpodx gui" in text


class TestRpmSpec:
    @pytest.fixture(scope="class")
    @staticmethod
    def spec() -> str:
        return _read("packaging/rpm/winpodx.spec")

    @pytest.mark.parametrize(
        "fragment",
        [
            "%{_datadir}/applications/winpodx.desktop",
            "%{_datadir}/icons/hicolor/scalable/apps/winpodx.svg",
            "%{_datadir}/metainfo/" + METAINFO_NAME,
        ],
    )
    def test_each_path_is_installed_and_owned(self, spec: str, fragment: str):
        # Once in %install, once in %files -- an unowned file fails the build.
        assert spec.count(fragment) >= 2, f"{fragment} must be installed AND listed in %files"

    def test_it_requires_freerdp_and_recommends_a_container_runtime(self, spec: str):
        assert "Requires:       freerdp" in spec
        assert "Recommends:     podman" in spec


class TestDebian:
    def test_desktop_and_metainfo_are_installed(self):
        install = _read("debian/winpodx.install")

        assert "data/winpodx.desktop usr/share/applications/" in install
        assert f"data/{METAINFO_NAME} usr/share/metainfo/" in install

    def test_the_icon_is_renamed_in_rules_because_dh_install_cannot(self):
        rules = _read("debian/rules")

        assert "execute_after_dh_auto_install:" in rules
        assert "icons/hicolor/scalable/apps/winpodx.svg" in rules

    def test_it_depends_on_freerdp_and_recommends_a_container_runtime(self):
        control = _read("debian/control")

        assert "freerdp" in control
        assert "podman" in control


class TestAurPkgbuilds:
    @pytest.mark.parametrize("pkgbuild", ["packaging/aur/PKGBUILD", "packaging/aur-git/PKGBUILD"])
    def test_it_installs_all_three_desktop_files(self, pkgbuild: str):
        text = _read(pkgbuild)

        assert "usr/share/applications/winpodx.desktop" in text
        assert "usr/share/metainfo/" + METAINFO_NAME in text
        assert "usr/share/icons/hicolor/scalable/apps/winpodx.svg" in text

    @pytest.mark.parametrize("pkgbuild", ["packaging/aur/PKGBUILD", "packaging/aur-git/PKGBUILD"])
    def test_the_svg_is_not_filed_under_a_pixel_size_directory(self, pkgbuild: str):
        # hicolor reserves size dirs for fixed-size bitmaps; an SVG belongs in
        # scalable/ or icon lookup can miss it.
        assert "hicolor/256x256/apps/winpodx.svg" not in _read(pkgbuild)


class TestCurlInstaller:
    def test_install_sh_installs_the_metainfo(self):
        text = _read("install.sh")

        assert "METAINFO_DIR=" in text
        assert METAINFO_NAME in text

    def test_uninstall_sh_removes_it_again(self):
        assert METAINFO_NAME in _read("uninstall.sh")
