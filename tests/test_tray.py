# SPDX-License-Identifier: MIT
"""Tray single-instance lock + the #573 Plasma submenu invariants.

``desktop/tray.py`` keeps everything except ``_acquire_tray_lock`` nested
inside the 840-line ``run_tray()``, so the flock is the only import-testable
unit -- and it is load-bearing: it is the second line of defence against a
duplicate tray when ``pgrep`` is unavailable (``tray_spawn`` runs the first).

The submenu rules that regressed repeatedly under KDE/Plasma (#573 and its
follow-ups) live in ``run_tray()`` closures, so they get source-shape guards
in the established style of ``test_tray_sleep_listener.py`` -- CI has no
display and no StatusNotifier host.

No PySide6 import here on purpose: Qt is lazy inside ``run_tray()``, and this
file must keep running on a CLI-only install.
"""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from winpodx.desktop import tray

TRAY_SRC = Path(tray.__file__)


def _src() -> str:
    return TRAY_SRC.read_text(encoding="utf-8")


def _runtime_lock() -> Path:
    return Path(os.environ["XDG_RUNTIME_DIR"]) / "winpodx" / "tray.lock"


@pytest.fixture(autouse=True)
def _reset_tray_lock():
    """Clear the module-level handle so each test starts unlocked, and close
    whatever a test leaves behind so no fd leaks into the next one."""
    tray._TRAY_LOCK_FH = None
    yield
    fh = tray._TRAY_LOCK_FH
    if fh is not None:
        fh.close()
    tray._TRAY_LOCK_FH = None


# --- _acquire_tray_lock ------------------------------------------------------


def test_lock_lands_under_runtime_dir_and_creates_it() -> None:
    lock = _runtime_lock()
    assert not lock.parent.exists()
    assert tray._acquire_tray_lock() is True
    assert lock.is_file()


def test_lock_records_the_owner_pid() -> None:
    assert tray._acquire_tray_lock() is True
    assert _runtime_lock().read_text() == str(os.getpid())


def test_handle_is_retained_on_the_module() -> None:
    # Dropping the handle would let CPython GC the file object, close the fd
    # and silently release the flock -- single-instance would evaporate.
    assert tray._acquire_tray_lock() is True
    assert tray._TRAY_LOCK_FH is not None
    assert not tray._TRAY_LOCK_FH.closed


def test_second_tray_is_refused_while_the_lock_is_held() -> None:
    lock = _runtime_lock()
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as holder:  # stand-in for the tray already running
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert tray._acquire_tray_lock() is False
        assert tray._TRAY_LOCK_FH is None


def test_lock_is_advisory_not_a_stale_file_check() -> None:
    # A leftover lockfile from a crashed tray must not wedge the next launch.
    lock = _runtime_lock()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999")
    assert tray._acquire_tray_lock() is True


def test_falls_back_to_config_dir_without_runtime_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = tmp_path / "cfg"
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    # config_dir is imported inside the function -> patch the defining module.
    monkeypatch.setattr("winpodx.utils.paths.config_dir", lambda: cfg)
    assert tray._acquire_tray_lock() is True
    assert (cfg / "tray.lock").is_file()


