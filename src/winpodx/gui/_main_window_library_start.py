# SPDX-License-Identifier: MIT
"""Start-menu chrome for the Applications page (Pinned / Recommended / All apps)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import AppInfo
from winpodx.core.i18n import tr
from winpodx.gui import launcher_state, theme
from winpodx.gui._launcher_rows import RecommendedRow, chain_tab_order
from winpodx.gui._widget_helpers import make_app_avatar, make_section_label
from winpodx.gui.icons import load_icon


class LibraryStartMixin:
    """Win11 Start anatomy mixed into ``LibraryPageMixin``."""

    def _style_library_search(self, box: QLineEdit) -> None:
        box.setObjectName("navSearch")
        box.setStyleSheet(
            theme.NAV_SEARCH + f"\nQLineEdit#navSearch {{ background: {theme.C.SURFACE0}; }}"
        )
        box.setMinimumHeight(theme.CONTROL_HEIGHT_W11)
        box.setMaximumWidth(600)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _pinned_heading_qss(self) -> str:
        return (
            f"background: transparent; color: {theme.C.TEXT}; "
            f"font-size: {theme.FONT_BODY}px; font-weight: 600;"
        )

    def _recommended_row_qss(self) -> str:
        return (
            f"QFrame#recommendedRow {{ background: transparent; border: none; "
            f"border-radius: {theme.RADIUS_M}px; }}"
            f"QFrame#recommendedRow:hover {{ background: {theme.C.SURFACE1}; }}"
            f"QFrame#recommendedRow:focus {{ border: {theme.FOCUS_RING}; }}"
        )

    def _mount_start_sections(self, launcher_layout: QVBoxLayout) -> None:
        pinned = QWidget()
        pinned.setStyleSheet("background: transparent;")
        pinned_outer = QVBoxLayout(pinned)
        pinned_outer.setContentsMargins(0, 0, 0, 0)
        pinned_outer.setSpacing(theme.SPACE_S)
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        hr = QHBoxLayout(header)
        hr.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(tr("Pinned"))
        heading.setObjectName("pinnedHeading")
        heading.setStyleSheet(self._pinned_heading_qss())
        hr.addWidget(heading)
        hr.addStretch(1)
        all_btn = QPushButton(f"{tr('All apps')} ›")
        all_btn.setObjectName("allAppsButton")
        all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        all_btn.setStyleSheet(theme.HYPERLINK_BTN)
        all_btn.clicked.connect(self._scroll_to_all_apps)
        hr.addWidget(all_btn)
        pinned_outer.addWidget(header)
        grid_wrap = QWidget()
        grid_wrap.setStyleSheet("background: transparent;")
        grid_wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._pinned_row = QGridLayout(grid_wrap)
        self._pinned_row.setContentsMargins(0, 0, 0, 0)
        self._pinned_row.setHorizontalSpacing(theme.SPACE_S)
        self._pinned_row.setVerticalSpacing(theme.SPACE_S)
        pinned_outer.addWidget(grid_wrap)
        pinned.setVisible(False)
        self._pinned_section = pinned
        self.all_apps_button = all_btn
        launcher_layout.addWidget(pinned)

        running = QWidget()
        running.setStyleSheet("background: transparent;")
        running.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        run_outer = QVBoxLayout(running)
        run_outer.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        run_outer.setContentsMargins(0, 0, 0, 0)
        run_outer.setSpacing(theme.SPACE_S)
        rec_heading = QLabel(tr("Recent"))
        rec_heading.setObjectName("recommendedHeading")
        rec_heading.setStyleSheet(self._pinned_heading_qss())
        run_outer.addWidget(rec_heading)
        self._running_row = QVBoxLayout()
        self._running_row.setContentsMargins(0, 0, 0, 0)
        self._running_row.setSpacing(theme.SPACE_XS)
        run_outer.addLayout(self._running_row)
        running.setVisible(False)
        self._running_section = running
        launcher_layout.addWidget(running)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {theme.C.OVERLAY0}; background: {theme.C.OVERLAY0};")
        divider.setFixedHeight(1)
        launcher_layout.addWidget(divider)

        all_apps_header = QWidget()
        all_apps_header.setStyleSheet("background: transparent;")
        all_apps_layout = QVBoxLayout(all_apps_header)
        all_apps_layout.setContentsMargins(0, 0, 0, 0)
        all_apps_layout.setSpacing(theme.SPACE_S)
        all_apps_layout.addWidget(make_section_label(tr("Applications")))
        category_wrap = QWidget()
        self._category_row = QHBoxLayout(category_wrap)
        self._category_row.setContentsMargins(0, 0, 0, 0)
        self._category_row.setSpacing(theme.SPACE_S)
        self._category_btns: list[QPushButton] = []
        all_apps_layout.addWidget(category_wrap)
        self._all_apps_header = all_apps_header
        launcher_layout.addWidget(all_apps_header)

    def _scroll_to_all_apps(self) -> None:
        header = getattr(self, "_all_apps_header", None)
        if header is None:
            return
        header.show()
        parent = header.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if isinstance(parent, QScrollArea):
            parent.ensureWidgetVisible(header)

    def _populate_pinned_grid(self, apps: list[AppInfo]) -> None:
        row = getattr(self, "_pinned_row", None)
        if row is None:
            return
        self._clear_layout(row)
        section = getattr(self, "_pinned_section", None)
        if section is not None:
            section.setVisible(bool(apps))
        cols = self._grid_cols()
        tile_w = 104 + 2 * theme.SPACE_S
        for col in range(min(len(apps), cols)):
            row.setColumnMinimumWidth(col, tile_w)
        row_h = 0
        for i, app in enumerate(apps):
            card = self._make_app_card(app)
            row.addWidget(card, i // cols, i % cols)
            row_h = max(row_h, card.height() or card.sizeHint().height())
        n_rows = (len(apps) + cols - 1) // cols if apps else 0
        for r in range(n_rows):
            row.setRowMinimumHeight(r, row_h)
        row.setColumnStretch(cols, 1)
        wrap = row.parentWidget()
        if wrap is not None and n_rows:
            wrap.setFixedHeight(n_rows * row_h + row.verticalSpacing() * (n_rows - 1))

    def _make_recommended_row(self, subtitle: str, app: AppInfo | None, stem: str) -> QFrame:
        title = app.full_name if app is not None else self._running_display_name(stem)
        activate = (
            (lambda a=app: self._launch_app(a))
            if app is not None
            else (lambda n=stem: self._focus_session(n))
        )
        row = RecommendedRow(title, activate)
        row.setStyleSheet(self._recommended_row_qss())
        row.setFixedHeight(40)
        h = QHBoxLayout(row)
        h.setContentsMargins(theme.SPACE_S, 0, theme.SPACE_S, 0)
        h.setSpacing(theme.SPACE_S)
        if app is not None:
            h.addWidget(make_app_avatar(app, size=24, radius=4, font_size=10))
        else:
            ic = QLabel()
            ic.setFixedSize(24, 24)
            ic.setPixmap(load_icon("grid", theme.C.SUBTEXT1, 24).pixmap(24, 24))
            ic.setStyleSheet("background: transparent;")
            h.addWidget(ic)
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        name_lbl = QLabel(title)
        name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        name_lbl.setStyleSheet(
            f"background: transparent; color: {theme.C.TEXT}; font-size: {theme.FONT_BODY}px;"
        )
        names.addWidget(name_lbl)
        cap = QLabel(subtitle)
        cap.setStyleSheet(
            f"background: transparent; color: {theme.C.SUBTEXT0}; "
            f"font-size: {theme.FONT_CAPTION}px;"
        )
        names.addWidget(cap)
        h.addLayout(names, 1)
        if subtitle == tr("Running"):
            row.set_running(True)
        return row

    def _refresh_running_strip(self) -> None:
        if not hasattr(self, "_running_row"):
            return
        self._clear_layout(self._running_row)
        from winpodx.gui import _main_window_library as lib

        try:
            sessions = lib.list_active_sessions()
        except Exception:  # noqa: BLE001 -- never break the home on enumeration
            sessions = []
        running_names = {s.app_name for s in sessions}
        self._running_names = running_names
        by_name = {a.name: a for a in self.apps}
        for s in sessions:
            self._running_row.addWidget(
                self._make_recommended_row(tr("Running"), by_name.get(s.app_name), s.app_name)
            )
        recent = [n for n in launcher_state.get_recent() if n not in running_names]
        for app in self._apps_by_names(recent, list(self.apps)):
            self._running_row.addWidget(self._make_recommended_row(tr("Recent"), app, app.name))
        focus_widgets = [
            item.widget()
            for layout in (self._pinned_row, self._running_row)
            for index in range(layout.count())
            if (item := layout.itemAt(index)) is not None and item.widget() is not None
        ]
        chain_tab_order(focus_widgets)
        self._running_section.setVisible(bool(sessions) or bool(recent))
        self._running_row.activate()
        self._running_section.updateGeometry()
        parent_layout = self._running_section.parentWidget()
        if parent_layout is not None and parent_layout.layout() is not None:
            parent_layout.layout().activate()

    def _restyle_library(self) -> None:
        box = getattr(self, "search_box", None)
        if box is not None:
            self._style_library_search(box)
        for name in ("refresh_btn", "btn_select", "btn_show_hidden", "btn_deleted"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setStyleSheet(theme.BTN_SECONDARY)
                btn.setFixedHeight(theme.CONTROL_HEIGHT_W11)
        add_btn = getattr(self, "add_app_btn", None)
        if add_btn is not None:
            add_btn.setStyleSheet(theme.BTN_PRIMARY)
            add_btn.setFixedHeight(theme.CONTROL_HEIGHT_W11)
        wrap = getattr(self, "_view_toggle_wrap", None)
        if wrap is not None:
            wrap.setStyleSheet(theme.VIEW_TOGGLE)
            wrap.setFixedHeight(theme.CONTROL_HEIGHT_W11)
        count = getattr(self, "app_count_label", None)
        if count is not None:
            count.setStyleSheet(
                f"background: transparent; color: {theme.C.OVERLAY0}; "
                f"font-size: {theme.FONT_CAPTION}px;"
            )
        container = getattr(self, "app_list_container", None)
        if container is not None:
            for key in ("pinnedHeading", "recommendedHeading"):
                lbl = container.findChild(QLabel, key)
                if lbl is not None:
                    lbl.setStyleSheet(self._pinned_heading_qss())
            for tile in container.findChildren(QFrame, "appTileBtn"):
                tile.setStyleSheet(theme.START_TILE)
                for lbl in tile.findChildren(QLabel, "appTileName"):
                    lbl.setStyleSheet(
                        f"background: transparent; color: {theme.C.TEXT}; "
                        f"font-size: {theme.FONT_CAPTION}px;"
                    )
            for rec_row in container.findChildren(QFrame, "recommendedRow"):
                rec_row.setStyleSheet(self._recommended_row_qss())
            for tile in container.findChildren(QFrame, "appTile"):
                tile.setStyleSheet(theme.APP_TILE)
        all_btn = getattr(self, "all_apps_button", None)
        if all_btn is not None:
            all_btn.setStyleSheet(theme.HYPERLINK_BTN)
        from winpodx.gui._main_window_library_chips import category_chip_qss

        for chip in getattr(self, "_category_btns", []):
            chip.setStyleSheet(category_chip_qss())
        progress = getattr(self, "refresh_progress", None)
        if progress is not None:
            progress.setStyleSheet(theme.PROGRESS)
