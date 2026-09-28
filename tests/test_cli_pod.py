# SPDX-License-Identifier: MIT
"""Behavior tests for the pod CLI command handlers."""

from __future__ import annotations

import argparse
import io
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from winpodx.cli import pod
from winpodx.core.config import Config
from winpodx.core.dockur_progress import DockurProgress
from winpodx.core.pod import PodState, PodStatus
from winpodx.core.transport.base import ExecResult


@pytest.fixture()
def cfg(monkeypatch: pytest.MonkeyPatch) -> Config:
    config = Config()
    config.pod.backend = "podman"
    config.pod.container_name = "test-windows"
    config.pod.image = "test/image"
    config.rdp.ip = "127.0.0.1"
    config.rdp.port = 3390
    config.rdp.user = "WPX-User"
    config.rdp.password = "fixture-password"
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: config))
    return config


def _exit_code(call) -> int:
    with pytest.raises(SystemExit) as exc:
        call()
    return exc.value.code


def test_handle_pod_routes_lifecycle_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    start = MagicMock()
    recreate = MagicMock()
    wait_ready = MagicMock()
    monkeypatch.setattr(pod, "_start", start)
    monkeypatch.setattr(pod, "_recreate", recreate)
    monkeypatch.setattr(pod, "_wait_ready", wait_ready)

    pod.handle_pod(argparse.Namespace(pod_command="start", wait=True, timeout=77, tuning="safe"))
    pod.handle_pod(argparse.Namespace(pod_command="recreate", keep_iso=True, wipe_storage=False))
    pod.handle_pod(
        argparse.Namespace(pod_command="wait-ready", timeout=88, logs=True, verbose=True)
    )

    start.assert_called_once_with(True, 77, tuning_override="safe")
    recreate.assert_called_once_with(wipe_storage=True, keep_iso=True, on_progress=None)
    wait_ready.assert_called_once_with(88, True, True)


def test_handle_pod_unknown_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert _exit_code(lambda: pod.handle_pod(argparse.Namespace(pod_command="wat"))) == 1
    assert "Usage: winpodx pod" in capsys.readouterr().out


@pytest.mark.parametrize("state", [PodState.RUNNING, PodState.STARTING])
def test_start_success_paths(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: PodState,
) -> None:
    ensure_compose = MagicMock()
    notify = MagicMock()
    backend = MagicMock()
    backend.wait_for_ready.return_value = True
    monkeypatch.setattr("winpodx.core.provisioner._ensure_config", lambda: cfg)
    monkeypatch.setattr("winpodx.core.provisioner._ensure_compose", ensure_compose)
    monkeypatch.setattr(
        "winpodx.core.pod.start_pod", lambda config: PodStatus(state=state, ip="10.0.0.2")
    )
    monkeypatch.setattr("winpodx.core.pod.get_backend", lambda config: backend)
    monkeypatch.setattr("winpodx.desktop.notify.notify_pod_started", notify)
    monkeypatch.setattr(pod, "_maybe_start_reverse_open_listener", MagicMock())

    pod._start(wait=True, timeout=99999, tuning_override="safe")

    out = capsys.readouterr().out
    assert "Overriding tuning_profile" in out
    assert "Pod is running" in out if state is PodState.RUNNING else "Pod is ready" in out
    ensure_compose.assert_called_once_with(cfg)
    notify.assert_called_once_with("10.0.0.2")
    if state is PodState.STARTING:
        backend.wait_for_ready.assert_called_once_with(3600)


def test_start_starting_without_wait_reports_next_action(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("winpodx.core.provisioner._ensure_config", lambda: cfg)
    monkeypatch.setattr("winpodx.core.provisioner._ensure_compose", lambda config: None)
    monkeypatch.setattr(
        "winpodx.core.pod.start_pod",
        lambda config: PodStatus(state=PodState.STARTING, ip="10.0.0.3"),
    )
    monkeypatch.setattr(pod, "_maybe_start_reverse_open_listener", lambda config: None)

    pod._start(wait=False, timeout=0)

    assert "start --wait" in capsys.readouterr().out


@pytest.mark.parametrize(
    "status,backend_ready,expected",
    [
        (PodStatus(PodState.ERROR, error="broken"), True, "Failed to start pod"),
        (PodStatus(PodState.STARTING, ip="10.0.0.3"), False, "Timeout waiting for RDP"),
    ],
)
def test_start_failure_paths(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: PodStatus,
    backend_ready: bool,
    expected: str,
) -> None:
    backend = MagicMock()
    backend.wait_for_ready.return_value = backend_ready
    monkeypatch.setattr("winpodx.core.provisioner._ensure_config", lambda: cfg)
    monkeypatch.setattr("winpodx.core.provisioner._ensure_compose", lambda config: None)
    monkeypatch.setattr("winpodx.core.pod.start_pod", lambda config: status)
    monkeypatch.setattr("winpodx.core.pod.get_backend", lambda config: backend)

    assert _exit_code(lambda: pod._start(wait=True, timeout=10)) == 1
    captured = capsys.readouterr()
    assert expected in captured.err


@pytest.mark.parametrize(
    "listener_result,expected",
    [("started", "started (pid 4321)"), ("failed", "start failed")],
)
def test_reverse_open_listener_reports_result(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    listener_result: str,
    expected: str,
) -> None:
    monkeypatch.setattr(
        "winpodx.cli.host_open.ensure_listener_running", lambda config: listener_result
    )
    monkeypatch.setattr("winpodx.reverse_open.lifecycle.is_listener_running", lambda: 4321)

    pod._maybe_start_reverse_open_listener(cfg)

    captured = capsys.readouterr()
    assert expected in captured.out + captured.err


def test_stop_decline_preserves_pod(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stop = MagicMock()
    session = SimpleNamespace(app_name="Word", pid=42)
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: [session])
    monkeypatch.setattr("winpodx.core.pod.stop_pod", stop)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")

    pod._stop()

    stop.assert_not_called()
    assert "Active sessions: Word" in capsys.readouterr().out


def test_stop_confirm_stops_listener_and_notifies(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stop = MagicMock()
    notify = MagicMock()
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: [])
    monkeypatch.setattr("winpodx.core.pod.stop_pod", stop)
    monkeypatch.setattr("winpodx.reverse_open.lifecycle.stop_listener", lambda: True)
    monkeypatch.setattr("winpodx.desktop.notify.notify_pod_stopped", notify)

    pod._stop()

    stop.assert_called_once_with(cfg)
    notify.assert_called_once_with()
    assert "Reverse-open listener stopped" in capsys.readouterr().out


def test_status_prints_pod_and_sessions(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda config: PodStatus(PodState.ERROR, error="agent unavailable"),
    )
    monkeypatch.setattr(
        "winpodx.core.process.list_active_sessions",
        lambda: [SimpleNamespace(app_name="Excel", pid=91)],
    )

    pod._status()

    out = capsys.readouterr().out
    assert "Backend:  podman" in out
    assert "State:    error" in out
    assert "Excel (PID 91)" in out
    assert "agent unavailable" in out


@pytest.mark.parametrize(
    "backend,state,responsive,results,exit_code",
    [
        ("manual", PodState.RUNNING, True, {}, 2),
        ("podman", PodState.STOPPED, True, {}, 2),
        ("podman", PodState.RUNNING, False, {}, 2),
        ("podman", PodState.RUNNING, True, {"agent": "ok", "rdp": "failed"}, 3),
        ("podman", PodState.RUNNING, True, {"agent": "ok", "rdp": "ok"}, None),
    ],
)
def test_apply_fixes_outcomes(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend: str,
    state: PodState,
    responsive: bool,
    results: dict[str, str],
    exit_code: int | None,
) -> None:
    cfg.pod.backend = backend
    apply = MagicMock(return_value=results)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(state))
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive",
        lambda config, timeout: responsive,
    )
    monkeypatch.setattr("winpodx.core.provisioner.apply_windows_runtime_fixes", apply)

    if exit_code is None:
        pod._apply_fixes()
        assert "All fixes applied" in capsys.readouterr().out
    else:
        assert _exit_code(pod._apply_fixes) == exit_code
    if backend == "podman" and state is PodState.RUNNING and responsive:
        apply.assert_called_once_with(cfg)


