# SPDX-License-Identifier: MIT
"""Configuration page: installation source, edition, locale, hardware, username."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QSizePolicy,
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
        self._storage = _PrefixLineEdit(initial.storage_path)
        self._storage.setReadOnly(reinstall)
        self._iso = _PrefixLineEdit(initial.win_iso)
        self._iso.setReadOnly(reinstall)
        self._storage_host = _path_host(self._storage)
        self._iso_host = _path_host(self._iso)
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
                action=self._storage_host,
                action_below=True,
            )
        )
        install_stack.addWidget(
            make_settings_card(
                "desktop",
                tr("Local Windows ISO"),
                tr("Replacement ISO selection is unavailable on reinstall.")
                if reinstall
                else tr("Leave blank to download Windows."),
                action=self._iso_host,
                action_below=True,
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
        win_stack.addWidget(
            make_settings_card("clock", tr("Timezone"), action=self._timezone, action_below=True)
        )
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
        preview_style = (
            f"background: transparent; color: {theme.C.SUBTEXT1}; "
            f"font-size: {theme.FONT_CAPTION}px;"
        )
        for preview in self.findChildren(QLabel, "setupPathPreview"):
            preview.setStyleSheet(preview_style)


class _PrefixLineEdit(QLineEdit):
    """Path editor that shows the prefix once, then leaves the caret alone."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._homed = False

    def showEvent(self, event) -> None:  # noqa: N802 — Qt override
        super().showEvent(event)
        if not self._homed:
            self.setCursorPosition(0)
            self._homed = True


class _WrappingPreview(QLabel):
    """Full-path preview that wraps instead of forcing the card wider."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802 — Qt override
        return QSize(0, super().minimumSizeHint().height())


def _path_host(editor: QLineEdit) -> QWidget:
    host = QWidget()
    host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    preview = _WrappingPreview(editor.text())
    preview.setObjectName("setupPathPreview")
    preview.setWordWrap(True)
    preview.setTextFormat(Qt.TextFormat.PlainText)
    preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    preview.setVisible(bool(editor.text()))
    editor.setAccessibleDescription(editor.text())
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.SPACE_XS)
    layout.addWidget(editor)
    layout.addWidget(preview)

    def sync(text: str) -> None:
        preview.setText(text)
        preview.setVisible(bool(text))
        editor.setAccessibleDescription(text)
        preview.updateGeometry()
        layout.invalidate()
        layout.activate()

    editor.textChanged.connect(sync)
    return host


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
