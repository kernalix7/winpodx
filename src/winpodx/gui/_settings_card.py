# SPDX-License-Identifier: MIT
"""Windows 11 SettingsCard / ToggleSwitch / section-group primitives."""

from __future__ import annotations

import html

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.gui import theme
from winpodx.gui.icons import load_icon

_DESC_MIN_WIDTH = 240


class _ElidedTitle(QLabel):
    """Single-line title that elides when squeezed; ``text()`` stays the full string."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt signature
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        rect = self.contentsRect()
        text = self.text()
        if self.fontMetrics().horizontalAdvance(text) <= rect.width():
            self.setToolTip("")
            super().paintEvent(event)
            return
        self.setToolTip(html.escape(text))
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        elided = self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter), elided)


def _card_qss(object_name: str) -> str:
    return theme.SETTINGS_CARD.replace("QFrame#settingsCard", f"QFrame#{object_name}")


def make_toggle_switch(checked: bool = False) -> QCheckBox:
    """Build a Windows 11 ToggleSwitch (painted 40x20 track with a sliding thumb)."""
    from winpodx.gui._toggle_switch import ToggleSwitch

    toggle = ToggleSwitch()
    toggle.setChecked(checked)
    return toggle


def make_settings_card(
    icon: str,
    title: str,
    description: str = "",
    *,
    action: QWidget | None = None,
    chevron: bool = False,
    object_name: str = "settingsCard",
    compact: bool = False,
) -> QFrame:
    """Build a Windows 11 SettingsCard: icon, title/description, optional action."""
    card = QFrame()
    card.setObjectName(object_name)
    card.setStyleSheet(_card_qss(object_name))
    card.setMinimumHeight(48 if compact else theme.SETTINGS_ROW_MIN)
    card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    outer = QBoxLayout(QBoxLayout.Direction.LeftToRight, card)
    if compact:
        outer.setContentsMargins(0, 0, 0, 0)
    else:
        outer.setContentsMargins(theme.SPACE_L, theme.SPACE_L, theme.SPACE_L, theme.SPACE_L)
    outer.setSpacing(theme.SPACE_M)

    inner = QWidget()
    row = QHBoxLayout(inner)
    if compact:
        row.setContentsMargins(theme.SPACE_S, theme.SPACE_S, theme.SPACE_S, theme.SPACE_S)
    else:
        row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.SPACE_M)

    icon_px = 24 if compact else 20
    if icon:
        icon_lbl = QLabel()
        icon_lbl.setObjectName("settingsCardIcon")
        icon_lbl.setFixedSize(icon_px, icon_px)
        icon_lbl.setPixmap(load_icon(icon, theme.C.SUBTEXT1, icon_px).pixmap(icon_px, icon_px))
        icon_lbl.setProperty("iconName", icon)
        icon_lbl.setProperty("iconColorAttr", "SUBTEXT1")
        icon_lbl.setProperty("iconSize", icon_px)
        row.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

    # The copy column is a QWidget (not a bare layout) so the description's fixed
    # width never becomes the column's *maximum* width — otherwise the leftover
    # row width would be handed to the action widget and stretch it.
    copy_host = QWidget()
    copy_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    copy = QVBoxLayout(copy_host)
    copy.setContentsMargins(0, 0, 0, 0)
    copy.setSpacing(2)
    title_lbl = _ElidedTitle(title)
    title_lbl.setTextFormat(Qt.TextFormat.PlainText)
    title_lbl.setStyleSheet(
        f"background: transparent; color: {theme.C.TEXT}; "
        f"font-size: {theme.FONT_BODY}px; font-weight: 400;"
    )
    desc_lbl = QLabel(description)
    desc_lbl.setTextFormat(Qt.TextFormat.PlainText)
    desc_lbl.setWordWrap(True)
    desc_lbl.setMinimumWidth(_DESC_MIN_WIDTH)
    desc_lbl.setStyleSheet(
        f"background: transparent; color: {theme.C.SUBTEXT1}; "
        f"font-size: {theme.FONT_CAPTION}px; font-weight: 400;"
    )
    desc_lbl.setVisible(bool(description))
    copy.addWidget(title_lbl)
    copy.addWidget(desc_lbl)
    row.addWidget(copy_host, 1)

    if action is not None:
        if not action.accessibleName():
            action.setAccessibleName(title)
        row.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)

    if chevron:
        chevron_lbl = QLabel()
        chevron_lbl.setFixedSize(16, 16)
        chevron_lbl.setPixmap(load_icon("chevron-right", theme.C.SUBTEXT1, 16).pixmap(16, 16))
        row.addWidget(chevron_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        card.chevron_widget = chevron_lbl

    outer.addWidget(inner, 1)

    card.title_label = title_lbl
    card.desc_label = desc_lbl
    card.action_widget = action
    card.row_layout = row
    return card


def make_settings_group(title: str = "") -> tuple[QWidget, QVBoxLayout]:
    """Build a Windows 11 settings section: optional Body Strong label + 4px stack."""
    holder, _outer, stack = make_settings_group_parts()
    if title:
        label = QLabel(title)
        label.setStyleSheet(
            f"background: transparent; color: {theme.C.TEXT}; "
            f"font-size: {theme.FONT_SUBHEAD}px; font-weight: 600;"
        )
        _outer.insertWidget(0, label)
    return holder, stack


def make_settings_group_parts() -> tuple[QFrame, QVBoxLayout, QVBoxLayout]:
    """Return (section, chrome layout, card stack) for a Settings group."""
    holder = QFrame()
    holder.setObjectName("settingsSection")
    holder.setStyleSheet("QFrame#settingsSection { background: transparent; border: none; }")
    outer = QVBoxLayout(holder)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(theme.SPACE_S)
    stack = QWidget()
    stack.setObjectName("settingsCardStack")
    layout = QVBoxLayout(stack)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.SPACE_XS)
    outer.addWidget(stack)
    return holder, outer, layout
