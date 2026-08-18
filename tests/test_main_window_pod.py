# SPDX-License-Identifier: MIT
"""Tests for the pod-control mixin, the License page, and the window shell.

``WinpodxWindow`` composes ~13 ``*Mixin`` classes; instantiating the whole
window drags in ``Config.load``, a live pod probe, the log tails and the
discovery worker. None of that is needed to exercise the mixins: each is a
plain class whose contract is a handful of ``self.*`` attributes, so every
test here mixes ONE mixin into a bare harness and stubs exactly what that
mixin reads. Nothing constructs a real ``WinpodxWindow``.

Everything outward-facing is stubbed -- ``Config.load``, ``pod_status``,
``stop_pod``, ``check_rdp_port``, ``AgentClient``, ``ensure_ready``,
``launch_app``, the launcher state file, the tray spawn and every modal
``QMessageBox``. Worker threads are replaced by an inline-running
``_SyncThread`` and ``time.sleep`` is neutered, so the file stays
deterministic and well under a second.

Covers:
  - PodStatusMixin: the launch path (cooldown debounce, the single-launch
    lock, the FreeRDP exit-code triage), pod start/stop with the active-
    session prompt, the 15 s polling timer, the agent/RDP transport dots
    and every pod state the chip renders.
  - LicensePageMixin: the page build, the MIT text card, one card per
    third-party acknowledgment and the LICENSE-unreadable fallback.
  - main_window: signal wiring, the log-bar ticker, the worker-thread
    join, screen fitting, scroll-area minimums, the resize/close event
    overrides and the ``run_gui()`` entry point.
"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent, QColor, QResizeEvent  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QWidget,
)

import winpodx.gui._main_window_license as lic_mod  # noqa: E402
import winpodx.gui._main_window_pod as pod_mod  # noqa: E402
import winpodx.gui.main_window as mw_mod  # noqa: E402
from winpodx.core.app import AppInfo  # noqa: E402
from winpodx.core.config import Config  # noqa: E402
from winpodx.core.i18n import tr  # noqa: E402
from winpodx.core.pod import PodState, PodStatus  # noqa: E402
from winpodx.gui import launcher_state  # noqa: E402
from winpodx.gui._main_window_license import LicensePageMixin  # noqa: E402
from winpodx.gui._main_window_pod import PodStatusMixin  # noqa: E402
from winpodx.gui.theme import C  # noqa: E402

# ----- shared helpers ----------------------------------------------------


def _ensure_qapp() -> QApplication:
    """Return a QApplication, creating one if needed."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeSignal:
    """Minimal stand-in for a Qt Signal that records emits + connects."""

    def __init__(self) -> None:
        self.emissions: list[tuple] = []
        self.connections: list[Any] = []

    def emit(self, *args: Any) -> None:
        self.emissions.append(args)

    def connect(self, slot: Any) -> None:
        self.connections.append(slot)


class _SyncThread:
    """``threading.Thread`` stand-in that runs the target inline on start()."""

    def __init__(self, target=None, daemon: bool = False, args=(), kwargs=None) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


