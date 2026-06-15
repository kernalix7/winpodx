# SPDX-License-Identifier: MIT
"""System tray icon using Qt (QSystemTrayIcon)."""

from __future__ import annotations

import logging
import sys

from winpodx.core.i18n import tr

log = logging.getLogger(__name__)


_TRAY_LOCK_FH = None  # held for the lifetime of the tray process


def _acquire_tray_lock() -> bool:
    """Return True if we got the tray flock, False if another tray owns it.

    The lockfile lives under ``$XDG_RUNTIME_DIR/winpodx/`` (falls back to
    ``~/.config/winpodx/``); the file handle is kept on the module so
    the lock survives until the process exits. ``GUI`` calls
    ``_maybe_spawn_tray`` which already does a ``pgrep`` pre-check, so
    this lock is the second line of defence -- catches the case where
    pgrep is unavailable or the user manually runs `winpodx tray` while
    one is already up.
    """
    import fcntl
    import os
    from pathlib import Path

    global _TRAY_LOCK_FH
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and Path(runtime).is_dir():
        lock_dir = Path(runtime) / "winpodx"
    else:
        from winpodx.utils.paths import config_dir

        lock_dir = config_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "tray.lock"
    try:
        fh = open(lock_path, "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        return False
    try:
        fh.truncate(0)
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    _TRAY_LOCK_FH = fh
    return True


def run_tray() -> None:
    """Launch the system tray icon application."""
    if not _acquire_tray_lock():
        log.info("WinPodX tray already running; exiting.")
        return

    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    except ImportError:
        from winpodx.utils.install_source import pyside6_install_hint

        print(pyside6_install_hint())
        sys.exit(1)

    from winpodx.core import devices as D
    from winpodx.core.app import list_available_apps
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, pod_status, start_pod, stop_pod
    from winpodx.core.process import list_active_sessions
    from winpodx.core.rdp import launch_app
    from winpodx.display.detector import display_info

    app = QApplication(sys.argv)
    app.setApplicationName("winpodx")
    # Bind to the installed desktop entry so Wayland compositors (KDE,
    # GNOME) resolve the app id -> correct icon + identity for the tray
    # item. Without this, KDE may render the StatusNotifierItem with a
    # generic/blank icon or quietly file it under the hidden overflow.
    app.setDesktopFileName("winpodx")
    app.setQuitOnLastWindowClosed(False)

    # Resolve the bundled SVG so the system-tray icon actually shows up.
    # Without ``tray.setIcon`` Qt logs ``QSystemTrayIcon::setVisible: No
    # Icon set`` and most DEs (KDE Plasma, GNOME extensions) just don't
    # render the indicator at all.
    from PySide6.QtGui import QIcon

    from winpodx.desktop.icons import bundled_data_path

    icon_path = bundled_data_path("winpodx-icon.svg")
    tray_icon = QIcon(str(icon_path)) if icon_path is not None else QIcon.fromTheme("computer")
    app.setWindowIcon(tray_icon)

    tray = QSystemTrayIcon()
    tray.setIcon(tray_icon)
    tray.setToolTip(tr("WinPodX - Windows App Integration"))

    menu = QMenu()

    dashboard_action = QAction(tr("Open Dashboard"))
    menu.addAction(dashboard_action)
    menu.addSeparator()

    status_action = QAction(tr("Status: checking..."))
    status_action.setEnabled(False)
    menu.addAction(status_action)

    sessions_action = QAction(tr("Sessions: 0"))
    sessions_action.setEnabled(False)
    menu.addAction(sessions_action)

    menu.addSeparator()

    start_action = QAction(tr("Start Pod"))
    stop_action = QAction(tr("Stop Pod"))
    restart_action = QAction(tr("Restart Pod"))

    def _open_dashboard() -> None:
        """Launch the main GUI window as a detached subprocess.

        Single-process GUI + tray would be ideal but the GUI's
        QApplication lifecycle clashes with the tray's
        ``setQuitOnLastWindowClosed(False)``. Spawning is the cleanest
        win until we unify the two into one process.
        """
        import os
        import shutil as _shutil
        import subprocess as _sp

        cmd = _shutil.which("winpodx") or sys.executable
        args = [cmd, "gui"] if cmd != sys.executable else [cmd, "-m", "winpodx", "gui"]
        try:
            _sp.Popen(
                args,
                stdin=_sp.DEVNULL,
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
                start_new_session=True,
                close_fds=True,
                env=os.environ.copy(),
            )
        except OSError as e:
            tray.showMessage(
                "WinPodX",
                tr("Could not open dashboard: {e}").format(e=e),
                QSystemTrayIcon.MessageIcon.Warning,
            )

    dashboard_action.triggered.connect(_open_dashboard)

    # Cache the previous state across refresh ticks so the tray can drive
    # state-transition behaviour — currently the RUNNING → UNRESPONSIVE
    # auto-recovery flow + its notifications. Holds the PodState value of
    # the most recent observation, or None at startup.
    state_cache: dict[str, object] = {"prev": None, "recovery_inflight": False}

    def _trigger_unresponsive_recovery(cfg: Config) -> None:
        """Run the agent-side TermService cycle in a background thread.

        The tray's status timer keeps polling while this runs. The
        recovery itself takes up to ~30 s (TermService stop + start +
        RDP re-probe window), so it must not block the UI thread.
        Re-entry is guarded via ``state_cache['recovery_inflight']`` so
        a flapping pod doesn't pile up overlapping recovery threads.
        """
        import threading

        from winpodx.core.pod.recovery import RecoveryAction, try_recover_rdp
        from winpodx.desktop.notify import (
            notify_pod_needs_manual_restart,
            notify_pod_recovered,
        )

        if state_cache["recovery_inflight"]:
            return
        state_cache["recovery_inflight"] = True

        def worker() -> None:
            try:
                result = try_recover_rdp(cfg)
            except Exception as e:  # noqa: BLE001 — must not crash the tray
                log.warning("Recovery worker crashed: %s", e)
                notify_pod_needs_manual_restart(f"recovery worker error: {e}")
                return
            finally:
                state_cache["recovery_inflight"] = False

            if result.success:
                notify_pod_recovered()
                return

            detail = ""
            if result.action == RecoveryAction.AGENT_UNREACHABLE:
                detail = "agent unreachable"
            elif result.action == RecoveryAction.RDP_STILL_DOWN:
                detail = "RDP still down after TermService restart"
            if result.detail:
                detail = f"{detail} — {result.detail}" if detail else result.detail
            notify_pod_needs_manual_restart(detail)

        threading.Thread(target=worker, name="winpodx-pod-recovery", daemon=True).start()

    def refresh_status() -> None:
        cfg = Config.load()
        try:
            s = pod_status(cfg)
            state_text = s.state.value
            if s.state == PodState.RUNNING:
                state_text += f" ({s.ip})"
            status_action.setText(tr("Pod: {state}").format(state=state_text))
            start_action.setEnabled(s.state == PodState.STOPPED)
            stop_action.setEnabled(s.state == PodState.RUNNING)
            restart_action.setEnabled(s.state in (PodState.RUNNING, PodState.UNRESPONSIVE))

            # State-transition behaviour: RUNNING → UNRESPONSIVE fires the
            # auto-recovery flow + a "trying to wake the guest"
            # notification. Recovery worker emits either
            # `notify_pod_recovered` or `notify_pod_needs_manual_restart`
            # when it completes, so we don't need to drive those here.
            prev = state_cache["prev"]
            if (
                s.state == PodState.UNRESPONSIVE
                and prev != PodState.UNRESPONSIVE
                and not state_cache["recovery_inflight"]
            ):
                # Suppress UNRESPONSIVE-driven recovery while install.sh
                # is running. [3/4] "Waiting for Windows activation" and
                # [4/4] "Waiting for OEM reboot pass" both legitimately
                # have RDP down for several minutes while Windows is in
                # Sysprep or rebooting -- firing TermService restart
                # against a guest that's still on its first boot loops
                # the install path and shows the user spurious
                # "Pod stopped responding" notifications.
                from winpodx.desktop.tray_spawn import _install_in_progress

                if not _install_in_progress():
                    from winpodx.desktop.notify import notify_pod_unresponsive

                    notify_pod_unresponsive(s.ip or cfg.rdp.ip)
                    _trigger_unresponsive_recovery(cfg)
            state_cache["prev"] = s.state
        except Exception as e:
            log.warning("Failed to get pod status: %s", e)
            status_action.setText(tr("Pod: error"))

        # Must not raise: refresh_status is a QTimer slot (30s tick) -- an
        # uncaught exception propagates out of the Qt event loop and aborts
        # app.exec(), so the tray icon appears at launch then silently vanishes
        # on the first tick. list_active_sessions() globs + reads + os.kill()s
        # and can raise OSError, so guard it (mirrors _rebuild_sessions_menu).
        try:
            active = list_active_sessions()
        except Exception as e:  # noqa: BLE001 -- never crash the tray event loop
            log.warning("Failed to list active sessions: %s", e)
            active = []
        sessions_action.setText(tr("Sessions: {n}").format(n=len(active)))

        # Keep the Terminate-Session / USB-Devices submenus fresh from the timer
        # tick (#573). KDE/Plasma's StatusNotifierItem renders the tray menu over
        # DBusMenu, which does NOT reliably deliver a *nested* submenu's
        # aboutToShow — so the two submenus (populated only via their own
        # aboutToShow) stayed stuck at their build-time content ("(no active
        # sessions)" / "(no USB devices detected)") and looked broken even though
        # the top-level "Sessions: N" count (refreshed here) was correct.
        # Rebuilding them on this always-firing QTimer tick keeps them current
        # regardless of whether aboutToShow fires. Guarded: this is a QTimer slot
        # and must never raise (an uncaught exception aborts app.exec()).
        for _rebuild in (_rebuild_sessions_menu, _rebuild_devices_menu):
            try:
                _rebuild()
            except Exception as e:  # noqa: BLE001 -- never crash the tray event loop
                log.warning("tray submenu refresh failed: %s", e)

        # Re-assert the icon every tick. KDE/GNOME StatusNotifier hosts can
        # restart (plasmashell reload, panel reconfigure, host crash) and Qt
        # does NOT reliably re-register the item afterwards -> the icon
        # vanishes and never comes back ("트레이가 자꾸 사라진다"). show() is a
        # no-op when the item is already registered and RE-registers it when
        # the host has come back, so re-asserting on every 30s tick keeps the
        # icon alive across host restarts. The startup _ensure_tray_visible
        # retry only covers the first appearance, not later host cycles.
        try:
            if QSystemTrayIcon.isSystemTrayAvailable():
                tray.show()
        except Exception as e:  # noqa: BLE001 -- never crash the tray event loop
            log.debug("tray re-assert failed: %s", e)

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
                            "WinPodX",
                            tr("{msg}: {detail}").format(msg=error_msg, detail=result.error),
                            QSystemTrayIcon.MessageIcon.Critical,
                        ),
                    )
                else:
                    QTimer.singleShot(
                        0,
                        lambda: tray.showMessage(
                            "WinPodX",
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
                        "WinPodX",
                        tr("{msg}: {detail}").format(msg=error_msg, detail=msg),
                        QSystemTrayIcon.MessageIcon.Critical,
                    ),
                )
            QTimer.singleShot(0, refresh_status)

        threading.Thread(target=wrapper, daemon=True).start()

    def on_start() -> None:
        cfg = Config.load()
        _run_in_thread(lambda: start_pod(cfg), tr("Pod started"), tr("Failed to start pod"))

    def on_stop() -> None:
        cfg = Config.load()
        _run_in_thread(lambda: stop_pod(cfg), tr("Pod stopped"), tr("Failed to stop pod"))

    def on_restart() -> None:
        cfg = Config.load()

        def _restart():
            stop_pod(cfg)
            return start_pod(cfg)

        _run_in_thread(_restart, tr("Pod restarted"), tr("Failed to restart pod"))

    start_action.triggered.connect(on_start)
    stop_action.triggered.connect(on_stop)
    restart_action.triggered.connect(on_restart)

    menu.addAction(start_action)
    menu.addAction(stop_action)
    menu.addAction(restart_action)

    menu.addSeparator()

    apps_menu = QMenu(tr("Launch App"))
    available_apps = list_available_apps()

    def make_launcher(executable: str, full_name: str):
        def launcher() -> None:
            cfg = Config.load()
            try:
                launch_app(cfg, executable)
                tray.showMessage(
                    "WinPodX",
                    tr("Launching {name}...").format(name=full_name),
                    QSystemTrayIcon.MessageIcon.Information,
                )
            except RuntimeError as e:
                tray.showMessage(
                    "WinPodX Error",
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
        no_apps = QAction(tr("(no apps - run 'winpodx setup')"))
        no_apps.setEnabled(False)
        apps_menu.addAction(no_apps)

    apps_menu.addSeparator()
    desktop_action = QAction(tr("Full Desktop"))

    def on_desktop() -> None:
        try:
            launch_app(Config.load())
            tray.showMessage(
                "WinPodX", tr("Opening desktop..."), QSystemTrayIcon.MessageIcon.Information
            )
        except RuntimeError as e:
            tray.showMessage("WinPodX Error", str(e), QSystemTrayIcon.MessageIcon.Critical)

    desktop_action.triggered.connect(on_desktop)
    apps_menu.addAction(desktop_action)

    menu.addMenu(apps_menu)

    # Terminate-session submenu (#450): lists the tracked .cproc RDP sessions;
    # each entry SIGTERMs that session via core.process.kill_session — the same
    # path as `winpodx app kill`. Rebuilt on open so it reflects live sessions.
    sessions_menu = QMenu(tr("Terminate Session"))

    def _make_session_kill(app_name: str):
        def handler() -> None:
            from winpodx.core.process import kill_session

            def op() -> None:
                if not kill_session(app_name):
                    raise RuntimeError("session not found or already closed")

            _run_in_thread(
                op,
                tr("Terminated session: {name}").format(name=app_name),
                tr("Failed to terminate {name}").format(name=app_name),
            )

        return handler

    def _rebuild_sessions_menu() -> None:
        sessions_menu.clear()
        try:
            from winpodx.core.process import list_active_sessions

            active = list_active_sessions()
        except Exception as e:  # noqa: BLE001 — never crash the tray on enumeration
            log.warning("sessions menu: enumeration failed: %s", e)
            active = []
        # Parent each QAction to the submenu (#573). A parentless QAction added
        # via addAction() is kept on the *Python* side only; when this closure
        # returns, the local refs drop and PySide6 garbage-collects the actions,
        # so the submenu ends up empty — which DBusMenu/Plasma exports as a
        # submenu with ZERO children that can't be opened (confirmed via
        # `dbusmenu GetLayout`: Terminate/USB had no children while the eagerly
        # built Launch App / Maintenance submenus did). Giving the QAction a C++
        # parent (the menu) makes it outlive the closure.
        if not active:
            empty = QAction(tr("(no active sessions)"), sessions_menu)
            empty.setEnabled(False)
            sessions_menu.addAction(empty)
            return
        for s in active:
            act = QAction(tr("Terminate: {name}").format(name=s.app_name), sessions_menu)
            act.triggered.connect(_make_session_kill(s.app_name))
            sessions_menu.addAction(act)

    # Refresh on open via aboutToShow so the list is current the instant the
    # user opens it — without this the submenu only refreshed on the 30 s timer
    # tick, so a session started after launch took up to ~30 s to appear (#573
    # follow-up; reporter saw ~10-12 s). The earlier worry that aboutToShow
    # broke opening under Plasma was a misdiagnosis — the real cause was the
    # parentless-QAction GC fixed above; with the actions parented, aboutToShow
    # is safe and re-fills the live list on each open. The timer rebuild stays
    # as a fallback for hosts that don't deliver the nested aboutToShow.
    sessions_menu.aboutToShow.connect(_rebuild_sessions_menu)
    _rebuild_sessions_menu()
    menu.addMenu(sessions_menu)

    menu.addSeparator()

    # USB device switcher (#300). A checkable entry per host USB device:
    # checked == redirected to the Windows guest. Toggling runs the
    # persist + live attach/detach off the UI thread (live op can take a
    # few seconds and may pop a pkexec prompt). The submenu is rebuilt on
    # every open so it tracks hot-plugged devices + the current assignment.
    devices_menu = QMenu(tr("USB Devices"))

    def _make_device_toggle(host: D.HostDevice):
        dc = host.to_device_config()
        label = host.label or dc.did

        def handler(checked: bool) -> None:
            cfg = Config.load()

            def op() -> None:
                try:
                    running = pod_status(cfg).state == PodState.RUNNING
                except Exception:  # noqa: BLE001 — treat unknown state as not-running
                    running = False
                if checked:
                    D.assign_device(cfg, dc)
                    if running:
                        D.live_attach(cfg.pod.backend, cfg.pod.container_name, dc)
                else:
                    D.unassign_device(cfg, dc)
                    if running:
                        D.live_detach(cfg.pod.backend, cfg.pod.container_name, dc)

            if checked:
                _run_in_thread(
                    op,
                    tr("Attached {name} to the guest").format(name=label),
                    tr("Failed to attach {name}").format(name=label),
                )
            else:
                _run_in_thread(
                    op,
                    tr("Released {name} back to the host").format(name=label),
                    tr("Failed to detach {name}").format(name=label),
                )

        return handler

    def _rebuild_devices_menu() -> None:
        devices_menu.clear()
        try:
            cfg = Config.load()
            assigned = {d.key for d in D.parse_entries(cfg.pod.devices) if d.dtype == "usb"}
            usb = D.list_host_usb()
        except Exception as e:  # noqa: BLE001 — never crash the tray on enumeration
            log.warning("device menu: enumeration failed: %s", e)
            usb, assigned = [], set()
        # Parent QActions to the submenu so they survive this closure (#573) —
        # see _rebuild_sessions_menu for the GC / empty-DBusMenu-children detail.
        if not usb:
            empty = QAction(tr("(no USB devices detected)"), devices_menu)
            empty.setEnabled(False)
            devices_menu.addAction(empty)
            return
        for host in usb:
            dc = host.to_device_config()
            act = QAction(host.label or dc.did, devices_menu)
            act.setCheckable(True)
            act.setChecked(dc.key in assigned)
            # `triggered` (not `toggled`) fires only on user clicks, so the
            # programmatic setChecked above doesn't kick off a spurious op.
            act.triggered.connect(_make_device_toggle(host))
            devices_menu.addAction(act)

    # Refresh on open (parented actions make aboutToShow safe) + timer fallback,
    # so hot-plugged USB devices show without waiting for the next tick — see the
    # Terminate-Session submenu above (#573 follow-up).
    devices_menu.aboutToShow.connect(_rebuild_devices_menu)
    _rebuild_devices_menu()
    menu.addMenu(devices_menu)

    menu.addSeparator()

    info = display_info()
    info_action = QAction(
        tr("Display: {session} / {de}").format(
            session=info["session_type"], de=info["desktop_environment"]
        )
    )
    info_action.setEnabled(False)
    menu.addAction(info_action)

    menu.addSeparator()

    maint_menu = QMenu(tr("Maintenance"))

    cleanup_action = QAction(tr("Clean Lock Files"))

    def on_cleanup() -> None:
        from winpodx.core.daemon import cleanup_lock_files

        removed = cleanup_lock_files()
        msg = (
            tr("Removed {n} lock files").format(n=len(removed))
            if removed
            else tr("No lock files found")
        )
        tray.showMessage("WinPodX", msg, QSystemTrayIcon.MessageIcon.Information)

    cleanup_action.triggered.connect(on_cleanup)
    maint_menu.addAction(cleanup_action)

    timesync_action = QAction(tr("Sync Windows Time"))

    def on_timesync() -> None:
        from winpodx.core.daemon import sync_windows_time

        ok = sync_windows_time(Config.load())
        msg = tr("Time synced") if ok else tr("Time sync failed")
        tray.showMessage("WinPodX", msg, QSystemTrayIcon.MessageIcon.Information)

    timesync_action.triggered.connect(on_timesync)
    maint_menu.addAction(timesync_action)

    suspend_action = QAction(tr("Suspend Pod"))

    def on_suspend() -> None:
        from winpodx.core.daemon import suspend_pod

        suspend_pod(Config.load())
        tray.showMessage("WinPodX", tr("Pod suspended"), QSystemTrayIcon.MessageIcon.Information)
        refresh_status()

    suspend_action.triggered.connect(on_suspend)
    maint_menu.addAction(suspend_action)

    menu.addMenu(maint_menu)

    menu.addSeparator()

    quit_action = QAction(tr("Quit WinPodX"))

    def _confirmed_quit() -> None:
        """Tear down GUI + pod before closing the tray.

        User asked the tray Quit to be a real exit -- stop the Windows
        pod, close any dashboard window the user may have open, then
        exit the tray itself. A QMessageBox confirms first so a stray
        click doesn't cycle the pod (~30s recreate cost + RDP session
        loss).
        """
        from PySide6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            None,
            "WinPodX",
            tr(
                "Quit WinPodX completely?\n\nThis stops the Windows container "
                "and closes any open dashboard window."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 1. Stop the Windows pod (best-effort -- a running session
        #    survives a tray-quit only if the user explicitly chose to,
        #    which is what the confirmation above is for).
        try:
            from winpodx.core.pod import stop_pod

            stop_pod(Config.load())
        except Exception as e:  # noqa: BLE001
            log.debug("stop_pod during tray-quit failed: %s", e)

        # 2. Close any winpodx GUI / dashboard process the user may
        #    have open. Three launcher cmdline shapes exist:
        #      (a) install.sh wrapper:  python -m winpodx gui
        #      (b) pip / venv entry pt: python /.../venv/bin/winpodx gui
        #      (c) source path-style:   python /.../src/winpodx/__main__.py gui
        #    A "python ... winpodx ... gui" pattern catches all three.
        #    Earlier we tried to anchor to ``-m winpodx gui`` only --
        #    that missed the venv entry-point shape and Quit silently
        #    no-op'd against a tray running under dev install. Quit is
        #    an explicit user click, so over-killing (the worst case is
        #    a stray pytest run that happens to have ``winpodx`` and
        #    ``gui`` in argv) is the safer failure mode.
        try:
            import subprocess as _sp

            _sp.run(
                ["pkill", "-f", r"python.*winpodx.*gui"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, _sp.TimeoutExpired):
            pass

        # 3. Quit the tray itself.
        app.quit()

    quit_action.triggered.connect(_confirmed_quit)
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.show()

    # Some DEs (KDE Plasma, GNOME + AppIndicator) register their
    # StatusNotifier host a few seconds AFTER login -- and the autostart
    # .desktop launches ``winpodx tray`` early, so the very first show()
    # can land before any tray host exists. Qt does not retry on its own,
    # so the icon silently never appears ("트레이가 안뜰 때가 있어").
    # Re-show() on a bounded timer until a host shows up; genuinely
    # tray-less sessions (e.g. stock GNOME) just exhaust the retries and
    # fall back to the dashboard.
    tray_retry = {"left": 30}  # ~60 s at a 2 s cadence

    def _ensure_tray_visible() -> None:
        # QTimer slot: must not raise, or the exception aborts the event loop
        # and kills the tray we're trying to keep alive.
        try:
            if QSystemTrayIcon.isSystemTrayAvailable():
                tray.show()  # re-register with the now-present host
                tray_retry_timer.stop()
                log.info("system-tray host appeared; tray icon shown.")
                return
            tray_retry["left"] -= 1
            if tray_retry["left"] <= 0:
                tray_retry_timer.stop()
                log.warning(
                    "no system-tray host after 60 s; this DE may lack a tray "
                    "(stock GNOME needs the AppIndicator extension). Use "
                    "`winpodx gui` for the dashboard instead."
                )
        except Exception as e:  # noqa: BLE001 -- never crash the tray event loop
            log.warning("tray visibility retry failed: %s", e)
            tray_retry_timer.stop()

    tray_retry_timer = QTimer()
    tray_retry_timer.timeout.connect(_ensure_tray_visible)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.info("no system-tray host yet; will retry show() for up to 60 s.")
        tray_retry_timer.start(2000)

    refresh_status()

    timer = QTimer()
    timer.timeout.connect(refresh_status)
    timer.start(30000)

    # Auto-start the pod on tray launch (i.e. on login / reboot, since the
    # autostart .desktop runs `winpodx tray`). Gated on cfg.pod.auto_start
    # (default on). Runs on a background thread so the icon appears
    # immediately; resumes a suspended pod, otherwise cold-starts it, and is
    # a no-op when it's already running. Best-effort -- a failure just leaves
    # the pod stopped and is logged, never crashes the tray.
    def _autostart_pod() -> None:
        try:
            c = Config.load()
            if not getattr(c.pod, "auto_start", True):
                return
            if c.pod.backend not in ("podman", "docker"):
                return
            if pod_status(c).state == PodState.RUNNING:
                return
            from winpodx.core.daemon import is_pod_paused, resume_pod

            if is_pod_paused(c):
                log.info("auto-start: resuming suspended pod on tray launch")
                resume_pod(c)
            else:
                log.info("auto-start: starting pod on tray launch")
                start_pod(c)
        except Exception as e:  # noqa: BLE001 -- never crash the tray
            log.warning("auto-start failed: %s", e)

    import threading

    threading.Thread(target=_autostart_pod, daemon=True).start()

    # systemd-logind PrepareForSleep listener (issue #TBD). On host
    # resume the QEMU guest sees a wall-clock jump and its RDP TCP
    # listener goes stale -- the user sees the tray frozen on
    # "starting" because the existing UNRESPONSIVE classifier can take
    # several poll cycles to fire. Subscribing to the system bus lets
    # us trigger recovery in seconds instead of minutes.
    def _on_prepare_for_sleep(active: bool) -> None:
        if active:
            # Pre-sleep -- nothing to do here yet (future: pause container).
            log.debug("PrepareForSleep(active=True): host suspending")
            return
        # Post-resume. Give the network stack a few seconds to come back
        # up, then refresh + trigger recovery if RDP is now stale.
        log.info("PrepareForSleep(active=False): host resumed, refreshing pod state")
        QTimer.singleShot(5000, refresh_status)

    try:
        from PySide6.QtDBus import QDBusConnection

        bus = QDBusConnection.systemBus()
        if bus.isConnected():
            # PySide6's QDBusConnection.connect requires the 6-arg overload
            # (service, path, interface, name, signature, slot). The signal
            # we want -- ``PrepareForSleep(b)`` -- emits a single boolean.
            # The 5-arg shape compiled fine but crashed at runtime with
            # ``TypeError: connect expected at least 6 arguments, got 5``
            # on the user's PySide6.
            ok = bus.connect(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "PrepareForSleep",
                "b",
                _on_prepare_for_sleep,
            )
            if not ok:
                log.debug(
                    "QDBus PrepareForSleep subscription failed; "
                    "host-suspend recovery will rely on the 30 s poll instead."
                )
        else:
            log.debug("system D-Bus not connected; skipping PrepareForSleep subscription")
    except ImportError:
        # PySide6.QtDBus not available in some packaging variants. The
        # 30 s status poll still catches resume eventually -- D-Bus is
        # the fast path, not the only path.
        log.debug("PySide6.QtDBus not available; skipping sleep listener")
    except TypeError as e:
        # Defensive: if a future PySide6 changes the overload shape again,
        # log + degrade to the 30 s poll instead of crashing the tray.
        log.warning(
            "QDBus PrepareForSleep subscribe failed (%s); tray will rely on the 30 s poll.",
            e,
        )

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

    # Periodic checksum-gated icon refresh (runs regardless of idle timeout):
    # picks up a Windows app update's new icon without a manual Refresh.
    from winpodx.core.daemon import run_icon_refresh_monitor

    threading.Thread(
        target=run_icon_refresh_monitor,
        args=(cfg, idle_stop),
        daemon=True,
    ).start()

    def on_tray_activate(reason: int) -> None:
        # Qt slot: any uncaught exception here aborts the event loop and kills
        # the tray, so catch broadly (not just RuntimeError).
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            try:
                launch_app(Config.load())
            except Exception as e:  # noqa: BLE001 -- never crash the tray
                tray.showMessage("WinPodX Error", str(e), QSystemTrayIcon.MessageIcon.Critical)

    tray.activated.connect(on_tray_activate)

    app.aboutToQuit.connect(idle_stop.set)
    sys.exit(app.exec())
