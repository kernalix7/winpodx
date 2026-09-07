# SPDX-License-Identifier: MIT
"""Windows 11 ToggleSwitch: a painted ``QCheckBox`` (40x20 track, sliding thumb)."""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox

from winpodx.gui import theme

_ANIM_MS = 150
_THUMB = 12
_THUMB_HOVER = 14
_PAD = 4


class ToggleSwitch(QCheckBox):
    """Fluent toggle. Keeps every ``QCheckBox`` signal/state; only the paint differs."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._offset = 1.0 if self.isChecked() else 0.0
        self._hover = False
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(theme.CONTROL_HEIGHT_W11)
        self.toggled.connect(self._animate_to)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate_to(self, checked: bool) -> None:
        self._anim.stop()
        if not self.isVisible():
            self._set_offset(1.0 if checked else 0.0)
            return
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt override
        super().setChecked(checked)
        if self._anim.state() != QPropertyAnimation.State.Running:
            self._set_offset(1.0 if checked else 0.0)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        base = super().sizeHint()
        text_w = self.fontMetrics().horizontalAdvance(self.text()) + 8 if self.text() else 0
        return QSize(theme.TOGGLE_W + text_w, max(base.height(), theme.CONTROL_HEIGHT_W11))

    def _track_rect(self) -> QRectF:
        y = (self.height() - theme.TOGGLE_H) / 2
        x = 0.0 if self.layoutDirection() == Qt.LayoutDirection.LeftToRight else 0.0
        return QRectF(x, y, theme.TOGGLE_W, theme.TOGGLE_H)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track_rect()
        on = self._offset
        accent = QColor(theme.C.BLUE)
        stroke = QColor(theme.C.SURFACE2)
        fill_off = QColor(theme.C.SURFACE0)
        enabled = self.isEnabled()
        if not enabled:
            accent = QColor(theme.C.OVERLAY1)
            stroke = QColor(theme.C.OVERLAY1)

        track_fill = QColor(fill_off)
        track_fill = _mix(track_fill, accent, on)
        pen = QPen(_mix(stroke, accent, on))
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.setBrush(track_fill)
        radius = theme.TOGGLE_H / 2
        p.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        thumb_d = _THUMB_HOVER if (self._hover and enabled) else _THUMB
        travel = theme.TOGGLE_W - 2 * _PAD - thumb_d
        cx = track.left() + _PAD + thumb_d / 2 + travel * on
        cy = track.center().y()
        thumb_color = _mix(QColor(theme.C.SUBTEXT1), QColor(theme.C.CRUST), on)
        if not enabled:
            thumb_color = QColor(theme.C.OVERLAY0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(thumb_color)
        p.drawEllipse(QRectF(cx - thumb_d / 2, cy - thumb_d / 2, thumb_d, thumb_d))

        if self.hasFocus():
            focus = QPen(QColor(theme.C.TEXT))
            focus.setWidthF(1.0)
            p.setPen(focus)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)

        if self.text():
            p.setPen(QColor(theme.C.TEXT))
            p.drawText(
                int(track.right() + 8),
                0,
                self.width() - int(track.right()) - 8,
                self.height(),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                self.text(),
            )
        p.end()

    def hitButton(self, pos) -> bool:  # noqa: N802
        return self.rect().contains(pos)


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )
