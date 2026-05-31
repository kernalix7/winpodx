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

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
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


class _LiveOpSignals(QObject):
    """Carries a background device op's result back to the GUI thread."""

    done = Signal(str)  # "" on success, else the error message


class _LiveOp(QRunnable):
    """Runs a slow live attach/detach off the Qt main thread so the window
    doesn't freeze during HMP + relay setup + the pkexec prompt (~tens of s)."""

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn
        self.signals = _LiveOpSignals()

    def run(self) -> None:  # executed on a QThreadPool worker thread
        try:
            self._fn()
            self.signals.done.emit("")
        except Exception as e:  # noqa: BLE001 — marshalled to the GUI thread
            self.signals.done.emit(str(e))


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

    def _run_live_op(self, fn, *, busy: str, ok: str, fail_title: str, did: str) -> None:
        """Run a slow live attach/detach (``fn``) on a worker thread so the GUI
        stays responsive (no freeze during HMP + relay + the pkexec prompt).

        Shows *busy* while it runs, then *ok* on success or a critical dialog on
        failure. Ignores new clicks while one op is in flight. The result is
        delivered to ``_on_live_op_finished`` — a bound method of the window
        (a QObject in the GUI thread), so Qt marshals it back via a queued
        connection rather than touching widgets from the worker thread.
        """
        if getattr(self, "_dev_busy", False):
            return
        self._dev_busy = True
        self._dev_op_ctx = (ok, fail_title, did)
        self._devices_status.setText(busy)
        op = _LiveOp(fn)
        op.signals.done.connect(self._on_live_op_finished)
        self._dev_op = op  # keep a reference so it isn't garbage-collected
        QThreadPool.globalInstance().start(op)

    def _on_live_op_finished(self, err: str) -> None:
        ok, fail_title, did = getattr(self, "_dev_op_ctx", ("", tr("Device op failed"), ""))
        self._dev_busy = False
        self._dev_op = None
        self._render_devices()
        if err:
            self._devices_status.setText(tr("Failed: ") + did)
            QMessageBox.critical(self, fail_title, f"{did}:\n\n{err}")
        else:
            self._devices_status.setText(ok)

    def _on_attach(self, host: D.HostDevice) -> None:
        from winpodx.cli.device import _guest_running

        if getattr(self, "_dev_busy", False):
            return
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
        self._render_devices()  # reflect the assignment immediately

        if dc.dtype == "usb":
            if _guest_running(cfg):
                self._run_live_op(
                    lambda: D.live_attach(cfg.pod.backend, cfg.pod.container_name, dc),
                    busy=tr("Live-attaching ")
                    + f"{dc.did} … "
                    + tr("(you may be prompted for your password)"),
                    ok=tr("Hot-plugged live: ") + dc.did,
                    fail_title=tr("USB hot-plug failed"),
                    did=dc.did,
                )
            else:
                self._devices_status.setText(
                    tr("Assigned ") + f"{dc.did}. " + tr("Applies when the guest is running.")
                )
        else:
            self._devices_status.setText(
                tr("Assigned ") + f"{dc.did}. " + tr("Restart the pod to apply (pod recreate).")
            )

    def _on_detach(self, host: D.HostDevice) -> None:
        from winpodx.cli.device import _guest_running

        if getattr(self, "_dev_busy", False):
            return
        dc = host.to_device_config()
        cfg = Config.load()
        cfg.pod.devices = [
            e for e in cfg.pod.devices if (p := D.parse_entry(e)) is None or p.key != dc.key
        ]
        cfg.pod.__post_init__()
        cfg.save()
        self._render_devices()

        if dc.dtype == "usb":
            if _guest_running(cfg):
                self._run_live_op(
                    lambda: D.live_detach(cfg.pod.backend, cfg.pod.container_name, dc),
                    busy=tr("Unplugging ") + f"{dc.did} …",
                    ok=tr("Unplugged live: ") + dc.did,
                    fail_title=tr("USB unplug failed"),
                    did=dc.did,
                )
            else:
                self._devices_status.setText(tr("Released ") + dc.did + ".")
        else:
            self._devices_status.setText(
                tr("Released ") + f"{dc.did}. " + tr("Restart the pod to apply.")
            )
