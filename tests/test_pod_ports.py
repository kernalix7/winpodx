# SPDX-License-Identifier: MIT
"""Tests for the host port preflight (#754)."""

from __future__ import annotations

import socket
import subprocess

from winpodx.core.config import Config
from winpodx.core.pod.ports import (
    PortConflict,
    _owner_hint,
    _port_in_use,
    check_host_ports,
    format_port_conflict_error,
)


class TestPortInUse:
    def test_bound_port_is_in_use(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            assert _port_in_use(port) is True
        finally:
            sock.close()

    def test_freed_port_is_not_in_use(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()  # release it before probing
        assert _port_in_use(port) is False


class TestCheckHostPorts:
    def test_manual_backend_returns_empty(self, monkeypatch):
        cfg = Config()
        cfg.pod.backend = "manual"
        # Even if something were listening, manual publishes no ports.
        monkeypatch.setattr("winpodx.core.pod.ports._port_in_use", lambda port: True)
        assert check_host_ports(cfg) == []

    def test_podman_backend_reports_conflicts(self, monkeypatch):
        cfg = Config()
        cfg.pod.backend = "podman"
        monkeypatch.setattr(
            "winpodx.core.pod.ports._port_in_use", lambda port: port == cfg.rdp.port
        )
        monkeypatch.setattr(
            "winpodx.core.pod.ports._owner_hint", lambda port: "gnome-remote-desktop"
        )
        conflicts = check_host_ports(cfg)
        assert len(conflicts) == 1
        assert conflicts[0] == PortConflict(
            port=cfg.rdp.port, label="RDP", owner="gnome-remote-desktop"
        )

    def test_docker_backend_no_conflicts_returns_empty(self, monkeypatch):
        cfg = Config()
        cfg.pod.backend = "docker"
        monkeypatch.setattr("winpodx.core.pod.ports._port_in_use", lambda port: False)
        assert check_host_ports(cfg) == []


class TestOwnerHint:
    def test_parses_ss_output(self, monkeypatch):
        fake = subprocess.CompletedProcess(
            args=["ss"],
            returncode=0,
            stdout=(
                "LISTEN 0      511    127.0.0.1:3390 0.0.0.0:* "
                'users:(("gnome-remote-desktop",pid=1234,fd=7))\n'
            ),
            stderr="",
        )
        monkeypatch.setattr("winpodx.core.pod.ports.subprocess.run", lambda *a, **k: fake)
        assert _owner_hint(3390) == "gnome-remote-desktop"

    def test_missing_ss_binary_returns_empty(self, monkeypatch):
        def _boom(*a, **k):
            raise FileNotFoundError()

        monkeypatch.setattr("winpodx.core.pod.ports.subprocess.run", _boom)
        assert _owner_hint(3390) == ""

    def test_timeout_returns_empty(self, monkeypatch):
        def _boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="ss", timeout=3)

        monkeypatch.setattr("winpodx.core.pod.ports.subprocess.run", _boom)
        assert _owner_hint(3390) == ""

    def test_no_matching_line_returns_empty(self, monkeypatch):
        fake = subprocess.CompletedProcess(
            args=["ss"],
            returncode=0,
            stdout="LISTEN 0      511    127.0.0.1:9999 0.0.0.0:*\n",
            stderr="",
        )
        monkeypatch.setattr("winpodx.core.pod.ports.subprocess.run", lambda *a, **k: fake)
        assert _owner_hint(3390) == ""


class TestFormatPortConflictError:
    def test_includes_header_and_conflict_line(self):
        conflicts = [PortConflict(port=3390, label="RDP", owner="gnome-remote-desktop")]
        msg = format_port_conflict_error(conflicts)
        assert "already in use" in msg
        assert "127.0.0.1:3390 [RDP] (gnome-remote-desktop)" in msg

    def test_unknown_owner_falls_back(self):
        msg = format_port_conflict_error([PortConflict(port=3390, label="RDP")])
        assert "(unknown process)" in msg

    def test_config_hint_present_for_rdp_and_vnc(self):
        rdp_msg = format_port_conflict_error([PortConflict(port=3390, label="RDP")])
        assert "config set rdp.port" in rdp_msg
        vnc_msg = format_port_conflict_error([PortConflict(port=8007, label="VNC")])
        assert "config set" in vnc_msg

    def test_config_hint_absent_for_agent_and_smb_only(self):
        agent_msg = format_port_conflict_error([PortConflict(port=8765, label="agent")])
        assert "config set" not in agent_msg
        smb_msg = format_port_conflict_error([PortConflict(port=4445, label="SMB (reverse-open)")])
        assert "config set" not in smb_msg

    def test_gnome_hint_only_when_rdp_conflicts(self):
        rdp_msg = format_port_conflict_error([PortConflict(port=3390, label="RDP")])
        assert "GNOME" in rdp_msg
        vnc_msg = format_port_conflict_error([PortConflict(port=8007, label="VNC")])
        assert "GNOME" not in vnc_msg
        agent_msg = format_port_conflict_error([PortConflict(port=8765, label="agent")])
        assert "GNOME" not in agent_msg
