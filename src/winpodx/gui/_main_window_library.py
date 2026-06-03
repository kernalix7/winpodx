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
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import AppInfo
from winpodx.core.i18n import tr
from winpodx.gui import launcher_state
from winpodx.gui._widget_helpers import (
    add_shadow,
    make_app_avatar,
    make_empty_panel,
    make_page_header,
    make_section_label,
    make_source_badge,
)
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    APP_CARD,
    APP_TILE,
    BTN_ACCENT,
    BTN_DANGER,
    BTN_GHOST,
    BTN_PRIMARY,
    BTN_SECONDARY,
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


class LibraryPageMixin:
    """Builds the Apps page + drives grid/list view + filter state."""

    def _build_library_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE_XXL, 0, SPACE_XXL, SPACE_L)
        layout.setSpacing(SPACE_M)

        layout.addWidget(make_page_header(tr("Apps")))

        toolbar = QHBoxLayout()
        toolbar.setSpacing(16)

        left_group = QHBoxLayout()
        left_group.setContentsMargins(0, 0, 0, 0)
        left_group.setSpacing(8)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr("Search apps by name..."))
        self.search_box.setStyleSheet(SEARCH_BAR)
        self.search_box.setMinimumWidth(360)
        self.search_box.setMaximumWidth(620)
        self.search_box.addAction(
            load_icon("search", C.OVERLAY0, 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._filter_apps)
        left_group.addWidget(self.search_box)

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

        add_btn = QPushButton(tr("+  Add App"))
        add_btn.setStyleSheet(BTN_PRIMARY)
        add_btn.clicked.connect(self._on_add_app)
        right_group.addWidget(add_btn)

        toolbar.addLayout(left_group, 1)
        toolbar.addLayout(right_group, 0)

        layout.addLayout(toolbar)

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
        scroll.setStyleSheet(SCROLL_AREA)

        self.app_list_container = QWidget()
        self.app_list_container.setStyleSheet("background: transparent;")
        launcher_layout = QVBoxLayout(self.app_list_container)
        launcher_layout.setContentsMargins(0, 0, 0, 0)
        launcher_layout.setSpacing(SPACE_XL)

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
        for app in apps:
            row.addWidget(self._make_app_card(app))
        row.addStretch()

    def _refresh_launcher_sections(self, filtered: list[AppInfo]) -> None:
        pinned = self._apps_by_names(launcher_state.get_pinned(), filtered)
        recent = self._apps_by_names(launcher_state.get_recent(), filtered)
        self._populate_launcher_row(self._pinned_section, self._pinned_row, pinned)
        self._populate_launcher_row(self._recent_section, self._recent_row, recent)

    def _refresh_launcher_home(self) -> None:
        self._filter_apps(self.search_box.text())

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
        pod_running = getattr(self, "_pod_state", "checking") == "running"
        any_apps = bool(self.apps)
        all_hidden = any_apps and all(a.hidden for a in self.apps)

        action_label = ""
        action_cb = None

        # (a) Nothing discovered yet AND Windows isn't up — the most likely
        # cause of an empty library on a fresh/stopped install.
        if not any_apps and not pod_running:
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

    def _populate_grid(self, apps: list[AppInfo]) -> None:
        """Grid view - cards."""
        cols = 4
        self.app_list_layout.setSpacing(SPACE_XL)
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE_XL)
        grid.setVerticalSpacing(SPACE_XL)
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

    def _populate_list(self, apps: list[AppInfo]) -> None:
        """List view - horizontal tiles."""
        self.app_list_layout.setSpacing(SPACE_M)
        for app in apps:
            self.app_list_layout.addWidget(self._make_app_tile(app))
        self.app_list_layout.addStretch()

    def _make_app_card(self, app: AppInfo) -> QWidget:
        """Grid card with compact app identity, status, and launch footer."""
        card = QFrame()
        card.setObjectName("appCard")
        card.setStyleSheet(APP_CARD)
        card.setMinimumWidth(188)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        add_shadow(card, blur=12, y=2, alpha=28)

        vl = QVBoxLayout(card)
        vl.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        vl.setSpacing(SPACE_M)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(SPACE_M)

        avatar = make_app_avatar(app, size=44, radius=12, font_size=19)
        top_row.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignLeft)

        identity = QVBoxLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setSpacing(4)

        name_lbl = QLabel(app.full_name)
        name_lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 13px; font-weight: 500;"
        )
        name_lbl.setWordWrap(False)
        name_lbl.setMaximumWidth(156)
        fm = name_lbl.fontMetrics()
        elided = fm.elidedText(app.full_name, Qt.TextElideMode.ElideRight, 156)
        name_lbl.setText(elided)
        name_lbl.setToolTip(app.full_name)
        identity.addWidget(name_lbl)

        meta_parts = []
        if app.categories:
            meta_parts.append(", ".join(app.categories[:2]))
        meta_parts.append(app.name)
        if app.hidden:
            meta_parts.append(tr("Hidden"))
        meta_lbl = QLabel(" • ".join(meta_parts))
        meta_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        meta_lbl.setWordWrap(False)
        meta_lbl.setMaximumWidth(156)
        meta_elided = meta_lbl.fontMetrics().elidedText(
            meta_lbl.text(), Qt.TextElideMode.ElideRight, 156
        )
        meta_lbl.setText(meta_elided)
        identity.addWidget(meta_lbl)

        top_row.addLayout(identity, 1)

        badge = make_source_badge(app)
        if badge is not None:
            top_row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)

        vl.addLayout(top_row)

        launch_btn = QPushButton(tr("▶  Launch"))
        launch_btn.setText(launch_btn.text().removeprefix("▶  "))
        launch_btn.setIcon(load_icon("play", C.CRUST, 16))
        launch_btn.setIconSize(QSize(16, 16))
        launch_btn.setStyleSheet(BTN_ACCENT)
        launch_btn.setMinimumWidth(118)
        launch_btn.setToolTip(tr("Launch {app}").format(app=app.full_name))
        launch_btn.clicked.connect(lambda _, a=app: self._launch_app(a))

        more_btn = QPushButton("")
        more_btn.setIcon(load_icon("overflow", C.OVERLAY0, 16))
        more_btn.setIconSize(QSize(16, 16))
        more_btn.setFixedSize(30, 30)
        more_btn.setToolTip(tr("Edit"))
        more_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {C.OVERLAY0};
                border: none;
                border-radius: 15px;
                font-size: 16px;
            }}
            QPushButton:hover {{
                color: {C.TEXT};
                background: {C.SURFACE1};
            }}
            QPushButton::menu-indicator {{
                image: none;
                width: 0px;
            }}
            """
        )

        menu = QMenu(more_btn)
        pin_action = menu.addAction(
            tr("Unpin") if launcher_state.is_pinned(app.name) else tr("Pin")
        )
        pin_action.setIcon(load_icon("pin", C.SUBTEXT1, 16))
        pin_action.triggered.connect(lambda _, a=app: self._on_toggle_pin_app(a))

        edit_action = menu.addAction(tr("Edit"))
        edit_action.triggered.connect(lambda _, a=app: self._on_edit_app(a))

        hide_action = menu.addAction(tr("Show") if app.hidden else tr("Hide"))
        hide_action.setToolTip(tr("Show in menu") if app.hidden else tr("Hide from menu"))
        hide_action.triggered.connect(lambda _, a=app: self._on_toggle_app_hidden(a))

        delete_action = menu.addAction(tr("Delete"))
        delete_action.triggered.connect(lambda _, a=app: self._on_delete_app(a))
        more_btn.setMenu(menu)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(SPACE_S)
        footer.addWidget(launch_btn, 0, Qt.AlignmentFlag.AlignLeft)
        footer.addStretch()
        footer.addWidget(more_btn, 0, Qt.AlignmentFlag.AlignRight)
        vl.addLayout(footer)
        return card

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

    def _filter_apps(self, text: str) -> None:
        q = text.lower()
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
