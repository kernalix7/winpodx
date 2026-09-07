# SPDX-License-Identifier: MIT
"""Guest-locale and immediate-preference field builders for Settings."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QComboBox, QLabel

from winpodx.core.i18n import tr
from winpodx.gui._main_window_settings_locale import (
    _COMMON_TIMEZONES,
    _DOCKUR_KEYBOARDS,
    _DOCKUR_LANGUAGES,
    _DOCKUR_REGIONS,
)
from winpodx.gui._toggle_switch import ToggleSwitch
from winpodx.gui._widget_helpers import mark_fluid_wrap
from winpodx.gui.theme import COMBO, FONT_CAPTION, C


class SettingsPrefsMixin:
    """Create Integration / Appearance widgets (toggles, UI language, guest locale)."""

    def _create_guest_locale_fields(self) -> None:
        self.input_language = self._build_locale_combo(
            cfg_value=self.cfg.pod.language,
            options=_DOCKUR_LANGUAGES,
            empty_label=tr("Auto (English)"),
        )
        self.input_language.setToolTip(
            tr(
                "Windows installation language (dockur LANGUAGE env). "
                "Applied on first install only; changing this on an existing "
                "guest requires `winpodx pod recreate --wipe-storage`."
            )
        )
        self.input_region = self._build_locale_combo(
            cfg_value=self.cfg.pod.region,
            options=_DOCKUR_REGIONS,
            empty_label=tr("Auto (en-001)"),
        )
        self.input_region.setToolTip(
            tr("Windows locale region in BCP-47 form (dockur REGION env). First-install only.")
        )
        self.input_keyboard = self._build_locale_combo(
            cfg_value=self.cfg.pod.keyboard,
            options=_DOCKUR_KEYBOARDS,
            empty_label=tr("Auto (en-US)"),
        )
        self.input_keyboard.setToolTip(
            tr("Windows keyboard layout (dockur KEYBOARD env). First-install only.")
        )
        from winpodx.utils.locale import detect_timezone

        detected_tz = detect_timezone()
        self.input_timezone = self._build_locale_combo(
            cfg_value=self.cfg.pod.timezone,
            options=_COMMON_TIMEZONES,
            empty_label=tr("Auto (detected: {tz})").format(tz=detected_tz),
        )
        self.input_timezone.setToolTip(
            tr(
                "Windows guest timezone (IANA name). Empty = host autodetect "
                "at compose time. Applied via OEM `tzutil /s <id>` on every "
                "container (re)create -- unlike language/region/keyboard, "
                "this does NOT require --wipe-storage."
            )
        )

    def _create_pref_fields(self) -> None:
        from winpodx.core.config import _UI_LANGUAGES
        from winpodx.core.config import Config as MimeCfg
        from winpodx.desktop.autostart import is_autostart_enabled, set_autostart

        autostart_label = tr("Start the Windows pod at login (launches the tray + boots the pod)")
        self.checkbox_autostart_tray = ToggleSwitch(autostart_label)
        self.checkbox_autostart_tray.setChecked(is_autostart_enabled())

        def _on_autostart_toggled(checked: bool) -> None:
            try:
                set_autostart(bool(checked))
            except OSError as exc:
                logging.getLogger(__name__).warning("Could not toggle autostart: %s", exc)

        self.checkbox_autostart_tray.toggled.connect(_on_autostart_toggled)

        mime_label = tr("Register file associations for discovered apps (Open with)")
        self.checkbox_mime_assoc = ToggleSwitch(mime_label)
        try:
            self.checkbox_mime_assoc.setChecked(MimeCfg.load().desktop.mime_associations)
        except Exception:  # noqa: BLE001
            self.checkbox_mime_assoc.setChecked(True)
        self.checkbox_mime_assoc.setToolTip(
            tr(
                "Adds them to 'Open with' on the next refresh; never sets them "
                "as the default app for a file type."
            )
        )

        def _on_mime_assoc_toggled(checked: bool) -> None:
            try:
                cfg = MimeCfg.load()
                cfg.desktop.mime_associations = bool(checked)
                cfg.save()
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning("Could not toggle file associations: %s", exc)

        self.checkbox_mime_assoc.toggled.connect(_on_mime_assoc_toggled)

        scan_label = tr("Discover all installed apps (not just Start Menu apps)")
        self.checkbox_full_app_scan = ToggleSwitch(scan_label)
        try:
            self.checkbox_full_app_scan.setChecked(MimeCfg.load().desktop.full_app_scan)
        except Exception:  # noqa: BLE001
            self.checkbox_full_app_scan.setChecked(False)
        self.checkbox_full_app_scan.setToolTip(
            tr(
                "Off (default): only apps from the Windows Start Menu are added, "
                "keeping the Linux menu clean. On: also scans App Paths, "
                "Chocolatey/Scoop shims and all UWP packages. Applies on next refresh."
            )
        )

        def _on_full_app_scan_toggled(checked: bool) -> None:
            try:
                cfg = MimeCfg.load()
                cfg.desktop.full_app_scan = bool(checked)
                cfg.save()
            except Exception as exc:  # noqa: BLE001
                logging.getLogger(__name__).warning("Could not toggle full app scan: %s", exc)

        self.checkbox_full_app_scan.toggled.connect(_on_full_app_scan_toggled)
        self._autostart_label = autostart_label
        self._mime_label = mime_label
        self._scan_label = scan_label

        lang_labels = {
            "auto": tr("Auto (system language)"),
            "en": "English",
            "ko": "한국어",
            "zh": "中文",
            "ja": "日本語",
            "de": "Deutsch",
            "fr": "Français",
            "it": "Italiano",
        }
        self.input_ui_language = QComboBox()
        self.input_ui_language.setStyleSheet(COMBO)
        for code in _UI_LANGUAGES:
            self.input_ui_language.addItem(lang_labels.get(code, code), code)
        cur = self.cfg.ui.language if self.cfg.ui.language in _UI_LANGUAGES else "auto"
        self.input_ui_language.setCurrentIndex(self.input_ui_language.findData(cur))
        self.input_ui_language.currentIndexChanged.connect(self._on_ui_language_changed)

        self.ui_lang_note = QLabel(tr("Restart WinPodX (tray / GUI) to apply the language change."))
        self.ui_lang_note.setWordWrap(True)
        mark_fluid_wrap(self.ui_lang_note)
        self.ui_lang_note.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: {FONT_CAPTION}px;"
        )

    def _on_ui_language_changed(self, idx: int) -> None:
        code = self.input_ui_language.itemData(idx)
        if not code:
            return
        try:
            from winpodx.core.config import Config
            from winpodx.core.i18n import set_language

            loaded = Config.load()
            loaded.ui.language = code
            loaded.save()
            set_language(code)
            self.cfg.ui.language = code
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("Could not set UI language: %s", exc)
