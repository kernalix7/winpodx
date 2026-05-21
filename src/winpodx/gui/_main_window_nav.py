# SPDX-License-Identifier: MIT
"""Navigation + first-launch mixin for ``WinpodxWindow``.

Holds page-switch behaviour (start/stop the app-log tail and the
Info-page auto-refresh based on which tab is showing) and the
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

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from winpodx.core.app import list_available_apps
from winpodx.gui.theme import C


class NavigationMixin:
    """Page switching + first-launch checks."""

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
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
        info_index = 4
        if index == info_index:
            self._start_info_auto_refresh()
        else:
            self._stop_info_auto_refresh()

    def _maybe_run_first_launch_checks(self) -> None:
        """v0.2.1: on GUI startup, resume any pending install steps and —
        if this is genuinely a first run (no apps registered yet) —
        surface a one-shot Quick Start dialog summarising system state.

        #255: when ``cfg.pod.initialized`` is False, the first-run setup
        prompt fires *before* the quick-start dialog -- user picks
        auto / customize / skip, setup runs (auto) or wizard opens
        (customize), then we proceed to the normal quick-start flow.
        Both branches stay best-effort and silent on success."""
        from winpodx.utils.pending import has_pending

        if has_pending():

            def _stream(line: str) -> None:
                self.log_signal.emit(line, C.SUBTEXT1)

            def _do() -> None:
                from winpodx.utils.pending import resume

                resume(printer=_stream)
                # After resume, refresh the GUI's app list so any newly-
                # registered entries appear without manual refresh.
                self.apps = list_available_apps()
                self.log_signal.emit(
                    "[winpodx] Pending setup resume finished — app list refreshed.",
                    C.GREEN,
                )

            threading.Thread(target=_do, daemon=True).start()

        # #255: first-run setup prompt -- only fires when config exists
        # but isn't marked initialized (or when config is missing). The
        # CLI's first-run prompt covers the terminal path; this is the
        # GUI counterpart.
        if not getattr(self.cfg.pod, "initialized", False):
            QTimer.singleShot(1500, self._show_first_run_setup_prompt)
            return

        # First-launch wizard: only show when no apps have ever been
        # discovered AND the welcome marker is missing. After dismiss
        # the marker is written so we don't pester returning users.
        marker = Path(self.cfg.path()).parent / ".welcomed"
        if not marker.exists() and not self.apps:
            QTimer.singleShot(1500, self._show_quick_start)

    def _show_first_run_setup_prompt(self) -> None:
        """First-run setup prompt (#255 GUI counterpart).

        Three-way modal: Auto / Customize / Skip. Auto runs
        ``winpodx setup`` (non-interactive) on a worker thread,
        streaming output into the GUI log. Customize launches the
        wizard (PR 7 of #255; until that lands, falls back to Auto
        with a notice). Skip dismisses without action -- prompt
        re-fires on next launch.
        """
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox(self)
        box.setWindowTitle("Set up winpodx")
        box.setText("winpodx has not been set up yet on this account.\n\nRun setup now?")
        box.setInformativeText(
            "Auto:      host-detected defaults, no prompts (~5-10 min for "
            "Windows ISO download + Sysprep + OEM apply)\n"
            "Customize: wizard -- pick every knob (CPU/RAM, edition, "
            "language, debloat, tuning, ...)\n"
            "Skip:      do nothing; you can run `winpodx setup` later"
        )
        auto_btn = box.addButton("Auto", QMessageBox.ButtonRole.AcceptRole)
        customize_btn = box.addButton("Customize", QMessageBox.ButtonRole.ActionRole)
        skip_btn = box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(auto_btn)
        box.exec()
        clicked = box.clickedButton()

        if clicked is skip_btn:
            return

        mode = "customize" if clicked is customize_btn else "auto"
        self._run_first_run_setup(mode)

    def _run_first_run_setup(self, mode: str) -> None:
        """Spawn ``winpodx setup`` on a worker thread, stream output
        through the existing log signal. After completion, reload cfg
        so ``initialized = True`` takes effect, then trigger the
        normal quick-start.
        """
        import argparse

        def _stream(line: str) -> None:
            self.log_signal.emit(line, C.SUBTEXT1)

        def _do() -> None:
            from winpodx.cli.setup_cmd import handle_setup
            from winpodx.core.config import Config

            args = argparse.Namespace(
                backend=None,
                win_version=None,
                update_image=False,
                migrate_storage=False,
                migrate_storage_target=None,
                non_interactive=(mode == "auto"),
                customize=(mode == "customize"),
            )
            try:
                handle_setup(args)
                self.cfg = Config.load()
                _stream("[winpodx] Setup complete.")
            except Exception as e:  # noqa: BLE001
                _stream(f"[winpodx] Setup failed: {e}")

        threading.Thread(target=_do, daemon=True).start()

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
            "Welcome to winpodx!",
            "",
            "First-run quick check:",
            f"  · Container backend ({self.cfg.pod.backend}): {snapshot['backend']}",
            f"  · FreeRDP: {snapshot['freerdp']}",
            f"  · Pod state: {snapshot['pod_state']}",
            f"  · RDP listener: {snapshot['rdp_port']}",
            f"  · Discovered apps: {snapshot['apps_count']}",
        ]
        if has_pending():
            lines.append("")
            lines.append("Pending setup steps detected — running them in the background.")
        lines.append("")
        lines.append("Tip: Tools → Live (app) tails the winpodx log in real time.")

        marker = Path(self.cfg.path()).parent / ".welcomed"
        try:
            marker.touch(exist_ok=True)
        except OSError:
            pass

        QMessageBox.information(self, "winpodx — Quick Start", "\n".join(lines))
