# SPDX-License-Identifier: MIT
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
from winpodx.core.i18n import tr
from winpodx.gui._widget_helpers import actionable_error, show_toast
from winpodx.gui.workers import DiscoveryWorker

# QStackedWidget index of the Terminal / Logs page. Page order (main_window):
# 0 Dashboard, 1 All apps, 2 Settings, 3 Tools, 4 Terminal/Logs, 5 Info,
# 6 Devices, 7 License. Used by the refresh-failure dialog's "View logs" action
# (was 3, which is the Tools page).
_LOGS_PAGE_INDEX = 4


class AppCrudMixin:
    """App profile CRUD + discovery worker behavior. Mix into ``WinpodxWindow``."""

    def _on_add_app(self) -> None:
        from winpodx.gui.app_dialog import AppProfileDialog, save_app_profile, set_custom_icon

        dlg = AppProfileDialog(self)
        if dlg.exec():
            data = dlg.get_result()
            save_app_profile(data)
            # A picked icon is copied into the new profile dir (#530).
            if dlg.chosen_icon_path():
                set_custom_icon(dlg.chosen_icon_path(), str(data["name"]))
            self._reload_apps()
            self.info_label.setText(tr("Added: {name}").format(name=data["full_name"]))

    def _on_edit_app(self, app: AppInfo) -> None:
        from winpodx.gui.app_dialog import (
            AppProfileDialog,
            preserve_app_icon,
            save_app_profile,
            set_custom_icon,
        )

        dlg = AppProfileDialog(
            self,
            name=app.name,
            full_name=app.full_name,
            executable=app.executable,
            categories=", ".join(app.categories),
            mime_types=", ".join(app.mime_types),
            icon_path=app.icon_path,
            edit_mode=True,
        )
        if dlg.exec():
            data = dlg.get_result()
            save_app_profile(data)
            if dlg.chosen_icon_path():
                # A deliberate custom icon wins over the carry-over (#530).
                set_custom_icon(dlg.chosen_icon_path(), str(data["name"]))
            else:
                # Keep the existing icon across the edit (rename / MIME change /
                # discovered->user override) so it doesn't reset to the generic
                # letter glyph (#530).
                preserve_app_icon(app.icon_path, str(data["name"]))
            self._reload_apps()
            self.info_label.setText(tr("Updated: {name}").format(name=data["full_name"]))

    def _on_reset_app(self, app: AppInfo) -> None:
        """Discard a user override and restore the auto-discovered profile (#530).

        Only meaningful (and only offered by the menu) when an edited app still
        has a ``discovered/<name>`` twin to fall back to; ``reset_app_profile``
        returns ``None`` otherwise and we leave everything untouched.
        """
        reply = QMessageBox.question(
            self,
            tr("Reset to Detected"),
            tr(
                "Discard your edits to '{name}' and restore the auto-detected "
                "profile, including its original icon?"
            ).format(name=app.full_name),
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from winpodx.core.app import reset_app_profile

        reverted = reset_app_profile(app.name)
        if reverted is None:
            self.info_label.setText(tr("Nothing to reset: {name}").format(name=app.full_name))
            return
        self._reload_apps()
        self.info_label.setText(tr("Reset to detected: {name}").format(name=reverted.full_name))

    def _on_delete_app(self, app: AppInfo) -> None:
        reply = QMessageBox.question(
            self,
            tr("Delete App"),
            tr(
                "Remove '{name}' profile?\nThis only removes the profile, not the Windows app."
            ).format(name=app.full_name),
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from winpodx.desktop.entry import remove_desktop_entry
        from winpodx.gui.app_dialog import delete_app_profile

        delete_app_profile(app.name)
        remove_desktop_entry(app.name)
        # A discovered profile would be re-created by the next discovery sweep;
        # record a tombstone so a deleted auto-discovered app stays gone (#514).
        if getattr(app, "source", "user") == "discovered":
            from winpodx.core.app import suppress_app_slug

            suppress_app_slug(app.name)
        self._reload_apps()
        self.info_label.setText(tr("Removed: {name}").format(name=app.full_name))

    def _on_toggle_app_hidden(self, app: AppInfo) -> None:
        """Hide a visible app (or show a hidden one) from the Linux app menu.

        Persists the choice into app.toml (sticky across rescans) and syncs the
        ``.desktop`` entry. After hiding, the app drops out of the default grid
        but is still reachable via the "Hidden" toggle for un-hiding.
        """
        from winpodx.core.app import set_app_hidden

        updated = set_app_hidden(app.name, not app.hidden)
        if updated is None:
            self.info_label.setText(tr("Could not update: {name}").format(name=app.full_name))
            return
        self._reload_apps()
        if updated.hidden:
            self.info_label.setText(tr("Hidden: {name}").format(name=app.full_name))
        else:
            self.info_label.setText(tr("Shown: {name}").format(name=app.full_name))

    def _reload_apps(self) -> None:
        self.apps = list_available_apps()
        self._refresh_hidden_button()
        self._refresh_deleted_button()
        # Clear any active search WITHOUT firing textChanged -> _filter_apps:
        # _refresh_launcher_home() below already triggers the single rebuild.
        # Two back-to-back rebuilds of app_list_layout raced Qt's heightForWidth
        # pass and helped trigger the discover-time SIGSEGV.
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self._refresh_launcher_home()
        visible = self._visible_apps()
        # "X of Y" mirrors the library toolbar format (Task 5); no search is
        # active right after a reload, so shown == total.
        self.app_count_label.setText(
            tr("{shown} of {total} apps").format(shown=len(visible), total=len(self.apps))
        )

    def _on_refresh_apps(self) -> None:
        """Entry point for the "Refresh Apps" button; kicks off the QThread worker."""
        # Bail while a scan is in flight. We check BOTH the UI state and the
        # live thread ref. The result slots flip the state back to "idle"
        # (re-enabling the button) the same event-loop tick `thread.finished`
        # fires, but the QThread/worker pair is not fully torn down until
        # `_cleanup_refresh_worker` nulls `self._refresh_thread`. Without the
        # ref check, a rapid re-click could overwrite self._refresh_thread /
        # self._refresh_worker (below) and drop the last Python ref to a
        # worker still finishing on its own thread — the cross-thread
        # double-free this guard, with `_cleanup_refresh_worker`, exists to
        # prevent.
        if self._refresh_state == "scanning" or self._refresh_thread is not None:
            return
        self._set_refresh_state("scanning")

        thread = QThread(self)
        worker = DiscoveryWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_refresh_succeeded)
        worker.failed.connect(self._on_refresh_failed)
        worker.finished.connect(thread.quit)
        # DiscoveryWorker is a PARENTLESS, Python-owned QObject — shiboken
        # keeps ownership and deleteLater() does NOT transfer it. So we must
        # NOT post a worker.deleteLater here. Qt6's QThreadPrivate::finish()
        # emits QThread::finished() *before* it flushes the worker's pending
        # DeferredDelete on the worker thread; the queued
        # _cleanup_refresh_worker (below) would then drop the last Python ref
        # and let shiboken delete the C++ worker from the MAIN thread while
        # the worker thread's in-flight DeferredDelete deletes it again ->
        # SIGSEGV in ~QObject. This is the residual race v0.2.0.10/.11 chased:
        # v0.2.0.10 nulled refs from the result slots (raced the deleteLater);
        # v0.2.0.11 moved the null to thread.finished but left BOTH delete
        # paths live. Fix: keep exactly ONE delete path — the Python ref drop
        # in _cleanup_refresh_worker, which runs on the main thread, only
        # once, and only after thread.wait() confirms the worker thread is
        # dead (so nothing races the delete).
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
        self.refresh_btn.setText(tr("Scanning...") if scanning else tr("Refresh Apps"))
        self.refresh_progress.setVisible(scanning)
        if scanning:
            self.info_label.setText(tr("Scanning pod for installed apps..."))

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
            msg = tr("Discovery complete: {count} app(s) updated").format(count=count)
            self.info_label.setText(msg)
            show_toast(self, msg, kind="success")
        else:
            msg = tr("Discovery complete: no new apps found")
            self.info_label.setText(msg)
            show_toast(self, msg, kind="info")

    @Slot()
    def _cleanup_refresh_worker(self) -> None:
        """Drop the Python refs to the worker + thread after the worker
        thread has fully exited.

        Bound to ``thread.finished``, which Qt6 emits from
        ``QThreadPrivate::finish()`` while the worker thread is still mid-
        teardown. We ``wait()`` first so the worker thread is provably dead
        before the last Python ref drops: DiscoveryWorker is a parentless,
        Python-owned QObject with no deleteLater, so dropping
        ``self._refresh_worker`` is what deletes the C++ object — and that
        delete must happen on the main thread, exactly once, with the worker
        thread joined so nothing races it. ``wait()`` returns near-instantly
        because the thread is already finishing. This is the piece
        v0.2.0.10/.11 missed: they moved the ref drop to thread.finished but
        left it racing the worker's own deleteLater flush."""
        thread = self._refresh_thread
        if thread is not None:
            try:
                thread.wait()
            except RuntimeError:
                # C++ QThread already gone (the sibling thread.deleteLater
                # won the race) — nothing left to join.
                pass
        self._refresh_worker = None
        self._refresh_thread = None

    @Slot(str, str)
    def _on_refresh_failed(self, kind: str, detail: str) -> None:
        self._set_refresh_state("idle")
        self.info_label.setText(tr("App discovery failed"))

        # v0.1.9.1: defer the QMessageBox creation to a clean event-loop tick.
        # PySide6 + Qt 6.x can SEGV in QMessageBox's font-inheritance lookup
        # when the dialog is constructed inside the queued-signal callback
        # frame — kernalix7 hit this on `_on_refresh_failed` after a
        # pod-not-running discovery failure. Re-dispatching via QTimer
        # unwinds the signal handler stack first.
        QTimer.singleShot(0, lambda: self._show_refresh_failure_dialog(kind, detail))

    def _show_refresh_failure_dialog(self, kind: str, detail: str) -> None:
        """Show an actionable failure dialog after the signal handler unwinds.

        Each failure kind offers the buttons that actually help recover from
        it (Task 6): start the pod, retry discovery, or jump to the Logs
        page. ``actionable_error`` returns the clicked label so we branch on
        it rather than juggling button objects.
        """
        if kind == "pod_not_running":
            start_label = tr("Start Pod")
            choice = actionable_error(
                self,
                tr("Pod Not Running"),
                tr("The Windows pod must be running to scan for apps."),
                actions=[start_label, tr("Close")],
                detail=detail,
            )
            if choice == start_label:
                self._on_start_pod()
            return

        if kind == "session_disconnected":
            # The pod IS running; what failed is the FreeRDP session
            # winpodx tried to use. Common when multi-session is mid-
            # activation (TermService cycle terminates the call) or
            # when the autologon session blipped. Don't suggest "Start
            # Pod" -- that's wrong. Suggest "Retry" instead.
            retry_label = tr("Retry")
            choice = actionable_error(
                self,
                tr("Discovery Session Disconnected"),
                tr(
                    "The discovery session was terminated by the guest before "
                    "results could be written.\n\n"
                    "This can happen when multi-session activation just cycled "
                    "TermService, or the autologon session briefly disconnected. "
                    "The pod itself is running; retrying usually succeeds."
                ),
                actions=[retry_label, tr("Close")],
                detail=detail,
            )
            if choice == retry_label:
                self._on_refresh_apps()
            return

        # Generic / module_missing: the only useful next step is to inspect
        # the logs, so offer "View logs".
        logs_label = tr("View logs")
        if kind == "module_missing":
            title = tr("Discovery Unavailable")
            message = tr("The app discovery module is not available in this install.")
        else:
            title = tr("Discovery Failed")
            message = detail or tr("An unexpected error occurred during app discovery.")
        choice = actionable_error(
            self,
            title,
            message,
            actions=[logs_label, tr("Close")],
            detail=detail,
        )
        if choice == logs_label:
            # Jump to the Logs page so the user can read the failure detail.
            switch = getattr(self, "_switch_page", None)
            if callable(switch):
                switch(_LOGS_PAGE_INDEX)
