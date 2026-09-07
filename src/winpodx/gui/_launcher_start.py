# SPDX-License-Identifier: MIT
"""Windows 11 Start-menu chrome mixed into ``LauncherWindow``."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui import launcher_state, theme
from winpodx.gui._launcher_rows import RecommendedRow, chain_tab_order
from winpodx.gui._launcher_style import (
    bottom_bar_qss,
    pinned_heading_qss,
    recommended_row_qss,
    restyle_launcher,
)
from winpodx.gui.icons import load_icon as load_chrome_icon

_TILE_W = 120
_ROW_H = 40
_AVATAR = 24


class LauncherStartMixin:
    """Pinned grid, Recommended rows, and Start bottom bar for the flyout."""

    def _restyle_launcher(self, _scheme: str = "") -> None:
        """Re-apply Fluent QSS from ``theme.*`` after a scheme rebuild."""
        restyle_launcher(self, avatar=_AVATAR)

    def _mount_start_sections(self, layout: QVBoxLayout) -> None:
        pinned = QWidget()
        pinned.setStyleSheet("background: transparent;")
        pinned_outer = QVBoxLayout(pinned)
        pinned_outer.setContentsMargins(0, 0, 0, 0)
        pinned_outer.setSpacing(theme.SPACE_M)
        header = QWidget()
        header.setStyleSheet("background: transparent;")
        hr = QHBoxLayout(header)
        hr.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(tr("Pinned"))
        heading.setObjectName("pinnedHeading")
        heading.setStyleSheet(pinned_heading_qss())
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
        self._pinned_section = pinned
        self.all_apps_button = all_btn
        layout.addWidget(pinned)

        running = QWidget()
        running.setStyleSheet("background: transparent;")
        running.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        run_outer = QVBoxLayout(running)
        run_outer.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        run_outer.setContentsMargins(0, 0, 0, 0)
        run_outer.setSpacing(theme.SPACE_M)
        rec_heading = QLabel(tr("Recent"))
        rec_heading.setObjectName("recommendedHeading")
        rec_heading.setStyleSheet(pinned_heading_qss())
        run_outer.addWidget(rec_heading)
        self._recommended_list = QVBoxLayout()
        self._recommended_list.setContentsMargins(0, 0, 0, 0)
        self._recommended_list.setSpacing(theme.SPACE_XS)
        run_outer.addLayout(self._recommended_list)
        self._recommended_section = running
        layout.addWidget(running)

    def _mount_bottom_bar(self, layout: QVBoxLayout) -> None:
        bar = QFrame()
        bar.setObjectName("launcherBottomBar")
        bar.setStyleSheet(bottom_bar_qss())
        bar.setFixedHeight(48)
        row = QHBoxLayout(bar)
        row.setContentsMargins(theme.SPACE_S, theme.SPACE_S, theme.SPACE_S, theme.SPACE_S)
        row.setSpacing(theme.SPACE_S)
        icon = QLabel()
        icon.setFixedSize(_AVATAR, _AVATAR)
        icon.setPixmap(load_chrome_icon("home", theme.C.TEXT, _AVATAR).pixmap(_AVATAR, _AVATAR))
        icon.setStyleSheet("background: transparent;")
        self._brand_icon = icon
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        brand = QLabel("WinPodX")
        brand.setStyleSheet(
            f"background: transparent; color: {theme.C.TEXT}; font-size: {theme.FONT_BODY}px;"
        )
        self._brand_label = brand
        row.addWidget(brand, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self._bottom_bar = bar
        layout.addWidget(bar)

    def _set_start_chrome_visible(self, visible: bool) -> None:
        for attr in ("_pinned_section", "_recommended_section", "_bottom_bar"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setVisible(visible)

    def _clear_box(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _populate_start_sections(self) -> None:
        if getattr(self, "_compact_mode", False):
            self._set_start_chrome_visible(False)
            return
        self._set_start_chrome_visible(True)
        self._populate_pinned_grid()
        self._populate_recommended()
        focus_widgets = [
            item.widget()
            for layout in (self._pinned_row, self._recommended_list)
            for index in range(layout.count())
            if (item := layout.itemAt(index)) is not None and item.widget() is not None
        ]
        chain_tab_order(focus_widgets)

    def _apps_by_slug(self) -> dict:
        return {entry.slug: entry for entry in getattr(self, "_apps", []) if entry.slug}

    def _populate_pinned_grid(self) -> None:
        row = getattr(self, "_pinned_row", None)
        if row is None:
            return
        self._clear_box(row)
        by_slug = self._apps_by_slug()
        pinned = [by_slug[slug] for slug in launcher_state.get_pinned() if slug in by_slug]
        wrap = row.parentWidget()
        avail = wrap.width() if wrap is not None and wrap.width() > 0 else self.width()
        cols = max(3, min(6, avail // (_TILE_W + row.horizontalSpacing())))
        for col in range(cols):
            row.setColumnMinimumWidth(col, _TILE_W)
        row.setColumnStretch(cols, 1)
        row_heights: dict[int, int] = {}
        for i, entry in enumerate(pinned):
            tile = self._make_tile(entry)
            tile.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            row_index = i // cols
            row.addWidget(tile, row_index, i % cols)
            row_heights[row_index] = max(row_heights.get(row_index, 0), tile.height())
        rows = (len(pinned) + cols - 1) // cols
        for row_index, height in row_heights.items():
            row.setRowMinimumHeight(row_index, height)
        if wrap is not None:
            grid_height = sum(row_heights.values()) + max(rows - 1, 0) * row.verticalSpacing()
            wrap.setFixedHeight(grid_height)
        row.activate()

    def _recommended_caption(self, slug: str) -> str:
        try:
            from winpodx.core.process import list_active_sessions

            if any(session.app_name == slug for session in list_active_sessions()):
                return tr("Running")
        except Exception:  # noqa: BLE001 — best-effort overlay; recents still render
            pass
        return tr("Recent")

    def _make_recommended_row(self, entry) -> QFrame:
        from winpodx.gui.launcher import load_icon as load_app_icon

        row = RecommendedRow(entry.name, lambda e=entry: self._launch_app(e))
        row.setStyleSheet(recommended_row_qss())
        row.setFixedHeight(_ROW_H)
        h = QHBoxLayout(row)
        h.setContentsMargins(theme.SPACE_S, 0, theme.SPACE_S, 0)
        h.setSpacing(theme.SPACE_S)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(_AVATAR, _AVATAR)
        px = load_app_icon(entry.icon).pixmap(_AVATAR, _AVATAR)
        if not px.isNull():
            icon_lbl.setPixmap(px)
        icon_lbl.setStyleSheet("background: transparent;")
        h.addWidget(icon_lbl)
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        name_lbl = QLabel(entry.name)
        name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        name_lbl.setStyleSheet(
            f"background: transparent; color: {theme.C.TEXT}; font-size: {theme.FONT_BODY}px;"
        )
        names.addWidget(name_lbl)
        cap = QLabel(self._recommended_caption(entry.slug))
        cap.setStyleSheet(
            f"background: transparent; color: {theme.C.SUBTEXT0}; "
            f"font-size: {theme.FONT_CAPTION}px;"
        )
        names.addWidget(cap)
        h.addLayout(names, 1)
        return row

    def _populate_recommended(self) -> None:
        box = getattr(self, "_recommended_list", None)
        if box is None:
            return
        self._clear_box(box)
        by_slug = self._apps_by_slug()
        recents = [by_slug[slug] for slug in launcher_state.get_recent() if slug in by_slug]
        for entry in recents:
            box.addWidget(self._make_recommended_row(entry))
        self._recommended_section.setVisible(bool(recents))
        box.activate()

    def _scroll_to_all_apps(self) -> None:
        area = getattr(self, "_scroll_area", None)
        grid = getattr(self, "_grid_container", None)
        if area is not None and grid is not None:
            area.ensureWidgetVisible(grid)
        apps = self._filtered_apps() if hasattr(self, "_filtered_apps") else []
        if apps and hasattr(self, "_focus_tile"):
            self._focus_tile(0)
