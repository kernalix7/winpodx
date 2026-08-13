# SPDX-License-Identifier: MIT
"""Tests for backend abstraction."""

import datetime
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from winpodx.backend.base import Backend, _container_uptime_secs, _parse_inspect_timestamp
from winpodx.backend.docker import DockerBackend
from winpodx.backend.manual import ManualBackend
from winpodx.backend.podman import PodmanBackend, is_rootless_podman
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


def test_start_pod_returns_error_on_port_conflict():
    """#754: a host port conflict must short-circuit before backend.start()."""
    from winpodx.core.pod.ports import PortConflict

    cfg = Config()
    fake_backend = MagicMock()
    fake_backend.is_running.return_value = False

    conflict = PortConflict(port=3390, label="RDP", owner="gnome-remote-desktop")

    with (
        patch("winpodx.core.pod.lifecycle.get_backend", return_value=fake_backend),
        patch("winpodx.core.pod.lifecycle.check_host_ports", return_value=[conflict]),
    ):
        status = start_pod(cfg)

    fake_backend.start.assert_not_called()
    fake_backend.wait_for_ready.assert_not_called()
    assert status.state == PodState.ERROR
    assert "3390" in status.error
    assert "RDP" in status.error


def test_start_pod_skips_port_check_when_pod_already_running():
    """A running/paused pod holds its own ports -- preflight must not run."""
    cfg = Config()
    fake_backend = MagicMock()
    fake_backend.is_running.return_value = True
    fake_backend.wait_for_ready.return_value = True

    with (
        patch("winpodx.core.pod.lifecycle.get_backend", return_value=fake_backend),
        patch("winpodx.core.pod.lifecycle.check_host_ports") as mock_check,
    ):
        status = start_pod(cfg)

    mock_check.assert_not_called()
    fake_backend.start.assert_called_once()
    assert status.state == PodState.RUNNING


class TestPodmanComposeCmdBrewOffPath:
    """#765/#725: `_compose_cmd` is the code path `start()`/`stop()` use --
    i.e. what the tray's Pod>Start button actually calls via
    `core.pod.start_pod`. A PATH-only `podman-compose` probe here silently
    no-ops the tray button for a Homebrew install (common on immutable
    distros like Bazzite) whose bin dir isn't on the desktop session's
    $PATH."""

    def test_on_path_unchanged(self, monkeypatch):
        from winpodx.backend.podman import PodmanBackend

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/podman-compose")
        backend = PodmanBackend(Config())
        cmd = backend._compose_cmd()
        assert cmd[0] == "/usr/bin/podman-compose"

    def test_found_via_brew_dir_uses_absolute_path(self, monkeypatch, tmp_path):
        from winpodx.backend.podman import PodmanBackend

        brew_dir = tmp_path / "linuxbrew" / "bin"
        brew_dir.mkdir(parents=True)
        compose_bin = brew_dir / "podman-compose"
        compose_bin.write_text("#!/bin/sh\n")
        compose_bin.chmod(0o755)

        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr("winpodx.utils.deps._BREW_COMPOSE_DIRS", (str(brew_dir),))

        backend = PodmanBackend(Config())
        cmd = backend._compose_cmd()
        assert cmd[0] == str(compose_bin)

    def test_raises_when_truly_absent(self, monkeypatch, tmp_path):
        from winpodx.backend.podman import PodmanBackend

        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr("winpodx.utils.deps._BREW_COMPOSE_DIRS", (str(tmp_path / "nowhere"),))

        backend = PodmanBackend(Config())
        try:
            backend._compose_cmd()
            raise AssertionError("expected RuntimeError")
        except RuntimeError as e:
            assert "podman-compose" in str(e)


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


def test_podman_backend_stop_keeps_container():
    # #573-session follow-up: stop must `compose stop` (keep the stopped
    # container), NOT `compose down` (which removes it and makes an
    # update-while-stopped recreate the container every time).
    from winpodx.backend.podman import PodmanBackend

    backend = PodmanBackend(Config())
    fake = MagicMock()
    fake.returncode = 0
    fake.stderr = ""
    # Mock _compose_cmd too: it raises if podman-compose isn't on PATH (CI).
    with (
        patch.object(backend, "_compose_cmd", return_value=["podman-compose", "-f", "c.yaml"]),
        patch("winpodx.backend.podman.subprocess.run", return_value=fake) as mock_run,
    ):
        backend.stop()
    cmd = mock_run.call_args[0][0]
    assert "stop" in cmd
    assert "down" not in cmd


