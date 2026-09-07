# SPDX-License-Identifier: MIT
"""Shared keyboard-accessible rows for launcher-style recent app lists."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QFrame, QLabel, QWidget


class RecommendedRow(QFrame):
    """Focusable recent-app row activated by mouse or standard keyboard keys."""

    def __init__(self, accessible_name: str, activate: Callable[[], None]) -> None:
        super().__init__()
        self._activate = activate
        self.setObjectName("recommendedRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(accessible_name)

    def set_running(self, running: bool) -> None:
        """Show or hide the live-session badge on the 24px icon."""
        from winpodx.gui._main_window_library_tiles import attach_running_dot

        icon = next(
            (
                child
                for child in self.findChildren(QLabel)
                if child.objectName() != "runningDot" and child.width() == 24
            ),
            None,
        )
        if icon is None:
            return
        if running:
            attach_running_dot(icon)
            return
        dot = icon.findChild(QLabel, "runningDot")
        if dot is not None:
            dot.hide()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._activate()
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if not event.isAutoRepeat():
                self._activate()
            event.accept()
            return
        super().keyPressEvent(event)


def chain_tab_order(widgets: Sequence[QWidget]) -> None:
    """Place each focusable widget immediately after its predecessor."""
    for current, following in zip(widgets, widgets[1:]):
        QWidget.setTabOrder(current, following)
