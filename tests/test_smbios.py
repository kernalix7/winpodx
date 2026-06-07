# SPDX-License-Identifier: MIT
"""Synthetic SMBIOS disguise blob (#246, T1.5) — encode / validate / write."""

from __future__ import annotations

import pytest

import winpodx.core.pod.compose as _compose_module
from winpodx.core.pod.smbios import build_disguise_smbios_blob, validate_blob


def _walk_types(blob: bytes) -> list[int]:
    types: list[int] = []
    i = 0
    while i < len(blob):
        types.append(blob[i])
        length = blob[i + 1]
        end = blob.find(b"\x00\x00", i + length)
        i = end + 2
    return types


def test_blob_builds_and_validates():
    blob = build_disguise_smbios_blob()
    assert isinstance(blob, bytes) and len(blob) > 0
    validate_blob(blob)  # raises on any encoding slip


def test_blob_contains_sensor_descriptor_types():
    # The types al-khaser checks for existence: voltage(26), temperature(28),
    # cooling(27), cache(7), slot(9), port connector(8), physical memory
    # array(16), memory device(17), OEM strings(11).
    types = _walk_types(build_disguise_smbios_blob())
    for t in (26, 28, 27, 7, 9, 8, 16, 17, 11):
        assert t in types, f"missing SMBIOS type {t}"


def test_memory_device_references_array_handle():
    # Type 17's first formatted field is the parent type-16 array handle; a
    # dangling reference would make the WMI memory tree look synthetic.
    from winpodx.core.pod.smbios import _H_MEMARRAY, _H_MEMDEV

    blob = build_disguise_smbios_blob()
    i = 0
    handles_seen = set()
    array_ref = None
    while i < len(blob):
        stype = blob[i]
        length = blob[i + 1]
        handle = int.from_bytes(blob[i + 2 : i + 4], "little")
        handles_seen.add(handle)
        if stype == 17:
            array_ref = int.from_bytes(blob[i + 4 : i + 6], "little")
        i = blob.find(b"\x00\x00", i + length) + 2
    assert _H_MEMARRAY in handles_seen
    assert _H_MEMDEV in handles_seen
    assert array_ref == _H_MEMARRAY


def test_blob_has_no_vm_marker_strings():
    # A raw SMBIOS string scan must not surface a VM / project marker.
    blob = build_disguise_smbios_blob()
    lowered = blob.lower()
    for marker in (b"qemu", b"bochs", b"seabios", b"winpodx", b"virtual"):
        assert marker not in lowered, f"leaked marker {marker!r} into SMBIOS blob"


def test_validate_rejects_truncated_header():
    with pytest.raises(ValueError):
        validate_blob(b"\x1c")  # too short for a 4-byte header


def test_validate_rejects_unterminated_string_set():
    # type 8 header (len 9) + 5 formatted bytes but no double-null terminator.
    bad = bytes([8, 9, 0, 0]) + bytes([1, 0xFF, 2, 0xFF, 0xFF]) + b"J1"
    with pytest.raises(ValueError):
        validate_blob(bad)


def test_write_blob_writes_and_returns_container_path(tmp_path):
    path = _compose_module._write_disguise_smbios_blob(str(tmp_path))
    assert path == "/oem/winpodx-smbios.bin"
    written = tmp_path / "winpodx-smbios.bin"
    assert written.exists()
    validate_blob(written.read_bytes())  # what we wrote is valid


def test_write_blob_failsafe_returns_none_on_bad_dir(tmp_path):
    # A non-existent OEM dir → None, never a dangling `-smbios file=` arg.
    bad = tmp_path / "does" / "not" / "exist"
    assert _compose_module._write_disguise_smbios_blob(str(bad)) is None
