# SPDX-License-Identifier: MIT
"""Horizontal usage bar: caption + detail over a rounded track."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter
from PySide6.QtWidgets import QWidget

from winpodx.core.i18n import tr
from winpodx.gui.theme import FONT_CAPTION, RADIUS_XS, C

_BAR_HEIGHT = 8
_BAR_MIN_W = 160


def _qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


class StatBar(QWidget):
    """Horizontal usage bar with a label above and 'used / total' text."""

    def __init__(
        self,
        caption: str,
        color: str,
        parent: QWidget | None = None,
        *,
        critical_color: str | None = None,
        critical_pct: float | None = None,
    ) -> None:
        super().__init__(parent)
        self._caption = caption
        self._color = color
        self._critical_color = critical_color
        self._critical_pct = critical_pct
        self._pct: float | None = None
        self._detail = "--"
        self.setAccessibleName(caption)
        self.setAccessibleDescription(self._detail)
        self.setMinimumWidth(_BAR_MIN_W)
        self.setMinimumHeight(FONT_CAPTION + _BAR_HEIGHT + 14)

    def sizeHint(self) -> QSize:
        return QSize(_BAR_MIN_W, FONT_CAPTION + _BAR_HEIGHT + 14)

    def set_value(self, pct: float | None, detail: str) -> None:
        """Update the bar. ``pct`` 0..100 fills the track; ``None`` leaves it empty."""
        if pct is not None:
            pct = max(0.0, min(100.0, float(pct)))
        self._pct = pct
        self._detail = detail
        description = f"{detail} — {tr('WARNING')}" if self._is_critical() else detail
        self.setAccessibleDescription(description)
        self.update()

    def _is_critical(self) -> bool:
        return (
            self._pct is not None
            and self._critical_color is not None
            and self._critical_pct is not None
            and self._pct >= self._critical_pct
        )

    def paintEvent(self, event) -> None:  # noqa: ARG002 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        label_font = QFont(self.font())
        label_font.setPixelSize(FONT_CAPTION)
        painter.setFont(label_font)
        label_rect = QRectF(0, 0, w, FONT_CAPTION + 4)

        painter.setPen(_qcolor(C.SUBTEXT1))
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._caption,
        )
        painter.setPen(_qcolor(C.SUBTEXT0))
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            self._detail,
        )

        track_top = label_rect.bottom() + 6
        track_rect = QRectF(0, track_top, w, _BAR_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_qcolor(C.SURFACE1))
        painter.drawRoundedRect(track_rect, RADIUS_XS, RADIUS_XS)

        if self._pct is not None and self._pct > 0:
            fill_w = max(_BAR_HEIGHT, w * self._pct / 100.0)
            fill_rect = QRectF(track_rect)
            fill_rect.setWidth(fill_w)
            fill_color = self._color
            critical_color = self._critical_color
            if self._is_critical() and critical_color is not None:
                fill_color = critical_color

            gradient = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
            gradient.setColorAt(0.0, _qcolor(fill_color, 0.85))
            gradient.setColorAt(1.0, _qcolor(fill_color))
            painter.setBrush(gradient)
            painter.drawRoundedRect(fill_rect, RADIUS_XS, RADIUS_XS)
        painter.end()
