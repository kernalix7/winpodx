# SPDX-License-Identifier: MIT
"""Behavioral contracts for additive, lossless desktop SVG export."""

from __future__ import annotations

import base64
import configparser
import os
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from typing import Final

import pytest

from winpodx.core.app import AppInfo
from winpodx.desktop.entry import _install_icon, install_desktop_entry

_SVG_NS: Final = "http://www.w3.org/2000/svg"
_NATIVE_SVG: Final = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
    b'<path fill="#123456" d="M0 0h16v16H0z"/></svg>\n'
)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png(width: int, height: int, padding: int = 0) -> bytes:
    rows = (b"\0" + b"\x19\x80\xc0\xff" * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"tEXt", b"Comment\0" + b"x" * padding)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def _app(source: Path, payload: bytes) -> AppInfo:
    source.write_bytes(payload)
    return AppInfo(
        name="sample", full_name="Sample", executable=r"C:\sample.exe", icon_path=str(source)
    )


@pytest.fixture
def icon_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))
    return tmp_path / "data" / "icons" / "hicolor"


def _assert_wrapper(path: Path, payload: bytes, dimensions: tuple[int, int]) -> None:
    assert path.is_file(), "PNG installation must also export a scalable SVG"
    root = ET.fromstring(path.read_bytes())
    width, height = dimensions
    assert root.tag == f"{{{_SVG_NS}}}svg"
    assert root.attrib["width"].isdecimal() and root.attrib["height"].isdecimal()
    assert (int(root.attrib["width"]), int(root.attrib["height"])) == dimensions
    assert tuple(map(float, root.attrib["viewBox"].replace(",", " ").split())) == (
        0,
        0,
        width,
        height,
    )
    images = root.findall(f".//{{{_SVG_NS}}}image")
    assert len(images) == 1
    image = images[0]
    assert (int(image.attrib["width"]), int(image.attrib["height"])) == dimensions
    for element in (root, image):
        assert element.get("preserveAspectRatio", "xMidYMid meet").split()[-1] == "meet"
    hrefs = [
        value
        for element in root.iter()
        for key, value in element.attrib.items()
        if key.rsplit("}", 1)[-1] in {"href", "src"}
    ]
    assert hrefs
    for href in hrefs:
        prefix, separator, encoded = href.partition(",")
        assert (prefix, separator) == ("data:image/png;base64", ",")
        assert base64.b64decode(encoded, validate=True) == payload
    for element in root.iter():
        assert "{http://www.w3.org/XML/1998/namespace}base" not in element.attrib
        assert all("url(" not in value for value in element.attrib.values())


@pytest.mark.parametrize(
    ("payload", "geometry"),
    [
        pytest.param(_png(128, 32), ((128, 32), 128), id="wide"),
        pytest.param(_png(1024, 1024), ((1024, 1024), 256), id="dimension-limit"),
        pytest.param(_png(32, 32, (1 << 20) - len(_png(32, 32))), ((32, 32), 32), id="byte-limit"),
    ],
)
def test_desktop_export_is_additive_when_png_is_valid(
    icon_root: Path, payload: bytes, geometry: tuple[tuple[int, int], int]
) -> None:
    # Given
    dimensions, bucket = geometry
    source = icon_root.parents[2] / "private.png"
    app = _app(source, payload)
    # When
    desktop_path = install_desktop_entry(app)
    # Then
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(desktop_path, encoding="utf-8")
    assert desktop["Desktop Entry"]["Icon"] == "winpodx-sample"
    assert app.icon_path == str(source)
    assert source.read_bytes() == payload
    assert (icon_root / f"{bucket}x{bucket}/apps/winpodx-sample.png").read_bytes() == payload
    _assert_wrapper(icon_root / "scalable/apps/winpodx-sample.svg", payload, dimensions)


def test_native_svg_is_copied_unchanged_when_installed(icon_root: Path) -> None:
    # Given
    app = _app(icon_root.parents[2] / "private.svg", _NATIVE_SVG)
    # When
    icon_name = _install_icon(app)
    # Then
    assert icon_name == "winpodx-sample"
    assert (icon_root / "scalable/apps/winpodx-sample.svg").read_bytes() == _NATIVE_SVG


@pytest.mark.parametrize(
    "payload",
    [_png(32, 32), b"malformed", _png(32, 32, 1 << 20)],
    ids=["valid-png", "malformed-png", "oversized-png"],
)
def test_native_svg_survives_when_png_is_installed(icon_root: Path, payload: bytes) -> None:
    # Given
    native = _app(icon_root.parents[2] / "native.svg", _NATIVE_SVG)
    _install_icon(native)
    app = _app(icon_root.parents[2] / "private.png", payload)
    # When
    icon_name = _install_icon(app)
    # Then
    assert icon_name == "winpodx-sample"
    assert (icon_root / "scalable/apps/winpodx-sample.svg").read_bytes() == _NATIVE_SVG
    assert (icon_root / "32x32/apps/winpodx-sample.png").read_bytes() == payload