def _sync_threads(monkeypatch: pytest.MonkeyPatch, module) -> list[_SyncThread]:
    """Swap the module's ``threading`` reference for an inline-running fake."""
    created: list[_SyncThread] = []

    def _factory(*args, **kwargs) -> _SyncThread:
        t = _SyncThread(*args, **kwargs)
        created.append(t)
        return t

    monkeypatch.setattr(module, "threading", SimpleNamespace(Thread=_factory, Lock=threading.Lock))
    return created


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neuter the post-spawn observation sleeps (``_launch_app`` imports time)."""
    import time

    monkeypatch.setattr(time, "sleep", lambda _secs: None)


def _make_cfg() -> Config:
    return Config()


def _app(name: str = "word") -> AppInfo:
    return AppInfo(name=name, full_name=name.title(), executable=f"C:\\{name}.exe")


class _FakeProc:
    def __init__(self, rc: int | None) -> None:
        self._rc = rc
        self.returncode = rc

    def poll(self) -> int | None:
        return self._rc


class _FakeSession:
    def __init__(self, process: Any = None, stderr_tail: bytes = b"") -> None:
        self.process = process
        self.stderr_tail = stderr_tail


# ----- PodStatusMixin harness --------------------------------------------


class PodHarness(PodStatusMixin):
    """Bare host exposing only what PodStatusMixin reads."""

    def __init__(self, cfg: Config, apps: list[AppInfo] | None = None) -> None:
        self.cfg = cfg
        self.apps = list(apps or [])
        self._pod_state = "checking"
        self._refresh_state = "idle"
        self._recently_launched: set[str] = set()
        self.pod_status_updated = FakeSignal()
        self.transport_status_updated = FakeSignal()
        self.app_launched = FakeSignal()
        self.app_launch_failed = FakeSignal()
        self.info_label = QLabel()
        self.pod_dot = QLabel()
        self.pod_label = QLabel()
        self.info_pod_dot = QLabel()
        self.info_pod_addr = QLabel()
        self.agent_dot = QLabel()
        self.rdp_dot = QLabel()
        self.banner_icon = QLabel()
        self.banner_text = QLabel()
        self.banner_btn = QPushButton()
        self.status_banner = QWidget()
        self.btn_start = QPushButton()
        self.btn_stop = QPushButton()
        self.refreshed = 0
        self.home_refreshed = 0

    def _on_refresh_apps(self) -> None:
        self.refreshed += 1

    def _refresh_launcher_home(self) -> None:
        self.home_refreshed += 1


def _pod_host(
    monkeypatch: pytest.MonkeyPatch,
    *,
    apps: list[AppInfo] | None = None,
    cfg: Config | None = None,
) -> PodHarness:
    """A PodStatusMixin harness with inline threads and no real Config file."""
    _ensure_qapp()
    _sync_threads(monkeypatch, pod_mod)
    config = cfg or _make_cfg()
    monkeypatch.setattr(pod_mod, "Config", SimpleNamespace(load=lambda: config))
    return PodHarness(config, apps)


# ----- PodStatusMixin: the launch path ------------------------------------


def test_launch_debounces_a_second_click_within_the_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    app = _app()
    host._recently_launched.add(app.name)

    host._launch_app(app)

    assert host.app_launch_failed.emissions == [(tr("Just launched. Please wait a moment."),)]
    assert host.app_launched.emissions == []


def test_launch_spawns_the_session_and_records_it_recent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    _no_sleep(monkeypatch)
    recorded: list[str] = []
    seen: list[tuple] = []
    monkeypatch.setattr(launcher_state, "record_recent", lambda name: recorded.append(name))
    monkeypatch.setattr("winpodx.core.provisioner.ensure_ready", lambda: host.cfg)
    monkeypatch.setattr(
        "winpodx.core.rdp.launch_app",
        lambda cfg, executable, **kw: seen.append((executable, kw)) or _FakeSession(),
    )

    host._launch_app(_app())

    assert host.info_label.text() == tr("Launching {app}...").format(app="Word")
    assert seen[0][0] == "C:\\word.exe"
    assert recorded == ["word"]
    assert host.app_launched.emissions == [("Word",)]
    assert host.app_launch_failed.emissions == []


@pytest.mark.parametrize("rc", [0, 129])
def test_launch_treats_a_clean_or_signalled_exit_as_success(
    monkeypatch: pytest.MonkeyPatch, rc: int
) -> None:
    host = _pod_host(monkeypatch)
    _no_sleep(monkeypatch)
    recorded: list[str] = []
    monkeypatch.setattr(launcher_state, "record_recent", lambda name: recorded.append(name))
    monkeypatch.setattr("winpodx.core.provisioner.ensure_ready", lambda: host.cfg)
    monkeypatch.setattr(
        "winpodx.core.rdp.launch_app",
        lambda cfg, executable, **kw: _FakeSession(process=_FakeProc(rc)),
    )

    host._launch_app(_app())

    assert recorded == ["word"]
    assert host.app_launched.emissions == [("Word",)]


def test_launch_surfaces_a_nonzero_freerdp_exit_with_its_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    _no_sleep(monkeypatch)
    recorded: list[str] = []
    monkeypatch.setattr(launcher_state, "record_recent", lambda name: recorded.append(name))
    monkeypatch.setattr("winpodx.core.provisioner.ensure_ready", lambda: host.cfg)
    monkeypatch.setattr(
        "winpodx.core.rdp.launch_app",
        lambda cfg, executable, **kw: _FakeSession(
            process=_FakeProc(128), stderr_tail=b"ERRCONNECT_LOGON_FAILURE"
        ),
    )

    host._launch_app(_app())

    assert recorded == []
    assert host.app_launched.emissions == []
    (message,) = host.app_launch_failed.emissions[0]
    assert tr("FreeRDP exited with code {code}").format(code=128) in message
    assert "ERRCONNECT_LOGON_FAILURE" in message


def test_launch_reports_the_traceback_when_ensure_ready_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    _no_sleep(monkeypatch)

    def _boom() -> Config:
        raise RuntimeError("no kvm device")

    monkeypatch.setattr("winpodx.core.provisioner.ensure_ready", _boom)

    host._launch_app(_app())

    (message,) = host.app_launch_failed.emissions[0]
    assert "no kvm device" in message
    assert host.app_launched.emissions == []
    # The lock must be released even on the failure path.
    assert PodStatusMixin._launch_lock.acquire(blocking=False)
    PodStatusMixin._launch_lock.release()


def test_launch_refuses_a_second_concurrent_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)
    _no_sleep(monkeypatch)
    monkeypatch.setattr(
        "winpodx.core.provisioner.ensure_ready",
        lambda: pytest.fail("ensure_ready must not run while another launch holds the lock"),
    )

    PodStatusMixin._launch_lock.acquire()
    try:
        host._launch_app(_app())
    finally:
        PodStatusMixin._launch_lock.release()

    assert host.app_launch_failed.emissions == [(tr("Another app is launching, please wait."),)]


# ----- PodStatusMixin: pod start / stop -----------------------------------


def test_start_pod_runs_ensure_ready_then_refreshes_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        "winpodx.core.provisioner.ensure_ready", lambda: calls.append("ensure_ready") or host.cfg
    )
    monkeypatch.setattr(
        pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING, ip="10.0.0.5")
    )
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda _ip, _port, timeout=1.0: True)

    host._on_start_pod()

    assert host.info_label.text() == tr("Starting pod...")
    assert calls == ["ensure_ready"]
    assert host.pod_status_updated.emissions == [("running", "10.0.0.5")]


def test_start_pod_reports_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)

    def _boom() -> Config:
        raise RuntimeError("port 3390 busy")

    monkeypatch.setattr("winpodx.core.provisioner.ensure_ready", _boom)

    host._on_start_pod()

    assert host.app_launch_failed.emissions == [
        (tr("Pod start failed: {error}").format(error="port 3390 busy"),)
    ]
    assert host.pod_status_updated.emissions == []


def test_stop_pod_stops_the_pod_when_nothing_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)
    stopped: list[Config] = []
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", list)
    monkeypatch.setattr("winpodx.core.pod.stop_pod", lambda cfg: stopped.append(cfg))
    monkeypatch.setattr(
        pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.STOPPED, ip="")
    )
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda _ip, _port, timeout=1.0: False)

    host._on_stop_pod()

    assert host.info_label.text() == tr("Stopping pod...")
    assert stopped == [host.cfg]
    assert host.pod_status_updated.emissions == [("stopped", "")]


def test_stop_pod_aborts_when_the_user_declines_the_session_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    asked: list[str] = []
    monkeypatch.setattr(
        "winpodx.core.process.list_active_sessions",
        lambda: [SimpleNamespace(app_name="word"), SimpleNamespace(app_name="excel")],
    )
    monkeypatch.setattr(
        "winpodx.core.pod.stop_pod", lambda cfg: pytest.fail("pod must not stop after a decline")
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda _p, _t, text: asked.append(text) or QMessageBox.StandardButton.No,
        ),
    )

    host._on_stop_pod()

    assert "word, excel" in asked[0]
    assert host.info_label.text() == ""


def test_stop_pod_proceeds_when_the_user_confirms(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)
    stopped: list[Config] = []
    monkeypatch.setattr(
        "winpodx.core.process.list_active_sessions",
        lambda: [SimpleNamespace(app_name="word")],
    )
    monkeypatch.setattr("winpodx.core.pod.stop_pod", lambda cfg: stopped.append(cfg))
    monkeypatch.setattr(
        pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.STOPPED, ip="")
    )
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda _ip, _port, timeout=1.0: False)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *_a, **_k: QMessageBox.StandardButton.Yes),
    )

    host._on_stop_pod()

    assert stopped == [host.cfg]


# ----- PodStatusMixin: the polling timer ----------------------------------


def test_status_timer_polls_at_the_15_second_cadence(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)
    monkeypatch.setattr(
        pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING, ip="10.0.0.5")
    )
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda _ip, _port, timeout=1.0: True)

    host._start_status_timer()
    try:
        assert host.status_timer.isActive()
        assert host.status_timer.interval() == 15000
        # The first poll happens immediately, not one interval later.
        assert host.pod_status_updated.emissions == [("running", "10.0.0.5")]
    finally:
        host.status_timer.stop()


class _FakeAgent:
    payload: dict = {"version": "0.10.4"}

    def __init__(self, _cfg: Config) -> None:
        pass

    def health(self) -> dict:
        return self.payload


def test_refresh_pod_status_emits_pod_and_transport_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    probed: list[tuple] = []
    monkeypatch.setattr(
        pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING, ip="10.0.0.5")
    )
    monkeypatch.setattr("winpodx.core.agent.AgentClient", _FakeAgent)
    monkeypatch.setattr(
        "winpodx.core.pod.check_rdp_port",
        lambda ip, port, timeout=1.0: probed.append((ip, port, timeout)) or True,
    )

    host._refresh_pod_status()

    assert host.pod_status_updated.emissions == [("running", "10.0.0.5")]
    assert host.transport_status_updated.emissions == [(True, True, "0.10.4")]
    assert probed == [(host.cfg.rdp.ip, host.cfg.rdp.port, 1.0)]


def test_refresh_pod_status_marks_the_agent_down_on_agent_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpodx.core.agent import AgentError

    host = _pod_host(monkeypatch)

    class _DeadAgent:
        def __init__(self, _cfg: Config) -> None:
            pass

        def health(self) -> dict:
            raise AgentError("connection refused")

    monkeypatch.setattr(
        pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING, ip="10.0.0.5")
    )
    monkeypatch.setattr("winpodx.core.agent.AgentClient", _DeadAgent)
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda _ip, _port, timeout=1.0: True)

    host._refresh_pod_status()

    assert host.transport_status_updated.emissions == [(False, True, "")]


def test_refresh_pod_status_survives_broken_agent_and_rdp_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)

    def _explode(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("probe blew up")

    monkeypatch.setattr(
        pod_mod, "pod_status", lambda _cfg: PodStatus(state=PodState.RUNNING, ip="10.0.0.5")
    )
    monkeypatch.setattr("winpodx.core.agent.AgentClient", _explode)
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", _explode)

    host._refresh_pod_status()

    # A broken probe must never break the timer: state still lands, dots go dark.
    assert host.pod_status_updated.emissions == [("running", "10.0.0.5")]
    assert host.transport_status_updated.emissions == [(False, False, "")]


def test_refresh_pod_status_reports_error_when_the_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)

    def _boom(_cfg: Config) -> PodStatus:
        raise RuntimeError("podman is gone")

    monkeypatch.setattr(pod_mod, "pod_status", _boom)
    monkeypatch.setattr(
        "winpodx.core.agent.AgentClient",
        lambda _cfg: pytest.fail("transports must not be probed after a pod-probe failure"),
    )

    host._refresh_pod_status()

    assert host.pod_status_updated.emissions == [("error", "")]
    assert host.transport_status_updated.emissions == [(False, False, "")]


# ----- PodStatusMixin: the transport dots ---------------------------------


def test_transport_dots_are_green_when_both_probes_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)

    host._on_transport_status(True, True, "0.10.4")

    assert C.GREEN in host.agent_dot.styleSheet()
    assert C.GREEN in host.rdp_dot.styleSheet()
    assert host.agent_dot.toolTip() == tr("Guest agent OK ({version})").format(version="0.10.4")
    assert host.rdp_dot.toolTip() == tr("RDP port 3390 reachable")
    assert host._last_agent_ok is True
    assert host._last_rdp_ok is True


def test_transport_agent_tooltip_drops_the_version_when_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)

    host._on_transport_status(True, True, "")

    assert host.agent_dot.toolTip() == tr("Guest agent OK")


def test_transport_agent_dot_is_peach_when_only_rdp_is_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)

    host._on_transport_status(False, True, "")

    # Agent down + RDP up is a soft fallback, not a launch-breaking failure.
    assert C.PEACH in host.agent_dot.styleSheet()
    assert C.GREEN in host.rdp_dot.styleSheet()
    assert host.agent_dot.toolTip() == tr("Agent down — using FreeRDP fallback (apps still launch)")


def test_transport_dots_are_red_when_rdp_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)

    host._on_transport_status(False, False, "")

    assert C.RED in host.agent_dot.styleSheet()
    assert C.RED in host.rdp_dot.styleSheet()
    assert host.rdp_dot.toolTip() == tr("RDP port 3390 unreachable — apps cannot launch")


def test_transport_status_reapplies_the_banner_while_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    host._pod_state = "running"
    host.status_banner.setVisible(True)

    host._on_transport_status(False, False, "")

    # The banner is unmounted chrome: re-deriving it must leave it hidden.
    assert host.status_banner.isVisible() is False


# ----- PodStatusMixin: the pod chip ---------------------------------------


@pytest.mark.parametrize(
    ("state", "color"),
    [
        ("running", C.GREEN),
        ("stopped", C.RED),
        ("starting", C.YELLOW),
        ("paused", C.PEACH),
        ("error", C.RED),
        ("unresponsive", C.SUBTEXT0),
    ],
)
def test_pod_status_paints_the_chip_for_every_state(
    monkeypatch: pytest.MonkeyPatch, state: str, color: str
) -> None:
    host = _pod_host(monkeypatch)

    host._on_pod_status(state, "")

    assert host._pod_state == state
    assert host.pod_label.text() == state
    assert color in host.pod_label.styleSheet()
    assert color in host.pod_dot.styleSheet()
    assert host.pod_dot.pixmap().isNull() is False


def test_pod_status_renders_checking_as_a_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)

    host._on_pod_status("checking", "")

    assert host.pod_label.text() == tr("probing…")


def test_pod_status_toggles_the_start_and_stop_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)

    host._on_pod_status("stopped", "")
    assert host.btn_start.isEnabled() is True
    assert host.btn_stop.isEnabled() is False

    host._on_pod_status("running", "10.0.0.5")
    assert host.btn_start.isEnabled() is False
    assert host.btn_stop.isEnabled() is True


def test_pod_status_shows_the_ip_only_while_running(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)

    host._on_pod_status("running", "10.0.0.5")
    assert host.info_pod_addr.text() == "10.0.0.5"

    host._on_pod_status("starting", "10.0.0.5")
    assert host.info_pod_addr.text() == ""


def test_pod_status_kicks_discovery_when_running_with_an_empty_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        pod_mod,
        "QTimer",
        SimpleNamespace(singleShot=lambda ms, fn: scheduled.append((ms, fn))),
    )

    host._on_pod_status("running", "10.0.0.5")

    assert scheduled == [(2000, host._on_refresh_apps)]


@pytest.mark.parametrize(
    ("apps", "refresh_state", "previous"),
    [
        ([_app()], "idle", "stopped"),  # library already populated
        ([], "scanning", "stopped"),  # a scan is already in flight
        ([], "idle", "running"),  # not a transition into running
    ],
)
def test_pod_status_does_not_re_kick_discovery(
    monkeypatch: pytest.MonkeyPatch,
    apps: list[AppInfo],
    refresh_state: str,
    previous: str,
) -> None:
    host = _pod_host(monkeypatch, apps=apps)
    host._refresh_state = refresh_state
    host._pod_state = previous
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        pod_mod,
        "QTimer",
        SimpleNamespace(singleShot=lambda ms, fn: scheduled.append((ms, fn))),
    )

    host._on_pod_status("running", "10.0.0.5")

    assert scheduled == []


def test_pod_status_repaints_the_empty_library_with_the_live_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    filtered: list[str] = []
    host._filter_apps = lambda text: filtered.append(text)
    host.search_box = SimpleNamespace(text=lambda: "wor")

    host._on_pod_status("starting", "")

    assert filtered == ["wor"]


def test_pod_status_repaints_the_empty_library_without_a_search_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)
    filtered: list[str] = []
    host._filter_apps = lambda text: filtered.append(text)

    host._on_pod_status("starting", "")

    assert filtered == [""]


def test_pod_status_survives_a_failing_library_repaint(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)

    def _boom(_text: str) -> None:
        raise RuntimeError("widget already deleted")

    host._filter_apps = _boom

    host._on_pod_status("starting", "")

    # A cosmetic refresh must never break the status paint.
    assert host.pod_label.text() == "starting"


# ----- PodStatusMixin: the status banner ----------------------------------


def test_status_banner_stays_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)
    host.status_banner.setVisible(True)

    host._apply_status_banner()

    assert host.status_banner.isVisible() is False


def test_status_banner_tolerates_an_unbuilt_widget(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)
    host.status_banner = None

    host._apply_status_banner()  # must not raise


@pytest.mark.parametrize(
    ("restart", "label"),
    [(False, "Start Now"), (True, "Restart")],
)
def test_set_banner_paints_the_row_and_button_label(
    monkeypatch: pytest.MonkeyPatch, restart: bool, label: str
) -> None:
    host = _pod_host(monkeypatch)

    host._set_banner("⏸", C.PEACH, "Pod is paused", restart=restart)

    assert host.banner_text.text() == "Pod is paused"
    assert host.banner_btn.text() == tr(label)
    assert C.PEACH in host.banner_icon.styleSheet()
    assert host.banner_icon.pixmap().isNull() is False


# ----- PodStatusMixin: launch result slots --------------------------------


def test_app_launched_updates_the_info_label_and_launcher_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _pod_host(monkeypatch)

    host._on_app_launched("Word")

    assert host.info_label.text() == tr("{name} launched").format(name="Word")
    assert host.home_refreshed == 1


def test_app_launch_failed_shows_the_error_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _pod_host(monkeypatch)
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "critical",
        staticmethod(lambda _p, _t, text: shown.append(text)),
    )

    host._on_app_launch_failed("ERRCONNECT_LOGON_FAILURE")

    assert host.info_label.text() == tr("Launch failed: {error}").format(
        error="ERRCONNECT_LOGON_FAILURE"
    )
    assert shown == ["ERRCONNECT_LOGON_FAILURE"]


# ----- LicensePageMixin ---------------------------------------------------


_MIT_SAMPLE = "MIT License\n\nCopyright (c) 2025 Kim DaeHyun\n\nPermission is hereby granted"


class LicenseHarness(LicensePageMixin, QWidget):
    """Bare host for the License page (a QWidget so pages get a parent)."""

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg


def _license_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path, *, text: str | None = _MIT_SAMPLE
) -> LicenseHarness:
    """A License harness whose bundle dir is a tmp dir, never the real install."""
    _ensure_qapp()
    if text is not None:
        (tmp_path / "LICENSE").write_text(text, encoding="utf-8")
    monkeypatch.setattr(lic_mod, "bundle_dir", lambda: tmp_path)
    return LicenseHarness(_make_cfg())


def _labels(widget: QWidget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


def test_license_page_renders_the_mit_text(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    host = _license_host(monkeypatch, tmp_path)

    page = host._build_license_page()
    page.setParent(host)

    views = page.findChildren(QTextEdit)
    assert len(views) == 1
    assert views[0].toPlainText() == _MIT_SAMPLE
    assert views[0].isReadOnly() is True
    assert tr("License text") in _labels(page)


def test_license_page_builds_one_card_per_acknowledgment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    host = _license_host(monkeypatch, tmp_path)

    page = host._build_license_page()
    page.setParent(host)

    cards = [f for f in page.findChildren(QFrame) if f.objectName() == "settingsSection"]
    # One card for the MIT text plus one per third-party entry.
    assert len(cards) == len(lic_mod._THIRD_PARTY_ACK) + 1
    texts = _labels(page)
    for name, license_, _purpose, url in lic_mod._THIRD_PARTY_ACK:
        assert name in texts
        assert license_ in texts
        assert url in texts


def test_ack_card_shows_name_license_purpose_and_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    host = _license_host(monkeypatch, tmp_path)

    card = host._build_ack_card("FreeRDP 3", "Apache-2.0", "RDP client", "https://example.invalid")
    card.setParent(host)

    texts = _labels(card)
    # Index 3 is the globe glyph label, which carries a pixmap and no text.
    assert texts == ["FreeRDP 3", "Apache-2.0", "RDP client", "", "https://example.invalid"]
    link = card.findChildren(QLabel)[4]
    # The URL is selectable text, never an auto-opening hyperlink.
    assert link.textInteractionFlags() == Qt.TextInteractionFlag.TextSelectableByMouse
    assert link.openExternalLinks() is False


def test_license_text_falls_back_when_the_bundle_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    host = _license_host(monkeypatch, tmp_path, text=None)

    text = host._read_license_text()

    assert "https://github.com/kernalix7/winpodx/blob/main/LICENSE" in text


# ----- main_window: signal wiring -----------------------------------------


class SignalHarness:
    """Bare host carrying the signals + slots ``_setup_signals`` wires up."""

    def __init__(self) -> None:
        self.pod_status_updated = FakeSignal()
        self.transport_status_updated = FakeSignal()
        self.app_launched = FakeSignal()
        self.app_launch_failed = FakeSignal()
        self.log_signal = FakeSignal()
        self.dashboard_updated = FakeSignal()
        self.bringup_started = FakeSignal()

    def _on_pod_status(self, state: str, ip: str) -> None:
        pass

    def _on_transport_status(self, agent_ok: bool, rdp_ok: bool, version: str) -> None:
        pass

    def _on_app_launched(self, name: str) -> None:
        pass

    def _on_app_launch_failed(self, error: str) -> None:
        pass

    def _log_append(self, line: str, color: str) -> None:
        pass

    def _update_log_bar(self, line: str, color: str) -> None:
        pass

    def _apply_snapshot(self, snapshot: Any) -> None:
        pass

    def _open_bringup_dialog(self) -> None:
        pass


def test_setup_signals_wires_every_slot() -> None:
    host = SignalHarness()

    mw_mod.WinpodxWindow._setup_signals(host)

    assert host.pod_status_updated.connections == [host._on_pod_status]
    assert host.transport_status_updated.connections == [host._on_transport_status]
    assert host.app_launched.connections == [host._on_app_launched]
    assert host.app_launch_failed.connections == [host._on_app_launch_failed]
    assert host.dashboard_updated.connections == [host._apply_snapshot]
    assert host.bringup_started.connections == [host._open_bringup_dialog]
    # log_signal fans out to the Terminal history AND the bottom ticker.
    assert host.log_signal.connections == [host._log_append, host._update_log_bar]


# ----- main_window: the bottom log ticker ---------------------------------


def test_log_bar_shifts_the_previous_line_down() -> None:
    _ensure_qapp()
    host = SimpleNamespace(log_bar_line1=QLabel(), log_bar_line2=QLabel())

    mw_mod.WinpodxWindow._update_log_bar(host, "first line", C.TEXT)
    mw_mod.WinpodxWindow._update_log_bar(host, "second line", C.TEXT)

    assert host.log_bar_line1.text() == "second line"
    assert host.log_bar_line2.text() == "first line"


def test_log_bar_elides_an_over_wide_line() -> None:
    _ensure_qapp()
    host = SimpleNamespace(log_bar_line1=QLabel(), log_bar_line2=QLabel())
    line = "winpodx " * 120

    mw_mod.WinpodxWindow._update_log_bar(host, line, C.TEXT)

    shown = host.log_bar_line1.text()
    assert shown != line
    assert shown.endswith("…")
    assert line.startswith(shown[:20])


# ----- main_window: worker-thread join ------------------------------------


class _FakeThread:
    def __init__(self, running: bool = True, raises: bool = False) -> None:
        self._running = running
        self._raises = raises
        self.events: list[str] = []

    def isRunning(self) -> bool:  # noqa: N802 - Qt signature
        if self._raises:
            raise RuntimeError("Internal C++ object already deleted")
        return self._running

    def quit(self) -> None:
        self.events.append("quit")

    def wait(self) -> None:
        self.events.append("wait")


def test_join_worker_threads_quits_and_waits_for_live_workers() -> None:
    refresh = _FakeThread(running=True)
    info = _FakeThread(running=False)
    host = SimpleNamespace(_refresh_thread=refresh, _info_thread=info)

    mw_mod.WinpodxWindow._join_worker_threads(host)

    assert refresh.events == ["quit", "wait"]
    assert info.events == []


def test_join_worker_threads_skips_missing_and_deleted_threads() -> None:
    dead = _FakeThread(raises=True)
    host = SimpleNamespace(_refresh_thread=None, _info_thread=dead)

    mw_mod.WinpodxWindow._join_worker_threads(host)  # must not raise

    assert dead.events == []


# ----- main_window: geometry ----------------------------------------------


class _GeometryHost:
    """Bare host exposing the QWidget geometry surface ``_fit_to_screen`` uses."""

    def __init__(self, *, min_w: int = 0, min_h: int = 0, screen: Any = None) -> None:
        self._preferred_size = (1100, 720)
        self._min_w = min_w
        self._min_h = min_h
        self._screen = screen
        self.resized: list[tuple] = []
        self.synced = 0

    def _sync_scroll_minimums(self) -> None:
        self.synced += 1

    def minimumWidth(self) -> int:  # noqa: N802 - Qt signature
        return self._min_w

    def minimumHeight(self) -> int:  # noqa: N802 - Qt signature
        return self._min_h

    def screen(self) -> Any:
        return self._screen

    def resize(self, width: int, height: int) -> None:
        self.resized.append((width, height))


def _fake_screen(width: int, height: int) -> Any:
    return SimpleNamespace(availableGeometry=lambda: QRect(0, 0, width, height))


def test_fit_to_screen_clamps_the_preferred_size_to_the_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _GeometryHost()
    monkeypatch.setattr(
        mw_mod, "QApplication", SimpleNamespace(primaryScreen=lambda: _fake_screen(800, 600))
    )

    mw_mod.WinpodxWindow._fit_to_screen(host)

    assert host.resized == [(740, 520)]
    assert host.synced == 1


def test_fit_to_screen_never_shrinks_below_the_content_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _GeometryHost(min_w=900, min_h=600)
    monkeypatch.setattr(
        mw_mod, "QApplication", SimpleNamespace(primaryScreen=lambda: _fake_screen(800, 600))
    )

    mw_mod.WinpodxWindow._fit_to_screen(host)

    assert host.resized == [(900, 600)]


def test_fit_to_screen_opens_at_the_preferred_size_on_a_large_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _GeometryHost()
    monkeypatch.setattr(
        mw_mod, "QApplication", SimpleNamespace(primaryScreen=lambda: _fake_screen(2560, 1440))
    )

    mw_mod.WinpodxWindow._fit_to_screen(host)

    assert host.resized == [(1100, 720)]


def test_fit_to_screen_falls_back_without_a_usable_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _GeometryHost(screen=_fake_screen(0, 0))
    monkeypatch.setattr(mw_mod, "QApplication", SimpleNamespace(primaryScreen=lambda: None))

    mw_mod.WinpodxWindow._fit_to_screen(host)

    # A zero-sized available area can't drive a clamp: open at the preference.
    assert host.resized == [(1100, 720)]
    assert host.synced == 0


def test_fit_to_screen_falls_back_with_no_screen_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _GeometryHost(screen=None)
    monkeypatch.setattr(mw_mod, "QApplication", SimpleNamespace(primaryScreen=lambda: None))

    mw_mod.WinpodxWindow._fit_to_screen(host)

    assert host.resized == [(1100, 720)]
    assert host.synced == 0


def test_scroll_minimums_track_the_page_content() -> None:
    _ensure_qapp()
    holder = QWidget()
    filled = QScrollArea(holder)
    inner = QLabel("a fairly wide page body that reports a real minimum size hint", filled)
    filled.setWidget(inner)
    empty = QScrollArea(holder)
    host = SimpleNamespace(findChildren=lambda _cls: [filled, empty])

    mw_mod.WinpodxWindow._sync_scroll_minimums(host)

    assert filled.minimumWidth() == inner.minimumSizeHint().width() + 18
    assert empty.minimumWidth() == 0


# ----- main_window: the Qt event overrides --------------------------------


class WindowShell(mw_mod.WinpodxWindow):
    """Live QMainWindow that deliberately skips ``WinpodxWindow.__init__``.

    ``resizeEvent`` / ``closeEvent`` call zero-arg ``super()``, which needs a
    real ``WinpodxWindow`` instance -- but the full ``__init__`` would load
    the config, build eight pages and start the pod poller. Initialising only
    the ``QMainWindow`` base gives a live Qt object with none of that; the
    page reflows are stubbed because no page exists to reflow.
    """

    def __init__(self) -> None:
        QMainWindow.__init__(self)
        self.reflowed: list[str] = []
        self.joined = 0

    def _reflow_settings(self) -> None:
        self.reflowed.append("settings")

    def _reflow_devices(self) -> None:
        self.reflowed.append("devices")

    def _reflow_dashboard(self) -> None:
        self.reflowed.append("dashboard")

    def _reflow_library(self) -> None:
        self.reflowed.append("library")

    def _join_worker_threads(self) -> None:
        self.joined += 1


def test_resize_event_reflows_every_responsive_page() -> None:
    _ensure_qapp()
    shell = WindowShell()
    try:
        shell.resizeEvent(QResizeEvent(QSize(900, 600), QSize(1100, 720)))

        assert shell.reflowed == ["settings", "devices", "dashboard", "library"]
    finally:
        shell.deleteLater()


def test_close_event_joins_the_worker_threads_and_accepts() -> None:
    _ensure_qapp()
    shell = WindowShell()
    try:
        event = QCloseEvent()
        shell.closeEvent(event)

        assert shell.joined == 1
        assert event.isAccepted() is True
    finally:
        shell.deleteLater()


# ----- main_window: run_gui() ---------------------------------------------


class _FakeWindow:
    def __init__(self) -> None:
        self.shown = 0

    def show(self) -> None:
        self.shown += 1


def _stub_run_gui(
    monkeypatch: pytest.MonkeyPatch, *, icon_path: Any
) -> tuple[list, list, list, list]:
    """Replace every outward call ``run_gui`` makes; return the recorders."""
    _ensure_qapp()
    apps: list = []
    policies: list = []
    exits: list = []
    trays: list = []

    class _FakeQApp:
        def __init__(self, argv: list) -> None:
            self.argv = argv
            self.name = ""
            self.style = ""
            self.icon = None
            self.palette = None
            self.exec_calls = 0
            apps.append(self)

        @classmethod
        def setHighDpiScaleFactorRoundingPolicy(cls, policy: Any) -> None:
            policies.append(policy)

        def setApplicationName(self, name: str) -> None:
            self.name = name

        def setStyle(self, style: str) -> None:
            self.style = style

        def setWindowIcon(self, icon: Any) -> None:
            self.icon = icon

        def setPalette(self, palette: Any) -> None:
            self.palette = palette

        def exec(self) -> int:
            self.exec_calls += 1
            return 7

    monkeypatch.setattr(mw_mod, "QApplication", _FakeQApp)
    monkeypatch.setattr(mw_mod, "WinpodxWindow", _FakeWindow)
    monkeypatch.setattr(
        mw_mod, "sys", SimpleNamespace(argv=["winpodx"], exit=lambda code: exits.append(code))
    )
    monkeypatch.setattr("winpodx.desktop.icons.bundled_data_path", lambda _name: icon_path)
    monkeypatch.setattr(
        "winpodx.desktop.tray_spawn.maybe_spawn_tray", lambda: trays.append("spawned")
    )
    return apps, policies, exits, trays


def test_run_gui_builds_the_app_window_and_tray(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    icon = tmp_path / "winpodx-icon.svg"
    icon.write_text("<svg/>", encoding="utf-8")
    apps, policies, exits, trays = _stub_run_gui(monkeypatch, icon_path=icon)

    mw_mod.run_gui()

    (app,) = apps
    assert app.argv == ["winpodx"]
    assert app.name == "winpodx"
    assert app.style == "Fusion"
    # Fractional scaling must pass through untouched.
    assert policies == [Qt.HighDpiScaleFactorRoundingPolicy.PassThrough]
    assert app.icon is not None
    assert app.palette.color(app.palette.ColorRole.Window) == QColor(C.BASE)
    assert app.palette.color(app.palette.ColorRole.Highlight) == QColor(C.BLUE)
    assert app.exec_calls == 1
    assert exits == [7]
    assert trays == ["spawned"]


def test_run_gui_skips_the_window_icon_when_the_bundle_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps, _policies, exits, trays = _stub_run_gui(monkeypatch, icon_path=None)

    mw_mod.run_gui()

    (app,) = apps
    assert app.icon is None
    assert app.palette is not None
    assert exits == [7]
    assert trays == ["spawned"]
