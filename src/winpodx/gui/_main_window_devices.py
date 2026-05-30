# SPDX-License-Identifier: MIT
"""Devices-tab mixin for ``WinpodxWindow`` (#286).

A two-column host<->guest device mover: the left column lists host USB / PCI
devices not yet assigned, the right column lists those passed through to the
guest. "Attach ->" moves a device to the guest; "<- Detach" releases it.

Safety: USB is low-risk and (when the guest is running with QMP) hot-plugs
live. PCI passthrough binds the device to vfio-pci, unbinding it from its host
driver and pulling its whole IOMMU group — so a risky PCI attach pops a
confirmation dialog listing the reasons (the GUI equivalent of the CLI's
``--force``) and always needs a guest restart to take effect.

Reuses the CLI orchestration glue (`cli.device._enumerate_host`,
`_guest_running`) and the core primitives in ``core.devices`` so the GUI and
CLI never drift.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core import devices as D
from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.gui.theme import C


class DevicesMixin:
    """Devices-tab behavior. Mix into ``WinpodxWindow``."""

    def _build_devices_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(12)

        title = QLabel(tr("Devices"))
        title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {C.TEXT};")
        outer.addWidget(title)

        subtitle = QLabel(
            tr(
                "Pass host USB / PCI devices through to the Windows guest. "
                "USB hot-plugs live; PCI needs a guest restart and confirmation."
            )
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 12px;")
        outer.addWidget(subtitle)

        bar = QHBoxLayout()
        refresh = QPushButton(tr("Refresh"))
        refresh.clicked.connect(self._render_devices)
        bar.addWidget(refresh)
        self._devices_status = QLabel("")
        self._devices_status.setStyleSheet(f"color: {C.SUBTEXT1}; font-size: 12px;")
        bar.addWidget(self._devices_status, 1)
        outer.addLayout(bar)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        self._dev_host_col, host_card = self._device_column(tr("Host devices"))
        self._dev_guest_col, guest_card = self._device_column(tr("Assigned to guest"))
        columns.addWidget(host_card, 1)
        columns.addWidget(guest_card, 1)
        outer.addLayout(columns, 1)

        self._render_devices()
        return page

    def _device_column(self, heading: str) -> tuple[QVBoxLayout, QWidget]:
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background: {C.SURFACE0}; border-radius: 12px; }}")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        head = QLabel(heading)
        head.setStyleSheet(f"font-weight: 600; color: {C.TEXT}; font-size: 14px;")
        lay.addWidget(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        return col, card

    # -- rendering --------------------------------------------------------

    def _clear_column(self, col: QVBoxLayout) -> None:
        while col.count():
            item = col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _render_devices(self) -> None:
        from winpodx.cli.device import _enumerate_host, _guest_running

        cfg = Config.load()
        assigned = {d.key: d for d in D.parse_entries(cfg.pod.devices)}
        hosts = _enumerate_host()
        host_by_key = {h.to_device_config().key: h for h in hosts}
        running = _guest_running(cfg)

        self._clear_column(self._dev_host_col)
        self._clear_column(self._dev_guest_col)

        # Left: host devices not currently assigned.
        n_host = 0
        for h in hosts:
            if h.to_device_config().key in assigned:
                continue
            self._dev_host_col.addWidget(self._device_row(h, assigned=False))
            n_host += 1
        if n_host == 0:
            self._dev_host_col.addWidget(self._empty_label(tr("No unassigned devices.")))
        self._dev_host_col.addStretch(1)

        # Right: assigned devices (use the live host entry when present so the
        # safety badge + label stay accurate, else reconstruct from config).
        if not assigned:
            self._dev_guest_col.addWidget(self._empty_label(tr("Nothing assigned yet.")))
        for key, dc in assigned.items():
            host = host_by_key.get(key) or D.HostDevice(dtype=dc.dtype, did=dc.did, label=dc.label)
            self._dev_guest_col.addWidget(self._device_row(host, assigned=True))
        self._dev_guest_col.addStretch(1)

        self._devices_status.setText(tr("Guest running: ") + (tr("yes") if running else tr("no")))

    def _empty_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 12px; padding: 6px;")
        return lbl

    def _device_row(self, host: D.HostDevice, *, assigned: bool) -> QWidget:
        safety = D.classify_safety(host)
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background: {C.SURFACE1}; border-radius: 8px; }}")
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(8)

        badge = QLabel(host.dtype.upper())
        badge_color = C.GREEN if safety.safe else C.RED
        badge.setStyleSheet(
            f"background: {badge_color}; color: #0d1117; border-radius: 4px;"
            " padding: 1px 6px; font-size: 11px; font-weight: 700;"
        )
        badge.setAlignment(Qt.AlignCenter)
        h.addWidget(badge)

        text = QLabel(f"{host.did}\n{host.label[:40] or tr('(unknown)')}")
        text.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
        h.addWidget(text, 1)

        if assigned:
            btn = QPushButton(tr("← Detach"))
            btn.clicked.connect(lambda _=False, dev=host: self._on_detach(dev))
        else:
            btn = QPushButton(tr("Attach →"))
            btn.clicked.connect(lambda _=False, dev=host: self._on_attach(dev))
        h.addWidget(btn)
        return row

    # -- actions ----------------------------------------------------------

    def _on_attach(self, host: D.HostDevice) -> None:
        from winpodx.cli.device import _guest_running

        dc = host.to_device_config()
        safety = D.classify_safety(host)
        if not safety.safe:
            reasons = "\n".join(f"• {r}" for r in safety.reasons)
            resp = QMessageBox.warning(
                self,
                tr("Risky passthrough"),
                tr("Passing this PCI device to the guest is risky:\n\n")
                + reasons
                + tr(
                    "\n\nThe whole IOMMU group moves together and the host"
                    " loses the device. Continue?"
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return

        cfg = Config.load()
        if dc.key in {d.key for d in D.parse_entries(cfg.pod.devices)}:
            return
        cfg.pod.devices = list(cfg.pod.devices) + [dc.to_entry()]
        cfg.pod.__post_init__()
        cfg.save()

        msg = tr("Assigned ") + f"{dc.dtype} {dc.did}. "
        if dc.dtype == "usb":
            if not getattr(cfg.pod, "usb_live", True):
                msg += tr("Enable usb_live + recreate to hot-plug USB.")
            elif _guest_running(cfg):
                try:
                    D.live_attach(cfg.pod.backend, cfg.pod.container_name, dc)
                    msg += tr("Hot-plugged live.")
                except D.HmpError as e:
                    msg += tr("Live attach failed.")
                    # Surface the failure loudly — don't bury it in the status line.
                    QMessageBox.critical(
                        self,
                        tr("USB hot-plug failed"),
                        tr("Couldn't hot-plug ") + f"{dc.did}:\n\n{e}",
                    )
            else:
                msg += tr("Applies when the guest is running.")
        else:
            msg += tr("Restart the pod to apply (winpodx pod recreate).")
        self._render_devices()
        self._devices_status.setText(msg)

    def _on_detach(self, host: D.HostDevice) -> None:
        from winpodx.cli.device import _guest_running

        dc = host.to_device_config()
        cfg = Config.load()
        cfg.pod.devices = [
            e for e in cfg.pod.devices if (p := D.parse_entry(e)) is None or p.key != dc.key
        ]
        cfg.pod.__post_init__()
        cfg.save()

        msg = tr("Released ") + f"{dc.dtype} {dc.did}. "
        if dc.dtype == "usb":
            if getattr(cfg.pod, "usb_live", True) and _guest_running(cfg):
                try:
                    D.live_detach(cfg.pod.backend, cfg.pod.container_name, dc)
                    msg += tr("Unplugged live.")
                except D.HmpError as e:
                    msg += tr("Live detach failed.")
                    QMessageBox.critical(
                        self,
                        tr("USB unplug failed"),
                        tr("Couldn't unplug ") + f"{dc.did}:\n\n{e}",
                    )
        else:
            msg += tr("Restart the pod to apply.")
        self._render_devices()
        self._devices_status.setText(msg)
