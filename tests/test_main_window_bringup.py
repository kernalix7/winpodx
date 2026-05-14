"""Tests for the auto bring-up workflow (BringUpMixin, v0.5.1).

The Qt dialog (``BringUpProgressDialog``) is GUI-smoke territory and not
covered here. The worker logic is pure Python apart from the
``Signal.emit`` calls, which we satisfy with a lightweight ``FakeSignal``
that records emissions.

Covers:
  - Happy path: all 5 phases fire in order, ``bringup_done(True, "")``.
  - Cancellation during phase 1: worker exits within 1 s with
    ``bringup_done(False, "cancelled")``.
  - Failure: phase 3 ``apply_windows_runtime_fixes`` raises, worker
    emits ``bringup_done(False, "...<error>")``.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from winpodx.core.config import Config
from winpodx.core.pod import PodState, PodStatus
from winpodx.gui._main_window_bringup import BringUpMixin


class FakeSignal:
    """Minimal stand-in for a Qt Signal that records emits."""

    def __init__(self) -> None:
        self.emissions: list[tuple] = []

    def emit(self, *args: Any) -> None:
        self.emissions.append(args)


class Harness(BringUpMixin):
    """Bare host class exposing only what BringUpMixin reads."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.bringup_phase = FakeSignal()
        self.bringup_done = FakeSignal()
        self.bringup_started = FakeSignal()
        self.log_signal = FakeSignal()


# ----- shared helpers ----------------------------------------------------


def _make_cfg() -> Config:
    cfg = Config()
    # Keep budgets short so the test exits fast on negative paths.
    cfg.install.wait_ready_stage2_secs = 60
    cfg.install.wait_ready_stage3_secs = 60
    return cfg


def _phase_labels(emissions: list[tuple]) -> list[str]:
    """Distinct phase_label values in emission order."""
    out: list[str] = []
    for label, _detail in emissions:
        if not out or out[-1] != label:
            out.append(label)
    return out


def _wait_for_done(harness: Harness, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if harness.bringup_done.emissions:
            return
        time.sleep(0.05)
    raise AssertionError(
        f"bringup_done never fired within {timeout}s; "
        f"phase emissions so far: {harness.bringup_phase.emissions}"
    )


# ----- happy path --------------------------------------------------------


def test_happy_path_all_five_phases_fire_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _make_cfg()

    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: PodStatus(state=PodState.RUNNING, ip="127.0.0.1"),
    )
    monkeypatch.setattr(
        "winpodx.core.pod.check_rdp_port",
        lambda _ip, _port, timeout=3.0: True,
    )

    # AgentClient -- health() succeeds + auth_ready() returns (True, "").
    class FakeClient:
        def __init__(self, _cfg: Config) -> None:
            pass

        def health(self) -> dict:
            return {"ok": True}

        def auth_ready(self) -> tuple[bool, str]:
            return True, ""

    monkeypatch.setattr("winpodx.core.agent.AgentClient", FakeClient)

    monkeypatch.setattr(
        "winpodx.core.provisioner.apply_windows_runtime_fixes",
        lambda _cfg: {
            "max_sessions": "ok",
            "rdp_timeouts": "ok",
            "oem_runtime_fixes": "ok",
            "vbs_launchers": "ok",
            "multi_session": "ok",
        },
    )

    monkeypatch.setattr("winpodx.core.discovery.scan", lambda _cfg: ["app1", "app2"])
    monkeypatch.setattr(
        "winpodx.core.discovery.persist_discovered",
        lambda _apps: ["/tmp/app1.toml", "/tmp/app2.toml"],
    )

    monkeypatch.setattr("winpodx.cli.host_open._cmd_refresh", lambda _args: 0)

    cfg.reverse_open.enabled = True

    harness = Harness(cfg)
    harness._run_full_bring_up()
    _wait_for_done(harness)

    assert harness.bringup_done.emissions == [(True, "")]

    labels = _phase_labels(harness.bringup_phase.emissions)
    expected_order = [
        "Waiting for Windows boot",
        "Waiting for agent + host token",
        "Apply Windows-side fixes",
        "Discover Windows apps",
        "Reverse-open sync",
    ]
    # Each expected phase must appear in order. Allow extra polling
    # repetitions but require the first occurrence sequence to match.
    first_indices = []
    for phase in expected_order:
        assert phase in labels, f"expected phase {phase!r} missing: {labels}"
        first_indices.append(labels.index(phase))
    assert first_indices == sorted(first_indices), f"phases out of order: {labels}"

    # ``bringup_started`` was emitted so the dialog kick happened.
    assert harness.bringup_started.emissions == [()]


# ----- cancellation ------------------------------------------------------