def test_docker_backend_stop_keeps_container():
    from winpodx.backend.docker import DockerBackend

    backend = DockerBackend(Config())
    fake = MagicMock()
    fake.returncode = 0
    fake.stderr = ""
    with (
        patch.object(backend, "_compose_cmd", return_value=["docker", "compose", "-f", "c.yaml"]),
        patch("winpodx.backend.docker.subprocess.run", return_value=fake) as mock_run,
    ):
        backend.stop()
    cmd = mock_run.call_args[0][0]
    assert "stop" in cmd
    assert "down" not in cmd


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"2026-05-21T07:55:40.190529036+09:00"', (2026, 5, 21, 7, 55, 40, 190529)),
        ("2026-05-21T07:55:40.5Z", (2026, 5, 21, 7, 55, 40, 500000)),
        ("2026-05-21 07:55:40.123456789 +0900 KST", (2026, 5, 21, 7, 55, 40, 123456)),
    ],
)
def test_parse_inspect_timestamp_accepts_runtime_formats(raw, expected):
    parsed = _parse_inspect_timestamp(raw)
    assert parsed is not None
    assert (
        parsed.year,
        parsed.month,
        parsed.day,
        parsed.hour,
        parsed.minute,
        parsed.second,
        parsed.microsecond,
    ) == expected
    assert parsed.utcoffset() is not None


@pytest.mark.parametrize("raw", ["", "not-a-time", "0001-01-01T00:00:00Z"])
def test_parse_inspect_timestamp_rejects_invalid_or_zero_time(raw):
    assert _parse_inspect_timestamp(raw) is None


def test_container_uptime_retries_compose_names_and_returns_elapsed_seconds():
    missing = subprocess.CompletedProcess([], 1, "", "missing")
    found = subprocess.CompletedProcess([], 0, '"2026-05-21T07:55:40+00:00"\n', "")

    class FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 21, 7, 56, 10, tzinfo=tz)

    with (
        patch("winpodx.backend.base.subprocess.run", side_effect=[missing, found]) as mock_run,
        patch("winpodx.backend.base.datetime.datetime", FixedDateTime),
    ):
        assert _container_uptime_secs("podman", "windows") == 30

    assert mock_run.call_args_list[0].args[0] == [
        "podman",
        "inspect",
        "--format",
        "{{json .State.StartedAt}}",
        "windows",
    ]
    assert mock_run.call_args_list[1].args[0][-1] == "winpodx_windows"


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("podman", 5)])
def test_container_uptime_returns_none_when_runtime_probe_fails(failure):
    with patch("winpodx.backend.base.subprocess.run", side_effect=failure):
        assert _container_uptime_secs("podman", "windows") is None


def test_container_uptime_returns_none_for_non_string_json_timestamp():
    result = subprocess.CompletedProcess([], 0, "123\n", "")
    with patch("winpodx.backend.base.subprocess.run", return_value=result):
        assert _container_uptime_secs("docker", "windows") is None


def test_container_uptime_returns_none_after_all_candidates_fail():
    result = subprocess.CompletedProcess([], 1, "", "not found")
    with patch("winpodx.backend.base.subprocess.run", return_value=result) as mock_run:
        assert _container_uptime_secs("docker", "windows") is None
    assert mock_run.call_count == 3


def test_backend_defaults_and_restart_order():
    events = []

    class ConcreteBackend(Backend):
        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

        def is_running(self):
            return True

        def get_ip(self):
            return "203.0.113.5"

    backend = ConcreteBackend(Config())
    backend.restart()

    assert events == ["stop", "start"]
    assert backend.is_paused() is False
    assert backend.uptime_secs() is None
    assert backend.wait_for_ready(timeout=0) is False


def test_backend_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        Backend(Config())


