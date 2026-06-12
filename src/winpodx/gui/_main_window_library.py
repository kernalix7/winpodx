# SPDX-License-Identifier: MIT
"""Library-page mixin for ``WinpodxWindow``.

Holds the methods that build and drive the Home launcher: the search
bar, pinned/recent rows, category chip row, the grid / list view
populators, individual card/tile builders, and the visibility /
filter / hidden-toggle state. Pulled out of
``main_window.py`` to keep that file focused on overall window
orchestration.

Host-class contract (only listed for readers; not enforced):
    apps: list[AppInfo]
    cfg: winpodx.core.config.Config
    _active_category: str          — set by _set_category.
    _view_mode: str                — "grid" | "list".
    _show_hidden: bool             — owned by this mixin (created in builder).
    _on_add_app / _on_edit_app / _on_delete_app  — AppCrudMixin.
    _on_refresh_apps                              — AppCrudMixin.
    _launch_app                                   — PodStatusMixin.
    Widgets created here (search_box, app_count_label, btn_grid, btn_list,
    refresh_btn, refresh_progress, btn_show_hidden, app_list_container,
    app_list_layout, _category_row, _category_btns) are accessed from
    sibling mixins via the shared ``self`` instance.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import AppInfo
from winpodx.core.i18n import tr
from winpodx.core.process import kill_session, list_active_sessions
from winpodx.gui import launcher_state
from winpodx.gui._widget_helpers import (
    add_shadow,
    make_app_avatar,
    make_empty_panel,
    make_section_label,
    make_source_badge,
)
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    APP_TILE,
    BTN_ACCENT,
    BTN_DANGER,
    BTN_GHOST,
    BTN_PRIMARY,
    BTN_SECONDARY,
    CHECKBOX,
    FILTER_CHIP,
    SCROLL_AREA,
    SEARCH_BAR,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    SPACE_XXL,
    VIEW_TOGGLE,
    C,
    avatar_color,
)

# Max category chips shown inline before collapsing the rest into a
# "+N more" overflow menu (Task 4). "All" is always shown and does not
# count against this cap.
_MAX_CATEGORY_CHIPS = 8


class _AppTile(QFrame):
    """Windows-Start-style launcher tile: icon above name, the whole tile is
    clickable (left-click launches), right-click opens the context menu. No
    border / badge / launch button on the face -- minimal, like the Start-menu.
    """

    def __init__(self, app: AppInfo, *, on_launch, on_menu) -> None:
        super().__init__()
        self._app = app
        self._on_launch = on_launch
        self._on_menu = on_menu
        self.setObjectName("appTileBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(app.full_name)
        self.setStyleSheet(
            "QFrame#appTileBtn { background: transparent; border: none;"
            " border-radius: 10px; }"
            f"QFrame#appTileBtn:hover {{ background: {C.SURFACE1}; }}"
        )
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE_S, SPACE_M, SPACE_S, SPACE_M)
        v.setSpacing(SPACE_S)
        v.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        avatar = make_app_avatar(app, size=48, radius=12, font_size=20)
        v.addWidget(avatar, 0, Qt.AlignmentFlag.AlignHCenter)

        name = QLabel(app.full_name)
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        name.setWordWrap(True)
        name.setFixedWidth(104)
        name.setStyleSheet(f"background: transparent; color: {C.TEXT}; font-size: 12px;")
        v.addWidget(name, 0, Qt.AlignmentFlag.AlignHCenter)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self._on_menu(self._app, self.mapToGlobal(pos))
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_launch(self._app)
        super().mousePressEvent(event)


class LibraryPageMixin:
    """Builds the Apps page + drives grid/list view + filter state."""

    def _build_library_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE_XXL, SPACE_XL, SPACE_XXL, SPACE_L)
        layout.setSpacing(SPACE_L)

        # Launcher hero: one large, centered search is the focal point (Start-
        # menu feel) -- no redundant page title.
        hero = QHBoxLayout()
        hero.addStretch(1)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Search apps by name..."))
        self.search_box.setStyleSheet(SEARCH_BAR)
        self.search_box.setMinimumHeight(46)
        self.search_box.setMinimumWidth(360)
        self.search_box.setMaximumWidth(600)
        self.search_box.addAction(
            load_icon("search", C.SUBTEXT0, 18),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter_apps)
        hero.addWidget(self.search_box, 3)
        hero.addStretch(1)
        layout.addLayout(hero)

        # Secondary action row -- small + quiet, under the hero search.
        toolbar = QHBoxLayout()
        toolbar.setSpacing(16)

        left_group = QHBoxLayout()
        left_group.setContentsMargins(0, 0, 0, 0)
        left_group.setSpacing(8)

        self.app_count_label = QLabel(
            tr("{shown} of {total} apps").format(shown=len(self.apps), total=len(self.apps))
        )
        self.app_count_label.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 12px;"
        )
        left_group.addWidget(self.app_count_label)

        right_group = QHBoxLayout()
        right_group.setContentsMargins(0, 0, 0, 0)
        right_group.setSpacing(8)

        toggle_wrap = QWidget()
        toggle_wrap.setStyleSheet(VIEW_TOGGLE)
        tgl = QHBoxLayout(toggle_wrap)
        tgl.setContentsMargins(0, 0, 0, 0)
        tgl.setSpacing(2)

        self.btn_grid = QPushButton("")
        self.btn_grid.setIcon(load_icon("grid", C.OVERLAY0, 16))
        self.btn_grid.setIconSize(QSize(16, 16))
        self.btn_grid.setCheckable(True)
        self.btn_grid.setChecked(True)
        self.btn_grid.setToolTip(tr("Grid view"))
        self.btn_grid.clicked.connect(lambda: self._set_view("grid"))
        tgl.addWidget(self.btn_grid)

        self.btn_list = QPushButton("")
        self.btn_list.setIcon(load_icon("list", C.OVERLAY0, 16))
        self.btn_list.setIconSize(QSize(16, 16))
        self.btn_list.setCheckable(True)
        self.btn_list.setToolTip(tr("List view"))
        self.btn_list.clicked.connect(lambda: self._set_view("list"))
        tgl.addWidget(self.btn_list)
        right_group.addWidget(toggle_wrap)

        self.refresh_btn = QPushButton(tr("Refresh Apps"))
        self.refresh_btn.setIcon(load_icon("refresh", C.TEXT, 16))
        self.refresh_btn.setIconSize(QSize(16, 16))
        self.refresh_btn.setStyleSheet(BTN_GHOST)
        self.refresh_btn.setToolTip(tr("Scan the running pod for installed Windows apps"))
        self.refresh_btn.clicked.connect(self._on_refresh_apps)
        right_group.addWidget(self.refresh_btn)

        # Hybrid filter UX — hidden apps (system shims auto-filtered by the
        # noise denylist, plus anything the user manually hid) collapse by
        # default. Click to expand; the count tells the user how much got
        # filtered so they can decide whether to dig in.
        self._show_hidden = False
        self.btn_show_hidden = QPushButton(tr("Hidden"))
        self.btn_show_hidden.setCheckable(True)
        self.btn_show_hidden.setStyleSheet(BTN_GHOST)
        self.btn_show_hidden.setToolTip(
            tr("Show apps filtered by the noise denylist or manually hidden")
        )
        self.btn_show_hidden.clicked.connect(self._on_toggle_hidden)
        right_group.addWidget(self.btn_show_hidden)

        # Restore-deleted entry point (#530). Deleting an app tombstones its
        # slug so discovery won't re-add it; this opens the un-delete list.
        # Hidden when there's nothing to restore.
        self.btn_deleted = QPushButton(tr("Deleted"))
        self.btn_deleted.setStyleSheet(BTN_GHOST)
        self.btn_deleted.setToolTip(tr("Restore apps you previously deleted"))
        self.btn_deleted.clicked.connect(self._on_open_deleted_apps)
        self.btn_deleted.setVisible(False)
        right_group.addWidget(self.btn_deleted)

        # Multi-select bulk-remove (#530). Toggling drops to list view (the only
        # tile with room for a checkbox -- the grid card is a minimal external
        # widget) and reveals the batch action bar below the toolbar.
        self._select_mode = False
        self._selected_names: set[str] = set()
        self.btn_select = QPushButton(tr("Select"))
        self.btn_select.setCheckable(True)
        self.btn_select.setStyleSheet(BTN_GHOST)
        self.btn_select.setToolTip(tr("Select multiple apps to remove at once"))
        self.btn_select.clicked.connect(self._on_toggle_select_mode)
        right_group.addWidget(self.btn_select)

        add_btn = QPushButton(tr("+  Add App"))
        add_btn.setStyleSheet(BTN_PRIMARY)
        add_btn.clicked.connect(self._on_add_app)
        right_group.addWidget(add_btn)

        toolbar.addLayout(left_group, 1)
        toolbar.addLayout(right_group, 0)

        layout.addLayout(toolbar)
        layout.addWidget(self._build_batch_bar())

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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(SCROLL_AREA)

        self.app_list_container = QWidget()
        self.app_list_container.setStyleSheet("background: transparent;")
        launcher_layout = QVBoxLayout(self.app_list_container)
        launcher_layout.setContentsMargins(0, 0, 0, 0)
        launcher_layout.setSpacing(SPACE_XL)

        # Command results -- the hero doubles as a command bar: typing a query
        # that matches an action (open a page, suspend/resume the pod, ...)
        # surfaces it here as a clickable row. Hidden when nothing matches.
        self._commands_section = QWidget()
        self._commands_section.setStyleSheet("background: transparent;")
        cmd_outer = QVBoxLayout(self._commands_section)
        cmd_outer.setContentsMargins(0, 0, 0, 0)
        cmd_outer.setSpacing(SPACE_M)
        cmd_outer.addWidget(make_section_label(tr("Commands")))
        self._commands_layout = QVBoxLayout()
        self._commands_layout.setContentsMargins(0, 0, 0, 0)
        self._commands_layout.setSpacing(SPACE_S)
        cmd_outer.addLayout(self._commands_layout)
        self._commands_section.setVisible(False)
        launcher_layout.addWidget(self._commands_section)

        # "Running" live-session strip -- winpodx knows what's actually running
        # (RDP session tracking), so surface it at the very top: a chip per live
        # session with a one-click terminate. Hidden when nothing is running.
        self._running_section, self._running_row = self._make_launcher_section(tr("Running"))
        launcher_layout.addWidget(self._running_section)

        self._pinned_section, self._pinned_row = self._make_launcher_section(tr("Pinned"))
        launcher_layout.addWidget(self._pinned_section)

        self._recent_section, self._recent_row = self._make_launcher_section(tr("Recent"))
        launcher_layout.addWidget(self._recent_section)

        all_apps_header = QWidget()
        all_apps_layout = QVBoxLayout(all_apps_header)
        all_apps_layout.setContentsMargins(0, 0, 0, 0)
        all_apps_layout.setSpacing(SPACE_M)
        all_apps_layout.addWidget(make_section_label(tr("All apps")))

        category_wrap = QWidget()
        self._category_row = QHBoxLayout(category_wrap)
        self._category_row.setContentsMargins(0, 0, 0, 0)
        self._category_row.setSpacing(8)
        self._category_btns: list[QPushButton] = []
        self._build_category_chips()
        all_apps_layout.addWidget(category_wrap)
        launcher_layout.addWidget(all_apps_header)

        self.app_list_layout = QVBoxLayout()
        self.app_list_layout.setContentsMargins(0, 0, 0, 0)
        self.app_list_layout.setSpacing(SPACE_XL)
        launcher_layout.addLayout(self.app_list_layout)
        self._refresh_hidden_button()
        self._refresh_launcher_home()

        scroll.setWidget(self.app_list_container)
        layout.addWidget(scroll)
        return page

    def _make_launcher_section(self, title: str) -> tuple[QWidget, QHBoxLayout]:
        section = QWidget()
        section.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_M)
        outer.addWidget(make_section_label(title))

        row_wrap = QWidget()
        row_wrap.setStyleSheet("background: transparent;")
        row = QHBoxLayout(row_wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_M)
        outer.addWidget(row_wrap)
        section.setVisible(False)
        return section, row

    def _build_category_chips(self) -> None:
        """Build category filter chips from available apps.

        Shows "All" first, then up to ``_MAX_CATEGORY_CHIPS`` category chips.
        Any remaining categories collapse into a "+N more" overflow menu so
        none are silently dropped (Task 4).
        """
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

        for cat in cats_sorted[:_MAX_CATEGORY_CHIPS]:
            btn = QPushButton(cat)
            btn.setCheckable(True)
            btn.setStyleSheet(FILTER_CHIP)
            btn.clicked.connect(lambda _, c=cat: self._set_category(c))
            self._category_row.addWidget(btn)
            self._category_btns.append(btn)

        overflow = cats_sorted[_MAX_CATEGORY_CHIPS:]
        if overflow:
            more_btn = QPushButton(tr("+{n} more").format(n=len(overflow)))
            more_btn.setCheckable(True)
            more_btn.setStyleSheet(FILTER_CHIP)
            more_btn.setToolTip(tr("More categories"))
            menu = QMenu(more_btn)
            for cat in overflow:
                menu.addAction(cat, lambda _=False, c=cat: self._set_category(c))
            more_btn.setMenu(menu)
            self._category_row.addWidget(more_btn)
            self._category_btns.append(more_btn)
            self._category_more_btn = more_btn
            self._overflow_categories = list(overflow)
        else:
            self._category_more_btn = None
            self._overflow_categories = []

        self._category_row.addStretch()

    def _clear_layout(self, layout: QHBoxLayout | QVBoxLayout | QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    def _apps_by_names(self, names: list[str], candidates: list[AppInfo]) -> list[AppInfo]:
        by_name = {app.name: app for app in candidates}
        return [by_name[name] for name in names if name in by_name]

    def _populate_launcher_row(
        self,
        section: QWidget,
        row: QHBoxLayout,
        apps: list[AppInfo],
    ) -> None:
        self._clear_layout(row)
        section.setVisible(bool(apps))
        if not apps:
            return
        # Cap the shelf to the number of tiles that actually fit the current
        # width so the Pinned / Recent rows never force the page wider than the
        # viewport (which clipped the All-apps grid on narrow / scaled windows).
        # Everything is still reachable in the grid below.
        visible = apps[: self._grid_cols()]
        for app in visible:
            row.addWidget(self._make_app_card(app))
        row.addStretch()

    def _refresh_launcher_sections(self, filtered: list[AppInfo]) -> None:
        pinned = self._apps_by_names(launcher_state.get_pinned(), filtered)
        recent = self._apps_by_names(launcher_state.get_recent(), filtered)
        self._populate_launcher_row(self._pinned_section, self._pinned_row, pinned)
        self._populate_launcher_row(self._recent_section, self._recent_row, recent)

    def _refresh_launcher_home(self) -> None:
        self._refresh_running_strip()
        self._filter_apps(self.search_box.text())

    def _running_display_name(self, stem: str) -> str:
        """Best-effort friendly name for a tracked session stem."""
        for a in self.apps:
            if a.name == stem:
                return a.full_name
        cleaned = stem.removeprefix("winpodx-uwp-").split("_")[0].replace("-", " ").strip()
        return cleaned.title() if cleaned else stem

    def _make_running_chip(self, app_name: str) -> QWidget:
        chip = QFrame()
        chip.setObjectName("runChip")
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setToolTip(tr("Focus window"))
        chip.setStyleSheet(
            f"QFrame#runChip {{ background: {C.SURFACE0}; border: 1px solid {C.SURFACE2};"
            " border-radius: 16px; }"
            f"QFrame#runChip:hover {{ border-color: {C.GREEN}; }}"
        )
        # Left-click the chip body -> raise/focus that app's window (the kill
        # button consumes its own clicks, so it won't trigger a focus).
        chip.mousePressEvent = lambda _e, n=app_name: self._focus_session(n)
        h = QHBoxLayout(chip)
        h.setContentsMargins(SPACE_M, SPACE_S, SPACE_S, SPACE_S)
        h.setSpacing(SPACE_S)

        dot = QLabel("●")
        dot.setStyleSheet(f"color: {C.GREEN}; font-size: 9px; background: transparent;")
        h.addWidget(dot)

        name = QLabel(self._running_display_name(app_name))
        name.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; background: transparent;")
        h.addWidget(name)

        kill_btn = QPushButton("")
        kill_btn.setIcon(load_icon("close", C.OVERLAY0, 14))
        kill_btn.setIconSize(QSize(14, 14))
        kill_btn.setFixedSize(22, 22)
        kill_btn.setToolTip(tr("Terminate"))
        kill_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 11px; }"
            f"QPushButton:hover {{ background: {C.SURFACE2}; }}"
        )
        kill_btn.clicked.connect(lambda _=False, n=app_name: self._terminate_session(n))
        h.addWidget(kill_btn)
        return chip

    def _refresh_running_strip(self) -> None:
        """Rebuild the 'Running' strip from the live RDP sessions."""
        if not hasattr(self, "_running_row"):
            return
        self._clear_layout(self._running_row)
        try:
            sessions = list_active_sessions()
        except Exception:  # noqa: BLE001 -- never break the home on enumeration
            sessions = []
        self._running_section.setVisible(bool(sessions))
        for s in sessions:
            self._running_row.addWidget(self._make_running_chip(s.app_name))
        if sessions:
            self._running_row.addStretch()

    def _terminate_session(self, app_name: str) -> None:
        try:
            kill_session(app_name)
        except Exception:  # noqa: BLE001 -- best-effort; refresh either way
            pass
        self._refresh_running_strip()

    def _focus_session(self, app_name: str) -> None:
        """Best-effort raise/focus of the app's window on the Linux desktop.

        FreeRDP RemoteApp windows are X11 (XWayland), so wmctrl / xdotool can
        activate them by WM_CLASS (== the session's wm-class token). Degrades
        quietly when neither tool is present.
        """
        import shutil
        import subprocess

        try:
            if shutil.which("wmctrl"):
                subprocess.run(["wmctrl", "-x", "-a", app_name], timeout=3, check=False)
            elif shutil.which("xdotool"):
                subprocess.run(
                    ["xdotool", "search", "--class", app_name, "windowactivate"],
                    timeout=3,
                    check=False,
                )
        except Exception:  # noqa: BLE001 -- best-effort, never break the UI
            pass

    def _command_specs(self):
        """Quick actions the hero command bar can run (label, icon, handler).
        Reuses existing tr() labels + handlers from sibling mixins."""
        return [
            # Page indices match the QStackedWidget order in main_window._build_ui
            # (Dashboard=0, All apps=1, then these). Keep in sync with the nav.
            (tr("Settings"), "gear", lambda: self._switch_page(2)),
            (tr("Tools"), "clean", lambda: self._switch_page(3)),
            (tr("Terminal / Logs"), "prompt", lambda: self._switch_page(4)),
            (tr("Info"), "pending", lambda: self._switch_page(5)),
            (tr("Devices"), "hardware", lambda: self._switch_page(6)),
            (tr("License"), "diamond", lambda: self._switch_page(7)),
            (tr("Suspend Pod"), "pause", self._on_suspend),
            (tr("Resume Pod"), "play", self._on_resume),
            (tr("Full Desktop"), "desktop", self._on_open_desktop),
            (tr("Refresh Apps"), "refresh", self._on_refresh_apps),
        ]

    def _make_command_row(self, label: str, icon: str, handler) -> QWidget:
        row = QFrame()
        row.setObjectName("cmdRow")
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setStyleSheet(
            f"QFrame#cmdRow {{ background: {C.SURFACE0}; border: 1px solid {C.SURFACE2};"
            " border-radius: 10px; }"
            f"QFrame#cmdRow:hover {{ border-color: {C.BLUE}; }}"
        )
        h = QHBoxLayout(row)
        h.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)
        h.setSpacing(SPACE_M)
        ic = QLabel()
        ic.setPixmap(load_icon(icon, C.SUBTEXT1, 16).pixmap(16, 16))
        ic.setStyleSheet("background: transparent;")
        h.addWidget(ic)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"background: transparent; color: {C.TEXT}; font-size: 13px;")
        h.addWidget(lbl)
        h.addStretch()
        row.mousePressEvent = lambda _e, fn=handler: fn()
        return row

    def _refresh_commands(self, q: str) -> None:
        """Show command rows matching the query (the hero acts as a command bar)."""
        if not hasattr(self, "_commands_layout"):
            return
        self._clear_layout(self._commands_layout)
        matches = []
        if q:
            for label, icon, handler in self._command_specs():
                if q in label.lower():
                    matches.append((label, icon, handler))
        self._commands_section.setVisible(bool(matches))
        for label, icon, handler in matches[:5]:
            self._commands_layout.addWidget(self._make_command_row(label, icon, handler))

    def _on_toggle_pin_app(self, app: AppInfo) -> None:
        if launcher_state.is_pinned(app.name):
            launcher_state.unpin(app.name)
        else:
            launcher_state.pin(app.name)
        self._refresh_launcher_home()

    def _set_category(self, category: str) -> None:
        self._active_category = category
        more_btn = getattr(self, "_category_more_btn", None)
        overflow = getattr(self, "_overflow_categories", [])
        for btn in self._category_btns:
            if btn is more_btn:
                # The overflow chip stays highlighted while any of its
                # collapsed categories is the active filter.
                btn.setChecked(category in overflow)
            else:
                btn.setChecked((category == "" and btn.text() == "All") or btn.text() == category)
        self._filter_apps(self.search_box.text())

    def _set_view(self, mode: str) -> None:
        self._view_mode = mode
        self.btn_grid.setChecked(mode == "grid")
        self.btn_list.setChecked(mode == "list")
        self._filter_apps(self.search_box.text())

    def _populate_app_view(self, apps: list[AppInfo]) -> None:
        """Populate apps in grid or list layout."""
        self._clear_layout(self.app_list_layout)

        if not apps:
            self.app_list_layout.addWidget(self._make_empty_state())
            self.app_list_layout.addStretch()
            return

        if self._view_mode == "grid":
            self._populate_grid(apps)
        else:
            self._populate_list(apps)

    def _make_empty_state(self) -> QWidget:
        """Build a context-aware empty-state panel (Task 1).

        Distinguishes the four real causes of an empty grid so the message
        and any affordance match the situation:
          (a) pod not running     -> prompt to start Windows
          (b) search/filter active -> "no match" + clear-filter hint
          (c) everything hidden     -> hint to toggle Hidden
          (d) genuinely none        -> the add-a-profile message
        """
        query = self.search_box.text().strip()
        category = self._active_category
        pod_state = getattr(self, "_pod_state", "checking")
        pod_running = pod_state == "running"
        cfg = getattr(self, "cfg", None)
        initialized = bool(cfg and getattr(cfg.pod, "initialized", False))
        # First-ever install: setup hasn't completed yet, but the pod is coming
        # up (dockur downloads + installs Windows inside the running container,
        # so the state is "starting" or even "running" for the whole ~20-40 min
        # install). Without this the grid showed "Windows isn't running" + a
        # Start button the entire time, reading as broken (#502 reporter).
        installing = (not initialized) and pod_state in ("starting", "running")
        any_apps = bool(self.apps)
        all_hidden = any_apps and all(a.hidden for a in self.apps)

        action_label = ""
        action_cb = None

        # (a0) First-time setup is in progress — show progress, no Start button.
        if not any_apps and installing:
            title = tr("Setting up Windows (first run)…")
            body = tr(
                "Downloading and installing Windows — this can take 20–40 minutes. "
                "You can watch progress at http://127.0.0.1:8006"
            )
        # (a) Nothing discovered yet AND Windows isn't up — the most likely
        # cause of an empty library on a fresh/stopped install.
        elif not any_apps and not pod_running:
            title = tr("Windows isn't running")
            body = tr("Start it to scan for your installed apps.")
            action_label = tr("Start Windows")
            action_cb = getattr(self, "_on_start_pod", None)
        # (b) A search or category filter is active but matched nothing.
        elif query or category:
            if query:
                title = tr("No apps match '{query}'").format(query=query)
            else:
                title = tr("No apps in '{category}'").format(category=category)
            body = tr("Clear the search or pick 'All' to see every app.")
        # (c) Everything is hidden and the Hidden toggle is off.
        elif all_hidden and not self._show_hidden:
            title = tr("All apps are hidden")
            body = tr("Toggle 'Hidden' in the toolbar to show them.")
        # (d) Genuinely nothing registered yet.
        else:
            title = tr("No apps yet")
            body = tr("Add a Windows app profile to get started.")

        panel = make_empty_panel(
            title,
            body,
            action_label=action_label,
            action_cb=action_cb if callable(action_cb) else None,
        )
        panel.setMinimumHeight(220)
        return panel

    def _grid_cols(self) -> int:
        """Column count for the tile grid, derived from the available width so
        tiles never force a horizontal scrollbar on narrow / scaled windows."""
        pages = getattr(self, "pages", None)
        width = pages.width() if pages is not None else 1100
        # Reserve for the page margins + the (now always-on) vertical scrollbar,
        # and budget ~140px per tile (≈120px tile + spacing) so the rightmost
        # column can't overflow and clip.
        content = max(300, width - 112)
        return max(3, min(6, content // 140))

    def _populate_grid(self, apps: list[AppInfo]) -> None:
        """Grid view - Start-menu-style icon tiles (dense)."""
        cols = self._grid_cols()
        self._current_grid_cols = cols
        self.app_list_layout.setSpacing(SPACE_L)
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE_S)
        grid.setVerticalSpacing(SPACE_S)
        grid.setContentsMargins(0, 0, 0, 0)
        for col in range(cols):
            grid.setColumnStretch(col, 1)

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

    def _reflow_library(self) -> None:
        """Re-flow the tile grid when the responsive column count changes on
        resize (avoids a horizontal scrollbar on narrow windows). No-op in
        list view or when the count is unchanged. Driven by the resizeEvent."""
        if getattr(self, "_view_mode", "grid") != "grid":
            return
        if not hasattr(self, "search_box"):
            return
        if self._grid_cols() != getattr(self, "_current_grid_cols", None):
            self._filter_apps(self.search_box.text())

    def _populate_list(self, apps: list[AppInfo]) -> None:
        """List view - horizontal tiles."""
        self.app_list_layout.setSpacing(SPACE_M)
        for app in apps:
            self.app_list_layout.addWidget(self._make_app_tile(app))
        self.app_list_layout.addStretch()

    def _make_app_card(self, app: AppInfo) -> QWidget:
        """A Start-menu-style launcher tile (icon + name, click to launch)."""
        return _AppTile(app, on_launch=self._launch_app, on_menu=self._show_app_menu)

    def _show_app_menu(self, app: AppInfo, global_pos) -> None:
        """Right-click context menu for a launcher tile: Pin / Edit / Hide /
        Delete. Launch is the left-click (the whole tile)."""
        menu = QMenu(self)
        pin_action = menu.addAction(
            tr("Unpin") if launcher_state.is_pinned(app.name) else tr("Pin")
        )
        pin_action.setIcon(load_icon("pin", C.SUBTEXT1, 16))
        pin_action.triggered.connect(lambda _=False, a=app: self._on_toggle_pin_app(a))

        edit_action = menu.addAction(tr("Edit"))
        edit_action.triggered.connect(lambda _=False, a=app: self._on_edit_app(a))

        # "Reset to Detected" only when a user override shadows a discovered
        # twin -- otherwise there's nothing to fall back to and Delete is the
        # right verb (#530).
        if getattr(app, "source", "user") == "user":
            from winpodx.core.app import discovered_profile_exists

            if discovered_profile_exists(app.name):
                reset_action = menu.addAction(tr("Reset to Detected"))
                reset_action.triggered.connect(lambda _=False, a=app: self._on_reset_app(a))

        hide_action = menu.addAction(tr("Show") if app.hidden else tr("Hide"))
        hide_action.triggered.connect(lambda _=False, a=app: self._on_toggle_app_hidden(a))

        delete_action = menu.addAction(tr("Delete"))
        delete_action.triggered.connect(lambda _=False, a=app: self._on_delete_app(a))
        menu.exec(global_pos)

    def _make_app_tile(self, app: AppInfo) -> QWidget:
        """Horizontal app tile with colored accent stripe."""
        color = avatar_color(app.name)

        tile = QFrame()
        tile.setObjectName("appTile")
        tile.setStyleSheet(APP_TILE)
        tile.setMinimumHeight(86)
        add_shadow(tile, blur=10, y=2, alpha=28)

        layout = QHBoxLayout(tile)
        layout.setContentsMargins(0, SPACE_S, SPACE_L, SPACE_S)
        layout.setSpacing(0)

        # In multi-select mode each tile grows a leading checkbox (#530).
        if getattr(self, "_select_mode", False):
            cb = QCheckBox()
            cb.setChecked(app.name in self._selected_names)
            # Use the themed indicator (bordered box, blue when checked); the
            # old bare "margin-left" stylesheet wiped the indicator style so the
            # box was invisible against the dark tile (#530 follow-up).
            cb.setStyleSheet(CHECKBOX + "QCheckBox { margin-left: 12px; }")
            cb.toggled.connect(lambda checked, n=app.name: self._on_tile_checked(n, checked))
            layout.addWidget(cb)
            layout.addSpacing(SPACE_S)

        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background: {color}; border-radius: 2px; margin: 8px 0 8px 8px;")
        layout.addWidget(stripe)
        layout.addSpacing(SPACE_M)

        avatar = make_app_avatar(app, size=40, radius=10, font_size=16)
        layout.addWidget(avatar)
        layout.addSpacing(SPACE_M)

        info = QVBoxLayout()
        info.setSpacing(2)

        name_lbl = QLabel(app.full_name)
        name_lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 14px; font-weight: 500;"
        )
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)
        name_row.addWidget(name_lbl)
        badge = make_source_badge(app)
        if badge is not None:
            name_row.addWidget(badge)
        name_row.addStretch()
        info.addLayout(name_row)

        meta_parts = []
        if app.categories:
            meta_parts.append(", ".join(app.categories[:2]))
        meta_parts.append(app.name)
        meta_lbl = QLabel(" • ".join(meta_parts))
        meta_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        info.addWidget(meta_lbl)

        layout.addLayout(info)
        layout.addStretch()

        launch_btn = QPushButton(tr("▶  Launch"))
        launch_btn.setText(launch_btn.text().removeprefix("▶  "))
        launch_btn.setIcon(load_icon("play", C.CRUST, 16))
        launch_btn.setIconSize(QSize(16, 16))
        launch_btn.setStyleSheet(BTN_ACCENT)
        launch_btn.setMinimumWidth(116)
        launch_btn.clicked.connect(lambda: self._launch_app(app))
        layout.addWidget(launch_btn)
        layout.addSpacing(8)

        edit_btn = QPushButton(tr("Edit"))
        edit_btn.setStyleSheet(BTN_SECONDARY)
        edit_btn.clicked.connect(lambda: self._on_edit_app(app))
        layout.addWidget(edit_btn)
        layout.addSpacing(6)

        # Surface "Reset" as a visible button (not just the right-click menu)
        # when this app is an edited override with a detected twin to fall back
        # to — the context-menu-only action was too hard to find (#530).
        if getattr(app, "source", "user") == "user":
            from winpodx.core.app import discovered_profile_exists

            if discovered_profile_exists(app.name):
                reset_btn = QPushButton(tr("Reset"))
                reset_btn.setStyleSheet(BTN_SECONDARY)
                reset_btn.setToolTip(tr("Restore the auto-detected profile + icon"))
                reset_btn.clicked.connect(lambda: self._on_reset_app(app))
                layout.addWidget(reset_btn)
                layout.addSpacing(6)

        hide_btn = QPushButton(tr("Show") if app.hidden else tr("Hide"))
        hide_btn.setStyleSheet(BTN_SECONDARY)
        hide_btn.clicked.connect(lambda: self._on_toggle_app_hidden(app))
        layout.addWidget(hide_btn)
        layout.addSpacing(6)

        del_btn = QPushButton("")
        del_btn.setIcon(load_icon("close", C.PEACH, 16))
        del_btn.setIconSize(QSize(16, 16))
        del_btn.setFixedSize(32, 32)
        del_btn.setStyleSheet(BTN_DANGER)
        del_btn.clicked.connect(lambda: self._on_delete_app(app))
        layout.addWidget(del_btn)

        return tile

    def _visible_apps(self) -> list[AppInfo]:
        """Apps that should appear in the grid given the current Hidden toggle.

        The hybrid filter sets ``hidden=True`` on noise-denylisted entries
        and on anything the user manually hid; by default we exclude those
        from the grid. Toggling "Hidden" includes them so the user can
        unhide individual entries.
        """
        if self._show_hidden:
            return list(self.apps)
        return [a for a in self.apps if not a.hidden]

    def _hidden_count(self) -> int:
        return sum(1 for a in self.apps if a.hidden)

    def _refresh_hidden_button(self) -> None:
        n = self._hidden_count()
        if n == 0:
            self.btn_show_hidden.setVisible(False)
            return
        self.btn_show_hidden.setVisible(True)
        if self._show_hidden:
            self.btn_show_hidden.setText(tr("Showing ({n})").format(n=n))
        else:
            self.btn_show_hidden.setText(tr("Hidden ({n})").format(n=n))

    def _on_toggle_hidden(self) -> None:
        self._show_hidden = self.btn_show_hidden.isChecked()
        self._refresh_hidden_button()
        self._filter_apps(self.search_box.text())

    # -- restore deleted apps (#530) --------------------------------------

    def _refresh_deleted_button(self) -> None:
        """Show the "Deleted (N)" button only when there are tombstones."""
        if not hasattr(self, "btn_deleted"):
            return
        from winpodx.core.app import suppressed_app_slugs

        n = len(suppressed_app_slugs())
        self.btn_deleted.setVisible(n > 0)
        self.btn_deleted.setText(tr("Deleted ({n})").format(n=n) if n else tr("Deleted"))

    def _on_open_deleted_apps(self) -> None:
        from winpodx.core.app import suppressed_app_slugs
        from winpodx.gui.deleted_apps_dialog import DeletedAppsDialog

        slugs = sorted(suppressed_app_slugs())
        if not slugs:
            self._refresh_deleted_button()
            return
        dlg = DeletedAppsDialog(self, slugs=slugs, on_restore=self._restore_deleted_slugs)
        dlg.exec()
        self._refresh_deleted_button()

    def _restore_deleted_slugs(self, slugs: list[str]) -> None:
        """Un-tombstone the given slugs and re-scan so they reappear (#530)."""
        from winpodx.core.app import (
            clear_suppressed_slugs,
            suppressed_app_slugs,
            unsuppress_app_slug,
        )

        # Restore-all wipes the whole tombstone file; a partial set unsuppresses each.
        if set(slugs) >= suppressed_app_slugs():
            clear_suppressed_slugs()
        else:
            for s in slugs:
                unsuppress_app_slug(s)
        self.info_label.setText(tr("Restoring {n} app(s) — re-scanning…").format(n=len(slugs)))
        # The discovered/<slug> dirs were removed on delete, so only a fresh
        # discovery sweep actually brings the apps back.
        self._on_refresh_apps()

    # -- multi-select bulk remove (#530) ----------------------------------

    def _build_batch_bar(self) -> QWidget:
        """Hidden-by-default action bar shown while in multi-select mode."""
        bar = QWidget()
        bar.setVisible(False)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, SPACE_S, 0, SPACE_S)
        row.setSpacing(SPACE_M)
        self._batch_label = QLabel(tr("{n} selected").format(n=0))
        self._batch_label.setStyleSheet(f"background: transparent; color: {C.SUBTEXT0};")
        row.addWidget(self._batch_label)
        row.addStretch()
        self._batch_hide_btn = QPushButton(tr("Hide selected"))
        self._batch_hide_btn.setStyleSheet(BTN_SECONDARY)
        self._batch_hide_btn.setEnabled(False)
        self._batch_hide_btn.clicked.connect(self._on_batch_hide)
        row.addWidget(self._batch_hide_btn)
        self._batch_remove_btn = QPushButton(tr("Remove selected"))
        self._batch_remove_btn.setStyleSheet(BTN_DANGER)
        self._batch_remove_btn.setEnabled(False)
        self._batch_remove_btn.clicked.connect(self._on_batch_remove)
        row.addWidget(self._batch_remove_btn)
        cancel = QPushButton(tr("Cancel"))
        cancel.setStyleSheet(BTN_GHOST)
        cancel.clicked.connect(self._exit_select_mode)
        row.addWidget(cancel)
        self._batch_bar = bar
        return bar

    def _on_toggle_select_mode(self) -> None:
        self._select_mode = self.btn_select.isChecked()
        self._selected_names.clear()
        # Grid cards can't host a checkbox, so lock the view to list while
        # selecting (re-enable the grid toggle on exit).
        self.btn_grid.setEnabled(not self._select_mode)
        # Checkboxes only render in list view, so entering select mode forces it
        # (which itself rebuilds via _set_view -> _filter_apps).
        if self._select_mode and getattr(self, "_view_mode", "grid") != "list":
            self._set_view("list")
        else:
            self._filter_apps(self.search_box.text())
        self._update_batch_bar()

    def _exit_select_mode(self) -> None:
        self.btn_select.setChecked(False)
        self._on_toggle_select_mode()

    def _on_tile_checked(self, name: str, checked: bool) -> None:
        if checked:
            self._selected_names.add(name)
        else:
            self._selected_names.discard(name)
        self._update_batch_bar()

    def _update_batch_bar(self) -> None:
        n = len(self._selected_names)
        self._batch_bar.setVisible(self._select_mode)
        self._batch_label.setText(tr("{n} selected").format(n=n))
        self._batch_remove_btn.setEnabled(n > 0)
        self._batch_hide_btn.setEnabled(n > 0)

    def _on_batch_hide(self) -> None:
        """Hide all selected apps from the Linux menu (reversible, no confirm)."""
        names = sorted(self._selected_names)
        if not names:
            return
        from winpodx.core.app import set_app_hidden

        hidden = sum(1 for name in names if set_app_hidden(name, True) is not None)
        self._selected_names.clear()
        self.btn_select.setChecked(False)
        self._select_mode = False
        self.btn_grid.setEnabled(True)
        self._reload_apps()
        self._update_batch_bar()
        self.info_label.setText(tr("Hid {n} apps").format(n=hidden))

    def _on_batch_remove(self) -> None:
        names = sorted(self._selected_names)
        if not names:
            return
        reply = QMessageBox.question(
            self,
            tr("Remove Apps"),
            tr(
                "Remove {n} selected app profiles?\n"
                "This only removes the profiles, not the Windows apps."
            ).format(n=len(names)),
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from winpodx.core.app import find_app, suppress_app_slug
        from winpodx.desktop.entry import remove_desktop_entry
        from winpodx.gui.app_dialog import delete_app_profile

        for name in names:
            app = find_app(name)
            delete_app_profile(name)
            remove_desktop_entry(name)
            # Tombstone discovered slugs so the next sweep doesn't resurrect them (#514).
            if app is not None and getattr(app, "source", "user") == "discovered":
                suppress_app_slug(name)

        self._selected_names.clear()
        self.btn_select.setChecked(False)
        self._select_mode = False
        self._reload_apps()  # refreshes self.apps + rebuilds the view
        self._update_batch_bar()
        self.info_label.setText(tr("Removed {n} apps").format(n=len(names)))

    def _filter_apps(self, text: str) -> None:
        # Re-entrancy guard. Rebuilding app_list_layout below adds word-wrapped
        # empty-state labels into a setWidgetResizable QScrollArea, which forces
        # a synchronous heightForWidth layout pass. If that pass re-enters
        # _filter_apps mid-rebuild -- via the window resizeEvent -> _reflow_library,
        # or the pod-status -> _filter_apps refresh, or a discover reload that
        # rebuilds twice -- Qt's QBoxLayout::heightForWidth recurses without bound
        # and segfaults the whole GUI ("QObject::setParent: ... different thread"
        # warnings then SIGSEGV, observed on Wayland after the discover button).
        # Coalesce the nested call into one trailing rebuild instead.
        if getattr(self, "_filtering", False):
            self._filter_pending = text
            return
        self._filtering = True
        try:
            self._filter_pending = None
            q = text.lower()
            self._refresh_commands(q)
            base = self._visible_apps()
            filtered = [a for a in base if q in a.full_name.lower() or q in a.name.lower()]
            if self._active_category:
                filtered = [a for a in filtered if self._active_category in a.categories]
            self._refresh_launcher_sections(filtered)
            self._populate_app_view(filtered)
            # "X of Y" so the toolbar count reconciles with the info bar's total
            # after a search/filter (Task 5).
            self.app_count_label.setText(
                tr("{shown} of {total} apps").format(shown=len(filtered), total=len(self.apps))
            )
        finally:
            self._filtering = False
        # Honor the most recent text if a nested call arrived during the rebuild.
        # This runs as a fresh top-level call (guard already released), so it
        # cannot recurse into the layout pass that triggered it.
        pending = self._filter_pending
        if pending is not None and pending != text:
            self._filter_pending = None
            self._filter_apps(pending)
