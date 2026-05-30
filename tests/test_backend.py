# SPDX-License-Identifier: MIT
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

    with patch("winpodx.core.pod.lifecycle.get_backend", return_value=fake_backend):
        status = start_pod(cfg)

    fake_backend.start.assert_called_once()
    fake_backend.wait_for_ready.assert_called_once_with(timeout=120)
    assert status.state == PodState.RUNNING


def test_start_pod_timeout_returns_starting():
    cfg = Config()

    fake_backend = MagicMock()
    fake_backend.start.return_value = None
    fake_backend.wait_for_ready.return_value = False

    with patch("winpodx.core.pod.lifecycle.get_backend", return_value=fake_backend):
        status = start_pod(cfg)

    fake_backend.wait_for_ready.assert_called_once()
    assert status.state == PodState.STARTING


def test_start_pod_start_failure_returns_error():
    cfg = Config()

    fake_backend = MagicMock()
    fake_backend.start.side_effect = RuntimeError("boom")

    with patch("winpodx.core.pod.lifecycle.get_backend", return_value=fake_backend):
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


# pod_status state-classification tests covering the new UNRESPONSIVE
# discriminator (#TBD). Mock the backend to control is_running /
# is_paused / uptime_secs deterministically, mock `check_rdp_port` to
# control the RDP probe, and verify each of the five states resolves.


def _patched_pod_status(*, running, paused, rdp_ok, uptime, backend_name="podman"):
    """Helper — run pod_status with the four input switches mocked."""
    from winpodx.core.pod import pod_status

    cfg = Config()
    cfg.pod.backend = backend_name
    fake_backend = MagicMock()
    fake_backend.is_running.return_value = running
    fake_backend.is_paused.return_value = paused
    fake_backend.uptime_secs.return_value = uptime

    with (
        patch("winpodx.core.pod.backend.get_backend", return_value=fake_backend),
        patch("winpodx.core.pod.backend.check_rdp_port", return_value=rdp_ok),
    ):
        return pod_status(cfg)


def test_pod_status_running_when_container_up_and_rdp_reachable():
    status = _patched_pod_status(running=True, paused=False, rdp_ok=True, uptime=300)
    assert status.state == PodState.RUNNING


def test_pod_status_starting_when_container_recent_and_rdp_down():
    """Container up < 180s + RDP miss = still booting, do not yet
    classify as UNRESPONSIVE."""
    status = _patched_pod_status(running=True, paused=False, rdp_ok=False, uptime=60)
    assert status.state == PodState.STARTING


def test_pod_status_unresponsive_when_container_old_and_rdp_down():
    """Container up past the 180s floor + RDP miss = guest stalled."""
    status = _patched_pod_status(running=True, paused=False, rdp_ok=False, uptime=900)
    assert status.state == PodState.UNRESPONSIVE


def test_pod_status_starting_when_uptime_unknown_on_non_container_backend():
    """the manual backend return None from uptime_secs() — they
    must fall back to STARTING (no auto-recovery for non-container)."""
    status = _patched_pod_status(
        running=True,
        paused=False,
        rdp_ok=False,
        uptime=None,
        backend_name="manual",
    )
    assert status.state == PodState.STARTING


def test_pod_status_starting_when_uptime_unknown_on_container_backend():
    """Container backend (podman / docker) returning None from
    ``uptime_secs`` must fall back to STARTING. The earlier post-#221
    attempt to classify None-on-container as UNRESPONSIVE flooded
    stderr during the first-boot Sysprep window with a WARN every two
    seconds while podman inspect legitimately couldn't yet hand back
    a parseable ``StartedAt``. Under-reporting UNRESPONSIVE during
    install is fine; over-reporting it spams the log + triggers
    false-positive auto-recovery. The function logs once when the
    fallback triggers so a genuinely broken uptime probe is still
    visible."""
    # Reset the module-level guard so the test asserts the warn path
    # the same way on every run.
    import winpodx.core.pod.backend as _backend_mod

    _backend_mod._UPTIME_NONE_WARNING_FIRED = False

    status = _patched_pod_status(
        running=True,
        paused=False,
        rdp_ok=False,
        uptime=None,
        backend_name="podman",
    )
    assert status.state == PodState.STARTING


def test_pod_status_paused_short_circuits_before_rdp_probe():
    """Paused state must win over RDP / uptime classification."""
    status = _patched_pod_status(running=True, paused=True, rdp_ok=False, uptime=900)
    assert status.state == PodState.PAUSED


def test_pod_status_stopped_when_container_not_running():
    status = _patched_pod_status(running=False, paused=False, rdp_ok=False, uptime=None)
    assert status.state == PodState.STOPPED