@pytest.mark.parametrize(
    ("stdout", "euid", "expected"),
    [("true\n", 0, True), ("false\n", 1000, False), ("unknown\n", 1000, True)],
)
def test_is_rootless_podman_uses_info_then_euid_fallback(stdout, euid, expected):
    is_rootless_podman.cache_clear()
    result = subprocess.CompletedProcess([], 0, stdout, "")
    with (
        patch("winpodx.backend.podman.subprocess.run", return_value=result) as mock_run,
        patch("winpodx.backend.podman.os.geteuid", return_value=euid),
    ):
        assert is_rootless_podman() is expected
    assert mock_run.call_args.args[0] == [
        "podman",
        "info",
        "--format",
        "{{.Host.Security.Rootless}}",
    ]
    is_rootless_podman.cache_clear()


def test_is_rootless_podman_falls_back_when_info_raises():
    is_rootless_podman.cache_clear()
    with (
        patch("winpodx.backend.podman.subprocess.run", side_effect=OSError),
        patch("winpodx.backend.podman.os.geteuid", return_value=0),
    ):
        assert is_rootless_podman() is False
    is_rootless_podman.cache_clear()


def test_podman_start_builds_exact_streaming_command():
    backend = PodmanBackend(Config())
    clean_env = {"PATH": "/usr/bin"}
    with (
        patch.object(backend, "_compose_cmd", return_value=["podman-compose", "-f", "c.yaml"]),
        patch.object(backend, "_run_streaming") as mock_stream,
        patch("winpodx.backend.podman.host_env", return_value=clean_env),
    ):
        backend.start()
    mock_stream.assert_called_once_with(
        ["podman-compose", "-f", "c.yaml", "up", "-d"],
        idle_limit=300,
        hard_cap=14400,
        description="podman compose up",
        env=clean_env,
    )


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CalledProcessError(1, ["podman-compose"], stderr="bad"),
        subprocess.TimeoutExpired(["podman-compose"], 300),
    ],
)
def test_podman_start_propagates_streaming_failures(failure):
    backend = PodmanBackend(Config())
    with (
        patch.object(backend, "_compose_cmd", return_value=["podman-compose"]),
        patch.object(backend, "_run_streaming", side_effect=failure),
        pytest.raises(type(failure)),
    ):
        backend.start()


