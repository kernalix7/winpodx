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
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
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
    BTN_GHOST,
    BTN_PRIMARY,
    GLOBAL_STYLE,
    SCROLL_AREA,
    TERMINAL,
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

    def _build_logs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 28, 32, 20)

        header = QHBoxLayout()
        title = QLabel("Terminal")
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        # Route container name through cfg so renamed pods still work.
        container = self.cfg.pod.container_name
        quick = [
            ("Status", ["podman", "ps", "-a", "--filter", f"name={container}"]),
            ("Pod logs", ["podman", "logs", "--tail", "100", container]),
            ("Live (pod)", "follow_pod"),
            ("App log", "tail_app_log"),
            ("Live (app)", "follow_app_log"),
            ("Inspect", ["podman", "inspect", container]),
            ("RDP Test", None),
            ("Stop tail", "stop_tail"),
            ("Clear", None),
        ]
        for label, cmd in quick:
            btn = QPushButton(label)
            btn.setStyleSheet(BTN_GHOST)
            if label == "Clear":
                btn.clicked.connect(lambda: self.log_output.clear())
            elif label == "RDP Test":
                btn.clicked.connect(self._on_rdp_test)
            elif cmd == "follow_pod":
                btn.clicked.connect(self._on_follow_pod_log)
            elif cmd == "tail_app_log":
                btn.clicked.connect(self._on_tail_app_log)
            elif cmd == "follow_app_log":
                btn.clicked.connect(self._on_follow_app_log)
            elif cmd == "stop_tail":
                btn.clicked.connect(self._on_stop_tail)
            else:
                btn.clicked.connect(lambda _, c=cmd: self._run_log_cmd(c))
            header.addWidget(btn)

        layout.addLayout(header)
        layout.addSpacing(10)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet(TERMINAL)
        layout.addWidget(self.log_output)

        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(8)

        prompt = QLabel("\u276f")
        prompt.setStyleSheet(
            f"background: transparent; color: {C.BLUE}; font-size: 16px; font-weight: bold;"
        )
        cmd_row.addWidget(prompt)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText(
            f"Enter command (e.g. podman logs {self.cfg.pod.container_name})"
        )
        self.cmd_input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.CRUST}; color: {C.TEXT};
                border: 1px solid {C.SURFACE0}; border-radius: 8px;
                padding: 10px 14px;
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {C.BLUE}; }}
        """)
        self.cmd_input.returnPressed.connect(self._on_cmd_enter)
        cmd_row.addWidget(self.cmd_input)

        run_btn = QPushButton("Run")
        run_btn.setStyleSheet(BTN_PRIMARY)
        run_btn.clicked.connect(self._on_cmd_enter)
        cmd_row.addWidget(run_btn)

        layout.addLayout(cmd_row)
        return page

    def _build_info_page(self) -> QWidget:
        """5-section system snapshot: System / Display / Dependencies / Pod / Config.

        Mirrors `winpodx info` via the shared `core.info.gather_info` helper.
        Pod section probes RDP/VNC ports + queries podman inspect, so the
        initial paint is async via QThread and the user can re-run on demand
        with the Refresh button.
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_AREA)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("Info")
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        refresh_btn = QPushButton("Refresh Info")
        refresh_btn.setIcon(QIcon.fromTheme("view-refresh"))
        refresh_btn.setStyleSheet(BTN_GHOST)
        refresh_btn.clicked.connect(self._refresh_info)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # Containers for the 5 cards. Initial population goes through
        # _refresh_info which dispatches a worker thread; until that thread
        # returns, each card shows "Loading...".
        self._info_cards: dict[str, QFrame] = {}
        self._info_card_bodies: dict[str, QVBoxLayout] = {}
        # Health goes first so the user lands on live state before the
        # static system snapshot. Each probe renders as `[OK] detail` with
        # a colored badge — matches the `winpodx check` CLI output.
        for key, label in [
            ("health", "Health"),
            ("system", "System"),
            ("display", "Display"),
            ("dependencies", "Dependencies"),
            ("pod", "Pod"),
            ("config", "Config"),
        ]:
            card = self._info_card(label)
            self._info_cards[key] = card
            layout.addWidget(card)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # v0.1.9.1: Defer the first fetch out of __init__. Calling
        # _refresh_info() synchronously here can race with the rest of
        # the main-window construction — the worker thread fires its
        # `done` signal back into a partially-built window and hits the
        # same QMessageBox font-lookup SEGV the Apps refresh path saw.
        QTimer.singleShot(0, self._refresh_info)
        return page

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
