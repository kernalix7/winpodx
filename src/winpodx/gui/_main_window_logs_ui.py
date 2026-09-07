# SPDX-License-Identifier: MIT
"""Logs-page chrome: toolbar, mantle viewer card, pinned command bar."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui._main_window_logs_toolbar import build_logs_toolbar, restyle_logs_toolbar
from winpodx.gui._main_window_secondary_style import apply_w11_button, mount_settings_column
from winpodx.gui.icons import load_icon


def viewer_card_qss() -> str:
    """Flat mantle SettingsCard surface for the log / license viewers."""
    return (
        f"QFrame#settingsCard {{ background: {theme_mod.C.MANTLE}; "
        f"border: {theme_mod.CARD_BORDER}; border-radius: {theme_mod.RADIUS_L}px; }}"
    )


def log_viewer_qss() -> str:
    """Borderless mono viewer; padding lives on the wrapping card."""
    token = getattr(theme_mod, "LOG_VIEWER", "")
    return token or (
        f"QTextEdit {{ background: {theme_mod.C.MANTLE}; color: {theme_mod.C.TEXT}; "
        f"font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace; "
        f"font-size: 12px; border: none; padding: 0; "
        f"selection-background-color: {theme_mod.C.SURFACE1}; }}"
    )


def style_log_viewer(viewer: QTextEdit) -> None:
    """Wrap lines, hide the horizontal bar, paint the mantle token."""
    viewer.setReadOnly(True)
    viewer.setStyleSheet(log_viewer_qss())
    viewer.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
    viewer.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    cell = viewer.fontMetrics().horizontalAdvance("0") or 8
    viewer.setMinimumWidth(cell * 40)


def _mount_command_history(host, cmd_input: QLineEdit) -> None:
    from PySide6.QtCore import QStringListModel

    host._cmd_history = []
    host._cmd_history_model = QStringListModel(host._cmd_history)
    completer = QCompleter(host._cmd_history_model, cmd_input)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    cmd_input.setCompleter(completer)


def build_logs_page(host) -> QWidget:
    """Assemble the Terminal page onto ``host`` (a ``LogsMixin``)."""
    page = QWidget()
    layout = mount_settings_column(page, scrolled=False)
    layout.setSpacing(theme_mod.SPACE_M)

    register = getattr(host, "_register_page_header", None)
    if callable(register):
        register(4, tr("Terminal"))
    layout.addWidget(build_logs_toolbar(host))

    host.log_output = QTextEdit()
    style_log_viewer(host.log_output)
    term_card = QFrame()
    term_card.setObjectName("settingsCard")
    term_card.setStyleSheet(viewer_card_qss())
    term_l = QVBoxLayout(term_card)
    term_l.setContentsMargins(
        theme_mod.SPACE_L, theme_mod.SPACE_L, theme_mod.SPACE_L, theme_mod.SPACE_L
    )
    term_l.addWidget(host.log_output)
    host._logs_viewer_card = term_card
    layout.addWidget(term_card, 1)

    cmd_bar = QWidget()
    cmd_bar.setObjectName("logsCommandBar")
    cmd_row = QHBoxLayout(cmd_bar)
    cmd_row.setContentsMargins(0, 0, 0, 0)
    cmd_row.setSpacing(theme_mod.SPACE_S)

    host.cmd_input = QLineEdit()
    host._logs_prompt = host.cmd_input.addAction(
        load_icon("prompt", theme_mod.C.BLUE, 16), QLineEdit.ActionPosition.LeadingPosition
    )
    host.cmd_input.setPlaceholderText(
        tr("Enter command (e.g. podman logs {container})").format(
            container=host.cfg.pod.container_name
        )
    )
    host.cmd_input.setStyleSheet(theme_mod.INPUT)
    host.cmd_input.setMinimumHeight(theme_mod.CONTROL_HEIGHT_W11)
    host.cmd_input.returnPressed.connect(host._on_cmd_enter)
    _mount_command_history(host, host.cmd_input)
    cmd_row.addWidget(host.cmd_input)

    run_btn = QPushButton(tr("Run"))
    apply_w11_button(run_btn, theme_mod.BTN_PRIMARY, role="primary")
    run_btn.clicked.connect(host._on_cmd_enter)
    host._logs_run_btn = run_btn
    cmd_row.addWidget(run_btn)

    layout.addWidget(cmd_bar)
    page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    host._logs_page = page
    return page


def restyle_logs(host) -> None:
    """Re-apply Fluent tokens after ``theme.rebuild``."""
    from winpodx.gui._main_window_secondary_style import restyle_settings_cards

    root = getattr(host, "_logs_page", None) or getattr(host, "page", None)
    if root is not None:
        restyle_settings_cards(root)
    card = getattr(host, "_logs_viewer_card", None)
    if card is not None:
        card.setStyleSheet(viewer_card_qss())
    output = getattr(host, "log_output", None)
    if output is not None:
        output.setStyleSheet(log_viewer_qss())
    cmd = getattr(host, "cmd_input", None)
    if cmd is not None:
        cmd.setStyleSheet(theme_mod.INPUT)
        cmd.setMinimumHeight(theme_mod.CONTROL_HEIGHT_W11)
    run = getattr(host, "_logs_run_btn", None)
    if run is not None:
        apply_w11_button(run, theme_mod.BTN_PRIMARY, role="primary")
    prompt = getattr(host, "_logs_prompt", None)
    if prompt is not None:
        prompt.setIcon(load_icon("prompt", theme_mod.C.BLUE, 16))
    restyle_logs_toolbar(host)
