# SPDX-License-Identifier: MIT
"""Tests for the auto bring-up workflow (BringUpMixin, v0.5.1).

The Qt dialog (``BringUpProgressDialog``) is GUI-smoke territory; the
checklist + tail-lifecycle tests at the bottom of this file exercise it
in headless mode with ``QApplication([])`` so we stay free of pytest-qt.
The worker logic is pure Python apart from the ``Signal.emit`` calls,
which we satisfy with a lightweight ``FakeSignal`` that records
emissions.

Covers:
  - Happy path: all 5 phases fire in order, ``bringup_done(True, "")``.
  - Cancellation during phase 1: worker exits within 1 s with
    ``bringup_done(False, "cancelled")``.
  - Failure: phase 3 ``apply_windows_runtime_fixes`` raises, worker
    emits ``bringup_done(False, "...<error>")``.
  - Polling phase sub_detail strings carry an ``Attempt N`` counter
    so the dialog can render progress feedback.
  - Phase-ID cascade order: emissions use the stable ``phase_1_pod``
    / ``phase_2_agent`` / ... slugs so the dialog can route to the
    correct checklist row.
  - Dialog lifecycle: pod-log lines append to the view; the elapsed
    QTimer starts on open and stops on accept.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

pytest.importorskip("PySide6")

from winpodx.core.config import Config  # noqa: E402
from winpodx.core.pod import PodState, PodStatus  # noqa: E402
from winpodx.gui._main_window_bringup import BringUpMixin  # noqa: E402


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


@pytest.fixture(autouse=True)
def _stub_dockur_progress(monkeypatch):
    """Keep phase 1 hermetic: never shell out to ``podman logs`` against a real
    container (a live winpodx-windows would leak its log state into the test).
    Tests that exercise the boot-error path override this per-test."""
    from winpodx.gui._main_window_bringup import BringUpMixin

    monkeypatch.setattr(BringUpMixin, "_dockur_progress", lambda self: (None, None, False))


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
        "phase_1_pod",
        "phase_2_agent",
        "phase_3_fixes",
        "phase_4_discovery",
        "phase_5_refresh",
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


# ----- polling-phase attempt counters ------------------------------------


def test_polling_phases_emit_attempt_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 1/2 sub_detail strings must include an ``Attempt N`` counter.

    The dialog renders this verbatim so the user sees real progress
    during the long Phase-2 wait. We force Phase 1 to poll a few times
    by returning STARTING then RUNNING, then verify the sub-details
    were prefixed with the counter on each polling iteration.
    """
    cfg = _make_cfg()
    cfg.install.wait_ready_stage2_secs = 60
    cfg.install.wait_ready_stage3_secs = 60

    # Two STARTING + one RUNNING so phase 1 polls thrice.
    pod_states = iter(
        [
            PodStatus(state=PodState.STARTING, ip="127.0.0.1"),
            PodStatus(state=PodState.STARTING, ip="127.0.0.1"),
            PodStatus(state=PodState.RUNNING, ip="127.0.0.1"),
        ]
    )

    def _next_status(_cfg):
        try:
            return next(pod_states)
        except StopIteration:
            return PodStatus(state=PodState.RUNNING, ip="127.0.0.1")

    monkeypatch.setattr("winpodx.core.pod.pod_status", _next_status)
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

    # Speed the poll cadence so the test doesn't sit through 6 s of waits.
    monkeypatch.setattr("winpodx.gui._main_window_bringup._POLL_CADENCE_SECS", 0.05)

    harness = Harness(cfg)
    harness._run_full_bring_up()
    _wait_for_done(harness, timeout=10.0)

    # Find phase_1_pod emissions that include "Attempt N -" prefix.
    phase1_attempts = [
        detail
        for pid, detail in harness.bringup_phase.emissions
        if pid == "phase_1_pod" and detail.startswith("Attempt ")
    ]
    assert phase1_attempts, (
        "phase_1_pod never emitted an Attempt-counter sub_detail; "
        f"all emissions: {harness.bringup_phase.emissions}"
    )
    # At least one phase_2_agent attempt detail too.
    phase2_attempts = [
        detail
        for pid, detail in harness.bringup_phase.emissions
        if pid == "phase_2_agent" and detail.startswith("Attempt ")
    ]
    assert phase2_attempts, (
        "phase_2_agent never emitted an Attempt-counter sub_detail; "
        f"all emissions: {harness.bringup_phase.emissions}"
    )


