# SPDX-License-Identifier: MIT
"""Dashboard-local surfaces: status hero, settings recovery row, reverse-open."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui._main_window_navpane import _app_icon_pixmap
from winpodx.gui._ring_gauge import RingGauge
from winpodx.gui._widget_helpers import make_settings_card, make_settings_group, make_toggle_switch
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    BTN_GHOST,
    BTN_PRIMARY,
    CONTROL_HEIGHT_W11,
    FONT_BODY,
    FONT_CAPTION,
    FONT_TITLE,
    HIT_TARGET,
    RADIUS_S,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    SPACE_XS,
    C,
)

_RECOVERY_TEXT = {
    "running": "Protected — monitoring active",
    "checking": "Checking pod health…",
    "paused": "Pod is paused",
    "stopped": "Pod is stopped",
    "unknown": "Status unknown",
}

_QUICK_ACTIONS: tuple[tuple[str, str, str, int | None], ...] = (
    ("Full Desktop", "desktop", "_on_open_desktop", None),
    ("Refresh Apps", "refresh", "_on_refresh_apps", None),
    ("Terminal / Logs", "prompt", "_switch_page", 4),
    ("Settings", "gear", "_switch_page", 2),
)


def _apply_settings_card(frame: QFrame) -> QFrame:
    from winpodx.gui import theme as theme_mod

    name = frame.objectName() or "settingsCard"
    frame.setStyleSheet(theme_mod.SETTINGS_CARD.replace("QFrame#settingsCard", f"QFrame#{name}"))
    return frame


class _DashboardSurfacesMixin:
    """Named Dashboard surfaces: hero, settings recovery row, reverse-open row."""

    def _finish_dash_surface(self, frame: QFrame) -> None:
        _apply_settings_card(frame)

    def _build_status_hero(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("podStatusHero")
        _apply_settings_card(hero)
        outer = QVBoxLayout(hero)
        outer.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        outer.setSpacing(SPACE_M)

        left = QWidget()
        left_l = QHBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.setSpacing(SPACE_M)

        device = QLabel()
        device.setObjectName("deviceIcon")
        device.setFixedSize(56, 56)
        pixmap = _app_icon_pixmap(56)
        if pixmap is not None:
            device.setPixmap(pixmap)
        self._device_icon = device
        left_l.addWidget(device, 0, Qt.AlignmentFlag.AlignTop)

        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(SPACE_XS)
        pod_name = QLabel("WinPodX")
        pod_name.setStyleSheet(f"color: {C.TEXT}; font-size: {FONT_BODY}px; font-weight: 600;")
        self._pod_name_label = pod_name
        names.addWidget(pod_name)

        status = QHBoxLayout()
        status.setSpacing(SPACE_S)
        icon = QLabel()
        icon.setFixedSize(24, 24)
        icon.setPixmap(load_icon("warning", C.YELLOW, 24).pixmap(24, 24))
        self._pod_status_icon = icon
        status.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(SPACE_XS)
        label = QLabel(tr("Unknown"))
        label.setObjectName("podStatusLabel")
        label.setStyleSheet(f"color: {C.TEXT}; font-size: {FONT_TITLE}px; font-weight: 600;")
        detail = QLabel(tr("Status unknown"))
        detail.setObjectName("podStatusDetail")
        detail.setWordWrap(True)
        detail.setStyleSheet(f"color: {C.SUBTEXT1}; font-size: {FONT_CAPTION}px;")
        self._pod_status_label = label
        self._pod_status_detail = detail
        text.addWidget(label)
        text.addWidget(detail)
        status.addLayout(text, 1)
        names.addLayout(status)

        action = QPushButton(tr("Start Pod"))
        action.setObjectName("podPrimaryAction")
        action.setStyleSheet(
            BTN_PRIMARY
            + f" QPushButton {{ border-radius: {RADIUS_S}px; min-height: {HIT_TARGET}px; }}"
        )
        action.setMinimumHeight(HIT_TARGET)
        action.setEnabled(False)
        self._pod_primary_action = action
        self._hero_pod_state = "unknown"
        names.addWidget(action, 0, Qt.AlignmentFlag.AlignLeft)
        names.addStretch(1)
        left_l.addLayout(names, 1)

        cluster = QFrame()
        cluster.setObjectName("podMetricsCluster")
        cluster.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
        metrics = QBoxLayout(QBoxLayout.Direction.LeftToRight, cluster)
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics.setSpacing(SPACE_XL)
        metrics.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._metrics_layout = metrics
        self._metrics_cluster = cluster
        self._bar_ram = RingGauge(tr("RAM"), C.BLUE, cluster)
        self._bar_cpu = RingGauge(tr("CPU"), C.BLUE, cluster)
        self._bar_disk = RingGauge(
            tr("Disk C:"),
            C.BLUE,
            cluster,
            critical_color=C.RED,
            critical_pct=float(self.cfg.pod.disk_autogrow_threshold_pct),
        )
        metrics.addWidget(self._bar_ram)
        metrics.addWidget(self._bar_cpu)
        metrics.addWidget(self._bar_disk)

        row = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        row.setSpacing(SPACE_M)
        row.addWidget(left, 1)
        row.addWidget(cluster, 0, Qt.AlignmentFlag.AlignVCenter)
        self._dashboard_hero_layout = row
        self._dashboard_row1 = row
        outer.addLayout(row, 1)
        return hero

    def _build_settings_actions(self) -> QFrame:
        group, cards = make_settings_group()
        group.setObjectName("settingsActionList")
        status = QWidget()
        status_l = QHBoxLayout(status)
        status_l.setContentsMargins(0, 0, 0, 0)
        status_l.setSpacing(SPACE_S)
        self._recovery_icon = QLabel()
        self._recovery_icon.setFixedSize(18, 18)
        self._recovery_label = QLabel(tr("Monitoring"))
        self._recovery_label.setWordWrap(True)
        self._recovery_label.setStyleSheet(
            f"color: {C.SUBTEXT1}; font-size: {FONT_BODY}px; font-weight: 500;"
        )
        status_l.addWidget(self._recovery_icon)
        status_l.addWidget(self._recovery_label, 1)
        row = make_settings_card(
            "refresh",
            tr("Auto-recovery"),
            "",
            action=status,
            object_name="settingsActionRow",
            compact=True,
        )
        row.setFixedHeight(48)
        self._settings_row_layout = row.layout()
        cards.addWidget(row)
        return group

    def _build_reverse_open_card(self) -> QFrame:
        try:
            enabled = bool(self.cfg.reverse_open.enabled)
        except AttributeError:
            enabled = False
        toggle = make_toggle_switch(checked=enabled)
        toggle.setFixedHeight(HIT_TARGET)
        toggle.setFixedWidth(HIT_TARGET)
        toggle.toggled.connect(self._on_reverse_open_toggled)
        self._reverse_open_check = toggle
        card = make_settings_card(
            "reverse-associations",
            tr("Open Linux files in their matching Windows app"),
            tr("Right-click a file on Linux and send it to the Windows app registered for it."),
            action=toggle,
            object_name="reverseOpenRow",
        )
        self._reverse_open_layout = card.layout()
        return card

    def _build_quick_actions(self) -> QFrame:
        card = QFrame()
        card.setObjectName("quickActions")
        _apply_settings_card(card)
        row = QGridLayout(card)
        row.setContentsMargins(SPACE_L, SPACE_S, SPACE_L, SPACE_S)
        row.setSpacing(SPACE_S)
        self._quick_actions_layout = row
        self._quick_action_buttons: list[QPushButton] = []
        icons: list[str] = []
        for label, icon_name, handler, page in _QUICK_ACTIONS:
            btn = QPushButton(tr(label))
            btn.setIcon(load_icon(icon_name, C.TEXT, 16))
            btn.setIconSize(QSize(16, 16))
            btn.setStyleSheet(BTN_GHOST + "QPushButton { padding: 0px 8px; }")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setAccessibleName(tr(label))
            btn.setFixedHeight(CONTROL_HEIGHT_W11)
            target = getattr(self, handler, None)
            if callable(target) and page is None:
                btn.clicked.connect(target)
            elif callable(target):
                btn.clicked.connect(lambda _checked=False, fn=target, idx=page: fn(idx))
            self._quick_action_buttons.append(btn)
            icons.append(icon_name)
        self._quick_action_icons = icons
        self._reflow_quick_actions(compact=False)
        return card

    def _reflow_quick_actions(self, compact: bool) -> None:
        grid = getattr(self, "_quick_actions_layout", None)
        buttons = getattr(self, "_quick_action_buttons", ())
        if grid is None:
            return
        cols = 2 if compact else max(1, len(buttons))
        if grid.property("cols") == cols:
            return
        grid.setProperty("cols", cols)
        for btn in buttons:
            grid.removeWidget(btn)
        for i, btn in enumerate(buttons):
            grid.addWidget(btn, i // cols, i % cols)
        for c in range(max(cols, 4)):
            grid.setColumnStretch(c, 1 if c < cols else 0)

    def _apply_recovery_line(self, pod_state: str, icon_name: str, color: str) -> None:
        recovery_text = tr(_RECOVERY_TEXT.get(pod_state, _RECOVERY_TEXT["unknown"]))
        self._recovery_icon.setPixmap(load_icon(icon_name, color, 18).pixmap(18, 18))
        self._recovery_label.setText(recovery_text)
        self._recovery_label.setStyleSheet(
            f"color: {color}; font-size: {FONT_BODY}px; font-weight: 500;"
        )
