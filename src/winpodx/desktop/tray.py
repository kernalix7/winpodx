"""System tray icon using Qt (QSystemTrayIcon)."""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def run_tray() -> None:
    """Launch the system tray icon application."""
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError:
        print("PySide6 required. Install with: pip install winpodx[gui]")
        sys.exit(1)

    from winpodx.core.app import list_available_apps
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, pod_status, start_pod, stop_pod
    from winpodx.core.process import list_active_sessions
    from winpodx.core.rdp import launch_app
    from winpodx.display.detector import display_info

    app = QApplication(sys.argv)
    app.setApplicationName("winpodx")
    app.setQuitOnLastWindowClosed(False)

    tray = QSystemTrayIcon()
    tray.setToolTip("winpodx - Windows App Integration")

    menu = QMenu()

    status_action = QAction("Status: checking...")
    status_action.setEnabled(False)
    menu.addAction(status_action)

    sessions_action = QAction("Sessions: 0")
    sessions_action.setEnabled(False)
    menu.addAction(sessions_action)

    menu.addSeparator()

    start_action = QAction("Start Pod")
    stop_action = QAction("Stop Pod")
    restart_action = QAction("Restart Pod")

    def refresh_status() -> None:
        cfg = Config.load()
        try:
            s = pod_status(cfg)
            state_text = s.state.value
            if s.state == PodState.RUNNING:
                state_text += f" ({s.ip})"
            status_action.setText(f"Pod: {state_text}")
            start_action.setEnabled(s.state == PodState.STOPPED)
            stop_action.setEnabled(s.state == PodState.RUNNING)
            restart_action.setEnabled(s.state == PodState.RUNNING)
        except Exception as e:
            log.warning("Failed to get pod status: %s", e)
            status_action.setText("Pod: error")

        active = list_active_sessions()
        sessions_action.setText(f"Sessions: {len(active)}")

    def _run_in_thread(fn, success_msg: str, error_msg: str) -> None:
        """Run a pod operation in a background thread to avoid blocking UI."""
        import threading

        def wrapper() -> None:
            try:
                result = fn()
                if hasattr(result, "error") and result.error:
                    QTimer.singleShot(
                        0,
                        lambda: tray.showMessage(
                            "winpodx",
                            f"{error_msg}: {result.error}",
                            QSystemTrayIcon.MessageIcon.Critical,
                        ),
                    )
                else:
                    QTimer.singleShot(
                        0,
                        lambda: tray.showMessage(
                            "winpodx",
                            success_msg,
                            QSystemTrayIcon.MessageIcon.Information,
                        ),
                    )
            except Exception:
                import traceback

                err_detail = traceback.format_exc().splitlines()[-1]
                QTimer.singleShot(
                    0,
                    lambda msg=err_detail: tray.showMessage(
                        "winpodx",
                        f"{error_msg}: {msg}",
                        QSystemTrayIcon.MessageIcon.Critical,
                    ),
                )
            QTimer.singleShot(0, refresh_status)

        threading.Thread(target=wrapper, daemon=True).start()

    def on_start() -> None:
        cfg = Config.load()
        _run_in_thread(lambda: start_pod(cfg), "Pod started", "Failed to start pod")

    def on_stop() -> None:
        cfg = Config.load()
        _run_in_thread(lambda: stop_pod(cfg), "Pod stopped", "Failed to stop pod")

    def on_restart() -> None:
        cfg = Config.load()

        def _restart():
            stop_pod(cfg)
            return start_pod(cfg)

        _run_in_thread(_restart, "Pod restarted", "Failed to restart pod")

    start_action.triggered.connect(on_start)
    stop_action.triggered.connect(on_stop)
    restart_action.triggered.connect(on_restart)

    menu.addAction(start_action)
    menu.addAction(stop_action)
    menu.addAction(restart_action)

    menu.addSeparator()

    apps_menu = QMenu("Launch App")
    available_apps = list_available_apps()

    def make_launcher(executable: str, full_name: str):
        def launcher() -> None:
            cfg = Config.load()
            try:
                launch_app(cfg, executable)
                tray.showMessage(
                    "winpodx",
                    f"Launching {full_name}...",
                    QSystemTrayIcon.MessageIcon.Information,
                )
            except RuntimeError as e:
                tray.showMessage(
                    "winpodx Error",
                    str(e),
                    QSystemTrayIcon.MessageIcon.Critical,
                )
            QTimer.singleShot(2000, refresh_status)

        return launcher

    for app_info in available_apps[:20]:
        action = QAction(app_info.full_name)
        action.triggered.connect(make_launcher(app_info.executable, app_info.full_name))
        apps_menu.addAction(action)

    if not available_apps:
        no_apps = QAction("(no apps - run 'winpodx setup')")
        no_apps.setEnabled(False)
        apps_menu.addAction(no_apps)

    apps_menu.addSeparator()
    desktop_action = QAction("Full Desktop")

    def on_desktop() -> None:
        try:
            launch_app(Config.load())
            tray.showMessage(
                "winpodx", "Opening desktop...", QSystemTrayIcon.MessageIcon.Information
            )
        except RuntimeError as e:
            tray.showMessage("winpodx Error", str(e), QSystemTrayIcon.MessageIcon.Critical)

    desktop_action.triggered.connect(on_desktop)
    apps_menu.addAction(desktop_action)

    menu.addMenu(apps_menu)

    menu.addSeparator()

    info = display_info()
    info_action = QAction(f"Display: {info['session_type']} / {info['desktop_environment']}")
    info_action.setEnabled(False)
    menu.addAction(info_action)

    menu.addSeparator()

    maint_menu = QMenu("Maintenance")

    cleanup_action = QAction("Clean Lock Files")

    def on_cleanup() -> None:
        from winpodx.core.daemon import cleanup_lock_files

        removed = cleanup_lock_files()
        msg = f"Removed {len(removed)} lock files" if removed else "No lock files found"
        tray.showMessage("winpodx", msg, QSystemTrayIcon.MessageIcon.Information)

    cleanup_action.triggered.connect(on_cleanup)
    maint_menu.addAction(cleanup_action)

    timesync_action = QAction("Sync Windows Time")

    def on_timesync() -> None:
        from winpodx.core.daemon import sync_windows_time

        ok = sync_windows_time(Config.load())
        msg = "Time synced" if ok else "Time sync failed"
        tray.showMessage("winpodx", msg, QSystemTrayIcon.MessageIcon.Information)

    timesync_action.triggered.connect(on_timesync)
    maint_menu.addAction(timesync_action)

    suspend_action = QAction("Suspend Pod")

    def on_suspend() -> None:
        from winpodx.core.daemon import suspend_pod

        suspend_pod(Config.load())
        tray.showMessage("winpodx", "Pod suspended", QSystemTrayIcon.MessageIcon.Information)
        refresh_status()

    suspend_action.triggered.connect(on_suspend)
    maint_menu.addAction(suspend_action)

    menu.addMenu(maint_menu)

    menu.addSeparator()

    quit_action = QAction("Quit")
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.show()

    refresh_status()

    timer = QTimer()
    timer.timeout.connect(refresh_status)
    timer.start(30000)

    import threading

    idle_stop = threading.Event()
    cfg = Config.load()
    if cfg.pod.idle_timeout > 0:
        from winpodx.core.daemon import run_idle_monitor

        threading.Thread(
            target=run_idle_monitor,
            args=(cfg, idle_stop),
            daemon=True,
        ).start()

    def on_tray_activate(reason: int) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            try:
                launch_app(Config.load())
            except RuntimeError as e:
                tray.showMessage("winpodx Error", str(e), QSystemTrayIcon.MessageIcon.Critical)

    tray.activated.connect(on_tray_activate)

    app.aboutToQuit.connect(idle_stop.set)
    sys.exit(app.exec())