def test_owned_wrapper_is_atomically_replaced_when_target_is_read_only(icon_root: Path) -> None:
    # Given: obtain ownership through the public install path, not a marker literal.
    source = icon_root.parents[2] / "private.png"
    app = _app(source, _png(32, 32))
    _install_icon(app)
    wrapper = icon_root / "scalable/apps/winpodx-sample.svg"
    assert wrapper.is_file(), "Initial PNG install must generate a wrapper before refresh"
    before = wrapper.read_bytes()
    wrapper.chmod(0o444)
    replacement = _png(128, 32)
    source.write_bytes(replacement)
    with wrapper.open("rb") as prior_reader:
        # When
        icon_name = _install_icon(app)
        # Then: a reader of the old inode never sees a partial/in-place rewrite.
        assert prior_reader.read() == before
    assert icon_name == "winpodx-sample"
    _assert_wrapper(wrapper, replacement, (128, 32))
    assert wrapper.stat().st_mode & 0o777 == 0o644
    assert list(wrapper.parent.iterdir()) == [wrapper]


def test_wrapper_and_png_survive_when_svg_replacement_failure_occurs(
    icon_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    source = icon_root.parents[2] / "private.png"
    app = _app(source, _png(32, 32))
    _install_icon(app)
    wrapper = icon_root / "scalable/apps/winpodx-sample.svg"
    assert wrapper.is_file(), "Initial PNG install must generate a wrapper before refresh"
    before = wrapper.read_bytes()
    replacement = _png(64, 64)
    source.write_bytes(replacement)
    real_replace = os.replace
    failure = OSError("injected SVG replacement failure")
    replacement_attempted = False

    def replace_with_failure(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        nonlocal replacement_attempted
        if Path(dst) == wrapper:
            replacement_attempted = True
            raise failure
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_with_failure)
    # When: either propagation or best-effort logging may report the injected failure.
    try:
        _install_icon(app)
    except OSError as exc:
        assert exc is failure
    # Then
    assert replacement_attempted
    assert wrapper.read_bytes() == before
    assert source.read_bytes() == replacement
    assert (icon_root / "64x64/apps/winpodx-sample.png").read_bytes() == replacement
    assert list(wrapper.parent.iterdir()) == [wrapper]


def test_symlink_target_is_untouched_when_owned_wrapper_is_refreshed(icon_root: Path) -> None:
    # Given
    source = icon_root.parents[2] / "private.png"
    app = _app(source, _png(32, 32))
    _install_icon(app)
    wrapper = icon_root / "scalable/apps/winpodx-sample.svg"
    assert wrapper.is_file(), "Initial PNG install must generate a wrapper before refresh"
    before = wrapper.read_bytes()
    external = source.with_suffix(".svg")
    wrapper.rename(external)
    wrapper.symlink_to(external)
    replacement = _png(64, 64)
    source.write_bytes(replacement)
    # When
    icon_name = _install_icon(app)
    # Then
    assert icon_name == "winpodx-sample"
    assert external.read_bytes() == before
    assert (icon_root / "64x64/apps/winpodx-sample.png").read_bytes() == replacement


@pytest.mark.parametrize(
    "payload", [b"malformed", _png(32, 32, 1 << 20)], ids=["malformed", "oversized"]
)
def test_owned_wrapper_is_removed_when_refreshed_png_cannot_be_exported(
    icon_root: Path, payload: bytes
) -> None:
    # Given
    source = icon_root.parents[2] / "private.png"
    app = _app(source, _png(32, 32))
    _install_icon(app)
    wrapper = icon_root / "scalable/apps/winpodx-sample.svg"
    assert wrapper.is_file(), "Initial PNG install must generate a wrapper before refresh"
    source.write_bytes(payload)
    # When
    icon_name = _install_icon(app)
    # Then
    assert icon_name == "winpodx-sample"
    assert not wrapper.exists()
    assert (icon_root / "32x32/apps/winpodx-sample.png").read_bytes() == payload


@pytest.mark.parametrize(
    ("payload", "bucket"),
    [
        pytest.param(b"not a PNG", 32, id="malformed"),
        pytest.param(_png(32, 32)[:33], 32, id="truncated-after-ihdr"),
        pytest.param(_png(32, 32)[:29] + b"\0\0\0\0" + _png(32, 32)[33:], 32, id="bad-crc"),
        pytest.param(_png(1025, 1), 256, id="too-wide"),
        pytest.param(_png(1, 1025), 256, id="too-tall"),
        pytest.param(_png(32, 32, 1 << 20), 32, id="too-many-bytes"),
    ],
)
def test_png_fallback_survives_when_source_cannot_be_exported(
    icon_root: Path, payload: bytes, bucket: int
) -> None:
    # Given
    app = _app(icon_root.parents[2] / "private.png", payload)
    # When
    icon_name = _install_icon(app)
    # Then
    assert icon_name == "winpodx-sample"
    assert (icon_root / f"{bucket}x{bucket}/apps/winpodx-sample.png").read_bytes() == payload
    assert not (icon_root / "scalable/apps/winpodx-sample.svg").exists()
