# SPDX-License-Identifier: MIT
"""Start-menu tiles and Applications list rows for the library page."""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import AppInfo
from winpodx.core.i18n import tr
from winpodx.gui import theme
from winpodx.gui._widget_helpers import ElidingLabel, make_app_avatar, make_source_badge
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    APP_TILE,
    BTN_DANGER,
    BTN_SECONDARY,
    CONTROL_HEIGHT_W11,
    FONT_BODY,
    FONT_CAPTION,
    SPACE_S,
    C,
)

_DOT_PX = 8
_RING_PX = 2
_BADGE_PX = _DOT_PX + 2 * _RING_PX
_LIST_ROW_H = 48
_LIST_ICON = 24


class _ListTileHost(Protocol):
    """Host surface consumed by :func:`make_library_list_tile`."""

    _select_mode: bool
    _selected_names: set[str]

    def _on_tile_checked(self, name: str, checked: bool) -> None: ...
    def _launch_app(self, app: AppInfo) -> None: ...
    def _on_edit_app(self, app: AppInfo) -> None: ...
    def _on_reset_app(self, app: AppInfo) -> None: ...
    def _on_toggle_app_hidden(self, app: AppInfo) -> None: ...
    def _on_delete_app(self, app: AppInfo) -> None: ...


def attach_running_dot(anchor: QWidget) -> QLabel:
    """8px ``C.GREEN`` badge with a 2px ``C.SURFACE0`` ring at the icon corner."""
    existing = anchor.findChild(QLabel, "runningDot")
    if existing is not None:
        existing.setStyleSheet(
            f"background: {C.GREEN}; border: {_RING_PX}px solid {C.SURFACE0}; "
            f"border-radius: {_BADGE_PX // 2}px;"
        )
        existing.move(anchor.width() - _BADGE_PX, anchor.height() - _BADGE_PX)
        existing.show()
        return existing
    dot = QLabel(anchor)
    dot.setObjectName("runningDot")
    dot.setFixedSize(_BADGE_PX, _BADGE_PX)
    dot.setStyleSheet(
        f"background: {C.GREEN}; border: {_RING_PX}px solid {C.SURFACE0}; "
        f"border-radius: {_BADGE_PX // 2}px;"
    )
    dot.move(anchor.width() - _BADGE_PX, anchor.height() - _BADGE_PX)
    dot.show()
    return dot


class _AppTile(QFrame):
    """Windows-Start-style launcher tile: icon above name, click launches."""

    def __init__(
        self,
        app: AppInfo,
        *,
        on_launch: Callable[[AppInfo], None],
        on_menu: Callable[[AppInfo, QPoint], None],
    ) -> None:
        super().__init__()
        self._app = app
        self._on_launch = on_launch
        self._on_menu = on_menu
        self._running_dot: QLabel | None = None
        self.setObjectName("appTileBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(app.full_name)
        self.setToolTip(html.escape(app.full_name))
        self.setStyleSheet(theme.START_TILE)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
        v.setSpacing(SPACE_S)
        v.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self._avatar = make_app_avatar(app, size=32, radius=8, font_size=14)
        self._avatar.setObjectName("appAvatar")
        v.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignHCenter)

        if app.full_name.isascii() and not any(ch.isspace() for ch in app.full_name):
            name = ElidingLabel(app.full_name)
            name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            name.setStyleSheet(
                f"background: transparent; color: {C.TEXT}; font-size: {FONT_CAPTION}px;"
            )
            name.setFixedWidth(104)
            name.set_full_text(app.full_name)
            name.setFixedHeight(max(name.sizeHint().height(), name.fontMetrics().lineSpacing()))
        else:
            name = QLabel(app.full_name)
            name.setTextFormat(Qt.TextFormat.PlainText)
            name.setWordWrap(True)
            name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            name.setStyleSheet(
                f"background: transparent; color: {C.TEXT}; font-size: {FONT_CAPTION}px;"
            )
            name.setFixedWidth(104)
            name.ensurePolished()
            wrap_flags = name.alignment() | Qt.TextFlag.TextWordWrap
            required = (
                name.fontMetrics().boundingRect(0, 0, 104, 0, wrap_flags, app.full_name).height()
            )
            name.setFixedHeight(max(name.fontMetrics().lineSpacing(), required))
        name.setObjectName("appTileName")
        v.addWidget(name, 0, Qt.AlignmentFlag.AlignHCenter)
        tile_h = SPACE_S + 32 + SPACE_S + name.height() + SPACE_S
        self.setFixedSize(120, max(72, tile_h))

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self._on_menu(self._app, self.mapToGlobal(pos))
        )

    def set_running(self, running: bool) -> None:
        """Show or hide the live-session badge on the icon's bottom-right."""
        if running:
            self._running_dot = attach_running_dot(self._avatar)
            return
        if self._running_dot is not None:
            self._running_dot.hide()

    def _set_pressed(self, pressed: bool) -> None:
        self.setProperty("pressed", pressed)
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._set_pressed(True)
            self._on_launch(self._app)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._set_pressed(False)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if not event.isAutoRepeat():
                self._on_launch(self._app)
            event.accept()
            return
        super().keyPressEvent(event)


