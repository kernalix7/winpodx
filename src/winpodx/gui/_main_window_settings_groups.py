# SPDX-License-Identifier: MIT
"""Settings group assembly: Connection through Danger zone."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui._main_window_settings_cards import _add_group_chrome, _new_settings_group
from winpodx.gui._settings_card import make_settings_card
from winpodx.gui._widget_helpers import make_warning_callout, mark_fluid_wrap
from winpodx.gui.theme import (
    BTN_DANGER,
    BTN_PRIMARY,
    BTN_SECONDARY,
    COMBO,
    FONT_CAPTION,
    RADIUS_S,
    SPACE_S,
    C,
)


def _pin_wrap(widget: QWidget) -> None:
    for label in widget.findChildren(QLabel):
        if label.wordWrap():
            mark_fluid_wrap(label)


class SettingsGroupsMixin:
    """Mount Settings groups in task order and build composite cards."""

    def _mount_settings_groups(self, layout: QVBoxLayout) -> None:
        layout.addWidget(
            self._settings_card(
                tr("▣  RDP Connection"),
                tr("Remote Desktop Protocol settings"),
                [
                    (tr("Username"), self.input_user),
                    (tr("Host / IP"), self.input_ip),
                    (tr("Port"), self.input_port),
                    (tr("Scale %"), self.input_scale),
                    (tr("Windows DPI"), self.input_dpi),
                    (tr("Extra FreeRDP args"), self.input_extra_flags),
                ],
            )
        )
        hardware = self._settings_card(
            tr("▨  Hardware"),
            tr("Backend, edition, and resource allocation"),
            [
                (tr("Backend"), self.input_backend),
                (tr("Windows Edition"), self.input_win_version),
                (tr("CPU Cores"), self.input_cpu),
                (tr("RAM (GB)"), self.input_ram),
                (tr("Idle Timeout"), self.input_idle),
                (tr("Idle Action"), self.input_idle_action),
                (tr("Max Sessions (1-50)"), self.input_max_sessions),
            ],
        )
        self.budget_summary_label = QLabel("")
        self.budget_summary_label.setWordWrap(True)
        mark_fluid_wrap(self.budget_summary_label)
        self.budget_summary_label.setStyleSheet(
            f"color: {C.SUBTEXT0}; background: transparent; font-size: {FONT_CAPTION}px;"
        )
        hardware.layout().addWidget(self.budget_summary_label)
        layout.addWidget(hardware)

        try:
            from winpodx.utils.specs import (
                detect_tuning_capability,
                format_tuning_summary,
                recommend_tuning_profile,
            )

            tuning_cap = detect_tuning_capability(
                vm_cpu_cores=self.cfg.pod.cpu_cores, vm_ram_gb=self.cfg.pod.ram_gb
            )
            tuning_summary = format_tuning_summary(
                tuning_cap,
                recommend_tuning_profile(tuning_cap, user_pref=self.cfg.pod.tuning_profile),
            )
        except Exception:  # noqa: BLE001
            tuning_summary = tr("  (tuning detection failed; see `winpodx info` for details)")
        layout.addWidget(self._build_tuning_card(self.input_tuning_profile, tuning_summary))

        disguise, disguise_body = self._settings_card_shell(
            "hardware",
            tr("Bare-metal compatibility"),
            tr(
                "Hide the KVM/QEMU hypervisor so GPU passthrough (Nvidia code 43) and "
                "apps that refuse to run in a VM work. Not an anti-cheat bypass."
            ),
        )
        disguise_body.addWidget(self.input_disguise_level)
        layout.addWidget(disguise)

        layout.addWidget(self._build_windows_update_card())
        self._refresh_update_status()

        layout.addWidget(
            self._settings_card(
                tr("Applies immediately"),
                tr("These take effect right away — no need to click Save Settings."),
                [
                    (self._autostart_label, self.checkbox_autostart_tray),
                    (self._mime_label, self.checkbox_mime_assoc),
                ],
            )
        )
        try:
            from winpodx.gui.reverse_open_panel import build_panel as build_ropanel

            layout.addWidget(build_ropanel(self.cfg, parent=layout.parentWidget()))
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception(
                "reverse-open panel failed to build; Settings page continues without it"
            )

        appearance = self._settings_card(
            tr("🌐  Localization"),
            tr("Windows install language / region / keyboard / timezone"),
            [
                (tr("Language"), self.input_language),
                (tr("Region"), self.input_region),
                (tr("Keyboard"), self.input_keyboard),
                (tr("Timezone"), self.input_timezone),
                (self._scan_label, self.checkbox_full_app_scan),
                (tr("WinPodX UI language"), self.input_ui_language),
            ],
        )
        appearance.layout().addWidget(self.ui_lang_note)
        layout.addWidget(appearance)

        self.budget_warning_label = QLabel("")
        self.budget_warning_label.setWordWrap(True)
        mark_fluid_wrap(self.budget_warning_label)
        self.budget_warning_label.setStyleSheet(
            f"color: {C.YELLOW}; background: transparent; "
            f"font-size: {FONT_CAPTION}px; padding: 4px {SPACE_S}px;"
        )
        self.budget_warning_label.setVisible(False)
        layout.addWidget(self.budget_warning_label)
        layout.addWidget(self._build_danger_zone())

    def _build_tuning_card(self, profile_combo: QComboBox, summary_text: str) -> QFrame:
        card, body = self._settings_card_shell(
            "performance",
            tr("◨  Performance Tuning"),
            tr("QEMU + Windows-on-KVM knob preset"),
        )
        profile_combo.setMinimumHeight(32)
        profile_combo.setStyleSheet(COMBO)
        body.addWidget(profile_combo)
        summary_header = QLabel(tr("Detection summary (this host)"))
        summary_header.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; "
            f"font-size: {FONT_CAPTION}px; font-weight: 500;"
        )
        body.addWidget(summary_header)
        summary_frame = QFrame()
        summary_frame.setStyleSheet(
            f"background: {C.MANTLE}; border-radius: {RADIUS_S}px; padding: {SPACE_S - 2}px;"
        )
        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(SPACE_S + 2, SPACE_S, SPACE_S + 2, SPACE_S)
        summary_layout.setSpacing(0)
        self.tuning_summary_label = QLabel(summary_text)
        self.tuning_summary_label.setStyleSheet(
            f"background: transparent; font-family: 'JetBrainsMono Nerd Font', "
            f"'Cascadia Code', 'Fira Code', monospace; "
            f"font-size: {FONT_CAPTION}px; color: {C.SUBTEXT1};"
        )
        self.tuning_summary_label.setWordWrap(True)
        mark_fluid_wrap(self.tuning_summary_label)
        summary_layout.addWidget(self.tuning_summary_label)
        body.addWidget(summary_frame)
        return card

    def _build_windows_update_card(self) -> QFrame:
        group = self._settings_card(
            tr("Windows Update"),
            tr("Checking..."),
            [(tr("Password Rotation"), self.input_pw_max_age)],
        )
        caption = group.findChild(QLabel, "settingsGroupCaption")
        self._update_status_label = caption if caption is not None else QLabel(tr("Checking..."))
        self._btn_enable_updates = QPushButton(tr("Enable"))
        self._btn_enable_updates.setStyleSheet(BTN_PRIMARY)
        self._btn_enable_updates.setMinimumHeight(32)
        self._btn_enable_updates.clicked.connect(self._on_enable_updates)
        self._btn_disable_updates = QPushButton(tr("Disable"))
        self._btn_disable_updates.setStyleSheet(BTN_DANGER)
        self._btn_disable_updates.setMinimumHeight(32)
        self._btn_disable_updates.clicked.connect(self._on_disable_updates)
        self._btn_retry_updates = QPushButton(tr("Retry"))
        self._btn_retry_updates.setStyleSheet(BTN_SECONDARY)
        self._btn_retry_updates.setMinimumHeight(32)
        self._btn_retry_updates.clicked.connect(self._refresh_update_status)
        self._btn_retry_updates.setVisible(False)
        cluster = QWidget()
        buttons_row = QHBoxLayout(cluster)
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(SPACE_S)
        buttons_row.addWidget(self._btn_enable_updates)
        buttons_row.addWidget(self._btn_retry_updates)
        buttons_row.addStretch()
        stack = group.findChild(QWidget, "settingsCardStack")
        if stack is not None and stack.layout() is not None:
            stack.layout().addWidget(make_settings_card("", tr("Enable"), action=cluster))
        return group

    def _build_danger_zone(self) -> QFrame:
        recreate_text = tr(
            "Changing Port, CPU, RAM, Edition or Tuning Profile recreates the "
            "container (Windows reboots, ~1-2 min). Changing the Edition also "
            "wipes the Windows disk and reinstalls (~5-10 min)."
        )
        group, chrome, stack = _new_settings_group()
        _add_group_chrome(chrome, "warning", "Danger zone", recreate_text.split(".", 1)[0] + ".")
        recreate_callout = make_warning_callout(recreate_text, level="warn")
        locale_callout = make_warning_callout(
            tr(
                "Changing Language, Region or Keyboard wipes the Windows disk and "
                "reinstalls (~5-10 min) — these only apply on a fresh install. "
                "Timezone applies on the next recreate without a wipe."
            ),
            level="danger",
        )
        _pin_wrap(recreate_callout)
        _pin_wrap(locale_callout)
        stack.addWidget(recreate_callout)
        stack.addWidget(locale_callout)
        stack.addWidget(make_settings_card("", tr("Disable"), action=self._btn_disable_updates))
        self._remember_settings_group(group)
        return group