def test_run_tray_returns_early_when_another_tray_owns_the_lock(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Past this guard run_tray() builds a QApplication and blocks in exec(),
    # so returning promptly is itself the assertion.
    monkeypatch.setattr(tray, "_acquire_tray_lock", lambda: False)
    with caplog.at_level(logging.INFO, logger="winpodx.desktop.tray"):
        assert tray.run_tray() is None
    assert "already running" in caplog.text


# --- #573: Plasma DBusMenu submenu invariants --------------------------------


def test_session_submenu_actions_are_parented() -> None:
    # A parentless QAction is GC'd when the rebuild closure returns, leaving a
    # submenu that DBusMenu exports with ZERO children -- it won't open.
    src = _src()
    assert 'QAction(tr("(no active sessions)"), sessions_menu)' in src
    assert 'QAction(tr("Terminate: {name}").format(name=s.app_name), sessions_menu)' in src


def test_device_submenu_actions_are_parented() -> None:
    src = _src()
    assert 'QAction(tr("(no USB devices detected)"), devices_menu)' in src
    assert "QAction(host.label or dc.did, devices_menu)" in src


def test_both_submenus_refresh_on_open() -> None:
    src = _src()
    assert "sessions_menu.aboutToShow.connect(_rebuild_sessions_menu)" in src
    assert "devices_menu.aboutToShow.connect(_rebuild_devices_menu)" in src


def test_timer_tick_rebuilds_both_submenus_and_cannot_raise() -> None:
    # Plasma does not reliably deliver a *nested* submenu's aboutToShow, so the
    # periodic tick is the fallback -- and an uncaught exception in a QTimer
    # slot aborts app.exec(), taking the whole tray down.
    src = _src()
    loop = "for _rebuild in (_rebuild_apps_menu, _rebuild_sessions_menu, _rebuild_devices_menu):"
    assert loop in src
    tail = src[src.index(loop) :]
    assert "try:" in tail[:200]
    assert "except Exception" in tail[:400]


# --- run_tray behavioural coverage -------------------------------------------


class _Signal:
    def __init__(self) -> None:
        self.slots = []

    def connect(self, slot) -> None:
        self.slots.append(slot)

    def emit(self, *args) -> None:
        for slot in tuple(self.slots):
            slot(*args)


class _FakeTimer:
    instances = []

    def __init__(self) -> None:
        self.timeout = _Signal()
        self.interval = None
        self.stopped = False
        self.instances.append(self)

    def start(self, interval: int) -> None:
        self.interval = interval

    def stop(self) -> None:
        self.stopped = True

    @staticmethod
    def singleShot(_delay: int, slot) -> None:
        slot()


class _ImmediateThread:
    instances = []

    def __init__(self, target, args=(), kwargs=None, **_thread_options) -> None:
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self.instances.append(self)

    def start(self) -> None:
        self.target(*self.args, **self.kwargs)


def _find_action(menu, text: str):
    for action in menu.actions():
        if action.text() == text:
            return action
        submenu = action.menu()
        if submenu is not None:
            found = _find_action(submenu, text)
            if found is not None:
                return found
    return None


def _find_menu(menu, title: str):
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            if submenu.title() == title:
                return submenu
            found = _find_menu(submenu, title)
            if found is not None:
                return found
    return None


@pytest.fixture
def tray_runtime(monkeypatch: pytest.MonkeyPatch):
    from PySide6 import QtCore, QtWidgets

    from winpodx.core import devices
    from winpodx.core.app import AppInfo
    from winpodx.core.pod import PodState
    from winpodx.core.pod.backend import PodStatus

    state = SimpleNamespace(
        pod_state=PodState.STOPPED,
        sessions=[],
        usb=[],
        assigned=[],
        start=MagicMock(return_value=SimpleNamespace(error="")),
        stop=MagicMock(return_value=SimpleNamespace(error="")),
        launch=MagicMock(),
        kill=MagicMock(return_value=True),
        assign=MagicMock(),
        unassign=MagicMock(),
        attach=MagicMock(),
        detach=MagicMock(),
        cleanup=MagicMock(return_value=["one.lock", "two.lock"]),
        timesync=MagicMock(return_value=True),
        suspend=MagicMock(),
        ensure_listener=MagicMock(),
        icon_monitor=MagicMock(),
        session_reaper=MagicMock(),
        tray_available=True,
    )
    cfg = SimpleNamespace(
        pod=SimpleNamespace(
            auto_start=False,
            backend="podman",
            container_name="winpodx",
            devices=[],
            idle_timeout=0,
        ),
        rdp=SimpleNamespace(ip="127.0.0.1"),
    )
    apps = [
        AppInfo(name="word", full_name="Microsoft Word", executable=r"C:\Office\word.exe"),
        AppInfo(name="calc", full_name="Calculator", executable=r"C:\Windows\calc.exe"),
    ]

    class CapturingTray(QtWidgets.QSystemTrayIcon):
        instance = None

        def __init__(self) -> None:
            super().__init__()
            type(self).instance = self
            self.messages = []
            self.show_count = 0

        @staticmethod
        def isSystemTrayAvailable() -> bool:
            return state.tray_available

        def show(self) -> None:
            self.show_count += 1

        def showMessage(self, title, message, icon, timeout=10000) -> None:
            self.messages.append((title, message, icon, timeout))

    class FakeBus:
        def __init__(self) -> None:
            self.connection = None

        def isConnected(self) -> bool:
            return True

        def connect(self, *args) -> bool:
            self.connection = args
            return True

    bus = FakeBus()
    _FakeTimer.instances = []
    _ImmediateThread.instances = []
    monkeypatch.setattr(tray, "_acquire_tray_lock", lambda: True)
    monkeypatch.setattr(tray, "tr", lambda text: text)
    monkeypatch.setattr(QtCore, "QTimer", _FakeTimer)
    monkeypatch.setattr(QtWidgets, "QSystemTrayIcon", CapturingTray)
    monkeypatch.setattr(QtWidgets.QApplication, "exec", lambda _self: 0)
    # run_tray() builds QApplication(sys.argv) unconditionally; Qt permits one per
    # process, so any earlier GUI test in the same xdist worker makes it raise.
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(QtWidgets, "QApplication", lambda *_a, **_k: qapp)
    monkeypatch.setattr("threading.Thread", _ImmediateThread)
    monkeypatch.setattr("winpodx.core.config.Config.load", lambda: cfg)
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: apps)
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: PodStatus(state=state.pod_state, ip="10.0.0.2"),
    )
    monkeypatch.setattr("winpodx.core.pod.start_pod", state.start)
    monkeypatch.setattr("winpodx.core.pod.stop_pod", state.stop)
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: state.sessions)
    monkeypatch.setattr("winpodx.core.process.kill_session", state.kill)
    monkeypatch.setattr("winpodx.core.rdp.launch_app", state.launch)
    monkeypatch.setattr(
        "winpodx.display.detector.display_info",
        lambda: {"session_type": "wayland", "desktop_environment": "KDE"},
    )
    monkeypatch.setattr("winpodx.desktop.icons.bundled_data_path", lambda _name: None)
    monkeypatch.setattr("winpodx.desktop.tray_spawn._install_in_progress", lambda: True)
    state.assign.side_effect = lambda _cfg, dc: state.assigned.append(dc)
    state.unassign.side_effect = lambda _cfg, dc: state.assigned.remove(dc)
    monkeypatch.setattr(devices, "parse_entries", lambda _entries: list(state.assigned))
    monkeypatch.setattr(devices, "list_host_usb", lambda: list(state.usb))
    monkeypatch.setattr(devices, "assign_device", state.assign)
    monkeypatch.setattr(devices, "unassign_device", state.unassign)
    monkeypatch.setattr(devices, "live_attach", state.attach)
    monkeypatch.setattr(devices, "live_detach", state.detach)
    monkeypatch.setattr("winpodx.core.daemon.cleanup_lock_files", state.cleanup)
    monkeypatch.setattr("winpodx.core.daemon.sync_windows_time", state.timesync)
    monkeypatch.setattr("winpodx.core.daemon.suspend_pod", state.suspend)
    monkeypatch.setattr("winpodx.core.daemon.run_icon_refresh_monitor", state.icon_monitor)
    monkeypatch.setattr("winpodx.core.daemon.run_session_window_reaper", state.session_reaper)
    monkeypatch.setattr("winpodx.cli.host_open.ensure_listener_running", state.ensure_listener)
    monkeypatch.setattr("PySide6.QtDBus.QDBusConnection.systemBus", lambda: bus)

    with pytest.raises(SystemExit) as exited:
        tray.run_tray()
    assert exited.value.code == 0
    captured = CapturingTray.instance
    assert captured is not None
    state.tray = captured
    state.menu = captured.contextMenu()
    state.cfg = cfg
    state.app = qapp
    state.bus = bus
    state.timer = next(timer for timer in _FakeTimer.instances if timer.interval == 30000)
    yield state
    captured.hide()
    captured.setContextMenu(None)
    qapp.processEvents()


