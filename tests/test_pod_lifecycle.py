# SPDX-License-Identifier: MIT
from __future__ import annotations

from unittest.mock import MagicMock, patch

from winpodx.core.config import Config
from winpodx.core.pod.backend import PodState
from winpodx.core.pod.lifecycle import start_pod, stop_pod


def test_start_pod_returns_probe_error_before_port_preflight() -> None:
    cfg = Config()
    backend = MagicMock()
    backend.is_running.side_effect = RuntimeError("runtime unavailable")

    with (
        patch("winpodx.core.pod.lifecycle.get_backend", return_value=backend),
        patch("winpodx.core.pod.lifecycle.check_host_ports") as check_ports,
    ):
        status = start_pod(cfg)

    assert status.state is PodState.ERROR
    assert status.error == "runtime unavailable"
    check_ports.assert_not_called()
    backend.start.assert_not_called()


def test_start_pod_formats_port_conflict_and_skips_backend_start() -> None:
    cfg = Config()
    backend = MagicMock()
    backend.is_running.return_value = False
    conflicts = [MagicMock()]

    with (
        patch("winpodx.core.pod.lifecycle.get_backend", return_value=backend),
        patch("winpodx.core.pod.lifecycle.check_host_ports", return_value=conflicts) as check,
        patch(
            "winpodx.core.pod.lifecycle.format_port_conflict_error",
            return_value="RDP port 3390 is occupied",
        ) as format_error,
    ):
        status = start_pod(cfg)

    assert status.state is PodState.ERROR
    assert status.error == "RDP port 3390 is occupied"
    check.assert_called_once_with(cfg)
    format_error.assert_called_once_with(conflicts)
    backend.start.assert_not_called()


def test_start_pod_delegates_and_returns_running_with_configured_ip() -> None:
    cfg = Config()
    cfg.rdp.ip = "10.0.0.8"
    cfg.pod.boot_timeout = 77
    backend = MagicMock()
    backend.is_running.return_value = True
    backend.wait_for_ready.return_value = True

    with (
        patch("winpodx.core.pod.lifecycle.get_backend", return_value=backend),
        patch("winpodx.core.pod.lifecycle.check_host_ports") as check_ports,
    ):
        status = start_pod(cfg)

    assert status.state is PodState.RUNNING
    assert status.ip == "10.0.0.8"
    check_ports.assert_not_called()
    backend.start.assert_called_once_with()
    backend.wait_for_ready.assert_called_once_with(timeout=77)


def test_start_pod_reports_start_and_wait_errors() -> None:
    cfg = Config()
    backend = MagicMock()
    backend.is_running.return_value = True
    backend.start.side_effect = RuntimeError("start failed")

    with patch("winpodx.core.pod.lifecycle.get_backend", return_value=backend):
        status = start_pod(cfg)

    assert status.state is PodState.ERROR
    assert status.error == "start failed"
    backend.wait_for_ready.assert_not_called()

    backend.start.side_effect = None
    backend.wait_for_ready.side_effect = RuntimeError("readiness failed")
    with patch("winpodx.core.pod.lifecycle.get_backend", return_value=backend):
        status = start_pod(cfg)

    assert status.state is PodState.ERROR
    assert status.error == "readiness failed"


def test_start_pod_returns_starting_when_guest_is_not_ready() -> None:
    cfg = Config()
    backend = MagicMock()
    backend.is_running.return_value = True
    backend.wait_for_ready.return_value = False

    with patch("winpodx.core.pod.lifecycle.get_backend", return_value=backend):
        status = start_pod(cfg)

    assert status.state is PodState.STARTING
    assert status.ip == cfg.rdp.ip


def test_stop_pod_delegates_and_maps_backend_failure() -> None:
    cfg = Config()
    backend = MagicMock()

    with patch("winpodx.core.pod.lifecycle.get_backend", return_value=backend):
        assert stop_pod(cfg).state is PodState.STOPPED
    backend.stop.assert_called_once_with()

    backend.stop.side_effect = RuntimeError("stop failed")
    with patch("winpodx.core.pod.lifecycle.get_backend", return_value=backend):
        status = stop_pod(cfg)

    assert status.state is PodState.ERROR
    assert status.error == "stop failed"