@pytest.mark.parametrize(
    "action,handler",
    [
        ("on", "_multi_session_enable"),
        ("off", "_multi_session_disable"),
        ("status", "_multi_session_status"),
    ],
)
def test_multi_session_routes_action(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, action: str, handler: str
) -> None:
    target = MagicMock()
    monkeypatch.setattr(pod, handler, target)

    pod._multi_session(action)

    target.assert_called_once_with(cfg)


def test_multi_session_rejects_backend_and_action(
    cfg: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.pod.backend = "manual"
    assert _exit_code(lambda: pod._multi_session("on")) == 2
    cfg.pod.backend = "podman"
    assert _exit_code(lambda: pod._multi_session("invalid")) == 2
    assert "Unknown multi-session action" in capsys.readouterr().out


@pytest.mark.parametrize(
    "result,exit_code,expected",
    [
        (ExecResult(0, "QUEUED", ""), None, "activation queued"),
        (ExecResult(2, "NOT-STAGED", ""), 2, "not staged"),
        (ExecResult(4, "", "activation failed"), 3, "FAIL: rc=4"),
    ],
)
def test_multi_session_enable_results(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: ExecResult,
    exit_code: int | None,
    expected: str,
) -> None:
    run = MagicMock(return_value=result)
    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", run)

    if exit_code is None:
        pod._multi_session_enable(cfg)
    else:
        assert _exit_code(lambda: pod._multi_session_enable(cfg)) == exit_code

    assert expected in capsys.readouterr().out
    assert run.call_args.kwargs == {"description": "multi-session-enable", "timeout": 60}
    assert "rdprrap-activate.ps1" in run.call_args.args[1]


@pytest.mark.parametrize(
    "result,exit_code,expected",
    [
        (ExecResult(0, "disabled", ""), None, "multi-session disabled"),
        (ExecResult(2, "missing", ""), 2, "not found"),
        (ExecResult(5, "", "denied"), 3, "rc=5"),
    ],
)
def test_multi_session_disable_results(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: ExecResult,
    exit_code: int | None,
    expected: str,
) -> None:
    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", lambda *a, **k: result)

    if exit_code is None:
        pod._multi_session_disable(cfg)
    else:
        assert _exit_code(lambda: pod._multi_session_disable(cfg)) == exit_code

    assert expected in capsys.readouterr().out


def test_multi_session_status_prints_output_and_failure(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_via_transport",
        lambda *a, **k: ExecResult(7, "rdprrap status: failed", "probe failed"),
    )

    assert _exit_code(lambda: pod._multi_session_status(cfg)) == 3
    out = capsys.readouterr().out
    assert "rdprrap status: failed" in out
    assert "FAIL: rc=7" in out


@pytest.mark.parametrize("state,exit_code", [(PodState.RUNNING, None), (PodState.ERROR, 1)])
def test_restart_reports_result(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: PodState,
    exit_code: int | None,
) -> None:
    stop = MagicMock()
    monkeypatch.setattr("winpodx.core.pod.stop_pod", stop)
    monkeypatch.setattr(
        "winpodx.core.pod.start_pod",
        lambda config: PodStatus(state, error="restart failed"),
    )

    if exit_code is None:
        pod._restart()
        assert "Pod restarted" in capsys.readouterr().out
    else:
        assert _exit_code(pod._restart) == exit_code
        assert "restart failed" in capsys.readouterr().err
    stop.assert_called_once_with(cfg)


def test_recreate_aborted_without_typed_confirmation(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stop = MagicMock()
    monkeypatch.setattr("winpodx.core.pod.stop_pod", stop)
    monkeypatch.setattr("builtins.input", lambda: "no")

    assert _exit_code(lambda: pod._recreate(wipe_storage=True)) == 2
    stop.assert_not_called()
    assert "Aborted" in capsys.readouterr().out


@pytest.mark.parametrize(
    "wipe,keep,expected",
    [
        (False, False, "Pod recreated. Container picked up"),
        (True, False, "fresh storage"),
        (True, True, "reusing the cached ISO"),
    ],
)
def test_recreate_success_messages(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    wipe: bool,
    keep: bool,
    expected: str,
) -> None:
    wipe_storage = MagicMock()
    monkeypatch.setattr("builtins.input", lambda: "WIPE")
    monkeypatch.setattr("winpodx.core.pod.stop_pod", MagicMock())
    monkeypatch.setattr("winpodx.core.compose.generate_compose", MagicMock())
    monkeypatch.setattr("winpodx.core.pod.start_pod", lambda config: PodStatus(PodState.STARTING))
    monkeypatch.setattr(pod, "_wipe_pod_storage", wipe_storage)

    pod._recreate(wipe_storage=wipe, keep_iso=keep)

    assert expected in capsys.readouterr().out
    if wipe:
        wipe_storage.assert_called_once_with(cfg, keep_iso=keep)
    else:
        wipe_storage.assert_not_called()


def test_recreate_compose_and_start_failures(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("winpodx.core.pod.stop_pod", lambda config: None)
    monkeypatch.setattr(
        "winpodx.core.compose.generate_compose", MagicMock(side_effect=OSError("readonly"))
    )
    assert _exit_code(lambda: pod._recreate(wipe_storage=False)) == 1
    assert "readonly" in capsys.readouterr().err

    monkeypatch.setattr("winpodx.core.compose.generate_compose", lambda config: None)
    monkeypatch.setattr(
        "winpodx.core.pod.start_pod",
        lambda config: PodStatus(PodState.ERROR, error="boot failed"),
    )
    assert _exit_code(lambda: pod._recreate(wipe_storage=False)) == 1
    assert "boot failed" in capsys.readouterr().err


def test_wipe_named_volume_invokes_backend(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.pod.storage_path = ""
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr("subprocess.run", run)

    pod._wipe_pod_storage(cfg)

    assert run.call_args.args[0] == ["podman", "volume", "rm", "-f", "winpodx-data"]
    assert "Removed volume" in capsys.readouterr().out


def test_wipe_named_volume_keep_iso_uses_isolated_container(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg.pod.storage_path = ""
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr("subprocess.run", run)

    pod._wipe_pod_storage(cfg, keep_iso=True)

    command = run.call_args.args[0]
    assert command[:3] == ["podman", "run", "--rm"]
    assert "! -name '*.iso'" in command[-1]


def test_wipe_absent_bind_path_is_noop(
    cfg: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.pod.storage_path = str(tmp_path / "absent")

    pod._wipe_pod_storage(cfg)

    assert "absent; nothing to wipe" in capsys.readouterr().out


@pytest.mark.parametrize(
    "usage,autogrow,expected_exit",
    [
        (None, False, 1),
        (
            SimpleNamespace(
                total_bytes=10 * 1024**3,
                free_bytes=4 * 1024**3,
                used_bytes=6 * 1024**3,
                used_pct=60.0,
            ),
            True,
            None,
        ),
    ],
)
def test_disk_usage_output(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    usage: SimpleNamespace | None,
    autogrow: bool,
    expected_exit: int | None,
) -> None:
    cfg.pod.disk_autogrow = autogrow
    monkeypatch.setattr("winpodx.core.disk.get_guest_disk_usage", lambda config: usage)

    if expected_exit is None:
        pod._disk_usage()
        out = capsys.readouterr().out
        assert "10.0 GiB" in out
        assert "60.0%" in out
        assert "auto-grow            : on" in out
    else:
        assert _exit_code(pod._disk_usage) == expected_exit
        assert "Could not read guest disk usage" in capsys.readouterr().out


def test_grow_disk_extend_only_results(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("winpodx.core.disk.extend_guest_system_volume", lambda config: True)
    pod._grow_disk(target_size=None, increment=None, extend_only=True, assume_yes=False)
    assert "C: extended" in capsys.readouterr().out

    monkeypatch.setattr("winpodx.core.disk.extend_guest_system_volume", lambda config: False)
    assert (
        _exit_code(
            lambda: pod._grow_disk(
                target_size=None, increment=None, extend_only=True, assume_yes=False
            )
        )
        == 1
    )


def test_grow_disk_success_and_note(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = SimpleNamespace(
        old_size="64G", new_size="96G", partition_extended=False, note="extend later"
    )
    compute = MagicMock(return_value="96G")
    grow = MagicMock(return_value=result)
    monkeypatch.setattr("winpodx.core.disk.compute_grow_target", compute)
    monkeypatch.setattr("winpodx.core.disk.grow_disk", grow)

    pod._grow_disk(target_size="96G", increment=None, extend_only=False, assume_yes=True)

    out = capsys.readouterr().out
    assert "Disk grown 64G -> 96G" in out
    assert "extend later" in out
    grow.assert_called_once_with(cfg, target_size="96G", increment=None)


def test_grow_disk_validation_and_confirmation_failures(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from winpodx.core.disk import DiskError

    monkeypatch.setattr(
        "winpodx.core.disk.compute_grow_target", MagicMock(side_effect=DiskError("too large"))
    )
    assert (
        _exit_code(
            lambda: pod._grow_disk(
                target_size="999T", increment=None, extend_only=False, assume_yes=True
            )
        )
        == 1
    )
    assert "too large" in capsys.readouterr().err

    monkeypatch.setattr("winpodx.core.disk.compute_grow_target", lambda *a, **k: "96G")
    monkeypatch.setattr("builtins.input", lambda: "n")
    assert (
        _exit_code(
            lambda: pod._grow_disk(
                target_size="96G", increment=None, extend_only=False, assume_yes=False
            )
        )
        == 2
    )
    assert "Aborted" in capsys.readouterr().out


@pytest.mark.parametrize(
    "guest,results,exit_code",
    [
        (None, {"stage": "ok", "agent": "skipped current"}, None),
        (SimpleNamespace(winpodx="0.9", oem_bundle="14"), {"stage": "failed copy"}, 1),
    ],
)
def test_sync_guest_formats_versions_and_steps(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    guest: SimpleNamespace | None,
    results: dict[str, str],
    exit_code: int | None,
) -> None:
    host = SimpleNamespace(winpodx="1.0", oem_bundle="15")
    sync = MagicMock(return_value=results)
    monkeypatch.setattr("winpodx.core.guest_sync.host_version", lambda: host)
    monkeypatch.setattr("winpodx.core.guest_sync.read_guest_version", lambda config: guest)
    monkeypatch.setattr("winpodx.core.guest_sync.sync_guest", sync)

    if exit_code is None:
        pod._sync_guest(force=True)
    else:
        assert _exit_code(lambda: pod._sync_guest(force=True)) == exit_code

    out = capsys.readouterr().out
    assert "host:  WinPodX 1.0" in out
    assert "version stamp not found" in out if guest is None else "guest: WinPodX 0.9" in out
    assert "[OK  ]" in out if exit_code is None else "[FAIL]" in out
    sync.assert_called_once_with(cfg, force=True)


def test_sync_guest_rejects_backend_and_reports_exception(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.pod.backend = "manual"
    assert _exit_code(lambda: pod._sync_guest(force=False)) == 1
    assert "only supports podman/docker" in capsys.readouterr().out

    from winpodx.core.guest_sync import GuestSyncError

    cfg.pod.backend = "podman"
    version = SimpleNamespace(winpodx="1", oem_bundle="1")
    monkeypatch.setattr("winpodx.core.guest_sync.host_version", lambda: version)
    monkeypatch.setattr("winpodx.core.guest_sync.read_guest_version", lambda config: version)
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest", MagicMock(side_effect=GuestSyncError("offline"))
    )
    assert _exit_code(lambda: pod._sync_guest(force=False)) == 1
    assert "offline" in capsys.readouterr().err


def test_recover_oem_happy_path_uses_scoped_serve_directory(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("winpodx.core.provisioner._ensure_config", lambda: cfg)
    monkeypatch.setattr("shutil.which", lambda command: f"/usr/bin/{command}")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "ps" in command:
            return subprocess.CompletedProcess(command, 0, "test-windows\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    pod._recover_oem()

    out = capsys.readouterr().out
    assert "Invoke-WebRequest" in out
    assert "winpodx pod wait-ready" in out
    tar_command = next(command for command in calls if any("tar czf" in x for x in command))
    assert "/tmp/winpodx-recover/oem.tar.gz" in tar_command[-1]
    server_command = next(
        command for command in calls if any("python3 -m http.server 8766" in x for x in command)
    )
    assert "cd /tmp/winpodx-recover" in server_command[-1]


@pytest.mark.parametrize(
    "backend,which,ps_output,check_rc,expected",
    [
        ("manual", True, "", 0, "only supports podman/docker"),
        ("podman", False, "", 0, "not found on PATH"),
        ("podman", True, "other\n", 0, "not running"),
        ("podman", True, "test-windows\n", 1, "install.bat not found"),
    ],
)
def test_recover_oem_preflight_failures(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend: str,
    which: bool,
    ps_output: str,
    check_rc: int,
    expected: str,
) -> None:
    cfg.pod.backend = backend
    monkeypatch.setattr("winpodx.core.provisioner._ensure_config", lambda: cfg)
    monkeypatch.setattr("shutil.which", lambda command: command if which else None)

    def fake_run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if "ps" in command:
            return subprocess.CompletedProcess(command, 0, ps_output, "")
        return subprocess.CompletedProcess(command, check_rc, "", "")

    monkeypatch.setattr("subprocess.run", fake_run)

    assert _exit_code(pod._recover_oem) == 1
    assert expected in capsys.readouterr().out


class _ImmediateThread:
    def __init__(self, *, target, args=(), daemon=False):
        self.target = target
        self.args = args

    def start(self) -> None:
        if self.target.__name__ == "_drain":
            self.target(*self.args)

    def join(self, timeout: int) -> None:
        assert timeout == 3


class _LogProcess:
    def __init__(self, command, **kwargs):
        self.command = command
        lines = (
            "Downloading Windows\n"
            "4096K .... 50% 4.5M 2m3s\n"
            "you are using the BTRFS filesystem for /storage\n"
            "possible issues were detected in your install.bat\n"
            "SECURITY LEVEL ISSUES\n"
            "❯ Extracting Windows image\n"
            "BdsDxe: loading boot entry\n"
            "visit http://127.0.0.1:8006/ to view the screen\n"
        ).encode()
        self.stdout = io.BytesIO(lines)
        self.stderr = io.BytesIO(b"mknod: /dev/net/tun: exists\n")
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: int) -> None:
        assert timeout == 3

    def poll(self) -> int | None:
        return None


def test_wait_ready_streams_clean_logs_and_completes_all_phases(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.pod.storage_path = "/isolated/storage"
    cfg.pod.vnc_port = 8007
    cfg.pod.initialized = True
    created: list[_LogProcess] = []

    def fake_popen(command, **kwargs):
        process = _LogProcess(command, **kwargs)
        created.append(process)
        return process

    class _UnavailableReader:
        def __init__(self, vnc_port: int) -> None:
            assert vnc_port == 8007

        def poll(self) -> None:
            return None

    monkeypatch.setattr("winpodx.cli.setup_cmd._container_exists_on_backend", lambda config: True)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(PodState.RUNNING))
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive", lambda config, timeout: True
    )
    monkeypatch.setattr(pod, "_wait_for_oem_reboot", lambda config, timeout: False)
    monkeypatch.setattr("winpodx.core.guest_sync.maybe_autosync", lambda config: True)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pod.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr("winpodx.core.dockur_progress.DockurProgressReader", _UnavailableReader)

    pod._wait_ready(60, show_logs=True)

    out = capsys.readouterr().out
    assert "Downloading Windows ISO" in out
    assert "50%" in out and "ETA 2m3s" in out
    assert "lint notices" in out
    assert "127.0.0.1:8007" in out
    assert "OK Container running" in out
    assert "OK RDP port 3390 open" in out
    assert "OK Windows ready" in out
    assert "WARN OEM reboot pass marker still pending" in out
    assert "Guest synced to the upgraded host" in out
    assert created[0].command == ["podman", "logs", "-f", "--tail", "0", "test-windows"]
    assert created[0].terminated


def test_wait_ready_forwards_live_lines_to_on_log_without_changing_stdout(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: the same fully-mocked fresh-boot wait-ready surface as
    # test_wait_ready_streams_clean_logs_and_completes_all_phases, plus an
    # on_log collector to prove the best-effort forwarding of live output.
    cfg.pod.storage_path = "/isolated/storage"
    cfg.pod.vnc_port = 8007
    cfg.pod.initialized = True
    forwarded: list[str] = []

    class _UnavailableReader:
        def __init__(self, vnc_port: int) -> None:
            assert vnc_port == 8007

        def poll(self) -> None:
            return None

    monkeypatch.setattr("winpodx.cli.setup_cmd._container_exists_on_backend", lambda config: True)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(PodState.RUNNING))
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive", lambda config, timeout: True
    )
    monkeypatch.setattr(pod, "_wait_for_oem_reboot", lambda config, timeout: False)
    monkeypatch.setattr("winpodx.core.guest_sync.maybe_autosync", lambda config: False)
    monkeypatch.setattr(subprocess, "Popen", _LogProcess)
    monkeypatch.setattr(pod.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr("winpodx.core.dockur_progress.DockurProgressReader", _UnavailableReader)

    # When: a live-line callback is supplied.
    pod._wait_ready(60, show_logs=True, verbose=False, on_log=forwarded.append)

    # Then: stdout keeps the exact permanent lines it always produced.
    out = capsys.readouterr().out
    assert "OK Container running" in out
    assert "OK RDP port 3390 open" in out
    assert "OK Windows ready" in out

    # Then: on_log received the checkpoint lines and at least one container
    # milestone/progress line, mirroring what streamed to the console.
    joined = "\n".join(forwarded)
    assert "OK Container running" in joined
    assert "OK RDP port 3390 open" in joined
    assert "OK Windows ready" in joined
    assert any("Extracting Windows image" in line for line in forwarded)


def test_wait_ready_forwards_http_progress_to_on_log(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: HTTP progress polling is the active progress source.
    cfg.pod.vnc_port = 18006
    progress_seen = threading.Event()
    forwarded: list[str] = []

    class _Reader:
        def __init__(self, vnc_port: int) -> None:
            return None

        def poll(self) -> DockurProgress:
            progress_seen.set()
            return DockurProgress(text="HTTP progress primary", is_loading=False)

    monkeypatch.setattr("winpodx.cli.setup_cmd._container_exists_on_backend", lambda config: True)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(PodState.RUNNING))
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive",
        lambda config, timeout: progress_seen.wait(1.0),
    )
    monkeypatch.setattr(pod, "_wait_for_oem_reboot", lambda config, timeout: True)
    monkeypatch.setattr("winpodx.core.dockur_progress.DockurProgressReader", _Reader)
    monkeypatch.setattr(subprocess, "Popen", _LogProcess)

    # When
    pod._wait_ready(60, show_logs=True, verbose=False, on_log=forwarded.append)

    # Then: the HTTP progress text reached both stdout and on_log.
    assert "HTTP progress primary" in capsys.readouterr().out
    assert any("HTTP progress primary" in line for line in forwarded)


def test_wait_ready_prefers_msg_html_progress_on_configured_port(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.pod.vnc_port = 18006
    progress_seen = threading.Event()
    ports: list[int] = []

    class _Reader:
        def __init__(self, vnc_port: int) -> None:
            ports.append(vnc_port)

        def poll(self) -> DockurProgress:
            progress_seen.set()
            return DockurProgress(text="HTTP progress primary", is_loading=False)

    monkeypatch.setattr("winpodx.cli.setup_cmd._container_exists_on_backend", lambda config: True)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(PodState.RUNNING))
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive",
        lambda config, timeout: progress_seen.wait(1.0),
    )
    monkeypatch.setattr(pod, "_wait_for_oem_reboot", lambda config, timeout: True)
    monkeypatch.setattr("winpodx.core.dockur_progress.DockurProgressReader", _Reader)
    monkeypatch.setattr(subprocess, "Popen", _LogProcess)

    pod._wait_ready(60, show_logs=True)

    output = capsys.readouterr().out
    assert ports == [18006]
    assert "HTTP progress primary" in output
    assert "50%" not in output
    assert "OK Windows ready" in output
    assert "OK OEM reboot pass complete" in output


def test_wait_ready_log_eta_extends_deadline_while_http_progress_is_visible(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given
    responsive_timeouts: list[int] = []

    class _LoadingReader:
        def __init__(self, vnc_port: int) -> None:
            return None

        def poll(self) -> DockurProgress:
            return DockurProgress(text="Downloading Windows", is_loading=True)

    def wait_for_windows_responsive(config: Config, timeout: int) -> bool:
        responsive_timeouts.append(timeout)
        return True

    monkeypatch.setattr("winpodx.cli.setup_cmd._container_exists_on_backend", lambda config: True)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(PodState.RUNNING))
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive",
        wait_for_windows_responsive,
    )
    monkeypatch.setattr(pod, "_wait_for_oem_reboot", lambda config, timeout: True)
    monkeypatch.setattr("winpodx.core.dockur_progress.DockurProgressReader", _LoadingReader)
    monkeypatch.setattr(subprocess, "Popen", _LogProcess)
    monkeypatch.setattr(pod.threading, "Thread", _ImmediateThread)

    # When
    pod._wait_ready(60, show_logs=True)

    # Then
    assert len(responsive_timeouts) == 1
    assert responsive_timeouts[0] > 60


def test_wait_ready_http_progress_alone_does_not_extend_readiness_deadline(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    responsive_timeouts: list[int] = []

    class _Reader:
        def __init__(self, vnc_port: int) -> None:
            return None

        def poll(self) -> DockurProgress:
            return DockurProgress(text="Preparing Windows", is_loading=True)

    class _NoDownloadProcess(_LogProcess):
        def __init__(self, command, **kwargs) -> None:
            super().__init__(command, **kwargs)
            self.stdout = io.BytesIO(b"ordinary container status\n")
            self.stderr = io.BytesIO()

    monkeypatch.setattr("winpodx.cli.setup_cmd._container_exists_on_backend", lambda config: True)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(PodState.RUNNING))
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive",
        lambda config, timeout: responsive_timeouts.append(timeout) or True,
    )
    monkeypatch.setattr(pod, "_wait_for_oem_reboot", lambda config, timeout: True)
    monkeypatch.setattr("winpodx.core.dockur_progress.DockurProgressReader", _Reader)
    monkeypatch.setattr(subprocess, "Popen", _NoDownloadProcess)
    monkeypatch.setattr(pod.threading, "Thread", _ImmediateThread)

    pod._wait_ready(60, show_logs=True)

    assert responsive_timeouts == [60]


def test_wait_ready_download_liveness_extends_deadline_while_http_is_visible(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    responsive_timeouts: list[int] = []

    class _Reader:
        def __init__(self, vnc_port: int) -> None:
            return None

        def poll(self) -> DockurProgress:
            return DockurProgress(text="Downloading Windows", is_loading=True)

    class _DownloadProcess(_LogProcess):
        def __init__(self, command, **kwargs) -> None:
            super().__init__(command, **kwargs)
            self.stdout = io.BytesIO(b"Downloading Windows\n")
            self.stderr = io.BytesIO()

    class _OnePassEvent:
        def __init__(self) -> None:
            self.stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def set(self) -> None:
            self.stopped = True

        def wait(self, timeout: float | None = None) -> bool:
            self.stopped = True
            return True

    class _SelectedImmediateThread(_ImmediateThread):
        def start(self) -> None:
            if self.target.__name__ in ("_drain", "_download_heartbeat"):
                self.target(*self.args)

    monkeypatch.setattr("winpodx.cli.setup_cmd._container_exists_on_backend", lambda config: True)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(PodState.RUNNING))
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive",
        lambda config, timeout: responsive_timeouts.append(timeout) or True,
    )
    monkeypatch.setattr(pod, "_wait_for_oem_reboot", lambda config, timeout: True)
    monkeypatch.setattr("winpodx.core.dockur_progress.DockurProgressReader", _Reader)
    monkeypatch.setattr(subprocess, "Popen", _DownloadProcess)
    monkeypatch.setattr(pod.threading, "Event", _OnePassEvent)
    monkeypatch.setattr(pod.threading, "Thread", _SelectedImmediateThread)

    pod._wait_ready(60, show_logs=True)

    assert len(responsive_timeouts) == 1
    assert responsive_timeouts[0] > 60


def test_wait_ready_continues_polling_after_non_loading_http_progress(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    recovered = threading.Event()
    poll_count = 0

    class _Reader:
        def __init__(self, vnc_port: int) -> None:
            return None

        def poll(self) -> DockurProgress | None:
            nonlocal poll_count
            poll_count += 1
            if poll_count == 2:
                return None
            if poll_count >= 3:
                recovered.set()
            return DockurProgress(text="HTTP progress primary", is_loading=False)

    monkeypatch.setattr("winpodx.cli.setup_cmd._container_exists_on_backend", lambda config: True)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(PodState.RUNNING))
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive",
        lambda config, timeout: recovered.wait(4.0),
    )
    monkeypatch.setattr(pod, "_wait_for_oem_reboot", lambda config, timeout: True)
    monkeypatch.setattr("winpodx.core.dockur_progress.DockurProgressReader", _Reader)
    monkeypatch.setattr(subprocess, "Popen", _LogProcess)

    # When
    pod._wait_ready(60, show_logs=True)

    # Then
    output = capsys.readouterr().out
    assert poll_count >= 3
    assert output.count("HTTP progress primary") >= 2
    assert "OK Windows ready" in output


def test_wait_ready_rejects_manual_backend_and_missing_container(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.pod.backend = "manual"
    assert _exit_code(lambda: pod._wait_ready(60, False)) == 2
    assert "not supported" in capsys.readouterr().out

    cfg.pod.backend = "podman"
    monkeypatch.setattr("winpodx.cli.setup_cmd._container_exists_on_backend", lambda config: False)
    assert _exit_code(lambda: pod._wait_ready(60, False)) == 3
    assert "does not exist" in capsys.readouterr().out


@pytest.mark.parametrize(
    "line,expected",
    [
        ("42% 1.5M 1h2m3s", 3723),
        ("42% 1.5M=2m3s", None),
        ("42% 1.5M 0s", None),
        ("not progress", None),
    ],
)
def test_parse_wget_eta(line: str, expected: int | None) -> None:
    assert pod._parse_wget_eta_secs(line) == expected


def test_progress_helpers_and_line_splitter() -> None:
    pct, text = pod._format_wget_progress("100% 34.0M=4m27s") or (-1, "")
    assert pct == 100
    assert "34.0 MB/s" in text and "done in 4m27s" in text
    assert pod._format_wget_progress("ordinary log line") is None

    splitter = pod._LineSplitter()
    assert splitter.feed(b"") == []
    assert splitter.feed("alpha\nbe".encode()) == ["alpha"]
    assert splitter.partial == "be"
    assert splitter.feed("ta\nlast".encode()) == ["beta"]
    assert splitter.flush() == "last"
    assert splitter.flush() is None

    state: dict[str, object] = {"start": 1}
    pod._scrape_download_progress("10% -> 120%", state)
    assert state["pct"] == 100
    pod._scrape_download_progress("downloaded 1.5 GiB", state)
    assert state["size"] == "1.5 GiB"


def test_live_line_writes_clears_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    tty = io.StringIO()
    monkeypatch.setattr("builtins.open", lambda *args, **kwargs: tty)
    live = pod._LiveLine(enabled=True)
    assert live.usable
    live.set("working")
    live.clear()
    written = tty.getvalue()
    live.close()
    assert "working" in written
    assert not live.usable


def test_sync_password_agent_success_and_auth_failure(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = MagicMock()
    transport.exec.return_value = ExecResult(0, "password reset", "")
    monkeypatch.setattr("winpodx.core.transport.dispatch", lambda config, prefer: transport)

    pod._sync_password(non_interactive=True)

    assert transport.exec.call_args.kwargs == {"description": "sync-password", "timeout": 90}
    assert "net user 'WPX-User'" in transport.exec.call_args.args[0]
    assert "now in sync" in capsys.readouterr().out

    from winpodx.core.transport.base import TransportAuthError

    transport.exec.side_effect = TransportAuthError("bad token")
    assert _exit_code(lambda: pod._sync_password(non_interactive=True)) == 3
    assert "agent rejected" in capsys.readouterr().out


def test_sync_password_freerdp_fallback_requires_environment(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from winpodx.core.transport.base import TransportUnavailable

    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        MagicMock(side_effect=TransportUnavailable("agent down")),
    )
    monkeypatch.delenv("WINPODX_RECOVERY_PASSWORD", raising=False)
    assert _exit_code(lambda: pod._sync_password(non_interactive=True)) == 2
    assert "WINPODX_RECOVERY_PASSWORD" in capsys.readouterr().out

    monkeypatch.setenv("WINPODX_RECOVERY_PASSWORD", "recovery-only")
    run = MagicMock(return_value=ExecResult(0, "reset", ""))
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", run)
    pod._sync_password(non_interactive=True)
    assert run.call_args.args[0].rdp.password == "recovery-only"
    assert run.call_args.kwargs == {"description": "sync-password", "timeout": 120}


def test_wait_for_oem_reboot_observes_marker_then_two_absences(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = MagicMock()
    transport.exec.side_effect = [
        ExecResult(1, "", ""),
        ExecResult(0, "", ""),
        ExecResult(0, "", ""),
    ]
    monkeypatch.setattr("winpodx.core.transport.agent.AgentTransport", lambda config: transport)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    assert pod._wait_for_oem_reboot(cfg, timeout=60)
    assert transport.exec.call_count == 3
    assert transport.exec.call_args.kwargs == {"timeout": 10}


def test_wait_for_oem_reboot_tolerates_transport_error_until_grace(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    from winpodx.core.transport.base import TransportError

    transport = MagicMock()
    transport.exec.side_effect = TransportError("rebooting")
    ticks = iter([0, 0, 0, 31])
    monkeypatch.setattr("winpodx.core.transport.agent.AgentTransport", lambda config: transport)
    monkeypatch.setattr("time.monotonic", lambda: next(ticks))
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    assert pod._wait_for_oem_reboot(cfg, timeout=60)
    assert transport.exec.call_count == 1


def test_wipe_bind_mount_preserves_iso_and_removes_other_content(
    cfg: Config, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    storage = tmp_path / "storage"
    storage.mkdir()
    iso = storage / "windows.iso"
    iso.write_bytes(b"iso")
    (storage / "data.img").write_bytes(b"disk")
    nested = storage / "nested"
    nested.mkdir()
    (nested / "file").write_text("x")
    cfg.pod.storage_path = str(storage)

    pod._wipe_pod_storage(cfg, keep_iso=True)

    assert iso.exists()
    assert sorted(path.name for path in storage.iterdir()) == ["windows.iso"]
    assert "keeping the cached ISO" in capsys.readouterr().out


@pytest.mark.parametrize(
    "backend,password,state,result,expected_code,expected",
    [
        ("manual", "pw", PodState.RUNNING, (True, "unused"), 2, "nothing to resync"),
        ("podman", "", PodState.RUNNING, (True, "unused"), 2, "No Windows password"),
        ("podman", "pw", PodState.STOPPED, (True, "unused"), 2, "Pod is not running"),
        ("podman", "pw", PodState.RUNNING, (True, "token restored"), None, "OK: token restored"),
        ("podman", "pw", PodState.RUNNING, (False, "rejected"), 3, "FAIL: rejected"),
    ],
)
def test_resync_token_outcomes(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backend: str,
    password: str,
    state: PodState,
    result: tuple[bool, str],
    expected_code: int | None,
    expected: str,
) -> None:
    cfg.pod.backend = backend
    cfg.rdp.password = password
    resync = MagicMock(return_value=result)
    monkeypatch.setattr("winpodx.core.pod.pod_status", lambda config: PodStatus(state))
    monkeypatch.setattr("winpodx.core.agent_resync.resync_token", resync)

    if expected_code is None:
        pod._resync_token()
    else:
        assert _exit_code(pod._resync_token) == expected_code

    assert expected in capsys.readouterr().out
    if backend == "podman" and password and state is PodState.RUNNING:
        resync.assert_called_once_with(cfg)


@pytest.mark.parametrize(
    "handler,description",
    [
        (pod._multi_session_enable, "multi-session-enable"),
        (pod._multi_session_disable, "multi-session-disable"),
        (pod._multi_session_status, "multi-session-status"),
    ],
)
def test_multi_session_channel_errors(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    handler,
    description: str,
) -> None:
    from winpodx.core.windows_exec import WindowsExecError

    run = MagicMock(side_effect=WindowsExecError("channel down"))
    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", run)

    assert _exit_code(lambda: handler(cfg)) == 3
    assert "channel down" in capsys.readouterr().out
    assert run.call_args.kwargs["description"] == description


def test_sync_password_rejects_unsupported_backend_and_missing_target(
    cfg: Config, capsys: pytest.CaptureFixture[str]
) -> None:
    cfg.pod.backend = "manual"
    assert _exit_code(lambda: pod._sync_password(False)) == 2
    assert "not supported" in capsys.readouterr().out

    cfg.pod.backend = "podman"
    cfg.rdp.password = ""
    assert _exit_code(lambda: pod._sync_password(False)) == 2
    assert "nothing to sync" in capsys.readouterr().out


def test_grow_disk_reports_grow_error_and_partition_extension(
    cfg: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from winpodx.core.disk import DiskError

    monkeypatch.setattr("winpodx.core.disk.compute_grow_target", lambda *args, **kwargs: "96G")
    monkeypatch.setattr("winpodx.core.disk.grow_disk", MagicMock(side_effect=DiskError("resize")))
    assert (
        _exit_code(
            lambda: pod._grow_disk(
                target_size="96G", increment=None, extend_only=False, assume_yes=True
            )
        )
        == 1
    )
    assert "Grow failed: resize" in capsys.readouterr().err

    monkeypatch.setattr(
        "winpodx.core.disk.grow_disk",
        lambda *args, **kwargs: SimpleNamespace(
            old_size="64G", new_size="96G", partition_extended=True, note=""
        ),
    )
    pod._grow_disk(target_size="96G", increment=None, extend_only=False, assume_yes=True)
    assert "C: extended to fill" in capsys.readouterr().out


class TestPodReset:
    """`pod reset`: one command for "give me a clean guest" (#866 follow-up).

    `recreate --wipe-storage` already destroyed the disk, but it stopped once
    the container was up -- the guest still needed apply-fixes, discovery and
    reverse-open, and finding that out was left to the user.
    """

    def _args(self, **kw):
        import argparse as _a

        return _a.Namespace(**{"redownload_iso": False, "yes": True, **kw})

    def test_reset_wipes_keeps_the_iso_and_then_provisions(self, monkeypatch, capsys):
        from winpodx.cli import pod as pod_cli

        calls = {}
        monkeypatch.setattr(
            pod_cli,
            "_recreate",
            lambda **kw: calls.update(recreate=kw),
        )

        def _provision(_a, *, on_progress=None):
            calls["provisioned"] = True
            return 0

        monkeypatch.setattr("winpodx.cli.main._cmd_provision", _provision)

        pod_cli._reset(redownload_iso=False, assume_yes=True)

        assert calls["recreate"] == {
            "wipe_storage": True,
            "keep_iso": True,
            "assume_yes": True,
            "on_progress": None,
        }
        assert calls["provisioned"] is True
        assert "Reset complete" in capsys.readouterr().out

    def test_the_subcommand_is_actually_wired_to_reset(self, monkeypatch):
        """Guard the dispatch itself, not just the helper.

        The other tests call ``_reset`` directly, so a missing or misspelled
        ``pod_command`` branch would leave `winpodx pod reset` doing nothing
        while the suite stayed green.
        """
        import argparse as _a

        from winpodx.cli import pod as pod_cli

        calls = {}
        monkeypatch.setattr(pod_cli, "_recreate", lambda **kw: calls.update(recreate=kw))
        monkeypatch.setattr("winpodx.cli.main._cmd_provision", lambda _a, **kw: 0)

        pod_cli.handle_pod(_a.Namespace(pod_command="reset", redownload_iso=False, yes=True))

        assert calls["recreate"]["wipe_storage"] is True

    def test_redownload_iso_drops_the_cached_image(self, monkeypatch):
        from winpodx.cli import pod as pod_cli

        calls = {}
        monkeypatch.setattr(pod_cli, "_recreate", lambda **kw: calls.update(recreate=kw))
        monkeypatch.setattr("winpodx.cli.main._cmd_provision", lambda _a, **kw: 0)

        pod_cli._reset(redownload_iso=True, assume_yes=True)

        assert calls["recreate"]["keep_iso"] is False

    def test_without_confirmation_nothing_is_destroyed(self, monkeypatch, capsys):
        from winpodx.cli import pod as pod_cli

        calls = {}
        monkeypatch.setattr(pod_cli, "_recreate", lambda **kw: calls.update(recreate=kw))
        monkeypatch.setattr("winpodx.cli.main._cmd_provision", lambda _a, **kw: 0)
        monkeypatch.setattr("builtins.input", lambda: "yes")

        pod_cli._reset(redownload_iso=False, assume_yes=False)

        assert "recreate" not in calls
        assert "Aborted" in capsys.readouterr().out

    def test_failed_provisioning_is_reported_not_swallowed(self, monkeypatch, capsys):
        from winpodx.cli import pod as pod_cli

        monkeypatch.setattr(pod_cli, "_recreate", lambda **kw: None)
        monkeypatch.setattr("winpodx.cli.main._cmd_provision", lambda _a, **kw: 1)

        pod_cli._reset(redownload_iso=False, assume_yes=True)

        assert "provisioning did not finish" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["reset", "recreate"])
def test_handle_pod_forwards_progress_callback(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    # Given: a routed handler isolated from container operations.
    handler = MagicMock()
    callback = MagicMock()
    monkeypatch.setattr(pod, f"_{command}", handler)
    args = argparse.Namespace(pod_command=command, yes=True, redownload_iso=True, keep_iso=True)

    # When: a caller supplies structured progress.
    pod.handle_pod(args, on_progress=callback)

    # Then: the same callback reaches the selected handler with its existing flags.
    expected = (
        {"redownload_iso": True, "assume_yes": True}
        if command == "reset"
        else {"wipe_storage": True, "keep_iso": True}
    )
    handler.assert_called_once_with(**expected, on_progress=callback)


def test_reset_forwards_progress_to_recreate_and_provision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: reset's downstream operations are isolated.
    recreate = MagicMock()
    provision = MagicMock(return_value=0)
    callback = MagicMock()
    monkeypatch.setattr(pod, "_recreate", recreate)
    monkeypatch.setattr("winpodx.cli.main._cmd_provision", provision)

    # When: reset runs with progress enabled.
    pod._reset(assume_yes=True, on_progress=callback)

    # Then: both phases receive the same observer without changing reset options.
    recreate.assert_called_once_with(
        wipe_storage=True, keep_iso=True, assume_yes=True, on_progress=callback
    )
    assert provision.call_args.kwargs == {"on_progress": callback}
    assert vars(provision.call_args.args[0]) == {
        "retries": 5,
        "timeout": 3600,
        "logs": False,
        "verbose": False,
    }


@pytest.mark.parametrize("rc", [0, 4, 5])
@pytest.mark.parametrize("raises", [False, True])
def test_reset_progress_preserves_output_and_failure_semantics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rc: int,
    raises: bool,
) -> None:
    # Given: baseline reset output for a successful, timed-out or deferred provision.
    monkeypatch.setattr(pod, "_recreate", MagicMock())
    monkeypatch.setattr("winpodx.cli.main._cmd_provision", MagicMock(return_value=rc))
    pod._reset(assume_yes=True)
    baseline = capsys.readouterr()
    events: list[tuple[str, str]] = []

    def observe(stage: str, detail: str) -> None:
        events.append((stage, detail))
        if raises:
            raise RuntimeError("observer unavailable")

    # When: even a broken observer is supplied.
    result = pod._reset(assume_yes=True, on_progress=observe)

    # Then: reset still returns normally, and mirrors each print without duplicating output.
    assert result is None
    assert capsys.readouterr() == baseline
    assert events == [
        ("reset", "\nRe-running provisioning on the fresh guest..."),
        ("reset", (baseline.out.splitlines()[-1] if rc == 0 else baseline.err.rstrip("\n"))),
    ]


@pytest.mark.usefixtures("cfg")
@pytest.mark.parametrize("outcome", ["ready", "compose_error", "start_error", "aborted"])
@pytest.mark.parametrize("raises", [False, True])
def test_recreate_progress_mirrors_prints_without_changing_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    outcome: str,
    raises: bool,
) -> None:
    # Given: mocked destructive boundaries and the current CLI output for this outcome.
    monkeypatch.setattr("winpodx.core.pod.stop_pod", MagicMock())
    monkeypatch.setattr("winpodx.core.pod.disguise.validate_disguise_image", MagicMock())
    monkeypatch.setattr(pod, "_wipe_pod_storage", MagicMock())
    monkeypatch.setattr("builtins.input", lambda: "no")
    monkeypatch.setattr(
        "winpodx.core.compose.generate_compose",
        MagicMock(side_effect=OSError("readonly") if outcome == "compose_error" else None),
    )
    monkeypatch.setattr(
        "winpodx.core.pod.start_pod",
        MagicMock(
            return_value=PodStatus(
                PodState.ERROR if outcome == "start_error" else PodState.STARTING,
                error="boot failed",
            )
        ),
    )
    expected_exit = 2 if outcome == "aborted" else 1

    def run(callback: Callable[[str, str], None] | None = None) -> None:
        if outcome == "ready":
            pod._recreate(wipe_storage=True, keep_iso=True, assume_yes=True, on_progress=callback)
        else:
            with pytest.raises(SystemExit) as exc:
                pod._recreate(
                    wipe_storage=True,
                    keep_iso=True,
                    assume_yes=outcome != "aborted",
                    on_progress=callback,
                )
            assert exc.value.code == expected_exit

    run()
    baseline = capsys.readouterr()
    events: list[tuple[str, str]] = []

    def observe(stage: str, detail: str) -> None:
        events.append((stage, detail))
        if raises:
            raise RuntimeError("observer unavailable")

    # When: recreate reports to an optional observer.
    run(observe)

    # Then: all printed messages are mirrored, including failures and prompts; exits are unchanged.
    assert capsys.readouterr() == baseline
    assert events
    assert all(stage == "recreate" for stage, _ in events)
    rendered = "".join(
        detail + ("" if detail.endswith("Type 'WIPE' to confirm: ") else "\n")
        for _, detail in events
    )
    assert rendered == baseline.out + baseline.err


@pytest.mark.usefixtures("cfg")
@pytest.mark.parametrize(
    "regime",
    ["named_volume_removed", "named_volume_keep_iso", "bind_absent"],
)
@pytest.mark.parametrize("raises", [False, True])
def test_wipe_pod_storage_mirrors_outcomes_to_recreate_observer(
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    regime: str,
    raises: bool,
) -> None:
    # Given: a wipe regime with its host boundary mocked, plus the current
    # stdout the CLI already produces for it (the baseline the observer must
    # not alter).
    def _configure() -> None:
        if regime == "bind_absent":
            cfg.pod.storage_path = str(tmp_path / "absent")
            return
        cfg.pod.storage_path = ""
        run = MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        monkeypatch.setattr("subprocess.run", run)

    _configure()
    keep_iso = regime == "named_volume_keep_iso"
    pod._wipe_pod_storage(cfg, keep_iso=keep_iso)
    baseline = capsys.readouterr()
    assert baseline.out  # the wipe always narrates its outcome

    events: list[tuple[str, str]] = []

    def observe(stage: str, detail: str) -> None:
        events.append((stage, detail))
        if raises:
            raise RuntimeError("observer unavailable")

    # When: the same wipe runs with a (possibly broken) progress observer.
    _configure()
    result = pod._wipe_pod_storage(cfg, keep_iso=keep_iso, on_progress=observe)

    # Then: it still returns normally, stdout is byte-for-byte the baseline,
    # and every outcome line was mirrored to on_progress under the 'recreate'
    # stage (so the wizard log shows the wipe, not a silent gap).
    assert result is None
    assert capsys.readouterr() == baseline
    assert events, "no wipe outcome reached on_progress"
    assert all(stage == "recreate" for stage, _ in events)
    rendered = "".join(detail + "\n" for _, detail in events)
    assert rendered == baseline.out


@pytest.fixture()
def reset_case(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from copy import deepcopy

    baseline = Config()
    baseline.pod.backend = "podman"
    baseline.pod.container_name = "original-container"
    baseline.pod.storage_path = ""
    candidate = deepcopy(baseline)
    candidate.pod.win_version = "11"
    candidate.pod.container_name = "new-container"
    current = {"config": deepcopy(baseline)}
    controls = {"save_failure": "", "stop": PodState.STOPPED, "failure": "", "provision": 0}
    events: list[str] = []

    def save(config: Config) -> None:
        role = "candidate" if config is candidate else "original"
        events.append(f"save:{role}")
        if role == "candidate" and controls["save_failure"] == "before":
            raise OSError("save failed before replacement")
        current["config"] = deepcopy(config)
        if role == "candidate" and controls["save_failure"] == "after":
            raise OSError("save failed after replacement")

    def compose(config: Config) -> None:
        assert config.pod.container_name == "new-container"
        events.append("compose")
        if controls["failure"] == "compose":
            raise OSError("compose failed")

    def stop(config: Config) -> PodStatus:
        assert config.pod.container_name == "original-container"
        events.append("stop")
        return PodStatus(controls["stop"], error="stop failed")

    def wipe(config: Config, **kwargs) -> None:
        assert config.pod.container_name == "original-container"
        events.append("wipe")

    def start(config: Config) -> PodStatus:
        assert config.pod.container_name == "new-container"
        events.append("start")
        return PodStatus(
            PodState.ERROR if controls["failure"] == "start" else PodState.STARTING,
            error="boot failed",
        )

    def preflight(config: Config) -> None:
        assert config is candidate
        events.append("preflight")

    def disguise(config: Config) -> None:
        assert config is candidate
        events.append("disguise")

    def provision(args: argparse.Namespace, *, on_progress=None) -> int:
        events.append("provision")
        return controls["provision"]

    monkeypatch.setattr(Config, "load", classmethod(lambda cls: deepcopy(current["config"])))
    monkeypatch.setattr(Config, "save", save)
    monkeypatch.setattr("winpodx.setup_wizard.host_state.require_preflight", preflight)
    monkeypatch.setattr("winpodx.core.pod.disguise.validate_disguise_image", disguise)
    monkeypatch.setattr("winpodx.core.pod.stop_pod", stop)
    monkeypatch.setattr(pod, "_wipe_pod_storage", wipe)
    monkeypatch.setattr("winpodx.core.compose.generate_compose", compose)
    monkeypatch.setattr("winpodx.core.pod.start_pod", start)
    monkeypatch.setattr("winpodx.cli.main._cmd_provision", provision)
    return SimpleNamespace(
        baseline=baseline, candidate=candidate, current=current, controls=controls, events=events
    )


def test_handle_pod_forwards_candidate_only_for_reset(
    monkeypatch: pytest.MonkeyPatch, reset_case: SimpleNamespace
) -> None:
    # Given: a caller provides a candidate and baseline to the routed command.
    reset = MagicMock()
    recreate = MagicMock()
    monkeypatch.setattr(pod, "_reset", reset)
    monkeypatch.setattr(pod, "_recreate", recreate)
    args = argparse.Namespace(pod_command="reset", yes=True, redownload_iso=False)

    # When: reset and recreate are dispatched with the same optional context.
    pod.handle_pod(args, reset_config=reset_case.candidate, expected_config=reset_case.baseline)
    args.pod_command = "recreate"
    pod.handle_pod(args, reset_config=reset_case.candidate, expected_config=reset_case.baseline)

    # Then: only reset receives the candidate and baseline.
    reset.assert_called_once_with(
        redownload_iso=False,
        assume_yes=True,
        on_progress=None,
        reset_config=reset_case.candidate,
        expected_config=reset_case.baseline,
    )
    recreate.assert_called_once_with(wipe_storage=False, keep_iso=False, on_progress=None)


@pytest.mark.parametrize("field", ["backend", "storage_path", "container_name"])
def test_reset_baseline_mismatch_aborts_before_mutation(
    reset_case: SimpleNamespace, field: str
) -> None:
    # Given: persisted pod identity changed since the caller captured its baseline.
    setattr(reset_case.current["config"].pod, field, "changed")

    # When: reset checks the freshly loaded config against that baseline.
    assert (
        _exit_code(
            lambda: pod._reset(
                assume_yes=True,
                reset_config=reset_case.candidate,
                expected_config=reset_case.baseline,
            )
        )
        == 1
    )

    # Then: no pod was stopped and neither config nor storage changed.
    assert reset_case.events == []
    assert getattr(reset_case.current["config"].pod, field) == "changed"


def test_reset_accepts_equivalent_baseline_not_just_same_object(
    reset_case: SimpleNamespace,
) -> None:
    # Given: the baseline is an independent Config with the same stable identity.
    from copy import deepcopy

    expected = deepcopy(reset_case.baseline)
    expected.rdp.password = "old-password-from-earlier-snapshot"

    # When: the reset runs against a freshly loaded, separate instance.
    pod._reset(assume_yes=True, reset_config=reset_case.candidate, expected_config=expected)

    # Then: stopping precedes the candidate save and original-disk wipe.
    assert reset_case.events == [
        "disguise",
        "preflight",
        "stop",
        "save:candidate",
        "wipe",
        "compose",
        "start",
        "provision",
    ]
    assert reset_case.current["config"].pod.container_name == "new-container"


@pytest.mark.parametrize("failure", ["preflight", "disguise"])
def test_reset_validation_failure_keeps_pod_config_and_storage(
    reset_case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    # Given: a candidate fails one of the existing pre-destructive gates.
    if failure == "preflight":
        monkeypatch.setattr(
            "winpodx.setup_wizard.host_state.require_preflight",
            MagicMock(side_effect=RuntimeError("host unready")),
        )
    else:
        from winpodx.core.pod.disguise import DisguiseImageError

        monkeypatch.setattr(
            "winpodx.core.pod.disguise.validate_disguise_image",
            MagicMock(side_effect=DisguiseImageError("image stale")),
        )

    # When: reset attempts validation.
    assert _exit_code(lambda: pod._reset(assume_yes=True, reset_config=reset_case.candidate)) == 1

    # Then: neither stop, save nor wipe was reached.
    assert "stop" not in reset_case.events
    assert not any(event.startswith("save:") or event == "wipe" for event in reset_case.events)
    assert reset_case.current["config"] == reset_case.baseline


def test_reset_stop_error_aborts_without_save_or_wipe(reset_case: SimpleNamespace) -> None:
    # Given: stop_pod reports ERROR rather than STOPPED.
    reset_case.controls["stop"] = PodState.ERROR

    # When: reset tries to stop the old container.
    assert _exit_code(lambda: pod._reset(assume_yes=True, reset_config=reset_case.candidate)) == 1

    # Then: old config and disk remain untouched.
    assert reset_case.events == ["disguise", "preflight", "stop"]
    assert reset_case.current["config"] == reset_case.baseline


@pytest.mark.parametrize("failure", ["before", "after"])
def test_reset_save_failure_never_wipes_and_restores_only_after_replacement(
    reset_case: SimpleNamespace, failure: str
) -> None:
    # Given: candidate.save fails before or just after an atomic replacement.
    reset_case.controls["save_failure"] = failure

    # When: reset attempts the candidate save after stopping.
    assert _exit_code(lambda: pod._reset(assume_yes=True, reset_config=reset_case.candidate)) == 1

    # Then: a replaced file is rolled back; an unchanged file is not rewritten.
    expected = ["disguise", "preflight", "stop", "save:candidate"]
    if failure == "after":
        expected.append("save:original")
    assert reset_case.events == expected
    assert reset_case.current["config"] == reset_case.baseline


@pytest.mark.parametrize("failure", ["compose", "start", "provision"])
def test_reset_failure_after_wipe_retains_candidate(
    reset_case: SimpleNamespace, failure: str
) -> None:
    # Given: recreate or provision fails after the original disk has been wiped.
    if failure == "provision":
        reset_case.controls["provision"] = 1
    else:
        reset_case.controls["failure"] = failure

    # When: the reinstall reaches that failure.
    if failure == "provision":
        pod._reset(assume_yes=True, reset_config=reset_case.candidate)
    else:
        assert (
            _exit_code(lambda: pod._reset(assume_yes=True, reset_config=reset_case.candidate)) == 1
        )

    # Then: candidate remains persisted; rollback is forbidden after wipe begins.
    assert reset_case.events[:5] == ["disguise", "preflight", "stop", "save:candidate", "wipe"]
    assert "save:original" not in reset_case.events
    assert reset_case.current["config"].pod.container_name == "new-container"
