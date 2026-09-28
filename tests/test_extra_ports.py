# SPDX-License-Identifier: MIT
"""Extra host-to-guest port-forward validation (#826)."""

from __future__ import annotations

import pytest

from winpodx.core.pod.extra_ports import normalize_extra_ports, parse_extra_ports


def test_empty_port_list_is_the_default() -> None:
    assert normalize_extra_ports([]) == []
    assert parse_extra_ports([]) == ()


def test_single_port_shorthand_normalizes_to_explicit_loopback_mapping() -> None:
    assert normalize_extra_ports([" 25565/tcp "]) == ["127.0.0.1:25565:25565/tcp"]
    entry = parse_extra_ports(["25565:25566/udp"])[0]
    assert (entry.host_bind, entry.host_start, entry.guest_start, entry.protocol) == (
        "127.0.0.1",
        25565,
        25566,
        "udp",
    )


def test_explicit_lan_and_ranges_normalize_deterministically() -> None:
    assert normalize_extra_ports(["0.0.0.0:27016-27017:28016-28017/udp", "25565/tcp"]) == [
        "127.0.0.1:25565:25565/tcp",
        "0.0.0.0:27016-27017:28016-28017/udp",
    ]


@pytest.mark.parametrize(
    "entry",
    [
        "",
        "25565",
        "80/tcp",
        "0/tcp",
        "65536/tcp",
        "9999/icmp",
        "9999/tcp\n      - 0.0.0.0:1:1/tcp",
        '9999/tcp"',
        "127.0.0.2:9999:9999/tcp",
        "27017-27016/udp",
        "27016-27018:28016-28017/udp",
        "10000-11000/tcp",
        "1234-1235:80-81/tcp",
    ],
)
def test_invalid_or_unsafe_entries_are_rejected(entry: str) -> None:
    with pytest.raises(ValueError):
        parse_extra_ports([entry])


@pytest.mark.parametrize(
    "entry",
    [
        "3390/tcp",
        "3389/udp",
        "8007/tcp",
        "8006/tcp",
        "8765/tcp",
        "4445/tcp",
        "445/tcp",
        "10000:3389/tcp",
        "10000-10005:8004-8009/udp",
        "3390-3395:10000-10005/tcp",
    ],
)
def test_fixed_host_and_guest_ports_cannot_be_mapped(entry: str) -> None:
    with pytest.raises(ValueError):
        parse_extra_ports([entry])


def test_custom_fixed_host_ports_are_reserved() -> None:
    with pytest.raises(ValueError):
        parse_extra_ports(["15000/tcp"], rdp_port=15000)
    with pytest.raises(ValueError):
        parse_extra_ports(["15001/udp"], vnc_port=15001)


@pytest.mark.parametrize(
    "entries",
    [
        ["25000/tcp", "25000/tcp"],
        ["25000-25002:30000-30002/tcp", "25001:32000/tcp"],
        ["127.0.0.1:25000:25000/tcp", "0.0.0.0:25000:25000/tcp"],
        ["25000:30000/tcp", "25001:30000/tcp"],
    ],
)
def test_duplicate_or_overlapping_mappings_are_rejected(entries: list[str]) -> None:
    with pytest.raises(ValueError):
        parse_extra_ports(entries)


def test_tcp_and_udp_can_share_same_numeric_port() -> None:
    assert normalize_extra_ports(["25000/udp", "25000/tcp"]) == [
        "127.0.0.1:25000:25000/tcp",
        "127.0.0.1:25000:25000/udp",
    ]
