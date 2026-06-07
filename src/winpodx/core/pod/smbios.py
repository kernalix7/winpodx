# SPDX-License-Identifier: MIT
"""Synthetic SMBIOS structure blob for the bare-metal disguise (#246, T1.5).

al-khaser (and similar VM detectors) flag a guest as a VM when SMBIOS sensor /
descriptor structures are *absent* — a physical board exposes voltage probes,
temperature probes, a cooling device, cache, slots and port connectors, while
QEMU's default SMBIOS omits them. These checks test for the *existence* of the
corresponding ``Win32_*`` / ``CIM_*`` WMI classes, not live readings, so static
descriptor structures with nominal/unknown values are enough to satisfy them.

This module builds a small binary SMBIOS structure table (types 26 / 28 / 27 /
7 / 9 / 8 / 16 / 17 / 6 / 11) with **synthetic** values — no host serials or
live sensor data are read, so nothing machine-identifying is produced. The blob is
written to the OEM dir (mounted into the container) and passed to QEMU via
``-smbios file=``. Types 16 / 17 add the physical-memory-array / memory-device
descriptors (``Win32_PhysicalMemory`` existence); type 11 adds a benign OEM
string so a raw SMBIOS string scan finds no ``QEMU`` / ``winpodx`` marker.

Format: each structure is ``type(1) length(1) handle(2 LE) <formatted> <strings>``
where the string-set is the referenced 1-indexed null-terminated strings
followed by a terminating null (or a double-null when there are no strings),
per the DMTF SMBIOS spec. ``validate_blob`` walks the result back to catch an
encoding slip before it ever reaches QEMU.
"""

from __future__ import annotations

# Handles are arbitrary but must be unique within the table.
_H_TEMP = 0x1C00
_H_VOLT = 0x1A00
_H_COOL = 0x1B00
_H_CACHE = 0x0700
_H_SLOT = 0x0900
_H_PORT = 0x0800
_H_MEMARRAY = 0x1000
_H_MEMDEV = 0x1100
_H_MEMMOD = 0x0600
_H_OEMSTR = 0x0B00

_UNKNOWN_W = 0x8000  # SMBIOS "unknown" sentinel for WORD probe values


def _w(v: int) -> bytes:
    return (v & 0xFFFF).to_bytes(2, "little")


def _dw(v: int) -> bytes:
    return (v & 0xFFFFFFFF).to_bytes(4, "little")


def _structure(stype: int, handle: int, formatted: bytes, strings: list[str]) -> bytes:
    """Encode one SMBIOS structure (header + formatted area + string-set)."""
    length = 4 + len(formatted)
    out = bytes([stype & 0xFF, length & 0xFF]) + _w(handle) + formatted
    if strings:
        for s in strings:
            out += s.encode("ascii", "replace") + b"\x00"
        out += b"\x00"
    else:
        out += b"\x00\x00"
    return out


def _probe(stype: int, handle: int, description: str) -> bytes:
    """Voltage (26) / Temperature (28) probe — identical 22-byte layout."""
    formatted = (
        bytes([1, 0x03])  # description (string 1), location-and-status
        + _w(_UNKNOWN_W)  # maximum value
        + _w(_UNKNOWN_W)  # minimum value
        + _w(_UNKNOWN_W)  # resolution
        + _w(_UNKNOWN_W)  # tolerance
        + _w(_UNKNOWN_W)  # accuracy
        + _dw(0)  # OEM-defined
        + _w(_UNKNOWN_W)  # nominal value
    )
    return _structure(stype, handle, formatted, [description])