def make_library_list_tile(host: _ListTileHost, app: AppInfo) -> QFrame:
    """48px Applications list row: 24px icon, name + category, 32px Launch."""
    tile = QFrame()
    tile.setObjectName("appTile")
    tile.setStyleSheet(APP_TILE)
    tile.setFixedHeight(_LIST_ROW_H)

    layout = QHBoxLayout(tile)
    layout.setContentsMargins(SPACE_S, 0, SPACE_S, 0)
    layout.setSpacing(SPACE_S)
    layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

    if getattr(host, "_select_mode", False):
        cb = QCheckBox()
        cb.setChecked(app.name in host._selected_names)
        cb.setStyleSheet(theme.CHECKBOX + "QCheckBox { margin-left: 4px; }")
        cb.toggled.connect(lambda checked, n=app.name: host._on_tile_checked(n, checked))
        layout.addWidget(cb)

    avatar = make_app_avatar(app, size=_LIST_ICON, radius=4, font_size=10)
    layout.addWidget(avatar)

    info = QVBoxLayout()
    info.setContentsMargins(0, 0, 0, 0)
    info.setSpacing(0)
    name_lbl = QLabel(app.full_name)
    name_lbl.setTextFormat(Qt.TextFormat.PlainText)
    name_lbl.setStyleSheet(f"background: transparent; color: {C.TEXT}; font-size: {FONT_BODY}px;")
    name_row = QHBoxLayout()
    name_row.setContentsMargins(0, 0, 0, 0)
    name_row.setSpacing(SPACE_S)
    name_row.addWidget(name_lbl)
    badge = make_source_badge(app)
    if badge is not None:
        name_row.addWidget(badge)
    name_row.addStretch()
    info.addLayout(name_row)
    caption = ", ".join(app.categories[:2]) if app.categories else app.name
    meta_lbl = QLabel(caption)
    meta_lbl.setStyleSheet(
        f"background: transparent; color: {C.SUBTEXT1}; font-size: {FONT_CAPTION}px;"
    )
    info.addWidget(meta_lbl)
    layout.addLayout(info, 1)

    launch_btn = QPushButton(tr("▶  Launch"))
    launch_btn.setObjectName("launchBtn")
    launch_btn.setText(launch_btn.text().removeprefix("▶  "))
    launch_btn.setIcon(load_icon("play", C.TEXT, 16))
    launch_btn.setIconSize(QSize(16, 16))
    launch_btn.setStyleSheet(BTN_SECONDARY)
    launch_btn.setFixedHeight(CONTROL_HEIGHT_W11)
    launch_btn.clicked.connect(lambda: host._launch_app(app))
    layout.addWidget(launch_btn)

    edit_btn = QPushButton(tr("Edit"))
    edit_btn.setStyleSheet(BTN_SECONDARY)
    edit_btn.setFixedHeight(CONTROL_HEIGHT_W11)
    edit_btn.clicked.connect(lambda: host._on_edit_app(app))
    layout.addWidget(edit_btn)

    if getattr(app, "source", "user") == "user":
        from winpodx.core.app import discovered_profile_exists

        if discovered_profile_exists(app.name):
            reset_btn = QPushButton(tr("Reset"))
            reset_btn.setStyleSheet(BTN_SECONDARY)
            reset_btn.setFixedHeight(CONTROL_HEIGHT_W11)
            reset_btn.setToolTip(tr("Restore the auto-detected profile + icon"))
            reset_btn.clicked.connect(lambda: host._on_reset_app(app))
            layout.addWidget(reset_btn)

    hide_btn = QPushButton(tr("Show") if app.hidden else tr("Hide"))
    hide_btn.setStyleSheet(BTN_SECONDARY)
    hide_btn.setFixedHeight(CONTROL_HEIGHT_W11)
    hide_btn.clicked.connect(lambda: host._on_toggle_app_hidden(app))
    layout.addWidget(hide_btn)

    del_btn = QPushButton("")
    del_btn.setIcon(load_icon("close", C.PEACH, 16))
    del_btn.setIconSize(QSize(16, 16))
    del_btn.setFixedSize(CONTROL_HEIGHT_W11, CONTROL_HEIGHT_W11)
    del_btn.setStyleSheet(BTN_DANGER)
    del_btn.clicked.connect(lambda: host._on_delete_app(app))
    layout.addWidget(del_btn)
    return tile
