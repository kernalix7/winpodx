# SPDX-License-Identifier: MIT
"""Dashboard scheme restyle: re-apply Fluent tokens after ``theme.rebuild``."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLabel, QPushButton

from winpodx.gui._pod_state_chrome import mix_over, pod_state_color
from winpodx.gui.icons import load_icon

_STATE_ICON_COLOR = {
    "running": ("check", "GREEN"),
    "checking": ("refresh", "PEACH"),
    "paused": ("pause", "PEACH"),
    "stopped": ("stop", "OVERLAY1"),
    "unknown": ("warning", "YELLOW"),
}


class _DashboardStyleMixin:
    """Re-paints Dashboard surfaces from live ``theme.C`` / QSS tokens."""

    def _apply_hero_tint(self, state: str) -> None:
        from winpodx.gui import theme as theme_mod

        hero = self.findChild(QFrame, "podStatusHero")
        if hero is None:
            return
        mixed = mix_over(pod_state_color(state), theme_mod.C.SURFACE0, 0.06)
        hero.setStyleSheet(
            theme_mod.SETTINGS_CARD.replace("QFrame#settingsCard", "QFrame#podStatusHero")
            + f"\nQFrame#podStatusHero {{ background: {mixed}; }}"
        )

    def _restyle_dashboard(self) -> None:
        """Re-apply Fluent card / scroll / label colours after ``theme.rebuild``."""
        from winpodx.gui import theme as theme_mod

        scroll = getattr(self, "_dashboard_scroll", None)
        if scroll is not None:
            scroll.setStyleSheet(theme_mod.SCROLL_AREA)
        for name in (
            "settingsActionRow",
            "reverseOpenRow",
            "workspaceSurface",
            "quickActions",
            "runningNow",
        ):
            frame = self.findChild(QFrame, name)
            if frame is not None:
                frame.setStyleSheet(
                    theme_mod.SETTINGS_CARD.replace("QFrame#settingsCard", f"QFrame#{name}")
                )
        card = self.findChild(QFrame, "quickActions")
        if card is not None:
            for btn, icon_name in zip(
                card.findChildren(QPushButton),
                getattr(self, "_quick_action_icons", ()),
            ):
                btn.setIcon(load_icon(icon_name, theme_mod.C.TEXT, 16))
                btn.setStyleSheet(theme_mod.BTN_GHOST + "QPushButton { padding: 0px 8px; }")
        header = getattr(self, "_dashboard_header", None)
        if header is not None:
            labels = header.findChildren(QLabel)
            if labels:
                labels[0].setStyleSheet(
                    f"background: transparent; color: {theme_mod.C.TEXT}; "
                    f"font-size: {theme_mod.FONT_HERO}px; font-weight: 600;"
                )
            if len(labels) > 1:
                labels[1].setStyleSheet(theme_mod.PAGE_SUBTITLE)
        name_lbl = getattr(self, "_pod_name_label", None)
        if name_lbl is not None:
            name_lbl.setStyleSheet(
                f"color: {theme_mod.C.TEXT}; font-size: {theme_mod.FONT_BODY}px; font-weight: 600;"
            )
        status = getattr(self, "_pod_status_label", None)
        if status is not None:
            status.setStyleSheet(
                f"color: {theme_mod.C.TEXT}; font-size: {theme_mod.FONT_TITLE}px; font-weight: 600;"
            )
        detail = getattr(self, "_pod_status_detail", None)
        if detail is not None:
            detail.setStyleSheet(
                f"color: {theme_mod.C.SUBTEXT1}; font-size: {theme_mod.FONT_CAPTION}px;"
            )
        for name in ("_bar_ram", "_bar_cpu", "_bar_disk"):
            gauge = getattr(self, name, None)
            if gauge is not None:
                gauge.update()
        action = getattr(self, "_pod_primary_action", None)
        if action is not None:
            action.setStyleSheet(
                theme_mod.BTN_PRIMARY + f" QPushButton {{ border-radius: {theme_mod.RADIUS_S}px; "
                f"min-height: {theme_mod.HIT_TARGET}px; }}"
            )
        toggle = getattr(self, "_reverse_open_check", None)
        if toggle is not None:
            toggle.update()
        for section_name in ("pinnedWorkspaceSection", "recentWorkspaceSection"):
            section = self.findChild(QFrame, section_name)
            if section is None:
                continue
            for label in section.findChildren(QLabel):
                if label.parentWidget() is section:
                    label.setStyleSheet(
                        f"color: {theme_mod.C.TEXT}; font-size: {theme_mod.FONT_BODY}px; "
                        f"font-weight: 600;"
                    )
                    break
        state = getattr(self, "_hero_pod_state", "unknown")
        rec_icon, rec_attr = _STATE_ICON_COLOR.get(state, _STATE_ICON_COLOR["unknown"])
        rec_color = getattr(theme_mod.C, rec_attr)
        icon = getattr(self, "_pod_status_icon", None)
        if icon is not None:
            icon.setPixmap(load_icon(rec_icon, rec_color, 24).pixmap(24, 24))
        if getattr(self, "_recovery_label", None) is not None:
            self._apply_recovery_line(state, rec_icon, rec_color)
        self._apply_hero_tint(state)
        for badge in self.findChildren(QLabel, "runningBadge"):
            badge.setStyleSheet(
                f"background: {theme_mod.C.GREEN}; border: none; border-radius: 4px;"
            )
        running = self.findChild(QFrame, "runningNow")
        if running is not None:
            heading = running.findChild(QLabel, "runningNowHeading")
            if heading is not None:
                heading.setStyleSheet(
                    f"color: {theme_mod.C.TEXT}; font-size: {theme_mod.FONT_BODY}px; "
                    f"font-weight: 600;"
                )
            for btn in running.findChildren(QPushButton):
                btn.setStyleSheet(theme_mod.BTN_SECONDARY)