def test_run_tray_builds_status_apps_and_background_services(tray_runtime) -> None:
    assert _find_action(tray_runtime.menu, "Pod: stopped") is not None
    assert _find_action(tray_runtime.menu, "Microsoft Word") is not None
    assert _find_action(tray_runtime.menu, "Display: wayland / KDE") is not None
    assert tray_runtime.ensure_listener.call_args_list == [call(tray_runtime.cfg)]
    assert tray_runtime.icon_monitor.call_count == 1
    assert tray_runtime.session_reaper.call_count == 1
    assert tray_runtime.bus.connection[3:5] == ("PrepareForSleep", "b")


def test_pod_actions_call_start_stop_and_restart_with_loaded_config(tray_runtime) -> None:
    _find_action(tray_runtime.menu, "Start Pod").trigger()
    tray_runtime.pod_state = __import__("winpodx.core.pod", fromlist=["PodState"]).PodState.RUNNING
    tray_runtime.timer.timeout.emit()
    _find_action(tray_runtime.menu, "Stop Pod").trigger()
    _find_action(tray_runtime.menu, "Restart Pod").trigger()

    assert tray_runtime.start.call_args_list == [call(tray_runtime.cfg), call(tray_runtime.cfg)]
    assert tray_runtime.stop.call_args_list == [call(tray_runtime.cfg), call(tray_runtime.cfg)]
    assert _find_action(tray_runtime.menu, "Pod: running (10.0.0.2)") is not None


