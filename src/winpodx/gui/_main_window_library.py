# SPDX-License-Identifier: MIT
"""Library-page mixin for ``WinpodxWindow``.

Holds the methods that build and drive the Apps tab: the toolbar
(search / view toggle / refresh / hidden / add), the category chip row,
the grid / list view populators, individual card/tile builders, and
the visibility / filter / hidden-toggle state. Pulled out of
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

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import AppInfo
from winpodx.gui._widget_helpers import add_shadow, make_app_avatar, make_source_badge
from winpodx.gui.theme import (
    APP_CARD,
    APP_TILE,
    BTN_ACCENT,
    BTN_DANGER,
    BTN_GHOST,
    BTN_PRIMARY,
    FILTER_CHIP,
    SCROLL_AREA,
    SEARCH_BAR,
    VIEW_TOGGLE,
    C,
    avatar_color,
)


class LibraryPageMixin:
    """Builds the Apps page + drives grid/list view + filter state."""

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

        self.btn_grid = QPushButton("▦")
        self.btn_grid.setCheckable(True)
        self.btn_grid.setChecked(True)
        self.btn_grid.setToolTip("Grid view")
        self.btn_grid.clicked.connect(lambda: self._set_view("grid"))
        tgl.addWidget(self.btn_grid)

        self.btn_list = QPushButton("≡")
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

        # Hybrid filter UX — hidden apps (system shims auto-filtered by the
        # noise denylist, plus anything the user manually hid) collapse by
        # default. Click to expand; the count tells the user how much got
        # filtered so they can decide whether to dig in.
        self._show_hidden = False
        self.btn_show_hidden = QPushButton("Hidden")
        self.btn_show_hidden.setCheckable(True)
        self.btn_show_hidden.setStyleSheet(BTN_GHOST)
        self.btn_show_hidden.setToolTip(
            "Show apps filtered by the noise denylist or manually hidden"
        )
        self.btn_show_hidden.clicked.connect(self._on_toggle_hidden)
        toolbar.addWidget(self.btn_show_hidden)
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
        self._refresh_hidden_button()
        self._populate_app_view(self._visible_apps())

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
        add_shadow(card)

        vl = QVBoxLayout(card)
        vl.setContentsMargins(16, 18, 16, 14)
        vl.setSpacing(0)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(0)

        avatar = make_app_avatar(app, size=52, radius=14, font_size=22)
        top_row.addWidget(avatar, alignment=Qt.AlignmentFlag.AlignLeft)
        top_row.addStretch()

        badge = make_source_badge(app)
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

        launch_btn = QPushButton("▶")
        launch_btn.setFixedSize(32, 32)
        launch_btn.setToolTip(f"Launch {app.full_name}")
        launch_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: {C.GREEN};
                color: {C.CRUST};
                border: none;
                border-radius: 16px;
                font-size: 14px;
            }}
            QPushButton:hover {{ background: {C.TEAL}; }}
            """
        )
        launch_btn.clicked.connect(lambda _, a=app: self._launch_app(a))
        bottom.addWidget(launch_btn)
        bottom.addStretch()

        edit_btn = QPushButton("⋯")
        edit_btn.setFixedSize(28, 28)
        edit_btn.setToolTip("Edit")
        edit_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {C.OVERLAY0};
                border: none;
                border-radius: 14px;
                font-size: 16px;
            }}
            QPushButton:hover {{
                color: {C.TEXT};
                background: {C.SURFACE1};
            }}
            """
        )
        edit_btn.clicked.connect(lambda _, a=app: self._on_edit_app(a))
        bottom.addWidget(edit_btn)

        del_btn = QPushButton("✕")
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
        add_shadow(tile, blur=12, y=2, alpha=35)

        layout = QHBoxLayout(tile)
        layout.setContentsMargins(0, 0, 16, 0)
        layout.setSpacing(0)

        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background: {color}; border-radius: 2px; margin: 8px 0 8px 8px;")
        layout.addWidget(stripe)
        layout.addSpacing(14)

        avatar = make_app_avatar(app, size=40, radius=10, font_size=16)
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

        launch_btn = QPushButton("▶  Launch")
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

        del_btn = QPushButton("✕")
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
        prefix = "Showing" if self._show_hidden else "Hidden"
        self.btn_show_hidden.setText(f"{prefix} ({n})")

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
        self._populate_app_view(filtered)
        self.app_count_label.setText(f"{len(filtered)} apps")
