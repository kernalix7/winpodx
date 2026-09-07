# SPDX-License-Identifier: MIT
"""Terminal toolbar: level combo, segmented source, ghost actions."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui._main_window_secondary_style import apply_w11_button
from winpodx.gui.icons import load_icon

_GHOST_ICONS: dict[str, str] = {
    "Status": "session",
    "Inspect": "refresh",
    "RDP Test": "rdp",
    "Clear": "close",
}
_SOURCE_LABELS: tuple[str, ...] = ("Pod logs", "App log")
_GHOST_LABELS: tuple[str, ...] = ("Status", "Inspect", "RDP Test", "Clear")


def tint_log_action(btn: QPushButton, icon_name: str) -> None:
    """Re-tint a 32px ghost icon button from live theme tokens."""
    btn.setIcon(load_icon(icon_name, theme_mod.C.SUBTEXT1, 16))
    apply_w11_button(btn, theme_mod.BTN_GHOST, role="ghost")


def make_log_action_button(label: str, tooltip: str) -> QPushButton:
    """Icon-only 32px ghost used on the Terminal toolbar."""
    btn = QPushButton()
    btn.setAccessibleName(tr(label))
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedSize(theme_mod.CONTROL_HEIGHT_W11, theme_mod.CONTROL_HEIGHT_W11)
    btn.setIconSize(QSize(16, 16))
    icon_name = _GHOST_ICONS.get(label, "pending")
    btn.setProperty("iconName", icon_name)
    tint_log_action(btn, icon_name)
    return btn


def _wire_command(host, label: str, cmd, btn: QPushButton) -> None:
    if label == "Clear":
        btn.clicked.connect(lambda: host.log_output.clear())
    elif label == "RDP Test":
        btn.clicked.connect(host._on_rdp_test)
    elif cmd == "tail_app_log":
        btn.clicked.connect(host._on_tail_app_log)
    else:
        btn.clicked.connect(lambda _, c=cmd: host._run_log_cmd(c))


def _make_source_button(label: str) -> QPushButton:
    btn = QPushButton(tr(label))
    btn.setAccessibleName(tr(label))
    btn.setCheckable(True)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setFixedHeight(theme_mod.CONTROL_HEIGHT_W11)
    btn.setStyleSheet(theme_mod.BTN_GHOST)
    btn.setProperty("w11Role", "ghost")
    return btn


def _fill_level_combo(host) -> None:
    host.input_log_level = QComboBox()
    host.input_log_level.setStyleSheet(theme_mod.COMBO)
    host.input_log_level.setMinimumHeight(theme_mod.CONTROL_HEIGHT_W11)
    host.input_log_level.setMinimumWidth(120)
    for value in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "RAW"):
        host.input_log_level.addItem(value, value)
        if value == "RAW":
            host.input_log_level.setItemData(
                host.input_log_level.count() - 1,
                tr(
                    "RAW = DEBUG plus a live tail of the container's logs "
                    "(podman logs -f). Use when triaging boot / QEMU issues."
                ),
                Qt.ItemDataRole.ToolTipRole,
            )
    idx = host.input_log_level.findData(host.cfg.logging.level)
    if idx >= 0:
        host.input_log_level.setCurrentIndex(idx)
    host.input_log_level.setToolTip(
        tr(
            "Set the WinPodX logger level. Lower (DEBUG) shows more\n"
            "detail in the log file + this terminal; higher (ERROR)\n"
            "shows only errors. Change persists to winpodx.toml so\n"
            "future CLI / GUI runs honour the choice. Applied live —\n"
            "no WinPodX restart needed."
        )
    )
    host.input_log_level.currentIndexChanged.connect(host._on_log_level_changed)


def build_logs_toolbar(host) -> QWidget:
    """Assemble the 32px Terminal toolbar onto ``host``."""
    actions = QWidget()
    actions.setObjectName("logsToolbar")
    actions_l = QHBoxLayout(actions)
    actions_l.setContentsMargins(0, 0, 0, 0)
    actions_l.setSpacing(theme_mod.SPACE_XS)

    level_label = QLabel(tr("Log level:"))
    level_label.setStyleSheet(
        f"background: transparent; color: {theme_mod.C.SUBTEXT0}; "
        f"font-size: {theme_mod.FONT_CAPTION}px;"
    )
    _fill_level_combo(host)
    level_pair = QWidget()
    level_pair_layout = QHBoxLayout(level_pair)
    level_pair_layout.setContentsMargins(0, 0, 0, 0)
    level_pair_layout.setSpacing(theme_mod.SPACE_XS)
    level_pair_layout.addWidget(level_label)
    level_pair_layout.addWidget(host.input_log_level)
    actions_l.addWidget(level_pair)

    by_label = {label: cmd for label, cmd in host._diagnostic_commands()}
    source_wrap = QWidget()
    source_wrap.setObjectName("logsSource")
    src_l = QHBoxLayout(source_wrap)
    src_l.setContentsMargins(0, 0, 0, 0)
    src_l.setSpacing(0)
    group = QButtonGroup(source_wrap)
    group.setExclusive(True)
    host._logs_source_group = group
    host._logs_source_buttons = []
    special_tips = {
        "App log": tr("Show the tail of WinPodX's own log file"),
        "RDP Test": tr("Probe the RDP port (TCP handshake) for the configured guest"),
        "Clear": tr("Clear this terminal view"),
    }
    for label in _SOURCE_LABELS:
        cmd = by_label[label]
        btn = _make_source_button(label)
        if isinstance(cmd, list):
            btn.setToolTip(tr("Runs: {cmd}").format(cmd=" ".join(cmd)))
        else:
            btn.setToolTip(special_tips.get(label, tr(label)))
        _wire_command(host, label, cmd, btn)
        group.addButton(btn)
        src_l.addWidget(btn)
        host._logs_source_buttons.append(btn)
    actions_l.addWidget(source_wrap)

    host._logs_action_buttons = []
    for label in _GHOST_LABELS:
        cmd = by_label[label]
        if isinstance(cmd, list):
            tooltip = tr("Runs: {cmd}").format(cmd=" ".join(cmd))
        else:
            tooltip = special_tips.get(label, tr(label))
        btn = make_log_action_button(label, tooltip)
        _wire_command(host, label, cmd, btn)
        actions_l.addWidget(btn)
        host._logs_action_buttons.append(btn)
    actions_l.addStretch(1)
    return actions


def restyle_logs_toolbar(host) -> None:
    """Re-apply Fluent tokens on the Terminal toolbar after ``theme.rebuild``."""
    level = getattr(host, "input_log_level", None)
    if level is not None:
        level.setStyleSheet(theme_mod.COMBO)
        level.setMinimumHeight(theme_mod.CONTROL_HEIGHT_W11)
    for btn in getattr(host, "_logs_source_buttons", []):
        btn.setStyleSheet(theme_mod.BTN_GHOST)
        btn.setFixedHeight(theme_mod.CONTROL_HEIGHT_W11)
    for btn in getattr(host, "_logs_action_buttons", []):
        name = btn.property("iconName") or "pending"
        tint_log_action(btn, name)
