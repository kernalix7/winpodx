# SPDX-License-Identifier: MIT
"""winpodx main GUI: launcher home and pod manager."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import list_available_apps
from winpodx.core.config import Config
from winpodx.gui._main_window_apps import AppCrudMixin
from winpodx.gui._main_window_bringup import BringUpMixin
from winpodx.gui._main_window_dashboard import DashboardMixin
from winpodx.gui._main_window_devices import DevicesMixin
from winpodx.gui._main_window_header import HeaderMixin
from winpodx.gui._main_window_info import InfoPageMixin
from winpodx.gui._main_window_library import LibraryPageMixin
from winpodx.gui._main_window_license import LicensePageMixin
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
    BringUpMixin,
    DashboardMixin,
    DevicesMixin,
    HeaderMixin,
    InfoPageMixin,
    LibraryPageMixin,
    LicensePageMixin,
    LogsMixin,
    MaintenanceMixin,
    NavigationMixin,
    PodStatusMixin,
    SettingsPageMixin,
    QMainWindow,
):
    """Main window with launcher home and overflow-menu navigation."""

    # Thread-safe signals
    pod_status_updated = Signal(str, str)
    transport_status_updated = Signal(bool, bool, str)  # agent_ok, rdp_ok, agent_version
    app_launched = Signal(str)
    app_launch_failed = Signal(str)
    log_signal = Signal(str, str)
    # Dashboard resource snapshot, emitted from the off-thread probe and
    # painted onto the gauges on the GUI thread (see DashboardMixin).
    dashboard_updated = Signal(object)
    # v0.5.1 bring-up signals (see _main_window_bringup.py).
    # ``bringup_phase`` is (phase_label, sub_detail); the dialog renders
    # both rows. ``bringup_done`` is (success, error_msg). ``bringup_started``
    # is the cross-thread kick from the recreate worker to the GUI thread
    # so we can construct the dialog without touching Qt off-thread.
    bringup_phase = Signal(str, str)
    bringup_done = Signal(bool, str)
    bringup_started = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WinPodX")
        # No hardcoded minimum: the window minimum is derived entirely from the
        # page content (the widgets' own minimum boxes) by _sync_scroll_minimums()
        # — called after the UI is built and on every resize — so the window can't
        # be dragged narrower than what the buttons / terminal / forms need.
        # Preferred opening size, clamped to the screen by _fit_to_screen()
        # after the UI is built (a fixed 1100px window ran off the right edge
        # on smaller displays, clipping the Save button + Hardware column).
        self._preferred_size = (1100, 720)

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
        self._fit_to_screen()
        self._start_status_timer()

        # v0.5.1: always-on tails feeding the bottom log bar + Terminal.
        # The app-log tail starts unconditionally — ``tail -F`` handles
        # the missing-file case for fresh installs. The pod tail is
        # gated on ``cfg.logging.is_raw()`` so users on standard levels
        # don't see dockur boot noise in their bar.
        self._on_follow_app_log()
        if self.cfg.logging.is_raw():
            self._start_raw_pod_tail()

        # v0.2.1: pending-setup resume + first-run quick start. Fired
        # asynchronously after the window paints so the user sees the
        # app immediately rather than blocking on a network probe.
        QTimer.singleShot(800, self._maybe_run_first_launch_checks)

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override
        """Join any in-flight worker threads before the window — and its
        child QThread objects — are destroyed.

        Both the "Refresh Apps" DiscoveryWorker thread (_main_window_apps.py)
        and the Info-tab InfoWorker thread (_main_window_info.py) are created
        as ``QThread(self)`` — children of this window. If the window is torn
        down while one is still running, ``~QThread`` sees ``isRunning()`` and
        aborts ("QThread: Destroyed while thread is still running"). These
        threads have no other cancellation path, so we quit()+wait() them
        here. ``wait()`` is unbounded on purpose: a bounded wait that timed
        out would leave a running thread to be destroyed, re-introducing the
        abort. Both workers' run() always return (each emits its terminal
        signal from a try/except), so wait() is guaranteed to complete.
        """
        self._join_worker_threads()
        super().closeEvent(event)

    def _join_worker_threads(self) -> None:
        for attr in ("_refresh_thread", "_info_thread"):
            thread = getattr(self, attr, None)
            if thread is None:
                continue
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait()
            except RuntimeError:
                # Underlying C++ QThread already deleted via deleteLater —
                # the worker finished on its own; nothing to join.
                pass

    def _setup_signals(self) -> None:
        self.pod_status_updated.connect(self._on_pod_status)
        self.transport_status_updated.connect(self._on_transport_status)
        self.app_launched.connect(self._on_app_launched)
        self.app_launch_failed.connect(self._on_app_launch_failed)
        self.log_signal.connect(self._log_append)
        self.dashboard_updated.connect(self._apply_snapshot)
        # Fan-out: the same log_signal also feeds the always-visible
        # 2-line bottom log bar (the QLabel pair built by
        # HeaderMixin._build_log_bar). This way every line that flows
        # through Terminal's full QTextEdit history also flashes at
        # the bottom of the window regardless of which page is open.
        self.log_signal.connect(self._update_log_bar)
        # Bring-up dialog kick-off: the worker thread emits this and
        # _open_bringup_dialog (BringUpMixin) runs on the GUI thread.
        self.bringup_started.connect(self._open_bringup_dialog)

    def _update_log_bar(self, line: str, color: str) -> None:
        """Push the latest log line onto the bottom bar (2-line ticker)."""
        # Shift current top → second slot, put new line on top.
        self.log_bar_line2.setText(self.log_bar_line1.text())
        # Elide so long winpodx log messages don't blow out the bar
        # width — the full line is still in the Terminal QTextEdit.
        fm = self.log_bar_line1.fontMetrics()
        available = max(self.log_bar_line1.width() - 4, 200)
        elided = fm.elidedText(line, Qt.TextElideMode.ElideRight, available)
        self.log_bar_line1.setText(elided)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralRoot")
        central.setStyleSheet(f"QWidget#centralRoot {{ background: {C.MANTLE}; }}\n" + GLOBAL_STYLE)
        self.setCentralWidget(central)
        # Horizontal shell: left nav sidebar | content column (slim top strip
        # + stacked pages). Mirrors the Start-menu-style mockup.
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        content = QWidget()
        content_col = QVBoxLayout(content)
        content_col.setContentsMargins(0, 0, 0, 0)
        content_col.setSpacing(0)
        content_col.addWidget(self._build_top_strip())

        # Clean launcher chrome: the old full-width status banner and the
        # bottom info/log bars are NOT mounted -- the top-strip pod chip carries
        # pod state + start/stop, and logs live on the Terminal page. The
        # widgets are still built (kept referenced) so their updater methods
        # (_apply_status_banner / _update_log_bar / pod-status info labels)
        # stay valid no-ops on hidden, unmounted widgets. They are parented to
        # ``central`` and hidden so they never float as top-level windows or
        # flash a (0,0) ghost (same orphan-widget class as the License ghost).
        self.status_banner = self._build_status_banner()
        self._hidden_info_bar = self._build_info_bar()
        self._hidden_log_bar = self._build_log_bar()
        for _unmounted in (self.status_banner, self._hidden_info_bar, self._hidden_log_bar):
            _unmounted.setParent(central)
            _unmounted.hide()

        # Page order == nav order (the _switch_page nav-index == page-index
        # invariant). Dashboard is the home (index 0); the app launcher moves
        # to "All apps" (index 1). License stays last.
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_dashboard_page())
        self.pages.addWidget(self._build_library_page())
        self.pages.addWidget(self._build_settings_page())
        self.pages.addWidget(self._build_maintenance_page())
        self.pages.addWidget(self._build_logs_page())
        self.pages.addWidget(self._build_info_page())
        self.pages.addWidget(self._build_devices_page())
        self.pages.addWidget(self._build_license_page())
        content_col.addWidget(self.pages, 1)

        root.addWidget(content, 1)

    def _fit_to_screen(self) -> None:
        """Open at the preferred size, but never larger than the screen.

        Clamps the window to the available area of the *primary* screen so it
        can't open bigger than the display on small or fractionally-scaled
        setups. Placement (which monitor, where) is left to the window manager
        / compositor on purpose: forcing a position here parked the window on
        the leftmost monitor instead of the user's designated primary on
        multi-monitor setups (#498), and a client ``move()`` is a no-op on
        Wayland anyway. Clamping against the primary screen — not whatever
        screen the not-yet-shown window happens to be associated with (usually
        the leftmost) — also stops the window being squeezed to a narrow
        portrait monitor's width.
        """
        pref_w, pref_h = getattr(self, "_preferred_size", (1100, 720))
        screen = QApplication.primaryScreen() or self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            if avail.width() > 0 and avail.height() > 0:
                # Drive the minimum width from the page content (the widgets'
                # own minimum "boxes"), not a magic number, so the window can't
                # shrink past where buttons / the terminal / forms would clip.
                self._sync_scroll_minimums()
                w = max(self.minimumWidth(), min(pref_w, avail.width() - 60))
                h = max(self.minimumHeight(), min(pref_h, avail.height() - 80))
                self.resize(w, h)
                return
        self.resize(pref_w, pref_h)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        # Keep responsive page layouts (the Settings + Devices two-column
        # forms) in step with the live window width as the user drags it.
        if hasattr(self, "_reflow_settings"):
            self._reflow_settings()
        if hasattr(self, "_reflow_devices"):
            self._reflow_devices()
        if hasattr(self, "_reflow_dashboard"):
            self._reflow_dashboard()
        if hasattr(self, "_reflow_library"):
            self._reflow_library()
        self._sync_scroll_minimums()

    def _sync_scroll_minimums(self) -> None:
        """Let the content's own minimum size drive the window minimum.

        Page bodies live in ``QScrollArea``s, which by default report a tiny
        minimum (they exist to shrink + scroll), so the window could otherwise
        shrink *below* the point where the buttons / monospace terminal / form
        rows actually fit and clip them. Pin each scroll area's minimum width to
        its content's ``minimumSizeHint`` (the widgets' real "boxes") so the
        window can't be dragged narrower than the widest visible page needs.
        Runs after the reflows, so a two-column page that has stacked to one
        column reports its smaller single-column minimum. Never raises.
        """
        from PySide6.QtWidgets import QScrollArea

        for area in self.findChildren(QScrollArea):
            inner = area.widget()
            if inner is not None:
                # +18 leaves room for the vertical scrollbar.
                area.setMinimumWidth(inner.minimumSizeHint().width() + 18)


def run_gui() -> None:
    """Launch the winpodx GUI application."""
    # Fractional display scaling (common on KDE / GNOME Wayland at 125% / 150%)
    # must pass through untouched -- rounding it to an integer factor makes
    # fixed-size widgets and the custom-painted gauges mismatch the rest of the
    # UI ("text breaks, scale breaks"). Must be set before the QApplication.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
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

    # Spawn the tray subprocess so the user gets system-tray + auto-
    # recovery (RUNNING -> UNRESPONSIVE detection + agent-driven RDP
    # repair) without having to manually run `winpodx tray &`. tray.py
    # acquires a flock on its lockfile, so a second invocation when one
    # is already running exits silently instead of stacking icons.
    from winpodx.desktop.tray_spawn import maybe_spawn_tray

    maybe_spawn_tray()

    sys.exit(app.exec())
