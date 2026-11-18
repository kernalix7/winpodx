# SPDX-License-Identifier: MIT
"""Tray locking, menu behavior, and the asynchronous launch/quit lifecycle.

``desktop/tray.py`` keeps everything except ``_acquire_tray_lock`` nested
inside ``run_tray()``, so the flock is the only import-testable unit -- and it
is load-bearing: it is the second line of defence against a duplicate tray
when ``pgrep`` is unavailable (``tray_spawn`` runs the first).

The behavioural tests drive the real ``run_tray()`` closures against an
offscreen QApplication. Launching from the tray must preflight pod readiness on
a background worker without blocking the Qt event loop, deliver results through
queued signal delivery on the GUI thread, and finish in-flight readiness before
the final stop on Quit (#901).

"""

from __future__ import annotations

import fcntl
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from winpodx.desktop import tray
from winpodx.desktop.tray_spawn import _install_in_progress as _read_install_marker

_REAL_THREAD = threading.Thread


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

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        pass


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
        ready=MagicMock(),
        launch=MagicMock(),
        kill=MagicMock(return_value=True),
        assign=MagicMock(),
        notification_threads=[],
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
            state.notification_threads.append(QtCore.QThread.currentThread())

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
    state.ready.return_value = cfg
    state.real_timer = QtCore.QTimer
    monkeypatch.setattr(tray, "_acquire_tray_lock", lambda: True)
    monkeypatch.setattr(tray, "tr", lambda text: text)
    monkeypatch.setattr(QtCore, "QTimer", _FakeTimer)
    monkeypatch.setattr(QtWidgets, "QSystemTrayIcon", CapturingTray)
    monkeypatch.setattr(QtWidgets.QApplication, "exec", lambda _self: 0)
    # run_tray() builds QApplication(sys.argv) unconditionally; Qt permits one per
    # process, so any earlier GUI test in the same xdist worker makes it raise.
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    state.gui_thread = QtCore.QThread.currentThread()
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
    monkeypatch.setattr("winpodx.core.provisioner.ensure_ready", state.ready)
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
    state.menu.close()
    state.menu.deleteLater()
    captured._winpodx_sleep_listener.deleteLater()
    captured.deleteLater()
    qapp.processEvents()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)


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


