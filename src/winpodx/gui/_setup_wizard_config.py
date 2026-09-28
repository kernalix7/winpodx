# SPDX-License-Identifier: MIT
"""Configuration page: installation source, edition, locale, hardware, username."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.config import WIN_VERSION_LABELS
from winpodx.core.i18n import tr
from winpodx.gui import theme
from winpodx.gui._main_window_settings_locale import (
    _COMMON_TIMEZONES,
    _DOCKUR_KEYBOARDS,
    _DOCKUR_LANGUAGES,
    _DOCKUR_REGIONS,
)
from winpodx.gui._settings_card import make_settings_card, make_settings_group
from winpodx.gui._setup_wizard_model import SetupAnswers, host_spec_summary
from winpodx.gui._widget_helpers import guard_wheel_scroll


class ConfigurationPage(QWidget):
    """Collect the knobs ``apply_setup_presets`` understands."""

    def __init__(
        self,
        initial: SetupAnswers,
        parent: QWidget | None = None,
        *,
        reinstall: bool = False,
    ) -> None:
        super().__init__(parent)
        self._backend = _combo((("Podman", "podman"), ("Docker", "docker")), initial.backend)
        self._backend.setEnabled(not reinstall)
        self._storage = QLineEdit(initial.storage_path)
        self._storage.setReadOnly(reinstall)
        self._iso = QLineEdit(initial.win_iso)
        self._iso.setReadOnly(reinstall)
        self._edition = _combo(
            ((label, value) for value, label in WIN_VERSION_LABELS.items()),
            initial.win_version,
        )
        self._language = _combo(_DOCKUR_LANGUAGES, initial.language)
        self._region = _combo(_DOCKUR_REGIONS, initial.region)
        self._keyboard = _combo(_DOCKUR_KEYBOARDS, initial.keyboard)
        self._timezone = _combo(_COMMON_TIMEZONES, initial.timezone)
        self._cpu = QSpinBox()
        self._cpu.setRange(1, 128)
        self._cpu.setValue(initial.cpu_cores)
        self._ram = QSpinBox()
        self._ram.setRange(1, 512)
        self._ram.setValue(initial.ram_gb)
        self._disk = _combo(
            (("64G", "64G"), ("128G", "128G"), ("256G", "256G"), ("512G", "512G")),
            initial.disk_size,
        )
        self._user = QLineEdit(initial.rdp_user)
        self._host = QLabel(host_spec_summary())
        self._host.setWordWrap(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_L)
        installation, install_stack = make_settings_group(tr("Installation"))
        install_stack.addWidget(
            make_settings_card(
                "gear",
                tr("Backend"),
                tr("Kept from the existing configuration on reinstall.") if reinstall else "",
                action=self._backend,
            )
        )
        install_stack.addWidget(
            make_settings_card(
                "hardware",
                tr("Storage directory"),
                tr("Kept on reinstall; storage is not moved.")
                if reinstall
                else tr("Leave blank to use the default storage directory."),
                action=self._storage,
            )
        )
        install_stack.addWidget(
            make_settings_card(
                "desktop",
                tr("Local Windows ISO"),
                tr("Replacement ISO selection is unavailable on reinstall.")
                if reinstall
                else tr("Leave blank to download Windows."),
                action=self._iso,
            )
        )
        windows, win_stack = make_settings_group(tr("Windows"))
        win_stack.addWidget(
            make_settings_card("desktop", tr("Windows edition"), action=self._edition)
        )
        win_stack.addWidget(make_settings_card("globe", tr("UI language"), action=self._language))
        win_stack.addWidget(make_settings_card("globe", tr("Regional format"), action=self._region))
        win_stack.addWidget(
            make_settings_card("globe", tr("Keyboard layout"), action=self._keyboard)
        )
        win_stack.addWidget(make_settings_card("clock", tr("Timezone"), action=self._timezone))
        hardware, hw_stack = make_settings_group(tr("Hardware"))
        hw_stack.addWidget(make_settings_card("performance", tr("CPU cores"), action=self._cpu))
        hw_stack.addWidget(make_settings_card("performance", tr("RAM (GB)"), action=self._ram))
        hw_stack.addWidget(make_settings_card("hardware", tr("Disk size"), action=self._disk))
        hw_stack.addWidget(self._host)
        account, acc_stack = make_settings_group(tr("Account"))
        acc_stack.addWidget(
            make_settings_card("session", tr("Windows username"), action=self._user)
        )
        root.addWidget(installation)
        root.addWidget(windows)
        root.addWidget(hardware)
        root.addWidget(account)
        root.addStretch(1)
        guard_wheel_scroll(self)

    def answers(self) -> SetupAnswers:
        """Read the current widget values."""
        return SetupAnswers(
            win_version=str(self._edition.currentData() or "11"),
            language=str(self._language.currentData() or "English"),
            region=str(self._region.currentData() or "en-001"),
            keyboard=str(self._keyboard.currentData() or "en-US"),
            timezone=str(self._timezone.currentData() or "UTC"),
            cpu_cores=int(self._cpu.value()),
            ram_gb=int(self._ram.value()),
            disk_size=str(self._disk.currentData() or "64G"),
            rdp_user=self._user.text().strip() or "Docker",
            tuning_profile="auto",
            backend=str(self._backend.currentData()),
            storage_path=self._storage.text().strip(),
            win_iso=self._iso.text().strip(),
        )

    def _restyle(self) -> None:
        self._host.setStyleSheet(
            f"background: transparent; color: {theme.C.SUBTEXT1}; "
            f"font-size: {theme.FONT_CAPTION}px;"
        )
        for combo in (
            self._backend,
            self._edition,
            self._language,
            self._region,
            self._keyboard,
            self._timezone,
            self._disk,
        ):
            combo.setStyleSheet(theme.COMBO)
            combo.setFixedHeight(theme.CONTROL_HEIGHT_W11)
        for spin in (self._cpu, self._ram):
            spin.setStyleSheet(theme.SPIN_BOX)
            spin.setFixedHeight(theme.CONTROL_HEIGHT_W11)
        for line_edit in (self._user, self._storage, self._iso):
            line_edit.setStyleSheet(theme.INPUT)
            line_edit.setFixedHeight(theme.CONTROL_HEIGHT_W11)


def _combo(options: Iterable[tuple[str, str]], current: str) -> QComboBox:
    box = QComboBox()
    found = False
    for label, value in options:
        box.addItem(str(label), str(value))
        if str(value) == current:
            found = True
    if current and not found:
        box.addItem(current, current)
    idx = box.findData(current)
    if idx >= 0:
        box.setCurrentIndex(idx)
    return box
