# SPDX-License-Identifier: MIT
"""Tools-page SettingsCard row builders for ``MaintenanceMixin``."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QPushButton

from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui._main_window_secondary_style import (
    apply_w11_button,
    make_ghost_button,
    make_named_settings_group,
)
from winpodx.gui._settings_card import make_settings_card


class MaintenanceCardsMixin:
    """Win11 SettingsCard rows for Tools actions and live sessions."""

    def _make_tool_card(
        self,
        section: str,
        tools: list,
        *,
        base_idx: int,
    ) -> QFrame:
        """Wrap a labelled group of SettingsCard action rows."""
        card, layout = make_named_settings_group(section)
        for i, spec in enumerate(tools):
            icon, label, desc, handler = spec[0], spec[1], spec[2], spec[3]
            needs_running = bool(spec[4]) if len(spec) > 4 else False
            row = self._make_action_row(icon, label, desc, handler, base_idx + i)
            row.setProperty("needsRunning", needs_running)
            row.setProperty("idleCaption", desc)
            layout.addWidget(row)
        return card

    def _make_action_row(
        self,
        icon: str,
        label: str,
        desc: str,
        handler: object,
        color_idx: int,
    ) -> QFrame:
        """Build a 68px SettingsCard tool row with a 32px verb button."""
        del color_idx
        icon_name = {
            "⏸": "pause",
            "▶": "play",
            "▣": "desktop",
            "✧": "clean",
            "◷": "clock",
            "◆": "diamond",
            "⚙": "gear",
            "⊕": "plus",
            "↻": "refresh",
        }.get(icon, icon)
        btn = QPushButton(label)
        apply_w11_button(btn, theme_mod.BTN_SECONDARY, role="secondary")
        btn.clicked.connect(lambda _checked=False, h=handler: h())
        return make_settings_card(icon_name, label, desc, action=btn)

    def _make_session_row(self, app_name: str, pid: int) -> QFrame:
        """One live-session row: app name + PID + a Terminate ghost."""
        btn = make_ghost_button(tr("Terminate"))
        btn.clicked.connect(lambda _checked=False, n=app_name: self._on_terminate_session(n))
        return make_settings_card(
            "session",
            app_name,
            tr("PID {pid}").format(pid=pid),
            action=btn,
            compact=True,
            object_name="sessionCard",
        )

    def _sync_tools_pod_state(self, state: str, _ip: str = "") -> None:
        """Disable running-pod verbs and show why when the pod is stopped."""
        stopped = state == "stopped"
        reason = tr("Pod is stopped")
        root = getattr(self, "_tools_page", None)
        if root is None:
            return
        for card in root.findChildren(QFrame, "settingsCard"):
            if not card.property("needsRunning"):
                continue
            btn = getattr(card, "action_widget", None)
            if btn is not None:
                btn.setEnabled(not stopped)
            desc = getattr(card, "desc_label", None)
            original = card.property("idleCaption") or ""
            if desc is not None:
                desc.setText(reason if stopped else original)