@pytest.fixture
def installation_recovery(
    tray_runtime: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    from winpodx.core.pod.recovery import RecoveryAction, RecoveryResult

    marker = Path(os.environ["XDG_CONFIG_HOME"]) / "winpodx" / ".install_in_progress"
    marker.parent.mkdir(parents=True, exist_ok=True)
    recovery = MagicMock(return_value=RecoveryResult(True, RecoveryAction.RESTARTED_TERMSERVICE))
    notifications = MagicMock()
    monkeypatch.setattr("winpodx.desktop.tray_spawn._install_in_progress", _read_install_marker)
    monkeypatch.setattr("winpodx.core.pod.recovery.try_recover_rdp", recovery)
    monkeypatch.setattr(
        "winpodx.desktop.notify.notify_pod_unresponsive", notifications.unresponsive
    )
    monkeypatch.setattr("winpodx.desktop.notify.notify_pod_recovered", notifications.recovered)
    monkeypatch.setattr(
        "winpodx.desktop.notify.notify_pod_needs_manual_restart", notifications.manual_restart
    )
    return SimpleNamespace(
        runtime=tray_runtime, marker=marker, recover=recovery, notify=notifications
    )


def test_auto_recovery_skips_when_installation_active(
    installation_recovery: SimpleNamespace,
) -> None:
    from winpodx.core.pod import PodState

    installation_recovery.marker.write_text(str(os.getpid()), encoding="utf-8")
    runtime = installation_recovery.runtime
    runtime.pod_state = PodState.UNRESPONSIVE

    runtime.timer.timeout.emit()

    installation_recovery.recover.assert_not_called()
    assert installation_recovery.notify.mock_calls == []
    assert runtime.tray.messages == []


def test_auto_recovery_resumes_when_installation_ends_while_unresponsive(
    installation_recovery: SimpleNamespace,
) -> None:
    from winpodx.core.pod import PodState

    # Given a suppressed poll during installation, with RDP still unresponsive.
    installation_recovery.marker.write_text(str(os.getpid()), encoding="utf-8")
    runtime = installation_recovery.runtime
    runtime.pod_state = PodState.UNRESPONSIVE
    runtime.timer.timeout.emit()
    installation_recovery.recover.assert_not_called()
    assert installation_recovery.notify.mock_calls == []

    # When installation ends without an intervening RUNNING status.
    installation_recovery.marker.unlink()
    runtime.timer.timeout.emit()

    # Then the next poll can recover and notify normally.
    installation_recovery.recover.assert_called_once_with(runtime.cfg)
    assert installation_recovery.notify.mock_calls == [
        call.unresponsive("10.0.0.2"),
        call.recovered(),
    ]


@pytest.mark.parametrize("success", [True, False], ids=["success", "failure"])
def test_auto_recovery_suppresses_late_result_when_installation_starts(
    installation_recovery: SimpleNamespace, success: bool
) -> None:
    from winpodx.core.pod import PodState
    from winpodx.core.pod.recovery import RecoveryAction, RecoveryResult

    # Given recovery that starts before installation but returns after it starts.
    def recover(_cfg: SimpleNamespace) -> RecoveryResult:
        installation_recovery.marker.write_text(str(os.getpid()), encoding="utf-8")
        action = RecoveryAction.RESTARTED_TERMSERVICE if success else RecoveryAction.RDP_STILL_DOWN
        return RecoveryResult(success, action)

    installation_recovery.recover.side_effect = recover
    runtime = installation_recovery.runtime
    runtime.pod_state = PodState.UNRESPONSIVE

    runtime.timer.timeout.emit()

    installation_recovery.recover.assert_called_once_with(runtime.cfg)
    assert installation_recovery.notify.mock_calls == [call.unresponsive("10.0.0.2")]
    assert runtime.tray.messages == []


def test_auto_recovery_suppresses_late_exception_when_installation_starts(
    installation_recovery: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    from winpodx.core.pod import PodState
    from winpodx.core.pod.recovery import RecoveryResult

    failure = RuntimeError("agent transport crashed")

    def recover(_cfg: SimpleNamespace) -> RecoveryResult:
        installation_recovery.marker.write_text(str(os.getpid()), encoding="utf-8")
        raise failure

    installation_recovery.recover.side_effect = recover
    runtime = installation_recovery.runtime
    runtime.pod_state = PodState.UNRESPONSIVE

    with caplog.at_level(logging.WARNING, logger=tray.__name__):
        runtime.timer.timeout.emit()

    installation_recovery.recover.assert_called_once_with(runtime.cfg)
    assert any(
        record.name == tray.__name__ and str(failure) in record.getMessage()
        for record in caplog.records
    )
    assert installation_recovery.notify.mock_calls == [call.unresponsive("10.0.0.2")]
    assert runtime.tray.messages == []


@pytest.mark.parametrize(
    ("success", "crash"),
    [(True, False), (False, False), (False, True)],
    ids=["success", "failure", "exception"],
)
def test_auto_recovery_can_run_again_after_installation_ends(
    installation_recovery: SimpleNamespace, success: bool, crash: bool
) -> None:
    from winpodx.core.pod import PodState
    from winpodx.core.pod.recovery import RecoveryAction, RecoveryResult

    def recover(_cfg: SimpleNamespace) -> RecoveryResult:
        installation_recovery.marker.write_text(str(os.getpid()), encoding="utf-8")
        if crash:
            raise RuntimeError("agent transport crashed")
        action = RecoveryAction.RESTARTED_TERMSERVICE if success else RecoveryAction.RDP_STILL_DOWN
        return RecoveryResult(success, action)

    # Given a completed recovery attempt followed by an installation-active poll.
    installation_recovery.recover.side_effect = recover
    runtime = installation_recovery.runtime
    runtime.pod_state = PodState.UNRESPONSIVE
    runtime.timer.timeout.emit()
    runtime.timer.timeout.emit()
    installation_recovery.recover.assert_called_once_with(runtime.cfg)
    installation_recovery.notify.reset_mock()

    # When installation ends, with RDP still down and recovery now able to succeed.
    installation_recovery.marker.unlink()
    installation_recovery.recover.side_effect = None
    runtime.timer.timeout.emit()

    # Then no stale inflight state blocks a new recovery or its normal notifications.
    assert installation_recovery.recover.call_args_list == [call(runtime.cfg), call(runtime.cfg)]
    assert installation_recovery.notify.mock_calls == [
        call.unresponsive("10.0.0.2"),
        call.recovered(),
    ]


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


@pytest.mark.parametrize("entry", ["Microsoft Word", "Full Desktop", "double-click"])
def test_rdp_failure_after_readiness_is_reported_without_escaping(
    tray_runtime, entry: str, caplog: pytest.LogCaptureFixture
) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    # The fixture worker runs inline, so an uncaught failure would escape
    # through the Qt slot and abort the tray event loop.
    tray_runtime.launch.side_effect = RuntimeError("guest offline")

    with caplog.at_level(logging.WARNING, logger=tray.__name__):
        if entry == "double-click":
            tray_runtime.tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
        else:
            _find_action(tray_runtime.menu, entry).trigger()

    assert tray_runtime.ready.call_count == 1
    assert tray_runtime.launch.call_count == 1
    _title, message, icon, _timeout = tray_runtime.tray.messages[-1]
    assert icon == QSystemTrayIcon.MessageIcon.Critical
    assert "guest offline" in message
    assert "guest offline" in caplog.text


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

    order: list[str] = []
    quit_app = MagicMock(side_effect=lambda: order.append("quit"))
    stop_listener = MagicMock(side_effect=lambda: order.append("listener"))
    pkill = MagicMock(side_effect=lambda *_args, **_kwargs: order.append("pkill"))
    tray_runtime.stop.side_effect = lambda _cfg: order.append("pod")
    monkeypatch.setattr(tray_runtime.app, "quit", quit_app)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr("winpodx.reverse_open.lifecycle.stop_listener", stop_listener)
    monkeypatch.setattr("subprocess.run", pkill)

    _find_action(tray_runtime.menu, "Quit WinPodX").trigger()

    # #691: the listener is stopped deliberately and before the collateral pkill.
    assert order == ["pod", "listener", "pkill", "quit"]
    assert tray_runtime.stop.call_args_list == [call(tray_runtime.cfg)]
    assert pkill.call_args.args[0] == ["pkill", "-f", r"python.*winpodx.*gui"]
    assert not tray_runtime.menu.isEnabled()


# --- #901: readiness preflight, GUI-thread results, ordered shutdown ---------


def _use_real_threads(monkeypatch: pytest.MonkeyPatch) -> list[threading.Thread]:
    """Undo the fixture's inline worker so tray work really runs off the GUI thread."""
    threads: list[threading.Thread] = []

    def create(*args, **kwargs):
        thread = _REAL_THREAD(*args, **kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(threading, "Thread", create)
    return threads


def _drop_threads(threads: list[threading.Thread]) -> None:
    for thread in threads:
        thread.join(10)


def _wait_until(app, predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert predicate(), "timed out waiting for the tray to settle"


def _trigger_entry(tray_runtime: SimpleNamespace, entry: str) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    if entry == "double-click":
        tray_runtime.tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
    else:
        _find_action(tray_runtime.menu, entry).trigger()


@pytest.mark.parametrize("entry", ["Microsoft Word", "Full Desktop", "double-click"])
def test_launch_preflights_readiness_before_opening_rdp(
    tray_runtime,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    from winpodx.core.pod import PodState

    entered = threading.Event()
    release = threading.Event()
    launched_states: list[PodState] = []
    threads = _use_real_threads(monkeypatch)

    def ready(cfg):
        entered.set()
        release.wait(10)
        tray_runtime.pod_state = PodState.RUNNING
        return tray_runtime.cfg

    def open_session(_cfg, *_args, **_kwargs):
        launched_states.append(tray_runtime.pod_state)

    tray_runtime.ready.side_effect = ready
    tray_runtime.launch.side_effect = open_session
    try:
        _trigger_entry(tray_runtime, entry)
        assert entered.wait(10)

        # A blocked preflight must not stall the Qt event loop.
        heartbeat: list[bool] = []
        tray_runtime.real_timer.singleShot(0, lambda: heartbeat.append(True))
        _wait_until(tray_runtime.app, lambda: bool(heartbeat))

        assert tray_runtime.launch.call_count == 0
        assert tray_runtime.ready.call_count == 1
        release.set()
        _wait_until(tray_runtime.app, lambda: bool(launched_states))
        _wait_until(tray_runtime.app, lambda: bool(tray_runtime.tray.messages))

        assert launched_states == [PodState.RUNNING]
        assert tray_runtime.tray.messages
        assert all(
            thread is tray_runtime.gui_thread for thread in tray_runtime.notification_threads
        )
    finally:
        release.set()
        _drop_threads(threads)
        tray_runtime.app.processEvents()


def test_readiness_failure_prevents_rdp_and_is_reported_on_the_gui_thread(
    tray_runtime, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    from winpodx.core.provisioner import ProvisionError

    threads = _use_real_threads(monkeypatch)

    def fail(_cfg):
        raise ProvisionError("guest readiness unavailable")

    tray_runtime.ready.side_effect = fail
    try:
        with caplog.at_level(logging.WARNING, logger=tray.__name__):
            _trigger_entry(tray_runtime, "Full Desktop")
            _wait_until(tray_runtime.app, lambda: bool(tray_runtime.tray.messages))

        tray_runtime.launch.assert_not_called()
        _title, message, icon, _timeout = tray_runtime.tray.messages[-1]
        assert icon == QSystemTrayIcon.MessageIcon.Critical
        assert "guest readiness unavailable" in message
        assert "guest readiness unavailable" in caplog.text
        assert len(tray_runtime.notification_threads) == 1
        assert tray_runtime.notification_threads[0] is tray_runtime.gui_thread
    finally:
        _drop_threads(threads)
        tray_runtime.app.processEvents()


def test_tray_launches_serialize_readiness_and_rdp(
    tray_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from winpodx.core.pod import PodState

    state_lock = threading.Lock()
    second_entered = threading.Event()
    order: list[str] = []
    ready_calls = 0
    launches = 0
    inflight = 0
    peak_inflight = 0
    threads = _use_real_threads(monkeypatch)

    def ready(_cfg):
        nonlocal ready_calls, inflight, peak_inflight
        with state_lock:
            call = ready_calls
            ready_calls += 1
            inflight += 1
            peak_inflight = max(peak_inflight, inflight)
            if call:
                second_entered.set()
        try:
            if call == 0:
                # Serialized launches keep the second worker blocked on the
                # launch lock, so this times out. A racing preflight would
                # enter here at the same time and set the event immediately.
                second_entered.wait(0.5)
            order.append(f"readiness-{call}")
            tray_runtime.pod_state = PodState.RUNNING
            return tray_runtime.cfg
        finally:
            with state_lock:
                inflight -= 1

    def open_session(_cfg, *_args, **_kwargs):
        nonlocal launches, inflight, peak_inflight
        with state_lock:
            inflight += 1
            peak_inflight = max(peak_inflight, inflight)
        order.append("rdp")
        launches += 1
        with state_lock:
            inflight -= 1

    tray_runtime.ready.side_effect = ready
    tray_runtime.launch.side_effect = open_session
    try:
        _trigger_entry(tray_runtime, "Microsoft Word")
        _trigger_entry(tray_runtime, "Full Desktop")
        _wait_until(tray_runtime.app, lambda: launches == 2)

        assert peak_inflight == 1
        assert order == ["readiness-0", "rdp", "readiness-1", "rdp"]
    finally:
        _drop_threads(threads)
        tray_runtime.app.processEvents()


def test_quit_waits_for_in_flight_readiness_before_final_stop(
    tray_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QMessageBox

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    order: list[str] = []
    threads = _use_real_threads(monkeypatch)

    def ready(_cfg):
        entered.set()
        release.wait(10)
        order.append("readiness")
        return tray_runtime.cfg

    def quit_app() -> None:
        order.append("quit")
        finished.set()

    tray_runtime.ready.side_effect = ready
    tray_runtime.stop.side_effect = lambda _cfg: order.append("pod-stop")
    monkeypatch.setattr(tray_runtime.app, "quit", quit_app)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        "winpodx.reverse_open.lifecycle.stop_listener",
        lambda: order.append("listener-stop"),
    )
    monkeypatch.setattr("subprocess.run", lambda *_args, **_kwargs: order.append("pkill"))
    try:
        _trigger_entry(tray_runtime, "Full Desktop")
        assert entered.wait(10)
        _find_action(tray_runtime.menu, "Quit WinPodX").trigger()
        assert not tray_runtime.menu.isEnabled()

        # Quit must keep the GUI alive and refuse new launches while it waits.
        _trigger_entry(tray_runtime, "double-click")
        heartbeat: list[bool] = []
        tray_runtime.real_timer.singleShot(0, lambda: heartbeat.append(True))
        _wait_until(tray_runtime.app, lambda: bool(heartbeat))
        assert order == []

        release.set()
        _wait_until(tray_runtime.app, finished.is_set)

        # The final stop happened after readiness, and its result never launched.
        assert order == ["readiness", "pod-stop", "listener-stop", "pkill", "quit"]
        tray_runtime.launch.assert_not_called()
        assert tray_runtime.ready.call_count == 1
    finally:
        release.set()
        _drop_threads(threads)
        tray_runtime.app.processEvents()
