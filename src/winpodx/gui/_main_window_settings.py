# SPDX-License-Identifier: MIT
"""Settings-page mixin for ``WinpodxWindow``.

Holds the Settings-tab builder, the live budget-warning updater, and the
save handler. Card/group anatomy lives in ``_main_window_settings_cards``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.gui._main_window_secondary_style import mount_settings_column
from winpodx.gui._main_window_settings_cards import SettingsCardsMixin
from winpodx.gui._main_window_settings_groups import SettingsGroupsMixin
from winpodx.gui._main_window_settings_hw import SettingsHwMixin
from winpodx.gui._main_window_settings_prefs import SettingsPrefsMixin
from winpodx.gui._main_window_settings_rdp import SettingsRdpMixin
from winpodx.gui._main_window_settings_save import SettingsSaveMixin
from winpodx.gui._widget_helpers import guard_wheel_scroll
from winpodx.gui.theme import BTN_PRIMARY, SCROLL_AREA


class SettingsPageMixin(
    SettingsSaveMixin,
    SettingsGroupsMixin,
    SettingsPrefsMixin,
    SettingsHwMixin,
    SettingsRdpMixin,
    SettingsCardsMixin,
):
    """Settings page: builds the form, validates input, persists changes."""

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(SCROLL_AREA)
        self._settings_scroll = scroll
        self._settings_page = page

        content = QWidget()
        layout = mount_settings_column(content)
        self._settings_cols = layout

        save_btn = QPushButton(tr("Save Settings"))
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.setFixedWidth(180)
        save_btn.setMinimumHeight(32)
        save_btn.clicked.connect(self._save_settings)
        self._settings_save_btn = save_btn
        register = getattr(self, "_register_page_header", None)
        if callable(register):
            register(
                2,
                tr("Settings"),
                tr("Configure RDP and container settings"),
                actions=save_btn,
            )
        else:
            save_btn.setParent(page)
            save_btn.hide()

        self._create_rdp_fields()
        self._create_hardware_fields()
        self._create_guest_locale_fields()
        self._create_pref_fields()
        self._wire_settings_dirty()
        self._mount_settings_groups(layout)

        self.input_ram.textChanged.connect(self._update_budget_warning)
        self.input_max_sessions.textChanged.connect(self._update_budget_warning)
        self._update_budget_warning()

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        guard_wheel_scroll(page)
        self._reflow_settings()
        return page

    def _mark_settings_dirty(self) -> None:
        btn = getattr(self, "_settings_save_btn", None)
        if btn is None:
            return
        btn.setProperty("dirty", True)
        if not btn.text().startswith("•"):
            btn.setText("• " + btn.text())
        style = btn.style()
        if style is not None:
            style.unpolish(btn)
            style.polish(btn)
        btn.update()

    def _clear_settings_dirty(self) -> None:
        btn = getattr(self, "_settings_save_btn", None)
        if btn is None:
            return
        btn.setProperty("dirty", False)
        btn.setText(tr("Save Settings"))
        style = btn.style()
        if style is not None:
            style.unpolish(btn)
            style.polish(btn)
        btn.update()

    def _wire_settings_dirty(self) -> None:
        for edit in (
            self.input_user,
            self.input_ip,
            self.input_port,
            self.input_extra_flags,
            self.input_cpu,
            self.input_ram,
            self.input_idle,
            self.input_max_sessions,
        ):
            edit.textChanged.connect(self._mark_settings_dirty)
        for combo in (
            self.input_scale,
            self.input_dpi,
            self.input_pw_max_age,
            self.input_backend,
            self.input_idle_action,
            self.input_win_version,
            self.input_language,
            self.input_region,
            self.input_keyboard,
            self.input_timezone,
            self.input_tuning_profile,
            self.input_disguise_level,
        ):
            combo.currentIndexChanged.connect(self._mark_settings_dirty)

    def _reflow_settings(self) -> None:
        cols = getattr(self, "_settings_cols", None)
        if cols is None:
            return
        if cols.direction() != QBoxLayout.Direction.TopToBottom:
            cols.setDirection(QBoxLayout.Direction.TopToBottom)

    def _update_budget_warning(self) -> None:
        from winpodx.core.config import check_session_budget, estimate_session_memory

        try:
            sessions = int(self.input_max_sessions.text() or "10")
            ram = int(self.input_ram.text() or "4")
        except ValueError:
            self.budget_warning_label.setVisible(False)
            if hasattr(self, "budget_summary_label"):
                self.budget_summary_label.setText("")
            return

        clamped_sessions = max(1, min(50, sessions))
        clamped_ram = max(1, ram)
        if hasattr(self, "budget_summary_label"):
            est = estimate_session_memory(clamped_sessions)
            self.budget_summary_label.setText(
                tr("Budget: {sessions} sessions x ~{per} MB + base ≈ {est:.1f} of {ram} GB").format(
                    sessions=clamped_sessions,
                    per=100,
                    est=est,
                    ram=clamped_ram,
                )
            )

        tmp = Config()
        tmp.pod.max_sessions = clamped_sessions
        tmp.pod.ram_gb = clamped_ram
        msg = check_session_budget(tmp)
        if msg:
            self.budget_warning_label.setText(tr("WARNING: {msg}").format(msg=msg))
            self.budget_warning_label.setVisible(True)
        else:
            self.budget_warning_label.setVisible(False)

    def _build_locale_combo(
        self,
        *,
        cfg_value: str,
        options: list[tuple[str, str]],
        empty_label: str,
    ) -> QComboBox:
        combo = QComboBox()
        combo.addItem(empty_label, "")
        for label, value in options:
            combo.addItem(label, value)
        idx = combo.findData(cfg_value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.addItem(f"{cfg_value} (custom)", cfg_value)
            combo.setCurrentIndex(combo.count() - 1)
        return combo
