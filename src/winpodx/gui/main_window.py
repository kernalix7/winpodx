"""winpodx main GUI: top-nav app launcher and pod manager."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
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
from winpodx.gui._main_window_nav import NavigationMixin
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
    NavigationMixin,
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
