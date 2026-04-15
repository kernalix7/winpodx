"""winpodx main GUI — top-nav app launcher and pod manager.

Requires PySide6. Install with system package manager or pip install PySide6.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal, Slot
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

        self._setup_signals()
        self._build_ui()
        self._start_status_timer()

    def _setup_signals(self) -> None:
        self.pod_status_updated.connect(self._on_pod_status)
        self.app_launched.connect(self._on_app_launched)
        self.app_launch_failed.connect(self._on_app_launch_failed)
        self.log_signal.connect(self._log_append)

    # ══════════════════════════════════════════════════════════════════════
    #  UI Construction — vertical stack: top bar → banner → content → info
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        central = QWidget()
        central.setStyleSheet(f"background: {C.MANTLE};" + GLOBAL_STYLE)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Top navigation bar
        root.addWidget(self._build_top_bar())

        # 2. Status banner (shown when pod not running)
        self.status_banner = self._build_status_banner()
        root.addWidget(self.status_banner)

        # 3. Content pages
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_library_page())
        self.pages.addWidget(self._build_settings_page())
        self.pages.addWidget(self._build_maintenance_page())
        self.pages.addWidget(self._build_logs_page())
        root.addWidget(self.pages)

        # 4. Bottom info bar
        root.addWidget(self._build_info_bar())

    # ── Top Bar ────────────────────────────────────────────────────────────

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setStyleSheet(TOP_BAR)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(0)

        # Logo icon + text
        icon_path = Path(__file__).parent.parent.parent.parent / "data" / "winpodx-icon.svg"
        if icon_path.exists():
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

        # Navigation tabs
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
        ]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, i=idx: self._switch_page(i))
            tabs.addWidget(btn)
            self.nav_buttons.append(btn)

        self.nav_buttons[0].setChecked(True)
        layout.addWidget(tab_container)
        layout.addStretch()

        # Pod status chip + controls
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

        # Inline pod controls
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

    # ── Status Banner ──────────────────────────────────────────────────────

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

    # ── Info Bar ───────────────────────────────────────────────────────────

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

        # Pod engine indicator (Docker Desktop style)
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

    # ══════════════════════════════════════════════════════════════════════
    #  Page: Apps — toolbar + category filters + grid/list view
    # ══════════════════════════════════════════════════════════════════════

    def _build_library_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 20)
        layout.setSpacing(0)

        # ── Toolbar: search + count + view toggle + add ──
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

        # View toggle (grid / list) — Heroic style
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

        add_btn = QPushButton("+  Add App")
        add_btn.setStyleSheet(BTN_PRIMARY)
        add_btn.clicked.connect(self._on_add_app)
        toolbar.addWidget(add_btn)

        layout.addLayout(toolbar)
        layout.addSpacing(12)

        # ── Category filter chips ──
        self._category_row = QHBoxLayout()
        self._category_row.setSpacing(6)
        self._category_btns: list[QPushButton] = []
        self._build_category_chips()
        layout.addLayout(self._category_row)
        layout.addSpacing(16)

        # ── App container (scroll area) ──
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

        # "All" chip
        all_btn = QPushButton("All")
        all_btn.setCheckable(True)
        all_btn.setChecked(True)
        all_btn.setStyleSheet(FILTER_CHIP)
        all_btn.clicked.connect(lambda: self._set_category(""))
        self._category_row.addWidget(all_btn)
        self._category_btns.append(all_btn)

        for cat in cats_sorted[:8]:  # max 8 chips
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
        """Grid view - Steam/Heroic style cards."""
        cols = 4
        grid = QGridLayout()
        grid.setSpacing(14)
        grid.setContentsMargins(0, 0, 0, 0)

        for i, app in enumerate(apps):
            card = self._make_app_card(app)
            grid.addWidget(card, i // cols, i % cols)

        # Fill last row with spacers
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
        """Grid card — large avatar + name + launch."""
        color = avatar_color(app.name)
        letter = app.full_name[0].upper() if app.full_name else "?"

        card = QFrame()
        card.setObjectName("appCard")
        card.setStyleSheet(APP_CARD)
        card.setMinimumHeight(190)
        card.setMinimumWidth(160)
        self._add_shadow(card)

        vl = QVBoxLayout(card)
        vl.setContentsMargins(16, 18, 16, 14)
        vl.setSpacing(0)

        # Large avatar
        avatar = QLabel(letter)
        avatar.setFixedSize(52, 52)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            f"background: {color};"
            f" color: {C.CRUST};"
            " border-radius: 14px;"
            " font-size: 22px; font-weight: bold;"
        )
        vl.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignLeft)
        vl.addSpacing(12)

        # Name
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

        # Category tag
        cat_text = app.categories[0] if app.categories else ""
        if cat_text:
            cat_lbl = QLabel(cat_text)
            cat_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
            vl.addWidget(cat_lbl)
        vl.addStretch()

        # Bottom action row
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
        letter = app.full_name[0].upper() if app.full_name else "?"

        tile = QFrame()
        tile.setObjectName("appTile")
        tile.setStyleSheet(APP_TILE)
        tile.setMinimumHeight(72)
        self._add_shadow(tile, blur=12, y=2, alpha=35)

        layout = QHBoxLayout(tile)
        layout.setContentsMargins(0, 0, 16, 0)
        layout.setSpacing(0)

        # Left accent stripe
        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background: {color}; border-radius: 2px; margin: 8px 0 8px 8px;")
        layout.addWidget(stripe)
        layout.addSpacing(14)

        # Avatar
        avatar = QLabel(letter)
        avatar.setFixedSize(40, 40)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            f"background: {color};"
            f" color: {C.CRUST};"
            f" border-radius: 10px; font-size: 16px; font-weight: bold;"
        )
        layout.addWidget(avatar)
        layout.addSpacing(14)

        # Info column
        info = QVBoxLayout()
        info.setSpacing(2)

        name_lbl = QLabel(app.full_name)
        name_lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 14px; font-weight: bold;"
        )
        info.addWidget(name_lbl)

        meta_parts = []
        if app.categories:
            meta_parts.append(", ".join(app.categories[:2]))
        meta_parts.append(app.name)
        meta_lbl = QLabel(" \u2022 ".join(meta_parts))
        meta_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        info.addWidget(meta_lbl)

        layout.addLayout(info)
        layout.addStretch()

        # Action buttons (right side)
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

    # ══════════════════════════════════════════════════════════════════════
    #  Page: Settings — two-column layout
    # ══════════════════════════════════════════════════════════════════════

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

        # Two-column layout
        cols = QHBoxLayout()
        cols.setSpacing(16)

        # Left column — RDP
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
        # Select current value
        current_dpi = self.cfg.rdp.dpi
        idx = self.input_dpi.findData(current_dpi)
        if idx >= 0:
            self.input_dpi.setCurrentIndex(idx)
        elif current_dpi > 0:
            # Custom value not in presets — add it
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

        # Right column — Container
        self.input_backend = QComboBox()
        self.input_backend.addItems(["podman", "docker", "libvirt", "manual"])
        self.input_backend.setCurrentText(self.cfg.pod.backend)

        self.input_cpu = QLineEdit(str(self.cfg.pod.cpu_cores))
        self.input_ram = QLineEdit(str(self.cfg.pod.ram_gb))
        self.input_idle = QLineEdit(str(self.cfg.pod.idle_timeout))

        pod_card = self._settings_card(
            "\u25a8  Container / VM",
            "Backend and resource allocation",
            [
                ("Backend", self.input_backend),
                ("CPU Cores", self.input_cpu),
                ("RAM (GB)", self.input_ram),
                ("Idle Timeout", self.input_idle),
            ],
        )
        cols.addWidget(pod_card)

        layout.addLayout(cols)
        layout.addSpacing(20)

        # Save
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

        # Accent line under header
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

    # ══════════════════════════════════════════════════════════════════════
    #  Page: Tools — full-width action rows with icon circles
    # ══════════════════════════════════════════════════════════════════════

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

        # Group: Pod Management
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

        # Group: System
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
        ]
        for i, (icon, label, desc, handler) in enumerate(sys_tools):
            layout.addWidget(self._make_action_row(icon, label, desc, handler, i + 3))

        layout.addSpacing(20)

        # Group: Windows Update
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

        # Check current status in background
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

    # ══════════════════════════════════════════════════════════════════════
    #  Page: Terminal — dock-style terminal
    # ══════════════════════════════════════════════════════════════════════

    def _build_logs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 28, 32, 20)

        # Header with quick actions
        header = QHBoxLayout()
        title = QLabel("Terminal")
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        quick = [
            ("Status", ["podman", "ps", "-a", "--filter", "name=winpodx"]),
            ("Logs", ["podman", "logs", "--tail", "50", "winpodx-windows"]),
            ("Inspect", ["podman", "inspect", "winpodx-windows"]),
            ("RDP Test", None),
            ("Clear", None),
        ]
        for label, cmd in quick:
            btn = QPushButton(label)
            btn.setStyleSheet(BTN_GHOST)
            if label == "Clear":
                btn.clicked.connect(lambda: self.log_output.clear())
            elif label == "RDP Test":
                btn.clicked.connect(self._on_rdp_test)
            else:
                btn.clicked.connect(lambda _, c=cmd: self._run_log_cmd(c))
            header.addWidget(btn)

        layout.addLayout(header)
        layout.addSpacing(10)

        # Terminal output
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet(TERMINAL)
        layout.addWidget(self.log_output)

        # Command input bar
        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(8)

        prompt = QLabel("\u276f")
        prompt.setStyleSheet(
            f"background: transparent; color: {C.BLUE}; font-size: 16px; font-weight: bold;"
        )
        cmd_row.addWidget(prompt)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("Enter command (e.g. podman logs winpodx-windows)")
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

    # ══════════════════════════════════════════════════════════════════════
    #  Terminal Logic
    # ══════════════════════════════════════════════════════════════════════

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

    # ══════════════════════════════════════════════════════════════════════
    #  App CRUD
    # ══════════════════════════════════════════════════════════════════════

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

    # ══════════════════════════════════════════════════════════════════════
    #  Actions
    # ══════════════════════════════════════════════════════════════════════

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)

    _launch_lock = threading.Lock()

    def _launch_app(self, app: AppInfo) -> None:
        self.info_label.setText(f"Launching {app.full_name}...")

        def _do() -> None:
            if not self._launch_lock.acquire(blocking=False):
                self.app_launch_failed.emit("Another app is launching, please wait.")
                return
            try:
                import time

                from winpodx.core.provisioner import ensure_ready
                from winpodx.core.rdp import launch_app

                cfg = ensure_ready()
                session = launch_app(cfg, app.executable)

                time.sleep(3)
                if session.process and session.process.poll() is not None:
                    rc = session.process.returncode
                    # 0 = normal exit, 128+signal = killed by signal (e.g. 145=SIGTERM)
                    if rc == 0 or rc > 128:
                        self.app_launched.emit(app.full_name)
                    else:
                        stderr = ""
                        if session.process.stderr:
                            stderr = session.process.stderr.read().decode(errors="replace")[-500:]
                        msg = f"FreeRDP exited with code {rc}"
                        if stderr:
                            msg += f"\n{stderr}"
                        self.app_launch_failed.emit(msg)
                else:
                    self.app_launched.emit(app.full_name)
            except Exception:
                import traceback

                self.app_launch_failed.emit(traceback.format_exc()[-800:])
            finally:
                self._launch_lock.release()

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

    def _save_settings(self) -> None:
        try:
            port = int(self.input_port.text() or "3389")
            scale = self.input_scale.currentData()
            cpu = int(self.input_cpu.text() or "4")
            ram = int(self.input_ram.text() or "4")
            idle = int(self.input_idle.text() or "0")
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Input",
                "Port, Scale, CPU, RAM, and Idle Timeout must be numbers.",
            )
            return

        # Detect which settings changed before saving
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
            import subprocess
            from pathlib import Path

            cfg = Config.load()
            runtime = "podman" if cfg.pod.backend == "podman" else "docker"
            base = Path(__file__).parent.parent.parent.parent
            script = base / "scripts" / "windows" / "debloat.ps1"
            if script.exists():
                try:
                    subprocess.run(
                        [
                            runtime,
                            "cp",
                            str(script),
                            "winpodx-windows:C:/debloat.ps1",
                        ],
                        capture_output=True,
                        check=True,
                        timeout=30,
                    )
                except (
                    subprocess.CalledProcessError,
                    subprocess.TimeoutExpired,
                    FileNotFoundError,
                ):
                    return
                try:
                    subprocess.run(
                        [
                            runtime,
                            "exec",
                            "winpodx-windows",
                            "powershell",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            "C:\\debloat.ps1",
                        ],
                        capture_output=True,
                        timeout=120,
                    )
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass
            self.pod_status_updated.emit("running", cfg.rdp.ip)

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

    # ══════════════════════════════════════════════════════════════════════
    #  Status Updates
    # ══════════════════════════════════════════════════════════════════════

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

        # Info bar pod indicator
        self.info_pod_dot.setStyleSheet(f"background: transparent; color: {color}; font-size: 8px;")
        self.info_pod_state.setText(state)
        self.info_pod_state.setStyleSheet(
            f"background: transparent; color: {color}; font-size: 11px;"
        )

        self.btn_start.setEnabled(state == "stopped")
        self.btn_stop.setEnabled(state == "running")

        # Show/hide status banner
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

    # Application icon
    icon_path = Path(__file__).parent.parent.parent.parent / "data" / "winpodx-icon.svg"
    if icon_path.exists():
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
