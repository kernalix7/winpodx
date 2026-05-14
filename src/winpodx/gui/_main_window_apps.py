"""App-CRUD + discovery mixin for ``WinpodxWindow``.

Holds the methods that drive Add/Edit/Delete profile flows and the
"Refresh Apps" QThread worker orchestration. Pulled out of
``main_window.py`` to keep that file focused on overall window
orchestration.

Host-class contract (only listed for readers; not enforced):
    apps: list[AppInfo]            — populated by _reload_apps.
    info_label: QLabel
    refresh_btn: QPushButton
    refresh_progress: QWidget
    search_box: QLineEdit
    app_count_label: QLabel
    _refresh_state: str            — "idle" | "scanning" | etc.
    _refresh_thread / _refresh_worker
        — managed entirely by this mixin.
    _refresh_hidden_button() / _visible_apps() / _populate_app_view()
    _on_start_pod()                — defined on the host class.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, QTimer, Slot
from PySide6.QtWidgets import QMessageBox

from winpodx.core.app import AppInfo, list_available_apps
from winpodx.gui.workers import DiscoveryWorker


class AppCrudMixin:
    """App profile CRUD + discovery worker behavior. Mix into ``WinpodxWindow``."""

    def _on_add_app(self) -> None:
        from winpodx.gui.app_dialog import AppProfileDialog, save_app_profile

        dlg = AppProfileDialog(self)
        if dlg.exec():
            data = dlg.get_result()
            save_app_profile(data)
            self._reload_apps()
            self.info_label.setText(f"Added: {data['full_name']}")

    def _on_edit_app(self, app: AppInfo) -> None:
        from winpodx.gui.app_dialog import AppProfileDialog, save_app_profile

        dlg = AppProfileDialog(
            self,
            name=app.name,
            full_name=app.full_name,
            executable=app.executable,
            categories=", ".join(app.categories),
            mime_types=", ".join(app.mime_types),
            edit_mode=True,
        )
        if dlg.exec():
            data = dlg.get_result()
            save_app_profile(data)
            self._reload_apps()
            self.info_label.setText(f"Updated: {data['full_name']}")

    def _on_delete_app(self, app: AppInfo) -> None:
        reply = QMessageBox.question(
            self,
            "Delete App",
            f"Remove '{app.full_name}' profile?\n"
            "This only removes the profile, not the Windows app.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from winpodx.desktop.entry import remove_desktop_entry
        from winpodx.gui.app_dialog import delete_app_profile

        delete_app_profile(app.name)
        remove_desktop_entry(app.name)
        self._reload_apps()
        self.info_label.setText(f"Removed: {app.full_name}")

    def _reload_apps(self) -> None:
        self.apps = list_available_apps()
        self._refresh_hidden_button()
        visible = self._visible_apps()
        self._populate_app_view(visible)
        self.search_box.clear()
        self.app_count_label.setText(f"{len(visible)} apps")

    def _on_refresh_apps(self) -> None:
        """Entry point for the "Refresh Apps" button; kicks off the QThread worker."""
        if self._refresh_state == "scanning":
            return
        self._set_refresh_state("scanning")

        thread = QThread(self)
        worker = DiscoveryWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_refresh_succeeded)
        worker.failed.connect(self._on_refresh_failed)
        worker.finished.connect(thread.quit)
        # v0.2.0.11: keep Qt's canonical cleanup chain (worker.deleteLater
        # processed by the worker thread's event loop *before* it exits,
        # then thread.deleteLater after the thread dies) BUT do not also
        # null out `self._refresh_worker` from the success/failure slots.
        # The previous code raced Python's ref drop with Qt's queued
        # deleteLater event → QObject destructor ran on a half-freed
        # pointer → Signal 11. Now Python refs are dropped only via
        # `_cleanup_refresh_worker`, bound to `thread.finished` which
        # fires after both worker and thread have been fully torn down
        # by Qt.
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._cleanup_refresh_worker)
        thread.finished.connect(thread.deleteLater)
        # Keep references so the QThread+QObject aren't garbage-collected mid-run.
        self._refresh_thread = thread
        self._refresh_worker = worker
        thread.start()

    def _set_refresh_state(self, state: str) -> None:
        self._refresh_state = state
        scanning = state == "scanning"
        self.refresh_btn.setEnabled(not scanning)
        self.refresh_btn.setText("Scanning..." if scanning else "Refresh Apps")
        self.refresh_progress.setVisible(scanning)
        if scanning:
            self.info_label.setText("Scanning pod for installed apps...")

    @Slot(int)
    def _on_refresh_succeeded(self, count: int) -> None:
        self._set_refresh_state("idle")
        # NOTE: don't null out _refresh_worker / _refresh_thread here —
        # see v0.2.0.11 comment in `_on_refresh_apps`. Cleanup happens
        # via `_cleanup_refresh_worker` once the thread.finished signal
        # fires (i.e. after Qt has drained the event loop and processed
        # any pending deleteLater on the worker).
        self._reload_apps()
        if count:
            self.info_label.setText(f"Discovery complete: {count} app(s) updated")
        else:
            self.info_label.setText("Discovery complete: no new apps found")

    @Slot()
    def _cleanup_refresh_worker(self) -> None:
        """Drop Python refs to the worker + thread once Qt has finished
        with them. Bound to ``thread.finished`` so it runs after the
        worker's event loop has drained — racing the ref drop with Qt's
        deleteLater is what crashed v0.2.0.10."""
        # `worker.deleteLater()` is implicit — once we drop the last
        # Python ref AND the thread is done, Qt collects the QObject on
        # the next event-loop tick of its owning thread (which is now
        # the main thread since the worker thread has exited).
        self._refresh_worker = None
        self._refresh_thread = None

    @Slot(str, str)
    def _on_refresh_failed(self, kind: str, detail: str) -> None:
        self._set_refresh_state("idle")
        self.info_label.setText("App discovery failed")

        # v0.1.9.1: defer the QMessageBox creation to a clean event-loop tick.
        # PySide6 + Qt 6.x can SEGV in QMessageBox's font-inheritance lookup
        # when the dialog is constructed inside the queued-signal callback
        # frame — kernalix7 hit this on `_on_refresh_failed` after a
        # pod-not-running discovery failure. Re-dispatching via QTimer
        # unwinds the signal handler stack first.
        QTimer.singleShot(0, lambda: self._show_refresh_failure_dialog(kind, detail))

    def _show_refresh_failure_dialog(self, kind: str, detail: str) -> None:
        """Build the failure QMessageBox after the signal handler has unwound."""
        if kind == "pod_not_running":
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Pod Not Running")
            box.setText("The Windows pod must be running to scan for apps.")
            if detail:
                box.setInformativeText(detail)
            start_btn = box.addButton("Start Pod", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is start_btn:
                self._on_start_pod()
            return

        if kind == "session_disconnected":
            # The pod IS running; what failed is the FreeRDP session
            # winpodx tried to use. Common when multi-session is mid-
            # activation (TermService cycle terminates the call) or
            # when the autologon session blipped. Don't suggest "Start
            # Pod" -- that's wrong. Suggest "Retry" instead.
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Discovery Session Disconnected")
            box.setText(
                "The discovery session was terminated by the guest before "
                "results could be written.\n\n"
                "This can happen when multi-session activation just cycled "
                "TermService, or the autologon session briefly disconnected. "
                "The pod itself is running; retrying usually succeeds."
            )
            if detail:
                box.setInformativeText(detail)
            retry_btn = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is retry_btn:
                self._on_refresh_apps()
            return

        if kind == "module_missing":
            QMessageBox.critical(
                self,
                "Discovery Unavailable",
                f"The app discovery module is not available in this install.\n\n{detail}",
            )
            return

        QMessageBox.critical(
            self,
            "Discovery Failed",
            detail or "An unexpected error occurred during app discovery.",
        )
