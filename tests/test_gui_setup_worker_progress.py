# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

_FINISH_PROVISIONING_STAGES: tuple[tuple[str, str], ...] = (
    ("wait_ready", "Waiting for the pod to become responsive..."),
    ("agent_settle", "Guest agent settling..."),
    ("apply_fixes", "Applying Windows-side runtime fixes..."),
    ("discovery", "Discovering and registering apps..."),
    ("reverse_open", "Starting reverse-open listener..."),
)


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_setup_worker_exposes_progress_signal() -> None:
    _ensure_qapp()
    from winpodx.gui._setup_wizard_worker import SetupWorker

    assert hasattr(SetupWorker, "progress")


def test_setup_worker_forwards_handle_setup_stage_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()

    def _fake_handle_setup(args, *, on_progress=None) -> None:
        assert on_progress is not None
        for stage, detail in _FINISH_PROVISIONING_STAGES:
            on_progress(stage, detail)

    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", _fake_handle_setup)
    from winpodx.gui._setup_wizard_worker import SetupWorker

    worker = SetupWorker(argparse.Namespace(), reinstall=False)
    stages: list[tuple[str, str]] = []
    worker.progress.connect(lambda stage, detail: stages.append((stage, detail)))
    done: list[tuple[bool, str]] = []
    worker.finished.connect(lambda ok, err: done.append((ok, err)))

    worker.run()

    assert stages == list(_FINISH_PROVISIONING_STAGES)
    assert done == [(True, "")]


@pytest.mark.parametrize("exit_code", [0, 3])
def test_reinstall_worker_emits_reset_progress_before_finished(
    monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    # Given: a real worker with preset persistence and reset isolated from the host.
    _ensure_qapp()
    from winpodx.core.config import Config
    from winpodx.gui._setup_wizard_worker import SetupWorker

    cfg = Config()
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))
    save = Mock()
    presets = Mock()
    monkeypatch.setattr(Config, "save", save)
    monkeypatch.setattr("winpodx.cli.setup_cmd.apply_setup_presets", presets)
    worker = SetupWorker(argparse.Namespace(), reinstall=True)
    events: list[tuple[str, str]] = []
    done: list[tuple[bool, str]] = []
    worker.progress.connect(lambda stage, detail: events.append((stage, detail)))
    worker.finished.connect(lambda ok, error: done.append((ok, error)))

    def reset(
        args: argparse.Namespace,
        *,
        on_progress: Callable[[str, str], None],
        reset_config: object,
        expected_config: object,
    ) -> None:
        assert vars(args) == {"pod_command": "reset", "yes": True, "redownload_iso": False}
        assert expected_config is cfg
        assert reset_config is not cfg
        assert save.call_count == 0
        for stage, detail in (("recreate", "Stopping pod..."), *_FINISH_PROVISIONING_STAGES):
            on_progress(stage, detail)
            assert events[-1] == (stage, detail)
            assert done == []
        raise SystemExit(exit_code)

    monkeypatch.setattr("winpodx.cli.pod.handle_pod", reset)

    # When: reinstall runs through its existing reset entry point.
    worker.run()

    # Then: live signals arrive before the existing success/failure completion signal.
    assert events == [("recreate", "Stopping pod..."), *_FINISH_PROVISIONING_STAGES]
    assert done == ([(True, "")] if exit_code == 0 else [(False, "3")])
    presets.assert_called_once()
    assert presets.call_args.args[1] is worker._args
    save.assert_not_called()


@pytest.mark.parametrize("ready", [True, False])
def test_reinstall_progress_flows_through_real_reset_and_provision(
    monkeypatch: pytest.MonkeyPatch, ready: bool
) -> None:
    # Given: real orchestration, with every destructive or guest-facing operation stubbed.
    _ensure_qapp()
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, PodStatus
    from winpodx.gui._setup_wizard_worker import SetupWorker

    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.reverse_open.enabled = False
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(Config, "save", Mock())
    monkeypatch.setattr("winpodx.cli.setup_cmd.apply_setup_presets", Mock())
    monkeypatch.setattr("winpodx.core.pod.disguise.validate_disguise_image", Mock())
    monkeypatch.setattr("winpodx.setup_wizard.host_state.require_preflight", Mock())
    monkeypatch.setattr("winpodx.core.pod.stop_pod", Mock(return_value=PodStatus(PodState.STOPPED)))
    monkeypatch.setattr("winpodx.cli.pod._wipe_pod_storage", Mock())
    monkeypatch.setattr("winpodx.core.compose.generate_compose", Mock())
    monkeypatch.setattr(
        "winpodx.core.pod.start_pod", Mock(return_value=PodStatus(PodState.STARTING))
    )
    monkeypatch.setattr("winpodx.core.transport.agent.AgentTransport", Mock())
    monkeypatch.setattr(
        "winpodx.core.provisioner.apply_windows_runtime_fixes", Mock(return_value={})
    )
    monkeypatch.setattr("winpodx.core.provisioner._run_discovery_with_retry", Mock(return_value=2))
    worker = SetupWorker(argparse.Namespace(), reinstall=True)
    events: list[tuple[str, str]] = []
    done: list[tuple[bool, str]] = []
    worker.progress.connect(lambda stage, detail: events.append((stage, detail)))
    worker.finished.connect(lambda ok, error: done.append((ok, error)))

    def wait_ready(
        timeout: int, *, on_log: Callable[[str], None] | None = None, **flags: bool
    ) -> None:
        assert timeout == 3600
        assert flags == {"show_logs": True, "verbose": False}
        if on_log is not None:
            on_log("live install milestone")
            assert events[-1] == ("wait_ready", "live install milestone")
            assert done == []
        if not ready:
            raise SystemExit(3)

    monkeypatch.setattr("winpodx.cli.pod._wait_ready", wait_ready)

    # When: reinstall traverses handle_pod, reset, recreate, provision and finish_provisioning.
    worker.run()

    # Then: logs arrive live; reset's existing non-fatal provision failure remains unchanged.
    assert ("wait_ready", "live install milestone") in events
    assert events[0] == ("recreate", "Wiping the Windows disk (confirmed).")
    assert events[-1][0] == "reset"
    if ready:
        assert ("discovery", "2 apps") in events
    else:
        assert all(stage != "discovery" for stage, _ in events)
    assert done == [(True, "")]
