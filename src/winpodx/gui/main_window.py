"""winpodx main GUI: top-nav app launcher and pod manager."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import list_available_apps
from winpodx.core.config import Config
from winpodx.gui._main_window_apps import AppCrudMixin
from winpodx.gui._main_window_header import HeaderMixin
from winpodx.gui._main_window_info import InfoPageMixin
from winpodx.gui._main_window_library import LibraryPageMixin
from winpodx.gui._main_window_logs import LogsMixin
from winpodx.gui._main_window_maintenance import MaintenanceMixin
from winpodx.gui._main_window_pod import PodStatusMixin
from winpodx.gui._main_window_settings import SettingsPageMixin
from winpodx.gui.theme import (
    GLOBAL_STYLE,
    C,
)
from winpodx.gui.workers import DiscoveryWorker

log = logging.getLogger(__name__)


class WinpodxWindow(
    AppCrudMixin,
    HeaderMixin,
    InfoPageMixin,
    LibraryPageMixin,
    LogsMixin,
    MaintenanceMixin,
    PodStatusMixin,
    SettingsPageMixin,
    QMainWindow,
):
    """Main window with horizontal top navigation bar."""

    # Thread-safe signals
    pod_status_updated = Signal(str, str)
    transport_status_updated = Signal(bool, bool, str)  # agent_ok, rdp_ok, agent_version
    app_launched = Signal(str)
    app_launch_failed = Signal(str)
    log_signal = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("winpodx")
        self.setMinimumSize(1000, 640)
        self.resize(1100, 720)

        self.cfg = Config.load()
        self.apps = list_available_apps()
        self._pod_state = "checking"
        self._view_mode = "grid"  # "grid" or "list"
        self._active_category = ""  # "" = all
        # Cooldown sentinel debounces rapid launch clicks; cleared via QTimer.
        self._recently_launched: set[str] = set()
        # Refresh state: idle -> scanning -> (success|error) -> idle.
        self._refresh_state = "idle"
        self._refresh_thread: QThread | None = None
        self._refresh_worker: DiscoveryWorker | None = None

        self._setup_signals()
        self._build_ui()
        self._start_status_timer()

        # v0.2.1: pending-setup resume + first-run quick start. Fired
        # asynchronously after the window paints so the user sees the
        # app immediately rather than blocking on a network probe.
        QTimer.singleShot(800, self._maybe_run_first_launch_checks)

    def _setup_signals(self) -> None:
        self.pod_status_updated.connect(self._on_pod_status)
        self.transport_status_updated.connect(self._on_transport_status)
        self.app_launched.connect(self._on_app_launched)
        self.app_launch_failed.connect(self._on_app_launch_failed)
        self.log_signal.connect(self._log_append)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet(f"background: {C.MANTLE};" + GLOBAL_STYLE)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_top_bar())

        self.status_banner = self._build_status_banner()
        root.addWidget(self.status_banner)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_library_page())
        self.pages.addWidget(self._build_settings_page())
        self.pages.addWidget(self._build_maintenance_page())
        self.pages.addWidget(self._build_logs_page())
        self.pages.addWidget(self._build_info_page())
        root.addWidget(self.pages)

        root.addWidget(self._build_info_bar())

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
        # v0.2.1: auto-start the winpodx app-log tail when the user
        # navigates to the Tools/Terminal page so they see live program
        # logs by default rather than just an empty terminal. Stops on
        # leaving so we don't leak `tail -F` processes.
        logs_index = 3  # _build_logs_page is the 4th page (Apps/Settings/Tools/Logs/Info)
        if index == logs_index:
            if getattr(self, "_tail_proc", None) is None:
                self._on_follow_app_log()
        else:
            self._on_stop_tail()

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
        Both branches are best-effort and silent on success."""
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

        # First-launch wizard: only show when no apps have ever been
        # discovered AND the welcome marker is missing. After dismiss
        # the marker is written so we don't pester returning users.
        marker = Path(self.cfg.path()).parent / ".welcomed"
        if not marker.exists() and not self.apps:
            QTimer.singleShot(1500, self._show_quick_start)

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


def run_gui() -> None:
    """Launch the winpodx GUI application."""
    app = QApplication(sys.argv)
    app.setApplicationName("winpodx")
    app.setStyle("Fusion")

    from winpodx.desktop.icons import bundled_data_path

    icon_path = bundled_data_path("winpodx-icon.svg")
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))

    from PySide6.QtGui import QPalette

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(C.BASE))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(C.TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(C.MANTLE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(C.SURFACE0))
    palette.setColor(QPalette.ColorRole.Text, QColor(C.TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(C.SURFACE0))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(C.TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(C.BLUE))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(C.CRUST))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(C.SURFACE0))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(C.TEXT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(C.OVERLAY0))
    app.setPalette(palette)

    window = WinpodxWindow()
    window.show()
    sys.exit(app.exec())
