# SPDX-License-Identifier: MIT
"""Navigation + first-launch mixin for ``WinpodxWindow``.

Holds page-switch behaviour (start/stop the app-log tail and the
Info-page auto-refresh based on which page is showing) and the
first-run quick-start wizard (resume pending install steps + show
the one-shot Welcome dialog).

Host-class contract (only listed for readers; not enforced):
    pages: QStackedWidget                      — built by _build_ui.
    nav_buttons: list[QPushButton]             — created by HeaderMixin.
    apps: list[AppInfo]
    cfg: winpodx.core.config.Config
    log_signal: Signal(str, str)
    _tail_proc                                 — managed by LogsMixin.
    _on_follow_app_log / _on_stop_tail         — LogsMixin.
    _start_info_auto_refresh / _stop_info_auto_refresh
                                               — InfoPageMixin.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMessageBox

from winpodx.core.i18n import tr
from winpodx.gui.theme import C


class NavigationMixin:
    """Page switching + first-launch checks."""

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        buttons = getattr(self, "nav_buttons", [])
        for i, btn in enumerate(buttons):
            btn.setChecked(i == index)
        focused = QApplication.focusWidget()
        selected = buttons[index] if 0 <= index < len(buttons) else None
        if focused in buttons and focused is not selected:
            focused.clearFocus()
        if hasattr(self, "_move_nav_indicator"):
            self._move_nav_indicator(index)
        if hasattr(self, "_update_page_header"):
            self._update_page_header(index)
        if hasattr(self, "_switch_page_light_dismiss"):
            self._switch_page_light_dismiss()

        # Dashboard (page 0): drive the live resource gauges only while it's
        # the visible page -- start the 5 s poll on entry, stop it on exit so
        # we never run podman stats / disk probes in the background.
        dashboard_index = 0
        if index == dashboard_index:
            if hasattr(self, "_populate_workspace"):
                self._populate_workspace()
            if hasattr(self, "_refresh_dashboard"):
                self._refresh_dashboard()
            if hasattr(self, "_dashboard_timer"):
                self._dashboard_timer.start()
        elif hasattr(self, "_dashboard_timer"):
            self._dashboard_timer.stop()

        # Applications (page 1): refresh the launcher's "Running" live-session strip
        # whenever it is opened so it reflects what's actually running.
        if index == 1 and hasattr(self, "_refresh_running_strip"):
            self._refresh_running_strip()
        # v0.5.1: tail processes are now always-on (started at
        # WinpodxWindow.__init__) and feed both the Terminal full
        # history AND the always-visible bottom log bar. We no
        # longer manage tail lifecycle on page switches; the
        # always-on design means the bottom bar keeps updating no
        # matter which page the user is on.

        # Auto-refresh the Info page Health card when the user is looking
        # at it. The probes hit /exec which spawns a child PS, so we keep
        # the cadence at 30s (cheap on a healthy install — ~2s for the
        # full sweep, dominated by guest_exec + guest_summary). Off-page,
        # the timer is paused so we don't poll the guest while idle.
        info_index = 5
        if index == info_index:
            self._start_info_auto_refresh()
        else:
            self._stop_info_auto_refresh()

        # Live-refresh the Tools-page session list while it's the visible
        # page so launching/closing an app shows up within ~2.5 s without
        # leaving the tab (#450). Off-page the poll is stopped so we don't
        # scan the runtime dir while the user is elsewhere.
        tools_index = 3
        if index == tools_index:
            if hasattr(self, "_refresh_sessions_panel"):
                self._refresh_sessions_panel(force=True)
            if hasattr(self, "_sessions_timer"):
                self._sessions_timer.start()
        elif hasattr(self, "_sessions_timer"):
            self._sessions_timer.stop()

    def _install_shortcuts(self) -> None:
        """Wire keyboard navigation: Alt+1..N switch launcher pages and
        Ctrl+F focuses the library search box.

        Idempotent — guarded so the once-on-startup caller (or any future
        re-entry) doesn't stack duplicate QShortcuts.
        """
        if getattr(self, "_shortcuts_installed", False):
            return
        self._shortcuts_installed = True

        for i in range(len(getattr(self, "nav_buttons", []))):
            sc = QShortcut(QKeySequence(f"Alt+{i + 1}"), self)
            sc.activated.connect(lambda idx=i: self._switch_page(idx))

        search_sc = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)

        def _focus_search() -> None:
            # Search lives on the "Applications" page (index 1); jump there first
            # so the box is visible, then focus + select-all for a retype.
            self._switch_page(1)
            box = getattr(self, "search_box", None)
            if box is not None:
                box.setFocus(Qt.FocusReason.ShortcutFocusReason)
                box.selectAll()

        search_sc.activated.connect(_focus_search)

    def _maybe_run_first_launch_checks(self) -> None:
        """Resume recorded setup work; only a new install needs the setup wizard."""
        # Startup-time GUI-thread hook (fired once via QTimer from __init__)
        # — a convenient, owned place to register keyboard shortcuts now
        # that nav_buttons + search_box exist.
        self._install_shortcuts()

        from winpodx.utils.pending import has_pending, list_pending

        pending_steps = list_pending()
        if self._first_install_pending and self._first_install_needs_setup and not pending_steps:
            QTimer.singleShot(0, self._show_first_run_setup_prompt)
            return

        if pending_steps or self._first_install_pending:

            def _stream(line: str) -> None:
                self.log_signal.emit(line, C.SUBTEXT1)

            def _do() -> None:
                from winpodx.core.transport import agent_only
                from winpodx.utils.pending import resume

                with agent_only():
                    resume(printer=_stream)
                self.pending_setup_resumed.emit(not has_pending())

            threading.Thread(target=_do, daemon=True).start()
            return

        # First-launch wizard: only show when no apps have ever been
        # discovered AND the welcome marker is missing. After dismiss
        # the marker is written so we don't pester returning users.
        marker = Path(self.cfg.path()).parent / ".welcomed"
        if not marker.exists() and not self.apps:
            QTimer.singleShot(1500, self._show_quick_start)

    @Slot(bool)
    def _on_pending_setup_resumed(self, cleared: bool) -> None:
        from winpodx.core.config import Config
        from winpodx.utils.pending import has_pending

        if not cleared or has_pending():
            return
        self.cfg = Config.load()
        if self._first_install_pending:
            self._first_install_setup_succeeded = True
            self._refresh_pod_status()
        else:
            self._reload_apps()
            self.log_signal.emit(
                "[WinPodX] Pending setup resume finished — app list refreshed.",
                C.GREEN,
            )

    def _show_first_run_setup_prompt(self) -> None:
        """First-run setup wizard. Skip dismisses; prompt re-fires next launch."""
        from winpodx.gui._setup_wizard import SetupWizardDialog

        self._first_install_setup_succeeded = False
        self.hide()
        dlg = SetupWizardDialog(self, mode="first-run", cfg=self.cfg)
        dlg.finished.connect(self._on_first_run_wizard_finished)
        self._first_run_wizard = dlg
        dlg.open()

    def _on_first_run_wizard_finished(self, result: int) -> None:
        from PySide6.QtWidgets import QDialog

        self.show()
        dlg = getattr(self, "_first_run_wizard", None)
        self._first_run_wizard = None
        if result != QDialog.DialogCode.Accepted or dlg is None:
            return
        from winpodx.core.config import Config

        self.cfg = Config.load()
        if self._first_install_pending:
            self._first_install_setup_succeeded = dlg.setup_succeeded
            if self._first_install_setup_succeeded:
                self._refresh_pod_status()
        else:
            self._reload_apps()
        if dlg.open_apps:
            self._switch_page(1)
        elif dlg.open_terminal:
            self._switch_page(4)

    def _show_quick_start(self) -> None:
        """First-run welcome dialog: brief checklist of what's set up,
        what's pending, and a 'Run checks now' button that fires the
        same resume() pipeline used after a partial install.

        Safe to dismiss — writing the .welcomed marker prevents repeat.
        """
        from winpodx.core.deps_quickcheck import collect_first_run_checks
        from winpodx.utils.pending import has_pending

        snapshot = collect_first_run_checks(self.cfg)
        lines = [
            tr("Welcome to WinPodX!"),
            "",
            tr("First-run quick check:"),
            tr("  · Container backend ({backend}): {status}").format(
                backend=self.cfg.pod.backend, status=snapshot["backend"]
            ),
            tr("  · FreeRDP: {status}").format(status=snapshot["freerdp"]),
            tr("  · Pod state: {status}").format(status=snapshot["pod_state"]),
            tr("  · RDP listener: {status}").format(status=snapshot["rdp_port"]),
            tr("  · Discovered apps: {count}").format(count=snapshot["apps_count"]),
        ]
        if has_pending():
            lines.append("")
            lines.append(tr("Pending setup steps detected — running them in the background."))
        lines.append("")
        lines.append(tr("Tip: the Terminal / Logs page shows a live tail of the WinPodX log."))

        marker = Path(self.cfg.path()).parent / ".welcomed"
        try:
            marker.touch(exist_ok=True)
        except OSError:
            pass

        box = QMessageBox(self)
        box.setWindowTitle(tr("WinPodX — Quick Start"))
        box.setText("\n".join(lines))
        # Real next-actions so a first-timer has somewhere obvious to go,
        # wired to the existing page-switch + refresh handlers.
        settings_btn = box.addButton(tr("Open Settings"), QMessageBox.ButtonRole.ActionRole)
        refresh_btn = box.addButton(tr("Refresh Apps"), QMessageBox.ButtonRole.ActionRole)
        close_btn = box.addButton(tr("Close"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(close_btn)
        box.exec()
        clicked = box.clickedButton()

        if clicked is settings_btn:
            self._switch_page(2)  # Settings page (nav index == page index)
        elif clicked is refresh_btn:
            self._on_refresh_apps()