def test_podman_run_streaming_forwards_argv_and_collects_success_output():
    backend = PodmanBackend(Config())
    proc = MagicMock()
    proc.stdout = iter(["pulling\n", "started\n"])
    proc.wait.return_value = 0
    with patch("winpodx.backend.podman.subprocess.Popen", return_value=proc) as mock_popen:
        backend._run_streaming(
            ["podman-compose", "up", "-d"],
            idle_limit=300,
            hard_cap=14400,
            description="compose up",
            env={"PATH": "/usr/bin"},
        )
    mock_popen.assert_called_once_with(
        ["podman-compose", "up", "-d"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env={"PATH": "/usr/bin"},
    )


def test_podman_run_streaming_raises_with_captured_output_on_nonzero_exit():
    backend = PodmanBackend(Config())
    proc = MagicMock()
    proc.stdout = iter(["failure detail\n"])
    proc.wait.return_value = 7
    with (
        patch("winpodx.backend.podman.subprocess.Popen", return_value=proc),
        pytest.raises(subprocess.CalledProcessError) as exc_info,
    ):
        backend._run_streaming(
            ["podman-compose", "up"],
            idle_limit=300,
            hard_cap=14400,
            description="compose up",
        )
    assert exc_info.value.returncode == 7
    assert exc_info.value.stderr == "failure detail\n"


@pytest.mark.parametrize(
    "backend_class,module", [(PodmanBackend, "podman"), (DockerBackend, "docker")]
)
def test_container_backend_state_degraded_paths(backend_class, module):
    backend = backend_class(Config())
    failed = subprocess.CompletedProcess([], 125, "", "daemon unavailable")
    with patch(f"winpodx.backend.{module}.subprocess.run", return_value=failed):
        assert backend.is_running() is False
    with patch(f"winpodx.backend.{module}.subprocess.run", side_effect=FileNotFoundError):
        assert backend.is_running() is False


@pytest.mark.parametrize("backend_class", [PodmanBackend, DockerBackend])
def test_container_backend_paused_state_and_configured_ip(backend_class):
    cfg = Config()
    cfg.rdp.ip = "192.0.2.20"
    backend = backend_class(cfg)
    with patch.object(backend, "_container_state", return_value="paused"):
        assert backend.is_running() is True
        assert backend.is_paused() is True
    assert backend.get_ip() == "192.0.2.20"


def test_podman_uptime_delegates_exact_runtime_and_container_name():
    cfg = Config()
    cfg.pod.container_name = "custom-pod"
    backend = PodmanBackend(cfg)
    with patch("winpodx.backend.podman._container_uptime_secs", return_value=45) as probe:
        assert backend.uptime_secs() == 45
    probe.assert_called_once_with("podman", "custom-pod")


def test_docker_compose_and_uptime_commands_use_configured_paths():
    cfg = Config()
    cfg.pod.container_name = "custom-docker"
    backend = DockerBackend(cfg)
    with patch.object(backend, "_compose_file", return_value="/cfg/compose.yaml"):
        assert backend._compose_cmd() == ["docker", "compose", "-f", "/cfg/compose.yaml"]
    with patch("winpodx.backend.docker._container_uptime_secs", return_value=46) as probe:
        assert backend.uptime_secs() == 46
    probe.assert_called_once_with("docker", "custom-docker")


def test_docker_start_builds_exact_command_and_propagates_failures():
    backend = DockerBackend(Config())
    completed = subprocess.CompletedProcess([], 0, "", "")
    with (
        patch.object(backend, "_compose_cmd", return_value=["docker", "compose", "-f", "c.yaml"]),
        patch("winpodx.backend.docker.host_env", return_value={"PATH": "/usr/bin"}),
        patch("winpodx.backend.docker.subprocess.run", return_value=completed) as mock_run,
    ):
        backend.start()
    mock_run.assert_called_once_with(
        ["docker", "compose", "-f", "c.yaml", "up", "-d"],
        check=True,
        capture_output=True,
        text=True,
        timeout=14400,
        env={"PATH": "/usr/bin"},
    )

    failure = subprocess.CalledProcessError(1, ["docker"], stderr="bad")
    with (
        patch.object(backend, "_compose_cmd", return_value=["docker", "compose"]),
        patch("winpodx.backend.docker.subprocess.run", side_effect=failure),
        pytest.raises(subprocess.CalledProcessError),
    ):
        backend.start()


@pytest.mark.parametrize(
    "backend_class,module", [(PodmanBackend, "podman"), (DockerBackend, "docker")]
)
def test_container_backend_stop_swallows_timeout(backend_class, module):
    backend = backend_class(Config())
    with (
        patch.object(backend, "_compose_cmd", return_value=[module, "compose"]),
        patch(
            f"winpodx.backend.{module}.subprocess.run",
            side_effect=subprocess.TimeoutExpired([module], 180),
        ),
    ):
        backend.stop()


@pytest.mark.parametrize("backend_class", [PodmanBackend, DockerBackend])
def test_container_backend_wait_for_ready_returns_immediately_when_rdp_is_available(backend_class):
    cfg = Config()
    cfg.rdp.ip = "198.51.100.8"
    cfg.rdp.port = 3390
    backend = backend_class(cfg)
    with (
        patch.object(backend, "is_running", return_value=True),
        patch.object(backend, "is_paused", return_value=False),
        patch("winpodx.core.pod.check_rdp_port", return_value=True) as check_port,
    ):
        assert backend.wait_for_ready(timeout=1) is True
    check_port.assert_called_once_with("198.51.100.8", 3390, timeout=3)


def test_manual_backend_running_and_ready_probe_exact_rdp_endpoint():
    cfg = Config()
    cfg.rdp.ip = "203.0.113.10"
    cfg.rdp.port = 3391
    backend = ManualBackend(cfg)
    with patch("winpodx.core.pod.check_rdp_port", return_value=True) as check_port:
        assert backend.is_running() is True
        assert backend.wait_for_ready(timeout=1) is True
    assert check_port.call_args_list[0].args == ("203.0.113.10", 3391)
    assert check_port.call_args_list[1].args == ("203.0.113.10", 3391)
    assert check_port.call_args_list[1].kwargs == {"timeout": 3}