def test_app_desktop_and_double_click_launch_real_handlers(tray_runtime) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    _find_action(tray_runtime.menu, "Microsoft Word").trigger()
    _find_action(tray_runtime.menu, "Full Desktop").trigger()
    tray_runtime.tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)

    assert tray_runtime.launch.call_args_list == [
        call(
            tray_runtime.cfg,
            r"C:\Office\word.exe",
            launch_uri=None,
            wm_class_hint=None,
            default_args=None,
            app_icon=None,
            rdp_overrides=None,
        ),
        call(tray_runtime.cfg),
        call(tray_runtime.cfg),
    ]
    messages = [(title, message) for title, message, _icon, _timeout in tray_runtime.tray.messages]
    assert ("WinPodX", "Launching Microsoft Word...") in messages
    assert ("WinPodX", "Opening desktop...") in messages


def test_sessions_submenu_rebuilds_and_kills_selected_session(tray_runtime) -> None:
    sessions_menu = _find_menu(tray_runtime.menu, "Terminate Session")
    assert [action.text() for action in sessions_menu.actions()] == ["(no active sessions)"]
    tray_runtime.sessions = [SimpleNamespace(app_name="Microsoft Word")]

    sessions_menu.aboutToShow.emit()
    _find_action(sessions_menu, "Terminate: Microsoft Word").trigger()

    assert [action.text() for action in sessions_menu.actions()] == ["Terminate: Microsoft Word"]
    assert tray_runtime.kill.call_args_list == [call("Microsoft Word")]


def test_devices_submenu_rebuilds_and_toggles_live_device(tray_runtime) -> None:
    from winpodx.core.devices import DeviceConfig, HostDevice
    from winpodx.core.pod import PodState

    devices_menu = _find_menu(tray_runtime.menu, "USB Devices")
    host = HostDevice("usb", "1234:5678", "Webcam")
    tray_runtime.usb = [host]
    tray_runtime.pod_state = PodState.RUNNING
    devices_menu.aboutToShow.emit()
    device_action = _find_action(devices_menu, "Webcam")

    device_action.trigger()
    action_after_rebuild = _find_action(devices_menu, "Webcam")
    action_after_rebuild.trigger()

    expected = DeviceConfig("usb", "1234:5678", "Webcam")
    assert tray_runtime.assign.call_args_list == [call(tray_runtime.cfg, expected)]
    assert tray_runtime.attach.call_args_list == [call("podman", "winpodx", expected)]
    assert tray_runtime.unassign.call_args_list == [call(tray_runtime.cfg, expected)]
    assert tray_runtime.detach.call_args_list == [call("podman", "winpodx", expected)]


def test_maintenance_actions_report_observable_results(tray_runtime) -> None:
    _find_action(tray_runtime.menu, "Clean Lock Files").trigger()
    _find_action(tray_runtime.menu, "Sync Windows Time").trigger()
    _find_action(tray_runtime.menu, "Suspend Pod").trigger()

    assert tray_runtime.cleanup.call_count == 1
    assert tray_runtime.timesync.call_args_list == [call(tray_runtime.cfg)]
    assert tray_runtime.suspend.call_args_list == [call(tray_runtime.cfg)]
    messages = [message for _title, message, _icon, _timeout in tray_runtime.tray.messages]
    assert "Removed 2 lock files" in messages
    assert "Time synced" in messages
    assert "Pod suspended" in messages


