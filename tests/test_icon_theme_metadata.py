# SPDX-License-Identifier: MIT
"""Filesystem-level contracts for XDG hicolor metadata and cache refresh."""

from __future__ import annotations

import configparser
import os
import struct
import subprocess
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest

from winpodx.desktop import icons
from winpodx.utils.paths import icons_dir


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    class _SandboxPaths:
        def __call__(self, *parts: str | os.PathLike[str]) -> Path:
            path = Path(*parts)
            for root in (Path("/usr/local/share"), Path("/usr/share")):
                if path.is_relative_to(root):
                    return tmp_path / "defaults" / path.relative_to("/")
            return path

        @staticmethod
        def home() -> Path:
            return Path.home()

    # Redirect only default system roots: even unchanged source cannot read host themes.
    monkeypatch.setattr(icons, "Path", _SandboxPaths())
    return tmp_path


def _write_index(theme: Path, content: str) -> Path:
    theme.mkdir(parents=True, exist_ok=True)
    index = theme / "index.theme"
    index.write_text(content, encoding="utf-8")
    return index


def _read_index(theme: Path) -> configparser.ConfigParser:
    parsed = configparser.ConfigParser(interpolation=None)
    with (theme / "index.theme").open(encoding="utf-8") as stream:
        parsed.read_file(stream)
    return parsed


@pytest.fixture
def installed_theme(sandbox: Path) -> Path:
    theme = icons_dir()
    for size in (64, 128):
        directory = theme / f"{size}x{size}" / "apps"
        directory.mkdir(parents=True)
        chunks = (
            (b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)),
            (b"IDAT", zlib.compress((b"\0" + b"\xff\0\0\xff" * size) * size)),
            (b"IEND", b""),
        )
        png = b"\x89PNG\r\n\x1a\n" + b"".join(
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            for kind, data in chunks
        )
        (directory / "winpodx-sample.png").write_bytes(png)
    scalable = theme / "scalable/apps"
    scalable.mkdir(parents=True)
    (scalable / "winpodx-native.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
        '<path d="M0 0h16v16H0z"/></svg>',
        encoding="utf-8",
    )
    return theme


@pytest.mark.parametrize("order", [("first", "second"), ("second", "first")])
def test_absent_index_uses_first_usable_absolute_xdg_theme(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, order: tuple[str, str]
) -> None:
    # Given
    monkeypatch.chdir(sandbox)
    for name in ("relative", "first", "second"):
        _write_index(
            sandbox / name / "icons/hicolor",
            f"[Icon Theme]\nName={name}\nX-Seed={name}\nDirectories=scalable/apps\n"
            "[scalable/apps]\nSize=64\nType=Scalable\nContext=Applications\n",
        )
    _write_index(sandbox / "invalid/icons/hicolor", "not an INI file\n")
    monkeypatch.setenv(
        "XDG_DATA_DIRS",
        f"relative::{sandbox / 'missing'}:{sandbox / 'invalid'}:"
        f"{sandbox / order[0]}:{sandbox / order[1]}",
    )
    # When
    icons._ensure_index_theme(icons_dir())
    # Then
    theme = _read_index(icons_dir())
    assert theme["Icon Theme"]["Name"] == order[0]
    assert theme["Icon Theme"]["X-Seed"] == order[0]


@pytest.mark.parametrize("data_dirs", [None, ""], ids=["unset", "empty"])
def test_default_search_prefers_usr_local_when_xdg_data_dirs_has_no_value(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, data_dirs: str | None
) -> None:
    # Given
    for root, name in (("usr/local/share", "local"), ("usr/share", "distribution")):
        _write_index(
            sandbox / "defaults" / root / "icons/hicolor",
            f"[Icon Theme]\nName={name}\nDirectories=scalable/apps\n"
            "[scalable/apps]\nSize=64\nType=Scalable\nContext=Applications\n",
        )
    monkeypatch.delenv("XDG_DATA_DIRS", raising=False)
    if data_dirs is not None:
        monkeypatch.setenv("XDG_DATA_DIRS", data_dirs)
    # When
    icons._ensure_index_theme(icons_dir())
    # Then
    assert _read_index(icons_dir())["Icon Theme"]["Name"] == "local"


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param(
            "[Icon Theme]\nName=Hicolor\nHidden=true\nDirectories=scalable/apps\n"
            "[scalable/apps]\nSize=64\nMinSize=1\nMaxSize=512\n"
            "Context=Applications\nType=Scalable\n",
            id="legacy-minimal",
        ),
        pytest.param(
            "[Icon Theme]\nName=Personal\nComment=100% personal\nInherits=custom-parent\n"
            "X-Owner=user\nDirectories=custom/actions,64x64/apps\n"
            "[custom/actions]\nSize=24\nType=Fixed\nContext=Actions\nX-Accent=blue\n"
            "[64x64/apps]\nSize=64\nType=Fixed\nX-Owner=user\n",
            id="custom-user",
        ),
    ],
)
def test_installed_directories_gain_metadata_without_clobbering_user_fields(
    installed_theme: Path, existing: str
) -> None:
    # Given
    _write_index(installed_theme, existing)
    original = _read_index(installed_theme)
    # When
    icons._ensure_index_theme(installed_theme)
    # Then
    repaired = _read_index(installed_theme)
    directories = {value.strip() for value in repaired["Icon Theme"]["Directories"].split(",")}
    assert {"64x64/apps", "128x128/apps", "scalable/apps"} <= directories
    assert set(original["Icon Theme"]["Directories"].split(",")) <= directories
    for section in original.sections():
        for key, value in original[section].items():
            if (section, key) != ("Icon Theme", "directories"):
                assert repaired[section][key] == value
    for size in (64, 128):
        section = repaired[f"{size}x{size}/apps"]
        assert section.getint("Size") == size
        assert section["Type"] == "Fixed"
        assert section["Context"] == "Applications"
    scalable = repaired["scalable/apps"]
    assert scalable["Type"] == "Scalable"
    assert scalable["Context"] == "Applications"
    assert 0 < scalable.getint("MinSize") <= scalable.getint("Size") <= scalable.getint("MaxSize")


def test_index_bytes_are_stable_when_repair_is_repeated(installed_theme: Path) -> None:
    # Given
    _write_index(installed_theme, "[Icon Theme]\nName=Personal\nDirectories=scalable/apps\n")
    icons._ensure_index_theme(installed_theme)
    before = (installed_theme / "index.theme").read_bytes()
    # When
    icons._ensure_index_theme(installed_theme)
    # Then
    assert (installed_theme / "index.theme").read_bytes() == before


def test_cache_refresh_targets_installed_xdg_theme(installed_theme: Path) -> None:
    # Given
    with patch.object(
        icons.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    ) as run:
        # When
        icons.refresh_icon_cache()
        # Then
        run.assert_any_call(
            ["gtk-update-icon-cache", "-f", "-t", str(installed_theme)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        run.assert_any_call(
            ["xdg-icon-resource", "forceupdate"], capture_output=True, text=True, timeout=30
        )
    assert (installed_theme / "index.theme").is_file()
    assert not (Path.home() / ".local/share/icons/hicolor/index.theme").exists()
