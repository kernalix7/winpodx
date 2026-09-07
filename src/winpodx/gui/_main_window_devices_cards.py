# SPDX-License-Identifier: MIT
"""Devices-page SettingsCard group/row builders for ``DevicesMixin``."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.core import devices as D
from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui._main_window_secondary_style import (
    apply_w11_button,
    make_ghost_button,
    make_named_settings_group,
)
from winpodx.gui._settings_card import make_settings_card
from winpodx.gui._widget_helpers import make_empty_panel
from winpodx.gui.icons import load_icon


class DevicesCardsMixin:
    """Win11 SettingsCard groups and 48px device rows."""

    def _device_column(
        self,
        heading: str,
        *,
        refresh: bool = False,
        status: bool = False,
    ) -> tuple[QVBoxLayout, QWidget]:
        holder, stack = make_named_settings_group()
        chrome = holder.layout()
        head = QWidget()
        row = QHBoxLayout(head)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme_mod.SPACE_S)
        title = QLabel(heading)
        title.setObjectName("settingsGroupTitle")
        title.setStyleSheet(
            f"background: transparent; color: {theme_mod.C.TEXT}; "
            f"font-size: {theme_mod.FONT_SUBHEAD}px; font-weight: 600;"
        )
        row.addWidget(title)
        row.addStretch()
        if refresh:
            btn = make_ghost_button(tr("Refresh"), icon="refresh")
            btn.clicked.connect(self._render_devices)
            row.addWidget(btn)
        elif status:
            status_lbl = getattr(self, "_devices_status", None)
            if status_lbl is not None:
                row.addWidget(status_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        chrome.insertWidget(0, head)

        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme_mod.SPACE_XS)
        col.addStretch(1)
        stack.addWidget(inner)
        return col, holder

    def _empty_label(self, text: str) -> QWidget:
        return make_empty_panel(text)

    def _set_group_count(self, group: QWidget, base: str, n: int) -> None:
        for label in group.findChildren(QLabel):
            if label.objectName() == "settingsGroupTitle":
                label.setText(f"{base} · {n}")
                return

    def _device_row(self, host: D.HostDevice, *, assigned: bool) -> QWidget:
        safety = D.classify_safety(host)
        full_label = host.label or tr("(unknown)")
        meta_parts = [host.did]
        if host.dtype == "usb":
            if host.bus:
                meta_parts.append(tr("Bus {bus}").format(bus=host.bus))
        else:
            if host.iommu_group is not None:
                meta_parts.append(tr("IOMMU {group}").format(group=host.iommu_group))
            if host.pci_class:
                meta_parts.append(tr(D.pci_class_name(host.pci_class)))
        metadata = " · ".join(meta_parts)
        if not safety.safe:
            metadata = f"{metadata} · {tr('Restart the pod to apply.')}"

        if assigned:
            btn = QPushButton(tr("← Detach"))
            btn.clicked.connect(lambda _=False, dev=host: self._on_detach(dev))
        else:
            btn = QPushButton(tr("Attach →"))
            btn.clicked.connect(lambda _=False, dev=host: self._on_attach(dev))
        apply_w11_button(btn, theme_mod.BTN_SECONDARY, role="secondary")

        icon_name = "usb" if host.dtype == "usb" else "hardware"
        card = make_settings_card(
            icon_name,
            full_label,
            metadata,
            action=btn,
            compact=True,
            object_name="deviceCard",
        )
        card.setToolTip(f"{full_label}\n{metadata}")
        if not safety.safe:
            warn = QLabel()
            warn.setObjectName("riskGlyph")
            warn.setFixedSize(16, 16)
            warn.setPixmap(load_icon("warning", theme_mod.C.YELLOW, 16).pixmap(16, 16))
            warn.setToolTip(
                tr("Why this is flagged:\n") + "\n".join(f"• {r}" for r in safety.reasons)
            )
            warn.setStyleSheet("background: transparent;")
            card.row_layout.insertWidget(1, warn, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _style_devices_status(self, label: QLabel) -> None:
        label.setStyleSheet(
            f"color: {theme_mod.C.SUBTEXT1}; font-size: {theme_mod.FONT_CAPTION}px; "
            f"font-weight: 400; background: transparent;"
        )