def test_timer_refreshes_status_sessions_devices_and_visibility(tray_runtime) -> None:
    from winpodx.core.devices import HostDevice
    from winpodx.core.pod import PodState

    tray_runtime.pod_state = PodState.RUNNING
    tray_runtime.sessions = [SimpleNamespace(app_name="Calculator")]
    tray_runtime.usb = [HostDevice("usb", "abcd:1234", "Keyboard")]
    shows_before = tray_runtime.tray.show_count

    tray_runtime.timer.timeout.emit()

    assert _find_action(tray_runtime.menu, "Pod: running (10.0.0.2)") is not None
    assert _find_action(tray_runtime.menu, "Sessions: 1") is not None
    assert _find_action(tray_runtime.menu, "Terminate: Calculator") is not None
    assert _find_action(tray_runtime.menu, "Keyboard") is not None
    assert tray_runtime.tray.show_count == shows_before + 1


def test_sleep_listener_schedules_refresh_only_after_resume(tray_runtime) -> None:
    listener = tray_runtime.tray._winpodx_sleep_listener
    shows_before = tray_runtime.tray.show_count

    listener.onPrepareForSleep(True)
    assert tray_runtime.tray.show_count == shows_before
    listener.onPrepareForSleep(False)
    assert tray_runtime.tray.show_count == shows_before + 1