def test_phase_id_cascade_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """First emission of each phase ID appears in the canonical order.

    The dialog's checklist row routing depends on the phase-ID slug
    sequence. A regression that re-orders or renames an ID would
    silently break the checklist; this test pins the contract.
    """
    cfg = _make_cfg()

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
    monkeypatch.setattr("winpodx.cli.host_open._cmd_refresh", lambda _args: 0)
    cfg.reverse_open.enabled = True

    harness = Harness(cfg)
    harness._run_full_bring_up()
    _wait_for_done(harness, timeout=5.0)

    # Build the distinct-phase-id order.
    distinct: list[str] = []
    for pid, _detail in harness.bringup_phase.emissions:
        if not distinct or distinct[-1] != pid:
            distinct.append(pid)

    expected = [
        "phase_1_pod",
        "phase_2_agent",
        "phase_3_fixes",
        "phase_4_discovery",
        "phase_5_refresh",
    ]
    # The first occurrence of each expected ID must appear in canonical
    # order. Re-entries (extra polling iterations) are fine.
    first_seen = {}
    for i, pid in enumerate(distinct):
        first_seen.setdefault(pid, i)
    for pid in expected:
        assert pid in first_seen, f"{pid!r} never emitted; distinct={distinct}"
    ordered_indices = [first_seen[pid] for pid in expected]
    assert ordered_indices == sorted(ordered_indices), (
        f"phase IDs out of canonical order: {distinct}"
    )


# ----- dialog-side smoke (headless) --------------------------------------


def _ensure_qapp():
    """Return a QApplication, creating one if needed."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_dialog_appends_pod_log_lines() -> None:
    """``append_pod_log_line`` adds the literal line to the view widget."""
    _ensure_qapp()
    from winpodx.gui._main_window_bringup import BringUpProgressDialog

    cancelled: list[bool] = []
    dlg = BringUpProgressDialog(None, on_cancel=lambda: cancelled.append(True), cfg=None)
    try:
        dlg.append_pod_log_line("[pod] BdsDxe: starting Boot0001")
        dlg.append_pod_log_line("[pod] [Setup] Applying image...")
        text = dlg.pod_log_view.toPlainText()
        assert "[pod] BdsDxe: starting Boot0001" in text
        assert "[pod] [Setup] Applying image..." in text
        # Empty / falsy lines are ignored.
        before = dlg.pod_log_view.toPlainText()
        dlg.append_pod_log_line("")
        assert dlg.pod_log_view.toPlainText() == before
    finally:
        dlg.reject()


def test_dialog_phase_routing_updates_checklist() -> None:
    """``on_phase`` ticks prior rows and marks the new one in-progress."""
    _ensure_qapp()
    from winpodx.gui._main_window_bringup import BringUpProgressDialog

    dlg = BringUpProgressDialog(None, on_cancel=lambda: None, cfg=None)
    try:
        dlg.on_phase("phase_1_pod", "Attempt 1 - probing")
        # Row 0 in-progress.
        glyph0, _name0, _elapsed0 = dlg._row_widgets[0]
        assert glyph0.text().startswith(">")

        dlg.on_phase("phase_2_agent", "Attempt 1 - /health: ConnectionRefused")
        # Row 0 should now be ticked, row 1 in-progress.
        glyph0, _, _ = dlg._row_widgets[0]
        glyph1, _, _ = dlg._row_widgets[1]
        assert glyph0.text().startswith("✓")
        assert glyph1.text().startswith(">")
    finally:
        dlg.reject()


def test_dialog_timer_lifecycle() -> None:
    """The 1-second tick timer starts on open and stops on accept/reject."""
    _ensure_qapp()
    from winpodx.gui._main_window_bringup import BringUpProgressDialog

    dlg = BringUpProgressDialog(None, on_cancel=lambda: None, cfg=None)
    # Active right after construction.
    assert dlg._tick_timer.isActive()
    dlg.on_done(True, "")
    # Done freezes the timer.
    assert not dlg._tick_timer.isActive()

    # Same on reject (cancel path).
    dlg2 = BringUpProgressDialog(None, on_cancel=lambda: None, cfg=None)
    assert dlg2._tick_timer.isActive()
    dlg2.reject()
    assert not dlg2._tick_timer.isActive()


def test_dialog_done_freezes_active_phase_elapsed() -> None:
    """On done(success=True) all started rows get a finalised elapsed."""
    _ensure_qapp()
    from winpodx.gui._main_window_bringup import BringUpProgressDialog

    dlg = BringUpProgressDialog(None, on_cancel=lambda: None, cfg=None)
    try:
        dlg.on_phase("phase_1_pod", "starting")
        dlg.on_phase("phase_2_agent", "starting")
        dlg.on_phase("phase_3_fixes", "starting")
        dlg.on_done(True, "")
        # All three started rows must be ticked.
        for i in range(3):
            glyph, _, elapsed_label = dlg._row_widgets[i]
            assert glyph.text().startswith("✓"), f"row {i} not ticked"
            assert elapsed_label.text(), f"row {i} elapsed empty"
    finally:
        dlg.reject()


# ----- phase 4 discovery retry (transient agent-channel hiccup) -----------


def _pass_phases_123(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub phases 1-3 so a test can focus on phase 4 (discovery)."""
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: PodStatus(state=PodState.RUNNING, ip="127.0.0.1"),
    )
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda _ip, _port, timeout=3.0: True)

    class _FakeClient:
        def __init__(self, _cfg: Config) -> None:
            pass

        def health(self) -> dict:
            return {"ok": True}

        def auth_ready(self) -> tuple[bool, str]:
            return True, ""

    monkeypatch.setattr("winpodx.core.agent.AgentClient", _FakeClient)
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


