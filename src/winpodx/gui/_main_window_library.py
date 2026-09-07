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
from winpodx.core.process import kill_session, list_active_sessions  # noqa: F401
from winpodx.gui import launcher_state, theme
from winpodx.gui._main_window_library_chips import LibraryChipsMixin
from winpodx.gui._main_window_library_start import LibraryStartMixin
from winpodx.gui._main_window_library_tiles import _AppTile, make_library_list_tile
from winpodx.gui._widget_helpers import (
    make_empty_panel,
    make_section_label,
)
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    BTN_DANGER,
    BTN_GHOST,
    BTN_PRIMARY,
    BTN_SECONDARY,
    PAGE_MARGIN_X,
    SCROLL_AREA,
    SCROLL_GUTTER,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    VIEW_TOGGLE,
    C,
)


class _LibraryPage(QWidget):
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        box = getattr(self, "_focus_search", None)
        if box is not None:
            box.setFocus(Qt.FocusReason.OtherFocusReason)


class LibraryPageMixin(LibraryChipsMixin, LibraryStartMixin):
    """Builds the Apps page + drives grid/list view + filter state."""

    def _build_library_page(self) -> QWidget:
        page = _LibraryPage()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, PAGE_MARGIN_X - SCROLL_GUTTER, SPACE_XL)
        layout.setSpacing(SPACE_XL)

        header_actions = QWidget()
        header_actions.setObjectName("libraryHeaderActions")
        self._library_header_actions = header_actions
        right_group = QHBoxLayout(header_actions)
        right_group.setContentsMargins(0, 0, 0, 0)
        right_group.setSpacing(SPACE_S)
        register = getattr(self, "_register_page_header", None)
        if callable(register):
            register(1, tr("Applications"), actions=header_actions)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(SPACE_M)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Search apps by name..."))
        self._style_library_search(self.search_box)
        self.search_box.addAction(
            load_icon("search", C.SUBTEXT0, 18),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter_apps)
        page._focus_search = self.search_box
        toolbar.addWidget(self.search_box, 1)

        self.app_count_label = QLabel(
            tr("{shown} of {total} apps").format(shown=len(self.apps), total=len(self.apps))
        )
        self.app_count_label.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT1}; font-size: {theme.FONT_CAPTION}px;"
        )
        toolbar.addWidget(self.app_count_label)

        toggle_wrap = QWidget()
        self._view_toggle_wrap = toggle_wrap
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
        toolbar.addWidget(toggle_wrap, 0)

        self.refresh_btn = QPushButton(tr("Refresh Apps"))
        self.refresh_btn.setIcon(load_icon("refresh", C.TEXT, 16))
        self.refresh_btn.setIconSize(QSize(16, 16))
        self.refresh_btn.setStyleSheet(BTN_SECONDARY)
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
        self.btn_show_hidden.setStyleSheet(BTN_SECONDARY)
        self.btn_show_hidden.setToolTip(
            tr("Show apps filtered by the noise denylist or manually hidden")
        )
        self.btn_show_hidden.clicked.connect(self._on_toggle_hidden)
        right_group.addWidget(self.btn_show_hidden)

        # Restore-deleted entry point (#530). Deleting an app tombstones its
        # slug so discovery won't re-add it; this opens the un-delete list.
        # Hidden when there's nothing to restore.
        self.btn_deleted = QPushButton(tr("Deleted"))
        self.btn_deleted.setStyleSheet(BTN_SECONDARY)
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
        self.btn_select.setStyleSheet(BTN_SECONDARY)
        self.btn_select.setToolTip(tr("Select multiple apps to remove at once"))
        self.btn_select.clicked.connect(self._on_toggle_select_mode)
        right_group.addWidget(self.btn_select)

        add_btn = QPushButton(tr("+  Add App"))
        add_btn.setStyleSheet(BTN_PRIMARY)
        add_btn.clicked.connect(self._on_add_app)
        self.add_app_btn = add_btn
        right_group.addWidget(add_btn)

        if not callable(register):
            toolbar.addWidget(header_actions, 0)

        layout.addLayout(toolbar)
        layout.addWidget(self._build_batch_bar())

        self.refresh_progress = QProgressBar()
        self.refresh_progress.setRange(0, 0)  # indeterminate
        self.refresh_progress.setTextVisible(False)
        self.refresh_progress.setFixedHeight(4)
        self.refresh_progress.setVisible(False)
        self.refresh_progress.setStyleSheet(theme.PROGRESS)
        layout.addWidget(self.refresh_progress)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Vertical scrollbar ALWAYS on (#567): with widgetResizable=True an
        # AsNeeded vertical bar fluctuates as content height changes, which
        # changes the viewport width, which makes QScrollArea.updateScrollBars
        # re-query the layout's heightForWidth — and with the word-wrapped app
        # name labels below that QBoxLayout::heightForWidth feedback recurses
        # without bound and SIGSEGVs the whole GUI on "Refresh Apps" (confirmed
        # by the crash backtrace; same family as #532). Pinning the bar on keeps
        # the viewport width stable so the loop can't form. The SCROLL_AREA
        # stylesheet keeps it visually unobtrusive.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
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

        self._mount_start_sections(launcher_layout)
        self._build_category_chips()

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

    def _clear_layout(self, layout: QHBoxLayout | QVBoxLayout | QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
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
        self._populate_pinned_grid(pinned)

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
            # (Dashboard=0, Applications=1, then these). Keep in sync with the nav.
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

    def _set_view(self, mode: str) -> None:
        self._view_mode = mode
        self.btn_grid.setChecked(mode == "grid")
        self.btn_list.setChecked(mode == "list")
        self._filter_apps(self.search_box.text())

    def _populate_app_view(self, apps: list[AppInfo]) -> None:
        """Populate apps in grid or list layout."""
        self._clear_layout(self.app_list_layout)

        if not apps:
            self._current_grid_cols = self._grid_cols()
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
            title = tr("Applications are hidden")
            body = tr("Toggle 'Hidden' in the toolbar to show them.")
        # (d) Genuinely nothing registered yet.
        else:
            title = tr("No apps yet")
            body = tr("Add a Windows app profile to get started.")
            action_label = tr("Refresh Apps")
            action_cb = getattr(self, "_on_refresh_apps", None)

        panel = make_empty_panel(
            title,
            body,
            action_label=action_label,
            action_cb=action_cb if callable(action_cb) else None,
        )
        panel.setMinimumHeight(220)
        return panel

    def _grid_cols(self) -> int:
        """Column count from content width: floor(avail / 128), clamped 3..6."""
        pitch = 104 + 2 * SPACE_S + SPACE_S
        live = getattr(self, "_live_pages_width", None)
        pages = getattr(self, "pages", None)
        width = live() if callable(live) else (pages.width() if pages is not None else 0)
        wrap = getattr(self, "_pinned_row", None)
        if wrap is not None and not callable(live):
            parent = wrap.parentWidget()
            if parent is not None and parent.width() > pitch * 3:
                width = parent.width()
        if width <= 0:
            width = 1100 - theme.NAV_PANE_WIDTH
        return max(3, min(6, width // pitch))

    def _populate_grid(self, apps: list[AppInfo]) -> None:
        """Grid view - Start-menu-style icon tiles (dense)."""
        cols = self._grid_cols()
        self._current_grid_cols = cols
        self.app_list_layout.setSpacing(SPACE_L)
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE_S)
        grid.setVerticalSpacing(SPACE_S)
        grid.setContentsMargins(0, 0, 0, 0)
        tile_w = 104 + 2 * SPACE_S
        for col in range(min(len(apps), cols)):
            grid.setColumnMinimumWidth(col, tile_w)
        grid.setColumnStretch(cols, 1)

        row_h = 0
        for i, app in enumerate(apps):
            card = self._make_app_card(app)
            grid.addWidget(card, i // cols, i % cols)
            row_h = max(row_h, card.height() or card.sizeHint().height())
        n_rows = (len(apps) + cols - 1) // cols if apps else 0
        for r in range(n_rows):
            grid.setRowMinimumHeight(r, row_h)

        remainder = len(apps) % cols
        if remainder:
            for j in range(remainder, cols):
                spacer = QWidget()
                spacer.setStyleSheet("background: transparent;")
                grid.addWidget(spacer, len(apps) // cols, j)

        grid_widget = QWidget()
        grid_widget.setLayout(grid)
        if n_rows:
            grid_widget.setMinimumHeight(n_rows * row_h + grid.verticalSpacing() * (n_rows - 1))
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
        tile = _AppTile(app, on_launch=self._launch_app, on_menu=self._show_app_menu)
        tile.set_running(app.name in getattr(self, "_running_names", set()))
        return tile

    def _show_app_menu(self, app: AppInfo, global_pos) -> None:
        """Right-click context menu for a launcher tile: Launch / Pin / Edit / Hide /
        Delete. Launch is also the left-click (the whole tile)."""
        menu = QMenu(self)
        menu.setStyleSheet(theme.GLOBAL_STYLE)
        if callable(getattr(self, "_launch_app", None)):
            launch_action = menu.addAction(tr("Launch"))
            launch_action.triggered.connect(lambda _=False, a=app: self._launch_app(a))
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
        """48px list row: 24px icon, name + category caption, 32px Launch."""
        return make_library_list_tile(self, app)

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
            self._sync_category_chip_counts()
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
