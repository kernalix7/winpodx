# SPDX-License-Identifier: MIT
"""Win11 Settings restyle helpers shared by the secondary GUI pages."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.gui import theme as theme_mod
from winpodx.gui._settings_card import make_settings_group
from winpodx.gui.icons import load_icon

CONTENT_MAX = theme_mod.CONTENT_MAX_WIDTH
_CARD_NAMES = frozenset(
    {
        "settingsCard",
        "sessionCard",
        "aboutDeviceCard",
        "deviceCard",
        "reverseOpenEnableRow",
        "reverseOpenSlugRow",
    }
)


def make_named_settings_group(title: str = "") -> tuple[QWidget, QVBoxLayout]:
    """``make_settings_group`` plus a restyle hook on the Body Strong label."""
    holder, layout = make_settings_group(title)
    if title:
        for label in holder.findChildren(QLabel):
            label.setObjectName("settingsGroupTitle")
            break
    return holder, layout


def constrain_settings_content(content: QWidget) -> None:
    """Cap a page body at the Win11 Settings content width, left-anchored."""
    content.setMaximumWidth(theme_mod.CONTENT_MAX_WIDTH)
    content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    layout = content.layout()
    if layout is not None:
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)


def mount_settings_column(host: QWidget, *, scrolled: bool = True) -> QVBoxLayout:
    """Left-anchored column, max ``CONTENT_MAX_WIDTH``, §4 body gutter."""
    gutter = theme_mod.SCROLL_GUTTER if scrolled else 0
    right = theme_mod.PAGE_MARGIN_X - gutter
    host.setMaximumWidth(theme_mod.CONTENT_MAX_WIDTH + right)
    host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, right, theme_mod.SPACE_XL)
    layout.setSpacing(theme_mod.SPACE_XL)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    return layout


def apply_card_qss(frame: QFrame) -> None:
    """Paint ``frame`` with the live ``SETTINGS_CARD`` token."""
    name = frame.objectName() or "settingsCard"
    frame.setStyleSheet(theme_mod.SETTINGS_CARD.replace("QFrame#settingsCard", f"QFrame#{name}"))


def make_value_label(text: str) -> QLabel:
    """Right-aligned secondary value for a SettingsCard action slot."""
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(
        f"background: transparent; color: {theme_mod.C.SUBTEXT1}; "
        f"font-size: {theme_mod.FONT_BODY}px;"
    )
    return lbl


def make_status_badge(status: str, color: str) -> QLabel:
    """Compact ok/warn/fail badge used as a SettingsCard action."""
    badge = QLabel(status.upper())
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setMinimumWidth(48)
    badge.setProperty("w11Color", color)
    badge.setStyleSheet(
        f"color: {color}; font-size: {theme_mod.FONT_CAPTION}px; font-weight: 500; "
        f"background: transparent; padding: 2px 6px;"
    )
    return badge


def chevron_button_qss() -> str:
    """Bare Win11 chevron glyph: no box at rest, subtle fill on hover."""
    return (
        f"QPushButton {{ background: transparent; border: none; padding: 0 8px;"
        f" color: {theme_mod.C.TEXT}; font-size: {theme_mod.FONT_BODY}px;"
        f" border-radius: {theme_mod.RADIUS_S}px; }}"
        f"QPushButton:hover {{ background: {theme_mod.rgba(theme_mod.C.TEXT, 0.06)}; }}"
        f"QPushButton:pressed {{ background: {theme_mod.rgba(theme_mod.C.TEXT, 0.04)}; }}"
        f"QPushButton:disabled {{ color: {theme_mod.C.OVERLAY0}; }}"
    )


def apply_w11_button(btn: QPushButton, qss: str, *, role: str = "secondary") -> QPushButton:
    """Force a 32px Fluent action control and remember its role for restyle."""
    btn.setStyleSheet(qss)
    btn.setFixedHeight(theme_mod.CONTROL_HEIGHT_W11)
    btn.setProperty("w11Role", role)
    return btn


def make_ghost_button(text: str = "", *, icon: str = "") -> QPushButton:
    """32px bare ghost control (chevron or labelled Refresh)."""
    btn = QPushButton(text)
    if icon:
        btn.setIcon(load_icon(icon, theme_mod.C.TEXT, 16))
        btn.setIconSize(QSize(16, 16))
    btn.setStyleSheet(chevron_button_qss())
    btn.setProperty("w11Role", "ghost")
    if not text:
        btn.setFixedSize(theme_mod.CONTROL_HEIGHT_W11, theme_mod.CONTROL_HEIGHT_W11)
    else:
        btn.setFixedHeight(theme_mod.CONTROL_HEIGHT_W11)
    return btn


def restyle_settings_cards(root: QWidget) -> None:
    """Re-apply Fluent card / action colours after ``theme.rebuild``."""
    for frame in root.findChildren(QFrame):
        if frame.objectName() not in _CARD_NAMES:
            continue
        apply_card_qss(frame)
        title = getattr(frame, "title_label", None)
        if title is not None:
            title.setStyleSheet(
                f"background: transparent; color: {theme_mod.C.TEXT}; "
                f"font-size: {theme_mod.FONT_BODY}px; font-weight: 400;"
            )
        desc = getattr(frame, "desc_label", None)
        if desc is not None:
            desc.setStyleSheet(
                f"background: transparent; color: {theme_mod.C.SUBTEXT1}; "
                f"font-size: {theme_mod.FONT_CAPTION}px; font-weight: 400;"
            )
        action = getattr(frame, "action_widget", None)
        if isinstance(action, QPushButton):
            role = action.property("w11Role")
            if role == "ghost":
                action.setStyleSheet(chevron_button_qss())
                continue
            qss = theme_mod.BTN_SECONDARY
            if role == "danger":
                qss = theme_mod.BTN_DANGER
            elif role == "primary":
                qss = theme_mod.BTN_PRIMARY
            apply_w11_button(action, qss, role=role or "secondary")
        elif isinstance(action, QLabel):
            color = action.property("w11Color") or theme_mod.C.SUBTEXT1
            action.setStyleSheet(
                f"color: {color}; font-size: {theme_mod.FONT_CAPTION}px; font-weight: 500; "
                f"background: transparent; padding: 2px 6px;"
            )
        for label in frame.findChildren(QLabel):
            attr = label.property("iconColorAttr")
            name = label.property("iconName")
            if not attr or not name:
                continue
            color = getattr(theme_mod.C, attr)
            size = int(label.property("iconSize") or 20)
            label.setPixmap(load_icon(name, color, size).pixmap(size, size))
            label.setProperty("iconColor", color)
    for label in root.findChildren(QLabel, "settingsGroupTitle"):
        label.setStyleSheet(
            f"background: transparent; color: {theme_mod.C.TEXT}; "
            f"font-size: {theme_mod.FONT_SUBHEAD}px; font-weight: 600;"
        )


class _SecondaryStyleMixin:
    """Dispatches per-page restyle hooks after a colour-scheme change."""

    def _restyle_secondary_pages(self) -> None:
        for name in (
            "_restyle_tools",
            "_restyle_devices",
            "_restyle_info",
            "_restyle_license",
            "_restyle_logs",
        ):
            fn = getattr(self, name, None)
            if callable(fn):
                fn()