def build_disguise_smbios_blob() -> bytes:
    """Build the synthetic SMBIOS structure blob (sensor/descriptor types)."""
    parts: list[bytes] = []

    # Type 28 — Temperature Probe
    parts.append(_probe(28, _H_TEMP, "CPU Temperature"))

    # Type 26 — Voltage Probe
    parts.append(_probe(26, _H_VOLT, "CPU Voltage"))

    # Type 27 — Cooling Device (references the temperature probe handle)
    cool = (
        _w(_H_TEMP)  # temperature probe handle
        + bytes([0x67, 0x01])  # device type + status, cooling unit group
        + _dw(0)  # OEM-defined
        + _w(_UNKNOWN_W)  # nominal speed
        + bytes([1])  # description (string 1)
    )
    parts.append(_structure(27, _H_COOL, cool, ["CPU Fan"]))

    # Type 7 — Cache Information (L1)
    cache = (
        bytes([1])  # socket designation (string 1)
        + _w(0x0180)  # cache configuration (enabled, WB, L1)
        + _w(0x0040)  # maximum cache size (64 KB granularity unit)
        + _w(0x0040)  # installed size
        + _w(0x0002)  # supported SRAM type
        + _w(0x0002)  # current SRAM type
        + bytes([0x01, 0x06, 0x05, 0x07])  # speed, ECC, cache type, associativity
    )
    parts.append(_structure(7, _H_CACHE, cache, ["L1 Cache"]))

    # Type 9 — System Slot (a PCIe slot)
    slot = (
        bytes([1, 0xB6, 0x0D, 0x03, 0x04])  # desig, type(PCIe), width, usage, length
        + _w(1)  # slot ID
        + bytes([0x04, 0x01])  # characteristics 1 + 2
    )
    parts.append(_structure(9, _H_SLOT, slot, ["PCIe Slot 1"]))

    # Type 8 — Port Connector
    port = bytes([1, 0xFF, 2, 0xFF, 0xFF])  # int-ref, int-type, ext-ref, ext-type, port-type
    parts.append(_structure(8, _H_PORT, port, ["J1", "USB"]))

    # Type 16 — Physical Memory Array (parent of the memory devices). Satisfies
    # the Win32_PhysicalMemoryArray existence check; QEMU's default SMBIOS omits
    # it, which al-khaser flags as a VM tell.
    mem_array = (
        bytes([0x03, 0x03, 0x03])  # location: system board, use: system memory, ecc: none
        + _dw(0x04000000)  # maximum capacity = 64 GB (in KB)
        + _w(0xFFFE)  # memory error info handle: not provided
        + _w(1)  # number of memory devices
    )
    parts.append(_structure(16, _H_MEMARRAY, mem_array, []))

    # Type 17 — Memory Device (one synthetic DIMM under the array). Satisfies the
    # Win32_PhysicalMemory / CIM_PhysicalMemory existence checks. All strings are
    # synthetic (no host serial / part is read); the serial is a static
    # placeholder, never a real module serial.
    mem_dev = (
        _w(_H_MEMARRAY)  # physical memory array handle
        + _w(0xFFFE)  # memory error info handle: not provided
        + _w(0x0040)  # total width: 64 bits
        + _w(0x0040)  # data width: 64 bits
        + _w(0x4000)  # size: 16384 MB (16 GB)
        + bytes([0x09])  # form factor: DIMM
        + bytes([0x00])  # device set: none
        + bytes([1])  # device locator (string 1)
        + bytes([2])  # bank locator (string 2)
        + bytes([0x1A])  # memory type: DDR4
        + _w(0x0080)  # type detail: synchronous
        + _w(0x0C80)  # speed: 3200 MT/s
        + bytes([3])  # manufacturer (string 3)
        + bytes([4])  # serial number (string 4)
        + bytes([5])  # asset tag (string 5)
        + bytes([6])  # part number (string 6)
    )
    parts.append(
        _structure(
            17,
            _H_MEMDEV,
            mem_dev,
            ["DIMM 0", "BANK 0", "Generic", "00000000", "Not Specified", "Generic Module"],
        )
    )

    # Type 6 — Memory Module Information (obsolete, pre-type-17). Best-effort
    # for the Win32_MemoryDevice WMI existence check, which type 17 alone does
    # not populate. Synthetic single module, 16 GB, socket "A0".
    mem_mod = (
        bytes([1])  # socket designation (string 1)
        + bytes([0xFF])  # bank connections: none
        + bytes([0x00])  # current speed: unknown
        + _w(0x0080)  # current memory type: bit7 (DIMM)
        + bytes([0x0E])  # installed size: 2^14 MB = 16 GB (bit7=0 single bank)
        + bytes([0x0E])  # enabled size: 16 GB
        + bytes([0x00])  # error status: none
    )
    parts.append(_structure(6, _H_MEMMOD, mem_mod, ["A0"]))

    # Type 11 — OEM Strings. Real boards expose a type-11 structure; QEMU often
    # omits it. A single benign "Default string" (what most retail boards ship)
    # avoids leaking "QEMU" / "winpodx" into a raw SMBIOS string scan.
    parts.append(_structure(11, _H_OEMSTR, bytes([1]), ["Default string"]))

    blob = b"".join(parts)
    validate_blob(blob)  # raise on any encoding slip before it reaches QEMU
    return blob


def validate_blob(blob: bytes) -> None:
    """Walk the blob structure-by-structure; raise ValueError on any slip.

    A defensive parse-back so a future edit can't ship a malformed table that
    QEMU would reject at boot. Checks each header length, that the formatted
    area fits, that the string-set is double-null terminated, and that handles
    are unique.
    """
    i = 0
    handles: set[int] = set()
    n = len(blob)
    while i < n:
        if i + 4 > n:
            raise ValueError("smbios: truncated header")
        length = blob[i + 1]
        handle = int.from_bytes(blob[i + 2 : i + 4], "little")
        if length < 4:
            raise ValueError(f"smbios: bad structure length {length}")
        if handle in handles:
            raise ValueError(f"smbios: duplicate handle 0x{handle:04x}")
        handles.add(handle)
        j = i + length  # start of string-set
        if j > n:
            raise ValueError("smbios: formatted area overruns blob")
        # Find the double-null that ends the string-set.
        end = blob.find(b"\x00\x00", j)
        if end == -1:
            raise ValueError("smbios: unterminated string-set")
        i = end + 2
    if not handles:
        raise ValueError("smbios: empty blob")
