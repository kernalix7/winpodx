# SPDX-License-Identifier: MIT
"""Self-contained dashboard widgets: a circular ``RingGauge`` and a horizontal
``StatBar``.

Both are pure-Qt custom-painted widgets with no dependency on the main window,
so they can be dropped into any layout (CPU / RAM / disk usage panels, etc.).
Colors and metrics come exclusively from :mod:`winpodx.gui.theme` tokens.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from winpodx.gui.theme import (
    FONT_CAPTION,
    RADIUS_XS,
    C,
)

# Geometry tokens (px). Kept local so the widgets stay self-contained.
_RING_PEN = 8  # arc / track stroke width
_RING_MIN = 120  # minimum square side for the ring
_BAR_HEIGHT = 8  # filled-track height for StatBar
_BAR_MIN_W = 160  # minimum bar width


def _qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    """Build a ``QColor`` from a ``#rrggbb`` theme token and 0..1 alpha."""
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


class RingGauge(QWidget):
    """Circular progress gauge: a colored arc over a faint track, with a big
    center value label and a small caption under it."""

    def __init__(self, caption: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caption = caption
        self._color = color
        self._pct: float | None = None
        self._center_text = "--"
        self.setMinimumSize(_RING_MIN, _RING_MIN)

    def sizeHint(self) -> QSize:
        return QSize(_RING_MIN, _RING_MIN)

    def set_value(self, pct: float | None, center_text: str) -> None:
        """Update the gauge. ``pct`` 0..100 sweeps the arc; ``None`` shows a
        faint full-track 'n/a' look."""
        if pct is not None:
            pct = max(0.0, min(100.0, float(pct)))
        self._pct = pct
        self._center_text = center_text
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ARG002 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        side = min(self.width(), self.height())
        inset = _RING_PEN / 2 + 2
        ox = (self.width() - side) / 2
        oy = (self.height() - side) / 2
        arc_rect = QRectF(ox + inset, oy + inset, side - 2 * inset, side - 2 * inset)

        # Background track ring.
        track_pen = QPen(_qcolor(C.SURFACE1))
        track_pen.setWidthF(_RING_PEN)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(arc_rect, 0, 360 * 16)

        # Value arc: from 90deg (top) clockwise, proportional to pct.
        if self._pct is not None and self._pct > 0:
            gradient = QLinearGradient(arc_rect.topLeft(), arc_rect.bottomRight())
            gradient.setColorAt(0.0, _qcolor(self._color))
            gradient.setColorAt(1.0, _qcolor(self._color, 0.7))
            arc_pen = QPen(gradient, _RING_PEN)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            # Qt angles are 1/16 degree, CCW positive; negate to go clockwise.
            span = int(-self._pct / 100.0 * 360 * 16)
            painter.drawArc(arc_rect, 90 * 16, span)

        # Center value (bold). Size is derived from the ring's actual side so
        # the text always fits the circle at any window size / display scale
        # (a fixed pixel size overflowed the ring under fractional HiDPI).
        value_font = QFont(self.font())
        value_font.setPixelSize(max(13, int(side * 0.17)))
        value_font.setBold(True)
        painter.setFont(value_font)
        painter.setPen(_qcolor(C.TEXT))
        value_rect = QRectF(arc_rect)
        value_rect.setHeight(arc_rect.height() * 0.62)
        value_rect.moveTop(arc_rect.top() + arc_rect.height() * 0.20)
        painter.drawText(value_rect, Qt.AlignmentFlag.AlignCenter, self._center_text)

        # Caption below the value (also size-derived).
        caption_font = QFont(self.font())
        caption_font.setPixelSize(max(9, int(side * 0.095)))
        painter.setFont(caption_font)
        painter.setPen(_qcolor(C.SUBTEXT0))
        caption_rect = QRectF(arc_rect)
        caption_rect.setTop(value_rect.bottom())
        painter.drawText(
            caption_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._caption,
        )
        painter.end()


class StatBar(QWidget):
    """Horizontal usage bar with a label above and 'used / total' text."""

    def __init__(self, caption: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._caption = caption
        self._color = color
        self._pct: float | None = None
        self._detail = "--"
        self.setMinimumWidth(_BAR_MIN_W)
        self.setMinimumHeight(FONT_CAPTION + _BAR_HEIGHT + 14)

    def sizeHint(self) -> QSize:
        return QSize(_BAR_MIN_W, FONT_CAPTION + _BAR_HEIGHT + 14)

    def set_value(self, pct: float | None, detail: str) -> None:
        """Update the bar. ``pct`` 0..100 fills the track; ``None`` leaves it
        empty with the detail text shown."""
        if pct is not None:
            pct = max(0.0, min(100.0, float(pct)))
        self._pct = pct
        self._detail = detail
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ARG002 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        # Top row: caption (left) + detail (right).
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

        # Track (rounded rect) under the labels.
        track_top = label_rect.bottom() + 6
        track_rect = QRectF(0, track_top, w, _BAR_HEIGHT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_qcolor(C.SURFACE1))
        painter.drawRoundedRect(track_rect, RADIUS_XS, RADIUS_XS)

        # Filled portion proportional to pct.
        if self._pct is not None and self._pct > 0:
            fill_w = max(_BAR_HEIGHT, w * self._pct / 100.0)
            fill_rect = QRectF(track_rect)
            fill_rect.setWidth(fill_w)
            gradient = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
            gradient.setColorAt(0.0, _qcolor(self._color, 0.85))
            gradient.setColorAt(1.0, _qcolor(self._color))
            painter.setBrush(gradient)
            painter.drawRoundedRect(fill_rect, RADIUS_XS, RADIUS_XS)
        painter.end()


if __name__ == "__main__":  # pragma: no cover - manual visual check
    import sys

    from PySide6.QtWidgets import QApplication, QVBoxLayout

    from winpodx.gui.theme import C as _C

    app = QApplication(sys.argv)
    root = QWidget()
    root.setStyleSheet(f"background: {_C.BASE};")
    layout = QVBoxLayout(root)

    ring = RingGauge("CPU", _C.BLUE)
    ring.set_value(62.0, "62%")
    layout.addWidget(ring)

    bar = StatBar("Disk C:", _C.GREEN)
    bar.set_value(45.0, "29 / 64 GB")
    layout.addWidget(bar)

    root.resize(320, 280)
    root.show()
    sys.exit(app.exec())
