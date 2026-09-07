# SPDX-License-Identifier: MIT
"""Win11 About-page cards for the Info tab."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui._main_window_navpane import _app_icon_pixmap
from winpodx.gui._main_window_secondary_style import (
    apply_card_qss,
    apply_w11_button,
    make_named_settings_group,
    make_status_badge,
    make_value_label,
    restyle_settings_cards,
)
from winpodx.gui._widget_helpers import make_settings_card
from winpodx.gui.icons import load_icon

_HEALTH_BADGE_ATTRS: dict[str, str] = {
    "ok": "GREEN",
    "warn": "YELLOW",
    "fail": "RED",
    "skip": "OVERLAY1",
}
_HEALTH_ICONS: dict[str, str] = {"ok": "check", "warn": "warning", "fail": "error"}


def _badge_color(status: str) -> str:
    attr = _HEALTH_BADGE_ATTRS.get(status, "SUBTEXT0")
    return getattr(theme_mod.C, attr)


def _value_color(value: str) -> str:
    upper = value.upper()
    if "MISSING" in upper:
        return theme_mod.C.RED
    if "UNKNOWN" in upper:
        return theme_mod.C.YELLOW
    return theme_mod.C.SUBTEXT1


def _colored_value_label(text: str) -> QLabel:
    lbl = make_value_label(text)
    color = _value_color(text)
    lbl.setProperty("w11Color", color)
    lbl.setStyleSheet(
        f"background: transparent; color: {color}; font-size: {theme_mod.FONT_BODY}px;"
    )
    return lbl


def _clear_card_body(body: QVBoxLayout) -> None:
    while body.count():
        item = body.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.deleteLater()


def _apply_health_icon(card: QFrame, icon_name: str, status: str) -> None:
    """Tint the SettingsCard row icon with the probe's live status colour."""
    attr = _HEALTH_BADGE_ATTRS.get(status, "SUBTEXT0")
    color = getattr(theme_mod.C, attr)
    for label in card.findChildren(QLabel):
        pixmap = label.pixmap()
        if pixmap is None or pixmap.isNull():
            continue
        label.setPixmap(load_icon(icon_name, color, 20).pixmap(20, 20))
        label.setProperty("iconColor", color)
        label.setProperty("iconName", icon_name)
        label.setProperty("iconColorAttr", attr)
        return


class _InfoCardsMixin:
    """About device card, health rows, and key/value SettingsCards."""

    def _build_about_device_card(self, action: QPushButton | None = None) -> QFrame:
        card = QFrame()
        card.setObjectName("aboutDeviceCard")
        apply_card_qss(card)
        row = QHBoxLayout(card)
        pad = theme_mod.SPACE_L
        row.setContentsMargins(pad, pad, pad, pad)
        row.setSpacing(theme_mod.SPACE_M)
        icon = QLabel()
        icon.setFixedSize(48, 48)
        pixmap = _app_icon_pixmap(48)
        if pixmap is not None:
            icon.setPixmap(pixmap)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(theme_mod.SPACE_XS)
        name = QLabel("WinPodX")
        name.setObjectName("aboutDeviceName")
        name.setStyleSheet(
            f"background: transparent; color: {theme_mod.C.TEXT}; "
            f"font-size: {theme_mod.FONT_BODY}px; font-weight: 600;"
        )
        version = QLabel("")
        version.setStyleSheet(
            f"background: transparent; color: {theme_mod.C.SUBTEXT1}; "
            f"font-size: {theme_mod.FONT_CAPTION}px;"
        )
        self._info_version_label = version
        copy.addWidget(name)
        copy.addWidget(version)
        row.addLayout(copy, 1)
        if action is not None:
            row.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _info_card(self, title: str, key: str = "") -> QFrame:
        holder, group = make_named_settings_group(tr(title))
        self._info_card_bodies[title.lower()] = group
        group.addWidget(make_settings_card("", tr("Loading...")))
        return holder

    def _render_health_card(self, probes: list[dict], overall: str) -> None:
        body = self._info_card_bodies.get("health")
        if body is None:
            return
        _clear_card_body(body)
        if not probes:
            body.addWidget(make_settings_card("", tr("No probes ran (health module unavailable).")))
            return
        overall_color = _badge_color(overall)
        overall_icon = _HEALTH_ICONS.get(overall, "pending")
        verdict = make_settings_card(
            overall_icon,
            tr("Overall: {status}").format(status=overall.upper() or tr("UNKNOWN")),
            action=make_status_badge(overall or "skip", overall_color),
        )
        _apply_health_icon(verdict, overall_icon, overall)
        body.addWidget(verdict)
        for p in probes:
            status = p.get("status", "")
            color = _badge_color(status)
            detail = str(p.get("detail", ""))
            duration = int(p.get("duration_ms", 0))
            if duration:
                detail = f"{detail} ({duration}ms)" if detail else f"{duration}ms"
            icon_name = _HEALTH_ICONS.get(status, "pending")
            row = make_settings_card(
                icon_name,
                p.get("name", ""),
                detail,
                action=make_status_badge(status, color),
            )
            _apply_health_icon(row, icon_name, status)
            body.addWidget(row)

    def _set_info_card_rows(self, key: str, rows: list[tuple[str, str]]) -> None:
        body = self._info_card_bodies.get(key)
        if body is None:
            return
        _clear_card_body(body)
        for label, value in rows:
            body.addWidget(
                make_settings_card("", label, action=_colored_value_label(value), compact=True)
            )

    def _restyle_info(self) -> None:
        root = getattr(self, "_info_page", None) or getattr(self, "page", None)
        if root is None:
            return
        restyle_settings_cards(root)
        version = getattr(self, "_info_version_label", None)
        if version is not None:
            version.setStyleSheet(
                f"background: transparent; color: {theme_mod.C.SUBTEXT1}; "
                f"font-size: {theme_mod.FONT_CAPTION}px;"
            )
        name = root.findChild(QLabel, "aboutDeviceName")
        if name is not None:
            name.setStyleSheet(
                f"background: transparent; color: {theme_mod.C.TEXT}; "
                f"font-size: {theme_mod.FONT_BODY}px; font-weight: 600;"
            )
        refresh = getattr(self, "_info_refresh_btn", None)
        if refresh is not None:
            apply_w11_button(refresh, theme_mod.BTN_GHOST, role="ghost")
            refresh.setFixedHeight(theme_mod.CONTROL_HEIGHT_W11)
        copy_btn = getattr(self, "_info_copy_btn", None)
        if copy_btn is not None:
            apply_w11_button(copy_btn, theme_mod.BTN_PRIMARY, role="primary")
