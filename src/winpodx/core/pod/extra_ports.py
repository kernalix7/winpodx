# SPDX-License-Identifier: MIT
"""Validated host-to-guest port forwards for compose and dockur USER_PORTS."""

from __future__ import annotations

import re
from dataclasses import dataclass

from winpodx.core.guest_disk import GUEST_SMB_PORT, SMB_HOST_PORT

_ENTRY_RE = re.compile(
    r"(?:(?P<bind>127\.0\.0\.1|0\.0\.0\.0):)?"
    r"(?P<host>[0-9]{1,5}(?:-[0-9]{1,5})?)"
    r"(?::(?P<guest>[0-9]{1,5}(?:-[0-9]{1,5})?))?"
    r"/(?P<protocol>tcp|udp)"
)
_MAX_FORWARDED_PORTS = 256


@dataclass(slots=True)
class InvalidPortMapping(ValueError):
    entry: str
    reason: str

    def __str__(self) -> str:
        return f"Invalid extra port {self.entry!r}: {self.reason}"


@dataclass(frozen=True, slots=True)
class PortForward:
    host_bind: str
    host_start: int
    host_end: int
    guest_start: int
    guest_end: int
    protocol: str

    def canonical(self) -> str:
        host = _format_range(self.host_start, self.host_end)
        guest = _format_range(self.guest_start, self.guest_end)
        return f"{self.host_bind}:{host}:{guest}/{self.protocol}"

    def guest_ports(self) -> list[str]:
        return [f"{port}/{self.protocol}" for port in range(self.guest_start, self.guest_end + 1)]


def _format_range(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"


def _parse_range(raw: str, entry: str) -> tuple[int, int]:
    parts = raw.split("-", 1)
    start = int(parts[0])
    end = int(parts[1]) if len(parts) == 2 else start
    if start < 1024 or end > 65535 or end < start:
        raise InvalidPortMapping(
            entry, "ports must be unprivileged and in ascending 1024-65535 order"
        )
    if end - start + 1 > _MAX_FORWARDED_PORTS:
        raise InvalidPortMapping(entry, "range exceeds 256 ports")
    return start, end


def parse_extra_ports(
    entries: list[str], *, rdp_port: int = 3390, vnc_port: int = 8007
) -> tuple[PortForward, ...]:
    """Parse mappings, rejecting reserved/overlapping host and guest endpoints."""
    if not isinstance(entries, list):
        raise InvalidPortMapping(str(entries), "expected a list of mappings")
    if not entries:
        return ()

    from winpodx.core.agent import AGENT_PORT

    fixed_ports = {3389, 8006, AGENT_PORT, GUEST_SMB_PORT, SMB_HOST_PORT, rdp_port, vnc_port}
    host_endpoints: dict[tuple[int, str], set[str]] = {}
    guest_endpoints: set[tuple[int, str]] = set()
    forwards: list[PortForward] = []
    total = 0
    for raw in entries:
        if not isinstance(raw, str):
            raise InvalidPortMapping(str(raw), "expected a string mapping")
        entry = raw.strip()
        match = _ENTRY_RE.fullmatch(entry)
        if match is None:
            raise InvalidPortMapping(raw, "expected [127.0.0.1|0.0.0.0:]HOST[:GUEST]/tcp|udp")
        host_start, host_end = _parse_range(match.group("host"), raw)
        guest_start, guest_end = _parse_range(match.group("guest") or match.group("host"), raw)
        if host_end - host_start != guest_end - guest_start:
            raise InvalidPortMapping(raw, "host and guest ranges must have equal lengths")
        total += host_end - host_start + 1
        if total > _MAX_FORWARDED_PORTS:
            raise InvalidPortMapping(raw, "total exceeds 256 forwarded ports")
        host_bind = match.group("bind") or "127.0.0.1"
        protocol = match.group("protocol")
        for offset in range(host_end - host_start + 1):
            host_port = host_start + offset
            guest_port = guest_start + offset
            if host_port in fixed_ports or guest_port in fixed_ports:
                raise InvalidPortMapping(raw, "collides with a fixed RDP/VNC/web/agent/SMB port")
            host_key = (host_port, protocol)
            bindings = host_endpoints.setdefault(host_key, set())
            if bindings and (
                host_bind in bindings or "0.0.0.0" in bindings or host_bind == "0.0.0.0"
            ):
                raise InvalidPortMapping(raw, "duplicate or overlapping host port")
            guest_key = (guest_port, protocol)
            if guest_key in guest_endpoints:
                raise InvalidPortMapping(raw, "duplicate guest port")
            bindings.add(host_bind)
            guest_endpoints.add(guest_key)
        forwards.append(
            PortForward(host_bind, host_start, host_end, guest_start, guest_end, protocol)
        )

    return tuple(
        sorted(
            forwards,
            key=lambda f: (f.host_start, f.protocol != "tcp", f.host_bind, f.guest_start),
        )
    )


def normalize_extra_ports(
    entries: list[str], *, rdp_port: int = 3390, vnc_port: int = 8007
) -> list[str]:
    """Return stable, explicit host-bind entries for persistence."""
    return [
        forward.canonical()
        for forward in parse_extra_ports(entries, rdp_port=rdp_port, vnc_port=vnc_port)
    ]