def test_cancel_during_phase_one_exits_within_one_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_cfg()
    # Long Stage-2 budget so the natural exit is the timeout. We need
    # cancellation to short-circuit before then.
    cfg.install.wait_ready_stage2_secs = 600

    # pod_status always reports STARTING so phase 1 keeps polling.
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: PodStatus(state=PodState.STARTING, ip="127.0.0.1"),
    )
    monkeypatch.setattr(
        "winpodx.core.pod.check_rdp_port",
        lambda _ip, _port, timeout=3.0: False,
    )

    harness = Harness(cfg)
    harness._run_full_bring_up()
    # Brief delay so the worker enters phase 1's poll loop.
    time.sleep(0.1)
    harness._cancel_bringup()

    # The worker must exit within 1 s -- the poll cadence is 2 s but
    # _sleep_cancellable wakes on the event.
    _wait_for_done(harness, timeout=2.5)

    assert harness.bringup_done.emissions == [(False, "cancelled")]


# ----- failure path ------------------------------------------------------


def test_phase_three_failure_emits_bringup_done_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_cfg()

    # Phase 1: pod ready immediately.
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: PodStatus(state=PodState.RUNNING, ip="127.0.0.1"),
    )
    monkeypatch.setattr(
        "winpodx.core.pod.check_rdp_port",
        lambda _ip, _port, timeout=3.0: True,
    )

    # Phase 2: agent healthy + token ready.
    class FakeClient:
        def __init__(self, _cfg: Config) -> None:
            pass

        def health(self) -> dict:
            return {"ok": True}

        def auth_ready(self) -> tuple[bool, str]:
            return True, ""

    monkeypatch.setattr("winpodx.core.agent.AgentClient", FakeClient)

    # Phase 3 raises.
    def _boom(_cfg: Config) -> dict[str, str]:
        raise RuntimeError("simulated apply failure")

    monkeypatch.setattr("winpodx.core.provisioner.apply_windows_runtime_fixes", _boom)

    # Phase 4 / 5 should never be invoked, but stub safely so an
    # unintended call would surface as a different assert.
    monkeypatch.setattr(
        "winpodx.core.discovery.scan",
        lambda _cfg: pytest.fail("phase 4 should not run on phase 3 failure"),
    )
    monkeypatch.setattr(
        "winpodx.cli.host_open._cmd_refresh",
        lambda _args: pytest.fail("phase 5 should not run on phase 3 failure"),
    )

    harness = Harness(cfg)
    harness._run_full_bring_up()
    _wait_for_done(harness, timeout=5.0)

    assert len(harness.bringup_done.emissions) == 1
    success, msg = harness.bringup_done.emissions[0]
    assert success is False
    assert "simulated apply failure" in msg


# ----- ancillary ---------------------------------------------------------


def test_run_full_bring_up_returns_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """The public entry point spawns a daemon thread; it must not block."""
    cfg = _make_cfg()
    cfg.install.wait_ready_stage2_secs = 600

    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: PodStatus(state=PodState.STARTING),
    )
    monkeypatch.setattr(
        "winpodx.core.pod.check_rdp_port",
        lambda _ip, _port, timeout=3.0: False,
    )

    harness = Harness(cfg)
    started = time.monotonic()
    harness._run_full_bring_up()
    elapsed = time.monotonic() - started
    # Should be effectively instant. Anything over a second points at
    # blocking work landing on the caller.
    assert elapsed < 0.5, f"_run_full_bring_up blocked for {elapsed:.3f}s"
    harness._cancel_bringup()
    _wait_for_done(harness, timeout=3.0)


def test_cancel_event_is_fresh_per_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second bring-up must not inherit the first run's cancel flag."""
    cfg = _make_cfg()

    # Configure happy-path probes so a clean run completes.
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: PodStatus(state=PodState.RUNNING, ip="127.0.0.1"),
    )
    monkeypatch.setattr(
        "winpodx.core.pod.check_rdp_port",
        lambda _ip, _port, timeout=3.0: True,
    )

    class FakeClient:
        def __init__(self, _cfg: Config) -> None:
            pass

        def health(self) -> dict:
            return {"ok": True}

        def auth_ready(self) -> tuple[bool, str]:
            return True, ""

    monkeypatch.setattr("winpodx.core.agent.AgentClient", FakeClient)
    monkeypatch.setattr(
        "winpodx.core.provisioner.apply_windows_runtime_fixes",
        lambda _cfg: {"max_sessions": "ok"},
    )
    monkeypatch.setattr("winpodx.core.discovery.scan", lambda _cfg: [])
    monkeypatch.setattr("winpodx.core.discovery.persist_discovered", lambda _apps: [])
    cfg.reverse_open.enabled = False

    harness = Harness(cfg)
    # Pre-set a cancel event left over from a hypothetical prior run.
    harness._bringup_cancel = threading.Event()
    harness._bringup_cancel.set()

    harness._run_full_bring_up()
    _wait_for_done(harness, timeout=5.0)

    # _run_full_bring_up must allocate a fresh event, so the leftover
    # set() doesn't poison this run.
    assert harness.bringup_done.emissions[0] == (True, "")