def test_unresponsive_transition_recovers_and_notifies(
    tray_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from winpodx.core.pod import PodState
    from winpodx.core.pod.recovery import RecoveryAction, RecoveryResult

    recovered = MagicMock()
    unresponsive = MagicMock()
    monkeypatch.setattr("winpodx.desktop.tray_spawn._install_in_progress", lambda: False)
    monkeypatch.setattr(
        "winpodx.core.pod.recovery.try_recover_rdp",
        lambda _cfg: RecoveryResult(True, RecoveryAction.RESTARTED_TERMSERVICE),
    )
    monkeypatch.setattr("winpodx.desktop.notify.notify_pod_unresponsive", unresponsive)
    monkeypatch.setattr("winpodx.desktop.notify.notify_pod_recovered", recovered)

    tray_runtime.pod_state = PodState.UNRESPONSIVE
    tray_runtime.timer.timeout.emit()

    assert _find_action(tray_runtime.menu, "Pod: unresponsive") is not None
    assert _find_action(tray_runtime.menu, "Restart Pod").isEnabled()
    assert unresponsive.call_args_list == [call("10.0.0.2")]
    assert recovered.call_count == 1


def test_recovery_failure_notification_includes_action_and_detail(
    tray_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from winpodx.core.pod import PodState
    from winpodx.core.pod.recovery import RecoveryAction, RecoveryResult

    manual_restart = MagicMock()
    monkeypatch.setattr("winpodx.desktop.tray_spawn._install_in_progress", lambda: False)
    monkeypatch.setattr(
        "winpodx.core.pod.recovery.try_recover_rdp",
        lambda _cfg: RecoveryResult(False, RecoveryAction.RDP_STILL_DOWN, "guest asleep"),
    )
    monkeypatch.setattr("winpodx.desktop.notify.notify_pod_unresponsive", MagicMock())
    monkeypatch.setattr("winpodx.desktop.notify.notify_pod_needs_manual_restart", manual_restart)

    tray_runtime.pod_state = PodState.UNRESPONSIVE
    tray_runtime.timer.timeout.emit()

    assert manual_restart.call_args_list == [
        call("RDP still down after TermService restart — guest asleep")
    ]


def test_autostart_resumes_a_paused_pod(tray_runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    paused = MagicMock(return_value=True)
    resume = MagicMock()
    monkeypatch.setattr("winpodx.core.daemon.is_pod_paused", paused)
    monkeypatch.setattr("winpodx.core.daemon.resume_pod", resume)
    tray_runtime.cfg.pod.auto_start = True

    autostart = next(
        thread.target
        for thread in _ImmediateThread.instances
        if thread.target.__name__ == "_autostart_pod"
    )
    autostart()

    assert paused.call_args_list == [call(tray_runtime.cfg)]
    assert resume.call_args_list == [call(tray_runtime.cfg)]
    assert tray_runtime.start.call_count == 0


def test_visibility_retry_reshows_icon_when_host_appears(tray_runtime) -> None:
    retry_timer = next(timer for timer in _FakeTimer.instances if timer.interval is None)
    tray_runtime.tray_available = False
    retry_timer.timeout.emit()
    assert not retry_timer.stopped

    shows_before = tray_runtime.tray.show_count
    tray_runtime.tray_available = True
    retry_timer.timeout.emit()

    assert tray_runtime.tray.show_count == shows_before + 1
    assert retry_timer.stopped


def test_dashboard_launch_failure_shows_warning(
    tray_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_to_spawn(*_args, **_kwargs):
        raise OSError("launcher unavailable")

    monkeypatch.setattr("subprocess.Popen", fail_to_spawn)

    _find_action(tray_runtime.menu, "Open Dashboard").trigger()

    title, message, _icon, _timeout = tray_runtime.tray.messages[-1]
    assert title == "WinPodX"
    assert message == "Could not open dashboard: launcher unavailable"


def test_app_and_desktop_launch_failures_show_critical_messages(tray_runtime) -> None:
    tray_runtime.launch.side_effect = RuntimeError("RDP unavailable")

    _find_action(tray_runtime.menu, "Microsoft Word").trigger()
    _find_action(tray_runtime.menu, "Full Desktop").trigger()

    messages = [(title, message) for title, message, _icon, _timeout in tray_runtime.tray.messages]
    assert messages[-2:] == [
        ("WinPodX Error", "RDP unavailable"),
        ("WinPodX Error", "RDP unavailable"),
    ]


def test_double_click_launch_failure_is_reported_without_escaping(tray_runtime) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    tray_runtime.launch.side_effect = RuntimeError("guest offline")

    tray_runtime.tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)

    title, message, _icon, _timeout = tray_runtime.tray.messages[-1]
    assert (title, message) == ("WinPodX Error", "guest offline")


def test_recovery_worker_exception_requests_manual_restart(
    tray_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from winpodx.core.pod import PodState

    manual_restart = MagicMock()

    def crash(_cfg):
        raise RuntimeError("agent transport crashed")

    monkeypatch.setattr("winpodx.desktop.tray_spawn._install_in_progress", lambda: False)
    monkeypatch.setattr("winpodx.core.pod.recovery.try_recover_rdp", crash)
    monkeypatch.setattr("winpodx.desktop.notify.notify_pod_unresponsive", MagicMock())
    monkeypatch.setattr("winpodx.desktop.notify.notify_pod_needs_manual_restart", manual_restart)

    tray_runtime.pod_state = PodState.UNRESPONSIVE
    tray_runtime.timer.timeout.emit()

    assert manual_restart.call_args_list == [call("recovery worker error: agent transport crashed")]


def test_quit_declined_keeps_pod_and_application_running(
    tray_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    quit_app = MagicMock()
    monkeypatch.setattr(tray_runtime.app, "quit", quit_app)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    _find_action(tray_runtime.menu, "Quit WinPodX").trigger()

    assert tray_runtime.stop.call_count == 0
    assert quit_app.call_count == 0


def test_quit_confirmed_stops_services_and_application(
    tray_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    quit_app = MagicMock()
    stop_listener = MagicMock()
    run = MagicMock()
    monkeypatch.setattr(tray_runtime.app, "quit", quit_app)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr("winpodx.reverse_open.lifecycle.stop_listener", stop_listener)
    monkeypatch.setattr("subprocess.run", run)

    _find_action(tray_runtime.menu, "Quit WinPodX").trigger()

    assert tray_runtime.stop.call_args_list == [call(tray_runtime.cfg)]
    assert stop_listener.call_count == 1
    assert run.call_args.args[0] == ["pkill", "-f", r"python.*winpodx.*gui"]
    assert quit_app.call_count == 1
