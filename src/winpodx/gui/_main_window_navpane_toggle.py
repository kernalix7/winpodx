# SPDX-License-Identifier: MIT
"""NavigationView pane toggle (hamburger) + compact-mode profile chrome."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QPushButton

from winpodx.gui import theme
from winpodx.gui.icons import load_icon

AVATAR_EXPANDED = 64
AVATAR_COMPACT = 32
TOGGLE_ICON = 16


class NavToggleMixin:
    """Pane open/close button and the avatar resize that keeps the rail intact."""

    _nav_user_compact: bool | None = None

    def _build_nav_toggle(self) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("navToggle")
        btn.setToolTip("WinPodX")
        btn.setAccessibleName("WinPodX")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        btn.setFixedSize(theme.NAV_PANE_COMPACT - 2 * theme.SPACE_S, theme.NAV_ITEM_HEIGHT)
        btn.setIconSize(QSize(TOGGLE_ICON, TOGGLE_ICON))
        btn.clicked.connect(self._on_nav_toggle)
        self._nav_toggle_btn = btn
        return btn

    def _on_nav_toggle(self) -> None:
        compact = not bool(getattr(self, "_nav_is_compact", False))
        self._nav_user_compact = compact
        self._set_nav_compact_mode(compact)
        after = getattr(self, "_on_nav_pane_toggled", None) or getattr(self, "_reflow_pages", None)
        if callable(after):
            QTimer.singleShot(0, after)

    def _nav_wants_compact(self, window_width: int, breakpoint: int) -> bool:
        auto = window_width < breakpoint
        if getattr(self, "_nav_auto_compact", None) is not auto:
            # Crossing the breakpoint changes display mode (WinUI adaptive
            # trigger); the user's pin only lives inside one mode, otherwise a
            # pane pinned open at 1300px would come back as a stuck overlay.
            self._nav_auto_compact = auto
            self._nav_user_compact = None
        user = getattr(self, "_nav_user_compact", None)
        return auto if user is None else user

    def _sync_nav_compact_chrome(self, compact: bool) -> None:
        avatar = getattr(self, "_profile_avatar", None)
        if avatar is not None:
            size = AVATAR_COMPACT if compact else AVATAR_EXPANDED
            if avatar.width() != size:
                from winpodx.gui._main_window_navpane import _app_icon_pixmap

                avatar.setFixedSize(size, size)
                pixmap = _app_icon_pixmap(size)
                if pixmap is not None:
                    avatar.setPixmap(pixmap)
        logo = getattr(self, "_profile_button", None)
        row = logo.layout() if logo is not None else None
        if isinstance(row, QHBoxLayout):
            pad = 0 if compact else theme.SPACE_S
            row.setContentsMargins(pad, theme.SPACE_M, pad, theme.SPACE_M)
            row.setAlignment(
                Qt.AlignmentFlag.AlignHCenter if compact else Qt.AlignmentFlag.AlignLeft
            )
        if logo is not None:
            logo.setMinimumHeight(
                (AVATAR_COMPACT if compact else AVATAR_EXPANDED) + 2 * theme.SPACE_M
            )

    def _restyle_nav_toggle(self) -> None:
        btn = getattr(self, "_nav_toggle_btn", None)
        if btn is None:
            return
        btn.setIcon(load_icon("menu", theme.C.TEXT, TOGGLE_ICON))
        btn.setStyleSheet(
            f"QPushButton#navToggle {{ background: transparent; border: none; "
            f"border-radius: {theme.RADIUS_M}px; }}"
            f"QPushButton#navToggle:hover {{ background: {theme.C.SURFACE0}; }}"
            f"QPushButton#navToggle:pressed {{ background: {theme.C.SURFACE1}; }}"
            f"QPushButton#navToggle:focus {{ border: 2px solid {theme.C.TEXT}; }}"
        )
