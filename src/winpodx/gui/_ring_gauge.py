# SPDX-License-Identifier: MIT
"""WinUI ``RingGauge`` (and a re-export of ``StatBar``).

Pure-Qt custom-painted widgets with no dependency on the main window.
Colors come exclusively from live :mod:`winpodx.gui.theme` tokens.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from winpodx.core.i18n import tr
from winpodx.gui._stat_bar import StatBar as StatBar
from winpodx.gui.theme import FONT_BODY, FONT_CAPTION, C

_RING_PEN = 6
_RING_DIAMETER = 72
_RING_COMPACT = 56
_CAPTION_GAP = 4
_ANIM_MS = 250


def _qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    """Build a ``QColor`` from a ``#rrggbb`` theme token and 0..1 alpha."""
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


class RingGauge(QWidget):
    """WinUI ring: accent arc on a subtle track, value in the hole, caption below."""

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
        self._center_text = "--"
        self._arc_frac = 0.0
        self._compact = False
        self.setAccessibleName(caption)
        self.setAccessibleDescription(self._center_text)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(self.sizeHint())
        self._anim = QPropertyAnimation(self, b"arc_fraction", self)
        self._anim.setDuration(_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    @property
    def _detail(self) -> str:
        return self._center_text

    def _caption_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(FONT_CAPTION)
        return font

    def _caption_metrics(self) -> QFontMetrics:
        return QFontMetrics(self._caption_font())

    def caption_rect(self) -> QRect:
        """Return the caption band in widget coordinates."""
        diameter = _RING_COMPACT if self._compact else _RING_DIAMETER
        height = self._caption_metrics().height()
        width = max(self.width(), diameter)
        return QRect(0, diameter + _CAPTION_GAP, width, height)

    def sizeHint(self) -> QSize:
        diameter = _RING_COMPACT if self._compact else _RING_DIAMETER
        band = self._caption_metrics().height() + _CAPTION_GAP + 4
        return QSize(diameter, diameter + band)

    def set_compact(self, compact: bool) -> None:
        if self._compact == compact:
            return
        self._compact = compact
        self.setMinimumSize(self.sizeHint())
        self.updateGeometry()
        self.update()

    def restyle(self) -> None:
        self.update()

    def _is_critical(self) -> bool:
        return (
            self._pct is not None
            and self._critical_color is not None
            and self._critical_pct is not None
            and self._pct >= self._critical_pct
        )

    def _get_arc_fraction(self) -> float:
        return self._arc_frac

    def _set_arc_fraction(self, value: float) -> None:
        self._arc_frac = float(value)
        self.update()

    arc_fraction = Property(float, _get_arc_fraction, _set_arc_fraction)

    def set_value(self, pct: float | None, center_text: str) -> None:
        """Update the gauge. ``pct`` 0..100 sweeps the arc; ``None`` is empty + em dash."""
        if pct is not None:
            pct = max(0.0, min(100.0, float(pct)))
        self._pct = pct
        self._center_text = center_text
        description = f"{center_text} — {tr('WARNING')}" if self._is_critical() else center_text
        self.setAccessibleDescription(description)
        self.setToolTip("" if self.center_label() == center_text else center_text)
        target = 0.0 if pct is None else pct / 100.0
        self._anim.stop()
        if not self.isVisible():
            self._set_arc_fraction(target)
            return
        self._anim.setStartValue(self._arc_frac)
        self._anim.setEndValue(target)
        self._anim.start()

    def _value_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(FONT_BODY)
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def center_label(self) -> str:
        """Text drawn inside the ring: the value if it fits, else the bare percent."""
        if self._pct is None:
            return "—"
        diameter = _RING_COMPACT if self._compact else _RING_DIAMETER
        inner = diameter - 2 * _RING_PEN - 4
        if QFontMetrics(self._value_font()).horizontalAdvance(self._center_text) <= inner:
            return self._center_text
        return f"{self._pct:.0f}%"

    def paintEvent(self, event) -> None:  # noqa: ARG002 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        diameter = _RING_COMPACT if self._compact else _RING_DIAMETER
        ox = (self.width() - diameter) / 2
        inset = _RING_PEN / 2
        arc_rect = QRectF(ox + inset, inset, diameter - _RING_PEN, diameter - _RING_PEN)

        track_pen = QPen(_qcolor(C.SURFACE1))
        track_pen.setWidthF(_RING_PEN)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(arc_rect, 0, 360 * 16)

        if self._arc_frac > 0:
            arc_hex = C.RED if self._is_critical() else C.BLUE
            arc_pen = QPen(_qcolor(arc_hex), _RING_PEN)
            arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(arc_pen)
            span = int(-self._arc_frac * 360 * 16)
            painter.drawArc(arc_rect, 90 * 16, span)

        unavailable = self._pct is None
        painter.setFont(self._value_font())
        painter.setPen(_qcolor(C.SUBTEXT0 if unavailable else C.TEXT))
        ring_box = QRectF(ox, 0, diameter, diameter)
        painter.drawText(ring_box, Qt.AlignmentFlag.AlignCenter, self.center_label())

        caption_font = self._caption_font()
        painter.setFont(caption_font)
        painter.setPen(_qcolor(C.SUBTEXT1))
        caption_rect = QRectF(self.caption_rect())
        painter.drawText(
            caption_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._caption,
        )
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