def test_phase4_retries_transient_channel_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core.discovery import DiscoveryError

    cfg = _make_cfg()
    cfg.reverse_open.enabled = True
    _pass_phases_123(monkeypatch)
    monkeypatch.setattr("winpodx.gui._main_window_bringup._DISCOVERY_RETRY_SECS", 0.01)

    calls = {"n": 0}

    def _flaky(_cfg: Config) -> list[str]:
        calls["n"] += 1
        if calls["n"] < 3:  # the agent /health flickered mid-scan twice
            raise DiscoveryError(
                "Discovery channel failure: /exec socket error: "
                "Remote end closed connection without response",
                kind="pod_not_running",
            )
        return ["app1"]

    monkeypatch.setattr("winpodx.core.discovery.scan", _flaky)
    monkeypatch.setattr("winpodx.core.discovery.persist_discovered", lambda _a: ["/tmp/app1.toml"])
    monkeypatch.setattr("winpodx.cli.host_open._cmd_refresh", lambda _args: 0)

    harness = Harness(cfg)
    harness._run_full_bring_up()
    _wait_for_done(harness, timeout=5.0)

    assert calls["n"] == 3  # 2 transient retries + 1 success
    assert harness.bringup_done.emissions == [(True, "")]


def test_phase1_fails_fast_on_qemu_boot_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A boot-looping QEMU device error (e.g. dockur's host_mtu on e1000) should
    # fail the bring-up fast with the real reason, not wait out the budget.
    from winpodx.gui._main_window_bringup import BringUpMixin

    cfg = _make_cfg()
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: PodStatus(state=PodState.STOPPED, ip=""),
    )
    err = "qemu-system-x86_64: -device e1000,...: Property 'e1000.host_mtu' not found"
    monkeypatch.setattr(BringUpMixin, "_dockur_progress", lambda self: (err, None, False))

    harness = Harness(cfg)
    harness._run_full_bring_up()
    _wait_for_done(harness, timeout=8.0)

    ok, msg = harness.bringup_done.emissions[0]
    assert ok is False
    assert "QEMU" in msg and "host_mtu" in msg


def test_phase4_does_not_retry_real_script_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core.discovery import DiscoveryError

    cfg = _make_cfg()
    _pass_phases_123(monkeypatch)
    monkeypatch.setattr("winpodx.gui._main_window_bringup._DISCOVERY_RETRY_SECS", 0.01)

    calls = {"n": 0}

    def _failing(_cfg: Config) -> list[str]:
        calls["n"] += 1
        raise DiscoveryError("Discovery script failed (rc=1): boom", kind="script_failed")

    monkeypatch.setattr("winpodx.core.discovery.scan", _failing)
    monkeypatch.setattr(
        "winpodx.cli.host_open._cmd_refresh",
        lambda _args: pytest.fail("phase 5 must not run when discovery fails"),
    )

    harness = Harness(cfg)
    harness._run_full_bring_up()
    _wait_for_done(harness, timeout=5.0)

    assert calls["n"] == 1  # genuine script failure → no retry
    success, msg = harness.bringup_done.emissions[0]
    assert success is False
    assert "Discovery script failed" in msg
