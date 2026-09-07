# SPDX-License-Identifier: MIT
"""Pod-state colour table, nav A/R chips, and solid tint compositing."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from winpodx.gui import theme

_PILL_COLOR_ATTR = {
    "running": "GREEN",
    "stopped": "SUBTEXT0",
    "paused": "YELLOW",
    "checking": "YELLOW",
    "starting": "YELLOW",
    "unresponsive": "RED",
    "error": "RED",
}
_COLOR_RE = re.compile(r"color:\s*(#[0-9A-Fa-f]{6})", re.IGNORECASE)
PILL_H = 22
CHIP = 16
DOT = 6


def pod_state_color(state: str) -> str:
    """Return the live ``theme.C`` hex for a pod-state word."""
    return getattr(theme.C, _PILL_COLOR_ATTR.get(state, "SUBTEXT0"))


def mix_over(fg: str, bg: str, alpha: float) -> str:
    """Composite ``fg`` at ``alpha`` over ``bg`` and return a solid ``#rrggbb``."""
    value_fg = fg.lstrip("#")
    value_bg = bg.lstrip("#")
    fr, fg_, fb = (int(value_fg[i : i + 2], 16) for i in (0, 2, 4))
    br, bg_, bb = (int(value_bg[i : i + 2], 16) for i in (0, 2, 4))
    red = round(fr * alpha + br * (1.0 - alpha))
    green = round(fg_ * alpha + bg_ * (1.0 - alpha))
    blue = round(fb * alpha + bb * (1.0 - alpha))
    return f"#{red:02x}{green:02x}{blue:02x}"


class MiniChip(QLabel):
    """16×16 A/R chip; keeps the colour the pod mixin injects via stylesheet."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFixedSize(CHIP, CHIP)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chip_color = theme.C.SUBTEXT0
        self._apply_chip()

    def setStyleSheet(self, css: str) -> None:  # noqa: N802
        match = _COLOR_RE.search(css)
        if match:
            self._chip_color = match.group(1)
        self._apply_chip()

    def _apply_chip(self) -> None:
        color = self._chip_color
        super().setStyleSheet(
            f"background: {theme.rgba(color, 0.16)}; color: {color}; "
            f"font-size: 12px; font-weight: 600; border-radius: {theme.RADIUS_S}px;"
        )
