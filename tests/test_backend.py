"""Tests for backend abstraction."""

from unittest.mock import MagicMock, patch

from winpodx.backend.manual import ManualBackend
from winpodx.core.config import Config
from winpodx.core.pod import PodState, start_pod


def test_manual_backend_start_stop():
    cfg = Config()
    cfg.rdp.ip = "192.168.1.100"
    backend = ManualBackend(cfg)
    backend.start()
    backend.stop()
    assert backend.get_ip() == "192.168.1.100"


def test_get_backend():
    from winpodx.core.pod import get_backend

    cfg = Config()

    cfg.pod.backend = "manual"
    assert type(get_backend(cfg)).__name__ == "ManualBackend"

    cfg.pod.backend = "podman"
    assert type(get_backend(cfg)).__name__ == "PodmanBackend"

    cfg.pod.backend = "docker"
    assert type(get_backend(cfg)).__name__ == "DockerBackend"


def test_start_pod_waits_for_ready_and_returns_running():
    cfg = Config()
    cfg.pod.boot_timeout = 120

    fake_backend = MagicMock()
    fake_backend.start.return_value = None
    fake_backend.wait_for_ready.return_value = True

    with patch("winpodx.core.pod.get_backend", return_value=fake_backend):
        status = start_pod(cfg)

    fake_backend.start.assert_called_once()
    fake_backend.wait_for_ready.assert_called_once_with(timeout=120)
    assert status.state == PodState.RUNNING


def test_start_pod_timeout_returns_starting():
    cfg = Config()

    fake_backend = MagicMock()
    fake_backend.start.return_value = None
    fake_backend.wait_for_ready.return_value = False

    with patch("winpodx.core.pod.get_backend", return_value=fake_backend):
        status = start_pod(cfg)

    fake_backend.wait_for_ready.assert_called_once()
    assert status.state == PodState.STARTING


def test_start_pod_start_failure_returns_error():
    cfg = Config()

    fake_backend = MagicMock()
    fake_backend.start.side_effect = RuntimeError("boom")

    with patch("winpodx.core.pod.get_backend", return_value=fake_backend):
        status = start_pod(cfg)

    fake_backend.wait_for_ready.assert_not_called()
    assert status.state == PodState.ERROR
    assert "boom" in status.error


def test_podman_backend_is_running_uses_configured_container_name():
    from winpodx.backend.podman import PodmanBackend

    cfg = Config()
    cfg.pod.container_name = "my-custom-pod"
    backend = PodmanBackend(cfg)

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "running\n"
    fake_result.stderr = ""

    with patch("winpodx.backend.podman.subprocess.run", return_value=fake_result) as mock_run:
        assert backend.is_running() is True

    args, _ = mock_run.call_args
    cmd = args[0]
    assert "name=my-custom-pod" in cmd
    assert "name=winpodx-windows" not in cmd


def test_docker_backend_is_running_uses_configured_container_name():
    from winpodx.backend.docker import DockerBackend

    cfg = Config()
    cfg.pod.container_name = "docker-win"
    backend = DockerBackend(cfg)

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "running\n"
    fake_result.stderr = ""

    with patch("winpodx.backend.docker.subprocess.run", return_value=fake_result) as mock_run:
        assert backend.is_running() is True

    args, _ = mock_run.call_args
    cmd = args[0]
    assert "name=docker-win" in cmd
