"""winpodx main GUI: top-nav app launcher and pod manager."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import AppInfo, list_available_apps
from winpodx.core.config import Config
from winpodx.core.pod import pod_status
from winpodx.gui.theme import (
    ACTION_ROW,
    APP_CARD,
    APP_TILE,
    BTN_ACCENT,
    BTN_DANGER,
    BTN_GHOST,
    BTN_PRIMARY,
    COMBO,
    FILTER_CHIP,
    GLOBAL_STYLE,
    INFO_BAR,
    INPUT,
    POD_CHIP,
    POD_CTRL,
    SCROLL_AREA,
    SEARCH_BAR,
    SETTINGS_SECTION,
    STATUS_BANNER_WARN,
    TAB_BTN,
    TERMINAL,
    TOP_BAR,
    VIEW_TOGGLE,
    C,
    accent_color,
    avatar_color,
)

log = logging.getLogger(__name__)


class _DiscoveryWorker(QObject):
    """Background worker that runs core.discovery scan and persist off the UI thread.

    State machine driven from the main window:
      idle -> scanning -> (succeeded | failed) -> idle

    Emits ``succeeded(count)`` with the number of persisted apps on success,
    or ``failed(kind, detail)`` where ``kind`` is a short token (``pod_not_running``,
    ``module_missing``, ``unexpected``) the UI uses to decide inline actions
    such as offering a "Start Pod" shortcut.
    """

    succeeded = Signal(int)
    failed = Signal(str, str)
    finished = Signal()

    @Slot()
    def run(self) -> None:
        try:
            from winpodx.core import discovery as discovery_mod
            from winpodx.core.config import Config
        except ImportError as exc:
            self.failed.emit("module_missing", str(exc))
            self.finished.emit()
            return

        try:
            cfg = Config.load()
            apps = discovery_mod.discover_apps(cfg)
        except Exception as exc:  # noqa: BLE001 — worker surfaces all errors to UI
            kind = "pod_not_running" if _looks_like_pod_down(exc) else "unexpected"
            self.failed.emit(kind, str(exc))
            self.finished.emit()
            return

        try:
            persisted = discovery_mod.persist_discovered(apps)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit("unexpected", str(exc))
            self.finished.emit()
            return

        # v0.2.0.10: parity with CLI's `_refresh_apps` — install .desktop
        # entries inline so the GUI Refresh button registers DE menu
        # entries the same way as `winpodx app refresh`. Bidirectional
        # sync (drop entries that no longer match a discovered app) is
        # handled by `_sync_desktop_entries`.
        try:
            _sync_desktop_entries(apps)
        except Exception:  # noqa: BLE001 — best-effort
            log.debug("GUI refresh: desktop-entry sync failed", exc_info=True)

        try:
            from winpodx.desktop.icons import refresh_icon_cache

            refresh_icon_cache()
        except Exception:  # noqa: BLE001 — cache refresh is best-effort
            pass

        try:
            count = len(persisted)
        except TypeError:
            count = len(apps)
        self.succeeded.emit(count)
        self.finished.emit()


def _sync_desktop_entries(discovered) -> None:
    """v0.2.0.10: bidirectional .desktop entry sync used by the GUI
    refresh worker. Mirrors `cli/app._register_desktop_entries` but
    safe to run from a worker thread (no Qt GUI calls)."""
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry, remove_desktop_entry
    from winpodx.utils.paths import applications_dir

    discovered_slugs = {d.slug or d.name for d in discovered}
    available = {a.name: a for a in list_available_apps()}
    for slug in discovered_slugs:
        info = available.get(slug)
        if info is not None:
            try:
                install_desktop_entry(info)
            except Exception:  # noqa: BLE001
                log.debug("install_desktop_entry failed for %s", slug, exc_info=True)

    apps_dir = applications_dir()
    if apps_dir.exists():
        for entry in apps_dir.glob("winpodx-*.desktop"):
            stem = entry.stem
            if not stem.startswith("winpodx-"):
                continue
            slug = stem[len("winpodx-") :]
            if slug in {"", "gui", "launcher"}:
                continue
            if slug in available:
                continue
            try:
                remove_desktop_entry(slug)
            except Exception:  # noqa: BLE001
                log.debug("remove_desktop_entry failed for %s", slug, exc_info=True)


def _looks_like_pod_down(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(tok in text for tok in ("pod", "container", "connection refused", "not running"))


class _InfoWorker(QObject):
    """Background worker for the Info page's gather_info() call.

    Hoisted to module level (was nested inside _refresh_info) so PySide6
    doesn't re-create the QObject metaclass on every refresh — repeated
    nested-class definition has been observed to interact badly with
    Qt's metaobject cache, contributing to the v0.1.9 SEGV path.
    """

    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, cfg) -> None:
        super().__init__()
        self.cfg = cfg

    @Slot()
    def run(self) -> None:
        try:
            from winpodx.core.info import gather_info

            self.done.emit(gather_info(self.cfg))
        except Exception as e:  # noqa: BLE001 — surface to UI via signal
            self.failed.emit(str(e))


class WinpodxWindow(QMainWindow):
    """Main window with horizontal top navigation bar."""

    # Thread-safe signals
    pod_status_updated = Signal(str, str)
    app_launched = Signal(str)
    app_launch_failed = Signal(str)
    log_signal = Signal(str, str)

    @staticmethod
    def _add_shadow(
        widget: QWidget,
        blur: int = 16,
        y: int = 3,
        alpha: int = 45,
    ) -> None:
        """Apply subtle drop shadow for depth effect."""
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(blur)
        shadow.setOffset(0, y)
        shadow.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(shadow)

    @staticmethod
    def _make_source_badge(app: AppInfo) -> QLabel | None:
        """Pill badge marking app provenance: Detected (from scan) vs Bundled.

        Returns None when ``AppInfo.source`` is absent (older cores) or equals
        the default user-authored provenance, so legacy apps stay unannotated.
        """
        source = getattr(app, "source", "bundled")
        if source == "discovered":
            text = "Detected"
            bg = C.SAPPHIRE
            fg = C.CRUST
        elif source == "bundled":
            text = "Bundled"
            bg = C.SURFACE2
            fg = C.SUBTEXT1
        else:
            return None

        badge = QLabel(text)
        badge.setStyleSheet(
            f"background: {bg}; color: {fg};"
            " border-radius: 7px;"
            " font-size: 9px; font-weight: bold;"
            " padding: 2px 7px;"
            " letter-spacing: 0.3px;"
        )
        return badge

    @staticmethod
    def _make_app_avatar(app: AppInfo, size: int, *, radius: int, font_size: int) -> QLabel:
        """Build the avatar label for an app row/card.

        When ``app.icon_path`` points at a real PNG / SVG, render the icon
        scaled to ``size`` with a subtle surface background for contrast.
        Otherwise fall back to the colored single-letter avatar (legacy
        look for apps without a discovered icon).
        """
        avatar = QLabel()
        avatar.setFixedSize(size, size)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon_path = (app.icon_path or "").strip()
        pixmap: QPixmap | None = None
        if icon_path and Path(icon_path).is_file():
            pad = max(4, size // 7)
            inner = size - pad * 2
            try:
                if icon_path.lower().endswith(".svg"):
                    renderer = QSvgRenderer(icon_path)
                    if renderer.isValid():
                        pm = QPixmap(inner, inner)
                        pm.fill(Qt.GlobalColor.transparent)
                        from PySide6.QtGui import QPainter

                        painter = QPainter(pm)
                        renderer.render(painter)
                        painter.end()
                        pixmap = pm
                else:
                    pm = QPixmap(icon_path)
                    if not pm.isNull():
                        pixmap = pm.scaled(
                            inner,
                            inner,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
            except Exception:  # noqa: BLE001
                pixmap = None

        if pixmap is not None and not pixmap.isNull():
            avatar.setPixmap(pixmap)
            avatar.setStyleSheet(
                f"background: {C.SURFACE1}; border-radius: {radius}px; padding: 0px;"
            )
            return avatar

        # Fallback: legacy colored letter avatar.
        color = avatar_color(app.name)
        letter = app.full_name[0].upper() if app.full_name else "?"
        avatar.setText(letter)
        avatar.setStyleSheet(
            f"background: {color};"
            f" color: {C.CRUST};"
            f" border-radius: {radius}px;"
            f" font-size: {font_size}px; font-weight: bold;"
        )
        return avatar

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
        self._refresh_worker: _DiscoveryWorker | None = None

        self._setup_signals()
        self._build_ui()
        self._start_status_timer()

    def _setup_signals(self) -> None:
        self.pod_status_updated.connect(self._on_pod_status)
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

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setStyleSheet(TOP_BAR)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(0)

        from winpodx.desktop.icons import bundled_data_path

        icon_path = bundled_data_path("winpodx-icon.svg")
        if icon_path is not None:
            renderer = QSvgRenderer(str(icon_path))
            pixmap = QPixmap(QSize(28, 24))
            pixmap.fill(Qt.GlobalColor.transparent)
            from PySide6.QtGui import QPainter

            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            logo_icon = QLabel()
            logo_icon.setPixmap(pixmap)
            logo_icon.setStyleSheet("background: transparent;")
            layout.addWidget(logo_icon)
            layout.addSpacing(8)

        logo_text = QLabel("winpodx")
        logo_text.setStyleSheet(
            f"background: transparent; color: {C.TEXT};"
            " font-size: 16px; font-weight: bold;"
            " letter-spacing: 1px;"
        )
        layout.addWidget(logo_text)
        layout.addSpacing(32)

        tab_container = QWidget()
        tab_container.setStyleSheet(TAB_BTN)
        tabs = QHBoxLayout(tab_container)
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(0)

        self.nav_buttons: list[QPushButton] = []
        for label, idx in [
            ("Apps", 0),
            ("Settings", 1),
            ("Tools", 2),
            ("Terminal", 3),
            ("Info", 4),
        ]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, i=idx: self._switch_page(i))
            tabs.addWidget(btn)
            self.nav_buttons.append(btn)

        self.nav_buttons[0].setChecked(True)
        layout.addWidget(tab_container)
        layout.addStretch()

        chip = QFrame()
        chip.setObjectName("podChip")
        chip.setStyleSheet(POD_CHIP)
        chip_l = QHBoxLayout(chip)
        chip_l.setContentsMargins(12, 4, 6, 4)
        chip_l.setSpacing(6)

        self.pod_dot = QLabel("\u25cf")
        self.pod_dot.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; font-size: 10px;"
        )
        chip_l.addWidget(self.pod_dot)

        self.pod_label = QLabel("checking")
        self.pod_label.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; font-size: 12px;"
        )
        chip_l.addWidget(self.pod_label)

        ctrl_w = QWidget()
        ctrl_w.setStyleSheet(POD_CTRL)
        ctrl_l = QHBoxLayout(ctrl_w)
        ctrl_l.setContentsMargins(4, 0, 0, 0)
        ctrl_l.setSpacing(2)

        self.btn_start = QPushButton("\u25b6")
        self.btn_start.setToolTip("Start Pod")
        self.btn_start.clicked.connect(self._on_start_pod)
        ctrl_l.addWidget(self.btn_start)

        self.btn_stop = QPushButton("\u25a0")
        self.btn_stop.setToolTip("Stop Pod")
        self.btn_stop.clicked.connect(self._on_stop_pod)
        ctrl_l.addWidget(self.btn_stop)

        chip_l.addWidget(ctrl_w)
        layout.addWidget(chip)

        return bar

    def _build_status_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("statusBanner")
        banner.setStyleSheet(STATUS_BANNER_WARN)

        layout = QHBoxLayout(banner)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        self.banner_icon = QLabel("\u26a0")
        self.banner_icon.setStyleSheet(
            f"background: transparent; color: {C.YELLOW}; font-size: 14px;"
        )
        layout.addWidget(self.banner_icon)

        self.banner_text = QLabel("Pod is not running")
        self.banner_text.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; font-size: 12px;"
        )
        layout.addWidget(self.banner_text)
        layout.addStretch()

        start_btn = QPushButton("Start Now")
        start_btn.setStyleSheet(
            f"QPushButton {{ background: {C.BLUE}; color: {C.CRUST};"
            f" border: none; border-radius: 6px;"
            f" padding: 4px 14px; font-size: 12px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {C.LAVENDER}; }}"
        )
        start_btn.clicked.connect(self._on_start_pod)
        layout.addWidget(start_btn)

        banner.setVisible(True)
        return banner

    def _build_info_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("infoBar")
        bar.setStyleSheet(INFO_BAR)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(16)

        self.info_label = QLabel(f"{len(self.apps)} apps available")
        self.info_label.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;"
        )
        layout.addWidget(self.info_label)
        layout.addStretch()

        self.info_pod_dot = QLabel("\u25cf")
        self.info_pod_dot.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 8px;"
        )
        layout.addWidget(self.info_pod_dot)

        self.info_pod_state = QLabel("checking")
        self.info_pod_state.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;"
        )
        layout.addWidget(self.info_pod_state)

        sep = QLabel("\u2502")
        sep.setStyleSheet(f"background: transparent; color: {C.SURFACE1}; font-size: 11px;")
        layout.addWidget(sep)

        backend_lbl = QLabel(f"{self.cfg.pod.backend}")
        backend_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        layout.addWidget(backend_lbl)

        res_lbl = QLabel(f"{self.cfg.pod.cpu_cores} CPU \u00b7 {self.cfg.pod.ram_gb} GB")
        res_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        layout.addWidget(res_lbl)

        return bar

    def _build_library_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 20)
        layout.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(12)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search apps...")
        self.search_box.setStyleSheet(SEARCH_BAR)
        self.search_box.setFixedWidth(340)
        self.search_box.textChanged.connect(self._filter_apps)
        toolbar.addWidget(self.search_box)

        toolbar.addStretch()

        self.app_count_label = QLabel(f"{len(self.apps)} apps")
        self.app_count_label.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 12px;"
        )
        toolbar.addWidget(self.app_count_label)
        toolbar.addSpacing(4)

        toggle_wrap = QWidget()
        toggle_wrap.setStyleSheet(VIEW_TOGGLE)
        tgl = QHBoxLayout(toggle_wrap)
        tgl.setContentsMargins(0, 0, 0, 0)
        tgl.setSpacing(2)

        self.btn_grid = QPushButton("\u25a6")
        self.btn_grid.setCheckable(True)
        self.btn_grid.setChecked(True)
        self.btn_grid.setToolTip("Grid view")
        self.btn_grid.clicked.connect(lambda: self._set_view("grid"))
        tgl.addWidget(self.btn_grid)

        self.btn_list = QPushButton("\u2261")
        self.btn_list.setCheckable(True)
        self.btn_list.setToolTip("List view")
        self.btn_list.clicked.connect(lambda: self._set_view("list"))
        tgl.addWidget(self.btn_list)
        toolbar.addWidget(toggle_wrap)
        toolbar.addSpacing(8)

        self.refresh_btn = QPushButton("Refresh Apps")
        self.refresh_btn.setIcon(QIcon.fromTheme("view-refresh"))
        self.refresh_btn.setStyleSheet(BTN_GHOST)
        self.refresh_btn.setToolTip("Scan the running pod for installed Windows apps")
        self.refresh_btn.clicked.connect(self._on_refresh_apps)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addSpacing(6)

        add_btn = QPushButton("+  Add App")
        add_btn.setStyleSheet(BTN_PRIMARY)
        add_btn.clicked.connect(self._on_add_app)
        toolbar.addWidget(add_btn)

        layout.addLayout(toolbar)
        layout.addSpacing(12)

        self.refresh_progress = QProgressBar()
        self.refresh_progress.setRange(0, 0)  # indeterminate
        self.refresh_progress.setTextVisible(False)
        self.refresh_progress.setFixedHeight(3)
        self.refresh_progress.setVisible(False)
        self.refresh_progress.setStyleSheet(
            f"QProgressBar {{ background: {C.SURFACE0}; border: none; border-radius: 1px; }}"
            f"QProgressBar::chunk {{ background: {C.BLUE}; }}"
        )
        layout.addWidget(self.refresh_progress)

        self._category_row = QHBoxLayout()
        self._category_row.setSpacing(6)
        self._category_btns: list[QPushButton] = []
        self._build_category_chips()
        layout.addLayout(self._category_row)
        layout.addSpacing(16)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_AREA)

        self.app_list_container = QWidget()
        self.app_list_container.setStyleSheet("background: transparent;")
        self.app_list_layout = QVBoxLayout(self.app_list_container)
        self.app_list_layout.setContentsMargins(0, 0, 0, 0)
        self.app_list_layout.setSpacing(0)
        self._populate_app_view(self.apps)

        scroll.setWidget(self.app_list_container)
        layout.addWidget(scroll)
        return page

    def _build_category_chips(self) -> None:
        """Build category filter chips from available apps."""
        cats: set[str] = set()
        for a in self.apps:
            cats.update(a.categories)
        cats_sorted = sorted(cats)

        all_btn = QPushButton("All")
        all_btn.setCheckable(True)
        all_btn.setChecked(True)
        all_btn.setStyleSheet(FILTER_CHIP)
        all_btn.clicked.connect(lambda: self._set_category(""))
        self._category_row.addWidget(all_btn)
        self._category_btns.append(all_btn)

        for cat in cats_sorted[:8]:
            btn = QPushButton(cat)
            btn.setCheckable(True)
            btn.setStyleSheet(FILTER_CHIP)
            btn.clicked.connect(lambda _, c=cat: self._set_category(c))
            self._category_row.addWidget(btn)
            self._category_btns.append(btn)

        self._category_row.addStretch()

    def _set_category(self, category: str) -> None:
        self._active_category = category
        for btn in self._category_btns:
            is_match = (category == "" and btn.text() == "All") or btn.text() == category
            btn.setChecked(is_match)
        self._filter_apps(self.search_box.text())

    def _set_view(self, mode: str) -> None:
        self._view_mode = mode
        self.btn_grid.setChecked(mode == "grid")
        self.btn_list.setChecked(mode == "list")
        self._filter_apps(self.search_box.text())

    def _populate_app_view(self, apps: list[AppInfo]) -> None:
        """Populate apps in grid or list layout."""
        while self.app_list_layout.count():
            item = self.app_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        if not apps:
            empty = QLabel("No apps found\n\nAdd a Windows app profile to get started")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                f"background: transparent; color: {C.OVERLAY0}; font-size: 15px; padding: 60px;"
            )
            self.app_list_layout.addWidget(empty)
            return

        if self._view_mode == "grid":
            self._populate_grid(apps)
        else:
            self._populate_list(apps)

    def _populate_grid(self, apps: list[AppInfo]) -> None:
        """Grid view - cards."""
        cols = 4
        grid = QGridLayout()
        grid.setSpacing(14)
        grid.setContentsMargins(0, 0, 0, 0)

        for i, app in enumerate(apps):
            card = self._make_app_card(app)
            grid.addWidget(card, i // cols, i % cols)

        remainder = len(apps) % cols
        if remainder:
            for j in range(remainder, cols):
                spacer = QWidget()
                spacer.setStyleSheet("background: transparent;")
                grid.addWidget(spacer, len(apps) // cols, j)

        grid_widget = QWidget()
        grid_widget.setLayout(grid)
        self.app_list_layout.addWidget(grid_widget)
        self.app_list_layout.addStretch()

    def _populate_list(self, apps: list[AppInfo]) -> None:
        """List view - horizontal tiles."""
        self.app_list_layout.setSpacing(8)
        for app in apps:
            self.app_list_layout.addWidget(self._make_app_tile(app))
        self.app_list_layout.addStretch()

    def _make_app_card(self, app: AppInfo) -> QWidget:
        """Grid card with large avatar, name, and launch."""
        card = QFrame()
        card.setObjectName("appCard")
        card.setStyleSheet(APP_CARD)
        card.setMinimumHeight(190)
        card.setMinimumWidth(160)
        self._add_shadow(card)

        vl = QVBoxLayout(card)
        vl.setContentsMargins(16, 18, 16, 14)
        vl.setSpacing(0)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(0)

        avatar = self._make_app_avatar(app, size=52, radius=14, font_size=22)
        top_row.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignLeft)
        top_row.addStretch()

        badge = self._make_source_badge(app)
        if badge is not None:
            top_row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

        vl.addLayout(top_row)
        vl.addSpacing(12)

        name_lbl = QLabel(app.full_name)
        name_lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 13px; font-weight: bold;"
        )
        name_lbl.setWordWrap(False)
        name_lbl.setMaximumWidth(200)
        fm = name_lbl.fontMetrics()
        elided = fm.elidedText(app.full_name, Qt.TextElideMode.ElideRight, 200)
        name_lbl.setText(elided)
        name_lbl.setToolTip(app.full_name)
        vl.addWidget(name_lbl)

        cat_text = app.categories[0] if app.categories else ""
        if cat_text:
            cat_lbl = QLabel(cat_text)
            cat_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
            vl.addWidget(cat_lbl)
        vl.addStretch()

        bottom = QHBoxLayout()
        bottom.setSpacing(6)

        launch_btn = QPushButton("\u25b6")
        launch_btn.setFixedSize(32, 32)
        launch_btn.setToolTip(f"Launch {app.full_name}")
        launch_btn.setStyleSheet(
            f"QPushButton {{ background: {C.GREEN};"
            f" color: {C.CRUST}; border: none;"
            " border-radius: 16px;"
            " font-size: 14px; }}"
            f"QPushButton:hover {{ background: {C.TEAL}; }}"
        )
        launch_btn.clicked.connect(lambda _, a=app: self._launch_app(a))
        bottom.addWidget(launch_btn)
        bottom.addStretch()

        edit_btn = QPushButton("\u22ef")
        edit_btn.setFixedSize(28, 28)
        edit_btn.setToolTip("Edit")
        edit_btn.setStyleSheet(
            f"QPushButton {{ background: transparent;"
            f" color: {C.OVERLAY0}; border: none;"
            " border-radius: 14px; font-size: 16px; }}"
            f"QPushButton:hover {{ color: {C.TEXT};"
            f" background: {C.SURFACE1}; }}"
        )
        edit_btn.clicked.connect(lambda _, a=app: self._on_edit_app(a))
        bottom.addWidget(edit_btn)

        del_btn = QPushButton("\u2715")
        del_btn.setFixedSize(28, 28)
        del_btn.setToolTip("Delete")
        del_btn.setStyleSheet(BTN_DANGER)
        del_btn.clicked.connect(lambda _, a=app: self._on_delete_app(a))
        bottom.addWidget(del_btn)

        vl.addLayout(bottom)
        return card

    def _make_app_tile(self, app: AppInfo) -> QWidget:
        """Horizontal app tile with colored accent stripe."""
        color = avatar_color(app.name)

        tile = QFrame()
        tile.setObjectName("appTile")
        tile.setStyleSheet(APP_TILE)
        tile.setMinimumHeight(72)
        self._add_shadow(tile, blur=12, y=2, alpha=35)

        layout = QHBoxLayout(tile)
        layout.setContentsMargins(0, 0, 16, 0)
        layout.setSpacing(0)

        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background: {color}; border-radius: 2px; margin: 8px 0 8px 8px;")
        layout.addWidget(stripe)
        layout.addSpacing(14)

        avatar = self._make_app_avatar(app, size=40, radius=10, font_size=16)
        layout.addWidget(avatar)
        layout.addSpacing(14)

        info = QVBoxLayout()
        info.setSpacing(2)

        name_lbl = QLabel(app.full_name)
        name_lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 14px; font-weight: bold;"
        )
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_row.addWidget(name_lbl)
        badge = self._make_source_badge(app)
        if badge is not None:
            name_row.addWidget(badge)
        name_row.addStretch()
        info.addLayout(name_row)

        meta_parts = []
        if app.categories:
            meta_parts.append(", ".join(app.categories[:2]))
        meta_parts.append(app.name)
        meta_lbl = QLabel(" \u2022 ".join(meta_parts))
        meta_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        info.addWidget(meta_lbl)

        layout.addLayout(info)
        layout.addStretch()

        launch_btn = QPushButton("\u25b6  Launch")
        launch_btn.setStyleSheet(BTN_ACCENT)
        launch_btn.clicked.connect(lambda: self._launch_app(app))
        layout.addWidget(launch_btn)
        layout.addSpacing(8)

        edit_btn = QPushButton("Edit")
        edit_btn.setStyleSheet(
            f"QPushButton {{ background: {C.SURFACE1}; color: {C.SUBTEXT0};"
            f" font-size: 12px; border-radius: 6px; padding: 6px 14px;"
            f" border: none; }}"
            f"QPushButton:hover {{ background: {C.SURFACE2};"
            f" color: {C.TEXT}; }}"
        )
        edit_btn.clicked.connect(lambda: self._on_edit_app(app))
        layout.addWidget(edit_btn)
        layout.addSpacing(6)

        del_btn = QPushButton("\u2715")
        del_btn.setFixedSize(32, 32)
        del_btn.setStyleSheet(BTN_DANGER)
        del_btn.clicked.connect(lambda: self._on_delete_app(app))
        layout.addWidget(del_btn)

        return tile

    def _filter_apps(self, text: str) -> None:
        q = text.lower()
        filtered = [a for a in self.apps if q in a.full_name.lower() or q in a.name.lower()]
        if self._active_category:
            filtered = [a for a in filtered if self._active_category in a.categories]
        self._populate_app_view(filtered)
        self.app_count_label.setText(f"{len(filtered)} apps")

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_AREA)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 28)

        title = QLabel("Settings")
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        layout.addWidget(title)

        sub = QLabel("Configure RDP and container settings")
        sub.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 13px;")
        layout.addWidget(sub)
        layout.addSpacing(20)

        cols = QHBoxLayout()
        cols.setSpacing(16)

        self.input_user = QLineEdit(self.cfg.rdp.user)
        self.input_ip = QLineEdit(self.cfg.rdp.ip)
        self.input_port = QLineEdit(str(self.cfg.rdp.port))
        self.input_scale = QComboBox()
        scale_options = [("100%", 100), ("140%", 140), ("180%", 180)]
        for label, val in scale_options:
            self.input_scale.addItem(label, val)
        current_scale = self.cfg.rdp.scale
        idx = next((i for i, (_, v) in enumerate(scale_options) if v == current_scale), 0)
        self.input_scale.setCurrentIndex(idx)

        self.input_dpi = QComboBox()
        dpi_options = [
            ("Auto", 0),
            ("100%  (96 DPI)", 100),
            ("125%  (120 DPI)", 125),
            ("150%  (144 DPI)", 150),
            ("175%  (168 DPI)", 175),
            ("200%  (192 DPI)", 200),
            ("250%  (240 DPI)", 250),
            ("300%  (288 DPI)", 300),
        ]
        for label, val in dpi_options:
            self.input_dpi.addItem(label, val)
        current_dpi = self.cfg.rdp.dpi
        idx = self.input_dpi.findData(current_dpi)
        if idx >= 0:
            self.input_dpi.setCurrentIndex(idx)
        elif current_dpi > 0:
            self.input_dpi.addItem(f"{current_dpi}%", current_dpi)
            self.input_dpi.setCurrentIndex(self.input_dpi.count() - 1)

        self.input_pw_max_age = QComboBox()
        pw_age_options = [
            ("Disabled", 0),
            ("1 day", 1),
            ("3 days", 3),
            ("7 days (default)", 7),
            ("14 days", 14),
            ("30 days", 30),
            ("90 days", 90),
        ]
        for label, val in pw_age_options:
            self.input_pw_max_age.addItem(label, val)
        current_age = self.cfg.rdp.password_max_age
        age_idx = self.input_pw_max_age.findData(current_age)
        if age_idx >= 0:
            self.input_pw_max_age.setCurrentIndex(age_idx)
        elif current_age > 0:
            self.input_pw_max_age.addItem(f"{current_age} days", current_age)
            self.input_pw_max_age.setCurrentIndex(self.input_pw_max_age.count() - 1)

        rdp_card = self._settings_card(
            "\u25a3  RDP Connection",
            "Remote Desktop Protocol settings",
            [
                ("Username", self.input_user),
                ("Host / IP", self.input_ip),
                ("Port", self.input_port),
                ("Scale %", self.input_scale),
                ("Windows DPI", self.input_dpi),
                ("Password Rotation", self.input_pw_max_age),
            ],
        )
        cols.addWidget(rdp_card)

        self.input_backend = QComboBox()
        self.input_backend.addItems(["podman", "docker", "libvirt", "manual"])
        self.input_backend.setCurrentText(self.cfg.pod.backend)

        self.input_cpu = QLineEdit(str(self.cfg.pod.cpu_cores))
        self.input_ram = QLineEdit(str(self.cfg.pod.ram_gb))
        self.input_idle = QLineEdit(str(self.cfg.pod.idle_timeout))
        self.input_max_sessions = QLineEdit(str(self.cfg.pod.max_sessions))

        pod_card = self._settings_card(
            "\u25a8  Container / VM",
            "Backend and resource allocation",
            [
                ("Backend", self.input_backend),
                ("CPU Cores", self.input_cpu),
                ("RAM (GB)", self.input_ram),
                ("Idle Timeout", self.input_idle),
                ("Max Sessions (1-50)", self.input_max_sessions),
            ],
        )
        cols.addWidget(pod_card)

        layout.addLayout(cols)

        # Budget warning \u2014 only visible when max_sessions over-subscribes ram_gb.
        # Live-updates as the user types in either field.
        self.budget_warning_label = QLabel("")
        self.budget_warning_label.setWordWrap(True)
        self.budget_warning_label.setStyleSheet(
            f"color: {C.YELLOW if hasattr(C, 'YELLOW') else '#e5c07b'}; "
            f"background: transparent; font-size: 12px; padding: 4px 8px;"
        )
        self.budget_warning_label.setVisible(False)
        layout.addWidget(self.budget_warning_label)
        self.input_ram.textChanged.connect(self._update_budget_warning)
        self.input_max_sessions.textChanged.connect(self._update_budget_warning)
        self._update_budget_warning()

        layout.addSpacing(20)

        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.setFixedWidth(180)
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page

    def _settings_card(
        self,
        title: str,
        subtitle: str,
        fields: list[tuple[str, QWidget]],
    ) -> QFrame:
        """Build a settings section card."""
        card = QFrame()
        card.setObjectName("settingsSection")
        card.setStyleSheet(
            SETTINGS_SECTION
            + f"QLabel {{ color: {C.TEXT}; font-size: 13px; background: transparent; }}"
            + INPUT
            + COMBO
        )
        self._add_shadow(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(4)

        header = QLabel(title)
        header.setStyleSheet(
            f"background: transparent; color: {C.BLUE}; font-size: 15px; font-weight: bold;"
        )
        layout.addWidget(header)

        sub = QLabel(subtitle)
        sub.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        layout.addWidget(sub)

        accent_line = QFrame()
        accent_line.setFixedHeight(1)
        accent_line.setStyleSheet(f"background: {C.SURFACE1};")
        layout.addWidget(accent_line)
        layout.addSpacing(14)

        form = QGridLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)

        for row, (label, widget) in enumerate(fields):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"background: transparent; color: {C.SUBTEXT0}; font-size: 13px;")
            form.addWidget(lbl, row, 0)
            form.addWidget(widget, row, 1)

        layout.addLayout(form)
        return card

    def _build_maintenance_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_AREA)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 24, 32, 20)

        title = QLabel("Tools")
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        layout.addWidget(title)

        sub = QLabel("System maintenance and pod management")
        sub.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 13px;")
        layout.addWidget(sub)
        layout.addSpacing(20)

        grp1 = QLabel("Pod Management")
        grp1.setStyleSheet(
            "background: transparent;"
            f" color: {C.SUBTEXT0};"
            " font-size: 11px;"
            " font-weight: bold;"
            " text-transform: uppercase;"
        )
        layout.addWidget(grp1)
        layout.addSpacing(8)

        pod_tools = [
            (
                "\u23f8",
                "Suspend Pod",
                "Pause container (keeps memory)",
                self._on_suspend,
            ),
            (
                "\u25b6",
                "Resume Pod",
                "Unpause a suspended container",
                self._on_resume,
            ),
            (
                "\u25a3",
                "Full Desktop",
                "Launch full Windows desktop",
                self._on_open_desktop,
            ),
        ]
        for i, (icon, label, desc, handler) in enumerate(pod_tools):
            layout.addWidget(self._make_action_row(icon, label, desc, handler, i))

        layout.addSpacing(20)

        grp2 = QLabel("System")
        grp2.setStyleSheet(
            "background: transparent;"
            f" color: {C.SUBTEXT0};"
            " font-size: 11px;"
            " font-weight: bold;"
            " text-transform: uppercase;"
        )
        layout.addWidget(grp2)
        layout.addSpacing(8)

        sys_tools = [
            (
                "\u2727",
                "Clean Locks",
                "Remove Office lock files",
                self._on_cleanup,
            ),
            (
                "\u25f7",
                "Sync Time",
                "Force Windows clock sync",
                self._on_timesync,
            ),
            (
                "\u25c6",
                "Debloat",
                "Disable telemetry & ads",
                self._on_debloat,
            ),
            (
                "\u2699",  # gear
                "Apply Windows Fixes",
                "Re-apply RDP timeout / NIC / TermService recovery to existing pod",
                self._on_apply_fixes,
            ),
        ]
        for i, (icon, label, desc, handler) in enumerate(sys_tools):
            layout.addWidget(self._make_action_row(icon, label, desc, handler, i + 3))

        layout.addSpacing(20)

        grp3 = QLabel("Windows Update")
        grp3.setStyleSheet(
            "background: transparent;"
            f" color: {C.SUBTEXT0};"
            " font-size: 11px;"
            " font-weight: bold;"
            " text-transform: uppercase;"
        )
        layout.addWidget(grp3)
        layout.addSpacing(8)

        update_row = QFrame()
        update_row.setObjectName("actionRow")
        update_row.setStyleSheet(ACTION_ROW)
        update_row.setMinimumHeight(64)
        rl = QHBoxLayout(update_row)
        rl.setContentsMargins(16, 8, 16, 8)

        update_icon = QLabel("\u21c5")
        update_icon.setFixedSize(36, 36)
        update_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        update_icon.setStyleSheet(
            f"background: {accent_color(7)}; color: {C.TEXT}; border-radius: 8px; font-size: 16px;"
        )
        rl.addWidget(update_icon)

        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel("Windows Update")
        lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 14px; font-weight: 600;"
        )
        col.addWidget(lbl)
        self._update_status_label = QLabel("Checking...")
        self._update_status_label.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 12px;"
        )
        col.addWidget(self._update_status_label)
        rl.addLayout(col, 1)

        self._btn_enable_updates = QPushButton("Enable")
        self._btn_enable_updates.setStyleSheet(BTN_PRIMARY)
        self._btn_enable_updates.setFixedWidth(90)
        self._btn_enable_updates.clicked.connect(self._on_enable_updates)
        rl.addWidget(self._btn_enable_updates)

        self._btn_disable_updates = QPushButton("Disable")
        self._btn_disable_updates.setStyleSheet(BTN_DANGER)
        self._btn_disable_updates.setFixedWidth(90)
        self._btn_disable_updates.clicked.connect(self._on_disable_updates)
        rl.addWidget(self._btn_disable_updates)

        layout.addWidget(update_row)

        self._refresh_update_status()

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page

    def _make_action_row(
        self,
        icon: str,
        label: str,
        desc: str,
        handler: object,
        color_idx: int,
    ) -> QFrame:
        """Build a single tool action row."""
        row = QFrame()
        row.setObjectName("actionRow")
        row.setStyleSheet(ACTION_ROW)
        row.setMinimumHeight(64)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_shadow(row, blur=12, y=2, alpha=35)

        rl = QHBoxLayout(row)
        rl.setContentsMargins(16, 0, 20, 0)
        rl.setSpacing(16)

        color = accent_color(color_idx)
        icon_circle = QLabel(icon)
        icon_circle.setFixedSize(36, 36)
        icon_circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_circle.setStyleSheet(
            f"background: {color}; color: {C.CRUST}; border-radius: 18px; font-size: 16px;"
        )
        rl.addWidget(icon_circle)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_lbl = QLabel(label)
        title_lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 14px; font-weight: bold;"
        )
        text_col.addWidget(title_lbl)
        desc_lbl = QLabel(desc)
        desc_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        text_col.addWidget(desc_lbl)
        rl.addLayout(text_col)
        rl.addStretch()

        arrow = QLabel("\u203a")
        arrow.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 20px;")
        rl.addWidget(arrow)

        row.mousePressEvent = lambda ev, h=handler: h()
        return row

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
        for key, label in [
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

    def _info_card(self, title: str) -> QFrame:
        """Card scaffold with a title bar + an empty body layout we mutate later."""
        card = QFrame()
        card.setObjectName("infoSection")
        card.setStyleSheet(
            SETTINGS_SECTION
            + f"QLabel {{ color: {C.TEXT}; font-size: 13px; background: transparent; }}"
        )
        self._add_shadow(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(6)

        header = QLabel(title)
        header.setStyleSheet(
            f"background: transparent; color: {C.BLUE}; font-size: 15px; font-weight: bold;"
        )
        layout.addWidget(header)

        accent = QFrame()
        accent.setFixedHeight(1)
        accent.setStyleSheet(f"background: {C.SURFACE1};")
        layout.addWidget(accent)
        layout.addSpacing(8)

        body = QVBoxLayout()
        body.setSpacing(4)
        layout.addLayout(body)

        # Stash the body layout on the frame for later population.
        card.setProperty("info_body", body)
        self._info_card_bodies[title.lower()] = body
        # Initial placeholder
        loading = QLabel("Loading...")
        loading.setStyleSheet(f"color: {C.OVERLAY0};")
        body.addWidget(loading)
        return card

    def _set_info_card_rows(self, key: str, rows: list[tuple[str, str]]) -> None:
        """Replace the body of an info card with label/value rows."""
        body = self._info_card_bodies.get(key)
        if body is None:
            return
        # Clear existing children.
        while body.count():
            item = body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for label, value in rows:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 12px;")
            val = QLabel(value)
            val.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
            val.setWordWrap(True)
            row.addWidget(lbl, 0)
            row.addStretch()
            row.addWidget(val, 1)
            holder = QWidget()
            holder.setLayout(row)
            body.addWidget(holder)

    def _refresh_info(self) -> None:
        """Re-run gather_info on a worker thread; populate cards on completion."""
        # Reentrancy guard: ignore rapid re-clicks while a previous worker
        # is still in flight. The previous worker's `done` will land first
        # and then the user can refresh again. Without this guard, a fast
        # double-click leaks a QThread + worker pair and races the
        # _info_card_bodies mutation in _apply_info_snapshot.
        if getattr(self, "_info_busy", False):
            return
        self._info_busy = True

        thread = QThread(self)
        worker = _InfoWorker(self.cfg)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._apply_info_snapshot)
        # done/failed both end the worker — chain quit + deleteLater on
        # both worker and thread so neither leaks across refreshes.
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # Clear the busy flag whichever way the worker finishes.
        worker.done.connect(self._on_info_done)
        worker.failed.connect(self._on_info_done)
        self._info_thread = thread
        self._info_worker = worker
        thread.start()

    @Slot()
    def _on_info_done(self, *_args) -> None:
        """Slot fired when the info worker finishes (success or failure)."""
        self._info_busy = False

    def _apply_info_snapshot(self, info: dict) -> None:
        """Map gather_info output into per-card row pairs."""
        sys_ = info.get("system", {})
        self._set_info_card_rows(
            "system",
            [
                ("winpodx", sys_.get("winpodx", "")),
                ("OEM bundle", sys_.get("oem_bundle", "")),
                ("rdprrap", sys_.get("rdprrap", "")),
                ("Distro", sys_.get("distro", "")),
                ("Kernel", sys_.get("kernel", "")),
            ],
        )
        disp = info.get("display", {})
        self._set_info_card_rows(
            "display",
            [
                ("Session type", disp.get("session_type", "")),
                ("Desktop env", disp.get("desktop_environment", "")),
                ("Wayland FreeRDP", disp.get("wayland_freerdp", "")),
                ("Raw scale", disp.get("raw_scale", "")),
                ("RDP scale", disp.get("rdp_scale", "")),
            ],
        )
        deps_rows = []
        for name, dep in info.get("dependencies", {}).items():
            ok = dep.get("found") == "true"
            path = dep.get("path") or ""
            value = ("OK " + path).strip() if ok else "MISSING"
            deps_rows.append((name, value))
        self._set_info_card_rows("dependencies", deps_rows)

        pod = info.get("pod", {})
        rdp_label = "reachable" if pod.get("rdp_reachable") else "unreachable"
        vnc_label = "reachable" if pod.get("vnc_reachable") else "unreachable"
        pod_rows = [
            ("State", str(pod.get("state", ""))),
        ]
        if pod.get("uptime"):
            pod_rows.append(("Started at", str(pod["uptime"])))
        pod_rows.extend(
            [
                (f"RDP {pod.get('rdp_port', '')}", rdp_label),
                (f"VNC {pod.get('vnc_port', '')}", vnc_label),
                ("Active sessions", str(pod.get("active_sessions", 0))),
            ]
        )
        self._set_info_card_rows("pod", pod_rows)

        conf = info.get("config", {})
        cfg_rows = [
            ("Path", str(conf.get("path", ""))),
            ("Backend", str(conf.get("backend", ""))),
            ("IP", f"{conf.get('ip', '')}:{conf.get('port', '')}"),
            ("User", str(conf.get("user", ""))),
            ("Scale", f"{conf.get('scale', '')}%"),
            ("Idle", f"{conf.get('idle_timeout', 0)}s"),
            ("Max sessions", str(conf.get("max_sessions", 0))),
            ("RAM (GB)", str(conf.get("ram_gb", 0))),
        ]
        warning = conf.get("budget_warning") or ""
        if warning:
            cfg_rows.append(("WARNING", warning))
        self._set_info_card_rows("config", cfg_rows)

    def _log_append(self, text: str, color: str = C.SUBTEXT1) -> None:
        """Append colored text to the log output."""
        import html

        safe = html.escape(text)
        self.log_output.append(f'<span style="color:{color}">{safe}</span>')
        sb = self.log_output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _run_log_cmd(self, cmd: list[str]) -> None:
        """Run command and show output in terminal."""
        import subprocess

        self._log_append(f"$ {' '.join(cmd)}", C.BLUE)

        def _do() -> None:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.stdout.strip():
                    self.log_signal.emit(result.stdout.strip(), C.SUBTEXT1)
                if result.stderr.strip():
                    self.log_signal.emit(result.stderr.strip(), C.YELLOW)
                if result.returncode != 0:
                    self.log_signal.emit(f"Exit code: {result.returncode}", C.RED)
            except subprocess.TimeoutExpired:
                self.log_signal.emit("Command timed out (30s)", C.RED)
            except FileNotFoundError:
                self.log_signal.emit(f"Command not found: {cmd[0]}", C.RED)

        threading.Thread(target=_do, daemon=True).start()

    # v0.2.0.10: live log streaming. The Pod logs button shows the last
    # 100 lines, but for first-install / debug the user wants to watch
    # the container output as Windows downloads / Sysprep / boots, and
    # also see winpodx's own application log (under XDG state) so they
    # can correlate guest events with host actions.
    def _on_follow_pod_log(self) -> None:
        import subprocess

        self._on_stop_tail()
        self._log_append(
            f"$ podman logs -f --tail 50 {self.cfg.pod.container_name} (Stop tail to end)",
            C.BLUE,
        )
        try:
            self._tail_proc = subprocess.Popen(
                [
                    self.cfg.pod.backend,
                    "logs",
                    "-f",
                    "--tail",
                    "50",
                    self.cfg.pod.container_name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as e:
            self._log_append(f"Could not start tail: {e}", C.RED)
            return
        self._tail_stop = threading.Event()
        threading.Thread(target=self._drain_tail, args=(self._tail_proc,), daemon=True).start()

    def _on_tail_app_log(self) -> None:
        from winpodx.utils.paths import config_dir

        log_path = config_dir() / "winpodx.log"
        self._log_append(f"$ tail {log_path}", C.BLUE)
        if not log_path.exists():
            self._log_append(
                "(no app log file yet — winpodx writes to it after the next CLI / GUI action)",
                C.OVERLAY0,
            )
            return
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self._log_append(f"Could not read app log: {e}", C.RED)
            return
        # Show only the last ~200 lines so we don't drown the view.
        lines = content.splitlines()[-200:]
        for line in lines:
            self._log_append(line, C.SUBTEXT1)

    def _on_follow_app_log(self) -> None:
        import subprocess

        from winpodx.utils.paths import config_dir

        log_path = config_dir() / "winpodx.log"
        self._on_stop_tail()
        self._log_append(f"$ tail -F {log_path} (Stop tail to end)", C.BLUE)
        # Pre-create the file so `tail -F` doesn't loop on FileNotFoundError.
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
        try:
            self._tail_proc = subprocess.Popen(
                ["tail", "-F", "-n", "50", str(log_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as e:
            self._log_append(f"Could not start tail: {e}", C.RED)
            return
        self._tail_stop = threading.Event()
        threading.Thread(target=self._drain_tail, args=(self._tail_proc,), daemon=True).start()

    def _drain_tail(self, proc) -> None:  # type: ignore[no-untyped-def]
        try:
            for line in iter(proc.stdout.readline, ""):
                if self._tail_stop.is_set():
                    break
                line = line.rstrip()
                if line:
                    self.log_signal.emit(line, C.SUBTEXT1)
        except Exception:  # noqa: BLE001
            log.debug("tail drain crashed", exc_info=True)
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def _on_stop_tail(self) -> None:
        proc = getattr(self, "_tail_proc", None)
        stop = getattr(self, "_tail_stop", None)
        if proc is None:
            return
        if stop is not None:
            stop.set()
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self._log_append("(tail stopped)", C.OVERLAY0)
        self._tail_proc = None

    _ALLOWED_COMMANDS = {
        "podman",
        "docker",
        "virsh",
        "winpodx",
        "podman-compose",
        "docker-compose",
        "xfreerdp",
        "xfreerdp3",
        "wlfreerdp",
        "wlfreerdp3",
        "systemctl",
        "journalctl",
        "ss",
        "ip",
        "ping",
    }

    def _on_cmd_enter(self) -> None:
        """Handle command input (allowlist-based)."""
        import shlex

        text = self.cmd_input.text().strip()
        if not text:
            return
        self.cmd_input.clear()

        try:
            cmd = shlex.split(text)
        except ValueError as e:
            self._log_append(f"Parse error: {e}", C.RED)
            return

        if not cmd or cmd[0] not in self._ALLOWED_COMMANDS:
            allowed = ", ".join(sorted(self._ALLOWED_COMMANDS))
            self._log_append(f"Blocked: allowed commands: {allowed}", C.RED)
            return

        self._run_log_cmd(cmd)

    def _on_rdp_test(self) -> None:
        self._log_append("$ Testing RDP connection...", C.BLUE)

        def _do() -> None:
            cfg = Config.load()
            from winpodx.core.pod import check_rdp_port

            ok = check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=5)
            if ok:
                self.log_signal.emit(f"RDP OK: {cfg.rdp.ip}:{cfg.rdp.port}", C.GREEN)
            else:
                self.log_signal.emit(f"RDP FAIL: {cfg.rdp.ip}:{cfg.rdp.port}", C.RED)

        threading.Thread(target=_do, daemon=True).start()

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
        self._populate_app_view(self.apps)
        self.search_box.clear()
        self.app_count_label.setText(f"{len(self.apps)} apps")

    def _on_refresh_apps(self) -> None:
        """Entry point for the "Refresh Apps" button; kicks off the QThread worker."""
        if self._refresh_state == "scanning":
            return
        self._set_refresh_state("scanning")

        thread = QThread(self)
        worker = _DiscoveryWorker()
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

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)

    # Serializes ensure_ready + Popen spawn so concurrent launches don't race.
    _launch_lock = threading.Lock()

    def _launch_app(self, app: AppInfo) -> None:
        # Per-app cooldown debounced via QTimer; released 3s later.
        if app.name in self._recently_launched:
            self.app_launch_failed.emit("Just launched. Please wait a moment.")
            return
        self._recently_launched.add(app.name)
        QTimer.singleShot(3000, lambda n=app.name: self._recently_launched.discard(n))

        self.info_label.setText(f"Launching {app.full_name}...")

        def _do() -> None:
            # Lock guards ensure_ready + launch_app only; dropped before the wait.
            if not self._launch_lock.acquire(blocking=False):
                self.app_launch_failed.emit("Another app is launching, please wait.")
                return
            session = None
            try:
                from winpodx.core.provisioner import ensure_ready
                from winpodx.core.rdp import launch_app

                cfg = ensure_ready()
                session = launch_app(cfg, app.executable)
            except Exception:
                import traceback

                self.app_launch_failed.emit(traceback.format_exc()[-800:])
                return
            finally:
                # Drop lock before the 3s observation so other launches aren't gated.
                self._launch_lock.release()

            # Post-spawn wait: catch early FreeRDP crashes (auth, missing host, etc.).
            import time

            time.sleep(3)
            if session.process and session.process.poll() is not None:
                rc = session.process.returncode
                # 0 = normal exit, 128+signal = killed by signal.
                if rc == 0 or rc > 128:
                    self.app_launched.emit(app.full_name)
                else:
                    time.sleep(0.2)  # let reaper drain stderr
                    stderr = session.stderr_tail.decode(errors="replace")[-500:]
                    msg = f"FreeRDP exited with code {rc}"
                    if stderr:
                        msg += f"\n{stderr}"
                    self.app_launch_failed.emit(msg)
            else:
                self.app_launched.emit(app.full_name)

        threading.Thread(target=_do, daemon=True).start()

    def _on_start_pod(self) -> None:
        self.info_label.setText("Starting pod...")

        def _do() -> None:
            try:
                from winpodx.core.provisioner import ensure_ready

                ensure_ready()
                self._refresh_pod_status()
            except Exception as e:
                self.app_launch_failed.emit(f"Pod start failed: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def _on_stop_pod(self) -> None:
        from winpodx.core.process import list_active_sessions

        sessions = list_active_sessions()
        if sessions:
            names = ", ".join(s.app_name for s in sessions)
            reply = QMessageBox.question(
                self,
                "Active Sessions",
                f"Active sessions: {names}\nStop pod anyway?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.info_label.setText("Stopping pod...")

        def _do() -> None:
            from winpodx.core.pod import stop_pod

            cfg = Config.load()
            stop_pod(cfg)
            self._refresh_pod_status()

        threading.Thread(target=_do, daemon=True).start()

    def _update_budget_warning(self) -> None:
        """Live-update the session memory budget warning label.

        Quiet when the estimate fits; shows a wrapped message when
        max_sessions over-subscribes ram_gb. Called whenever either
        spinbox text changes.
        """
        from winpodx.core.config import Config, check_session_budget

        try:
            sessions = int(self.input_max_sessions.text() or "10")
            ram = int(self.input_ram.text() or "4")
        except ValueError:
            self.budget_warning_label.setVisible(False)
            return

        tmp = Config()
        tmp.pod.max_sessions = max(1, min(50, sessions))
        tmp.pod.ram_gb = max(1, ram)
        msg = check_session_budget(tmp)
        if msg:
            self.budget_warning_label.setText(f"WARNING: {msg}")
            self.budget_warning_label.setVisible(True)
        else:
            self.budget_warning_label.setVisible(False)

    def _save_settings(self) -> None:
        try:
            port = int(self.input_port.text() or str(self.cfg.rdp.port))
            scale = self.input_scale.currentData()
            cpu = int(self.input_cpu.text() or "4")
            ram = int(self.input_ram.text() or "4")
            idle = int(self.input_idle.text() or "0")
            max_sessions = int(self.input_max_sessions.text() or "10")
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Input",
                "Port, Scale, CPU, RAM, Idle Timeout, and Max Sessions must be numbers.",
            )
            return

        old_cfg = Config.load()
        needs_container = (
            cpu != old_cfg.pod.cpu_cores
            or ram != old_cfg.pod.ram_gb
            or port != old_cfg.rdp.port
            or self.input_user.text() != old_cfg.rdp.user
        )

        self.cfg.rdp.user = self.input_user.text()
        self.cfg.rdp.ip = self.input_ip.text()
        self.cfg.rdp.port = port
        self.cfg.rdp.scale = scale
        self.cfg.rdp.dpi = self.input_dpi.currentData()
        self.cfg.rdp.password_max_age = self.input_pw_max_age.currentData()
        self.cfg.pod.backend = self.input_backend.currentText()
        self.cfg.pod.cpu_cores = cpu
        self.cfg.pod.ram_gb = ram
        self.cfg.pod.idle_timeout = idle
        self.cfg.pod.max_sessions = max_sessions
        # Let __post_init__ clamp max_sessions to [1, 50] before save.
        self.cfg.pod.__post_init__()
        self.cfg.save()

        if needs_container and self.cfg.pod.backend in ("podman", "docker"):
            reply = QMessageBox.question(
                self,
                "Restart Container",
                "CPU, RAM, or port changed.\nContainer must be recreated to apply.\n\nRestart now?",
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.info_label.setText("Recreating container...")
                QApplication.processEvents()

                def _recreate() -> None:
                    try:
                        from winpodx.cli.setup_cmd import (
                            _generate_compose,
                            _recreate_container,
                        )

                        _generate_compose(self.cfg)
                        _recreate_container(self.cfg)
                        self.app_launched.emit("Container restarted")
                    except Exception as e:
                        self.app_launch_failed.emit(f"Restart failed: {e}")

                threading.Thread(target=_recreate, daemon=True).start()
                return

        self.info_label.setText("Settings saved")

    def _on_cleanup(self) -> None:
        from winpodx.core.daemon import cleanup_lock_files

        removed = cleanup_lock_files()
        msg = f"Removed {len(removed)} lock files" if removed else "No lock files found"
        self.info_label.setText(msg)

    def _refresh_update_status(self) -> None:
        def _do() -> None:
            from winpodx.core.updates import get_update_status

            cfg = Config.load()
            status = get_update_status(cfg)
            if status == "enabled":
                self._update_status_label.setText("Windows Update is enabled")
                self._btn_enable_updates.setEnabled(False)
                self._btn_disable_updates.setEnabled(True)
            elif status == "disabled":
                self._update_status_label.setText("Windows Update is disabled")
                self._btn_enable_updates.setEnabled(True)
                self._btn_disable_updates.setEnabled(False)
            else:
                self._update_status_label.setText("Status unknown (container not running?)")
                self._btn_enable_updates.setEnabled(True)
                self._btn_disable_updates.setEnabled(True)

        threading.Thread(target=_do, daemon=True).start()

    def _on_enable_updates(self) -> None:
        self._update_status_label.setText("Enabling Windows Update...")
        self._btn_enable_updates.setEnabled(False)
        self._btn_disable_updates.setEnabled(False)

        def _do() -> None:
            from winpodx.core.updates import enable_updates

            cfg = Config.load()
            ok = enable_updates(cfg)
            if ok:
                self.app_launched.emit("Windows Update enabled")
            else:
                self.app_launch_failed.emit("Failed to enable Windows Update")
            self._refresh_update_status()

        threading.Thread(target=_do, daemon=True).start()

    def _on_disable_updates(self) -> None:
        reply = QMessageBox.question(
            self,
            "Disable Windows Update",
            "This will stop Windows Update services and block update domains.\n"
            "Security updates will NOT be installed while disabled.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._update_status_label.setText("Disabling Windows Update...")
        self._btn_enable_updates.setEnabled(False)
        self._btn_disable_updates.setEnabled(False)

        def _do() -> None:
            from winpodx.core.updates import disable_updates

            cfg = Config.load()
            ok = disable_updates(cfg)
            if ok:
                self.app_launched.emit("Windows Update disabled")
            else:
                self.app_launch_failed.emit("Failed to disable Windows Update")
            self._refresh_update_status()

        threading.Thread(target=_do, daemon=True).start()

    def _on_timesync(self) -> None:
        from winpodx.core.daemon import sync_windows_time

        ok = sync_windows_time(Config.load())
        self.info_label.setText("Time synced" if ok else "Time sync failed")

    def _on_suspend(self) -> None:
        from winpodx.core.daemon import suspend_pod

        ok = suspend_pod(Config.load())
        self.info_label.setText("Pod suspended" if ok else "Suspend failed")
        self._refresh_pod_status()

    def _on_resume(self) -> None:
        from winpodx.core.daemon import resume_pod

        ok = resume_pod(Config.load())
        self.info_label.setText("Pod resumed" if ok else "Resume failed")
        self._refresh_pod_status()

    def _on_debloat(self) -> None:
        reply = QMessageBox.question(
            self,
            "Debloat",
            "This will disable telemetry, ads, and bloat in Windows.\nProceed?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.info_label.setText("Running debloat...")

        def _do() -> None:
            from winpodx.core.windows_exec import WindowsExecError, run_in_windows

            cfg = Config.load()
            base = Path(__file__).parent.parent.parent.parent
            candidates = [
                base / "scripts" / "windows" / "debloat.ps1",
                Path.home()
                / ".local"
                / "bin"
                / "winpodx-app"
                / "scripts"
                / "windows"
                / "debloat.ps1",
            ]
            script = next((p for p in candidates if p.exists()), None)
            if script is None:
                self.app_launch_failed.emit("Debloat script not found")
                return

            try:
                payload = script.read_text(encoding="utf-8")
            except OSError as e:
                self.app_launch_failed.emit(f"Cannot read debloat script: {e}")
                return

            try:
                result = run_in_windows(cfg, payload, description="debloat", timeout=180)
            except WindowsExecError as e:
                self.app_launch_failed.emit(f"Debloat channel failure: {e}")
                return

            if result.rc == 0:
                self.app_launched.emit("Debloat complete")
            else:
                self.app_launch_failed.emit(
                    f"Debloat failed (rc={result.rc}): "
                    f"{result.stderr.strip() or result.stdout.strip()[:200]}"
                )
            self.pod_status_updated.emit("running", cfg.rdp.ip)

        threading.Thread(target=_do, daemon=True).start()

    def _on_apply_fixes(self) -> None:
        """v0.1.9.3: Apply Windows-side runtime fixes to the existing pod.

        Same idempotent helpers fired by `winpodx pod apply-fixes` and by
        `provisioner.ensure_ready` — but on demand from the GUI for users
        whose migrate short-circuited with "already current" so the
        Windows VM never received the OEM v7+v8 fixes.
        """
        self.info_label.setText("Applying Windows-side fixes...")

        def _do() -> None:
            from winpodx.core.pod import PodState, pod_status
            from winpodx.core.provisioner import apply_windows_runtime_fixes

            cfg = Config.load()
            try:
                state = pod_status(cfg).state
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(f"Apply fixes failed (pod probe): {e}")
                return

            if state != PodState.RUNNING:
                self.app_launch_failed.emit(
                    "Pod is not running — start it first via the Apps page or "
                    "`winpodx pod start --wait`."
                )
                return

            try:
                results = apply_windows_runtime_fixes(cfg)
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(f"Apply fixes raised: {e}")
                return

            ok_count = sum(1 for v in results.values() if v == "ok")
            total = len(results)
            failed = [k for k, v in results.items() if v != "ok"]
            if failed:
                detail = ", ".join(failed)
                self.app_launch_failed.emit(f"Apply fixes: {ok_count}/{total} OK; failed: {detail}")
            else:
                self.app_launched.emit(f"Windows-side fixes applied ({ok_count}/{total} OK)")

        threading.Thread(target=_do, daemon=True).start()

    def _on_open_desktop(self) -> None:
        self.info_label.setText("Opening Windows desktop...")

        def _do() -> None:
            try:
                from winpodx.core.provisioner import ensure_ready
                from winpodx.core.rdp import launch_desktop

                cfg = ensure_ready()
                launch_desktop(cfg)
                self.app_launched.emit("Windows Desktop")
            except Exception as e:
                self.app_launch_failed.emit(str(e))

        threading.Thread(target=_do, daemon=True).start()

    def _start_status_timer(self) -> None:
        self._refresh_pod_status()
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._refresh_pod_status)
        self.status_timer.start(15000)

    def _refresh_pod_status(self) -> None:
        def _do() -> None:
            try:
                cfg = Config.load()
                s = pod_status(cfg)
                self.pod_status_updated.emit(s.state.value, s.ip)
            except Exception:
                self.pod_status_updated.emit("error", "")

        threading.Thread(target=_do, daemon=True).start()

    @Slot(str, str)
    def _on_pod_status(self, state: str, ip: str) -> None:
        # v0.2.0.10: trigger auto-discovery once when pod transitions
        # to running AND the app list is empty. Solves the
        # fresh-install case where install.sh's wait-ready timed out
        # before Windows finished Sysprep — once GUI sees the pod
        # come up, kick off a scan in the background.
        if (
            state == "running"
            and self._pod_state != "running"
            and not self.apps
            and self._refresh_state == "idle"
        ):
            log.info("pod is now running and app list is empty — auto-firing discovery")
            QTimer.singleShot(2000, self._on_refresh_apps)

        self._pod_state = state
        colors = {
            "running": C.GREEN,
            "stopped": C.RED,
            "starting": C.YELLOW,
            "paused": C.PEACH,
            "error": C.RED,
        }
        color = colors.get(state, C.SUBTEXT0)
        ip_suffix = f" ({ip})" if ip and state == "running" else ""
        display = state + ip_suffix

        self.pod_dot.setStyleSheet(f"background: transparent; color: {color}; font-size: 10px;")
        self.pod_label.setText(display)
        self.pod_label.setStyleSheet(f"background: transparent; color: {color}; font-size: 12px;")

        self.info_pod_dot.setStyleSheet(f"background: transparent; color: {color}; font-size: 8px;")
        self.info_pod_state.setText(state)
        self.info_pod_state.setStyleSheet(
            f"background: transparent; color: {color}; font-size: 11px;"
        )

        self.btn_start.setEnabled(state == "stopped")
        self.btn_stop.setEnabled(state == "running")

        self.status_banner.setVisible(state != "running")
        if state == "paused":
            self.banner_icon.setText("\u23f8")
            self.banner_icon.setStyleSheet(
                f"background: transparent; color: {C.PEACH}; font-size: 14px;"
            )
            self.banner_text.setText("Pod is paused")
        elif state == "stopped":
            self.banner_icon.setText("\u26a0")
            self.banner_icon.setStyleSheet(
                f"background: transparent; color: {C.YELLOW}; font-size: 14px;"
            )
            self.banner_text.setText("Pod is not running")
        elif state == "starting":
            self.banner_icon.setText("\u25b6")
            self.banner_icon.setStyleSheet(
                f"background: transparent; color: {C.BLUE}; font-size: 14px;"
            )
            self.banner_text.setText("Pod is starting...")
        elif state == "error":
            self.banner_icon.setText("\u2717")
            self.banner_icon.setStyleSheet(
                f"background: transparent; color: {C.RED}; font-size: 14px;"
            )
            self.banner_text.setText("Pod error")

    @Slot(str)
    def _on_app_launched(self, name: str) -> None:
        self.info_label.setText(f"{name} launched")

    @Slot(str)
    def _on_app_launch_failed(self, error: str) -> None:
        self.info_label.setText(f"Launch failed: {error}")
        QMessageBox.critical(self, "Launch Error", error)


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
