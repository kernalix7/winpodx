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
    QBoxLayout,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core import devices as D
from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui._main_window_devices_cards import DevicesCardsMixin
from winpodx.gui._main_window_secondary_style import (
    apply_w11_button,
    chevron_button_qss,
    make_ghost_button,
    mount_settings_column,
    restyle_settings_cards,
)
from winpodx.gui._widget_helpers import (
    make_warning_callout,
    show_toast,
)
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    BTN_DANGER,
    BTN_PRIMARY,
    BTN_SECONDARY,
    FONT_BODY,
    SCROLL_AREA,
    SPACE_L,
    SPACE_M,
    SPACE_XL,
    C,
)


class _HostColumnSeam:
    """``_dev_host_col.count()`` seam: USB+PCI rows plus one trailing stretch."""

    def __init__(self, *columns: QVBoxLayout) -> None:
        self._columns = columns

    def count(self) -> int:
        widgets = 0
        stretches = 0
        for col in self._columns:
            for i in range(col.count()):
                item = col.itemAt(i)
                if item is None:
                    continue
                if item.spacerItem() is not None:
                    stretches += 1
                    continue
                if item.widget() is not None:
                    widgets += 1
        return widgets + (1 if stretches else 0)

    def parentWidget(self) -> QWidget | None:
        return self._columns[0].parentWidget() if self._columns else None


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


class DevicesMixin(DevicesCardsMixin):
    """Devices-tab behavior. Mix into ``WinpodxWindow``."""

    def _build_devices_page(self) -> QWidget:
        page = QWidget()
        shell = QVBoxLayout(page)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(SCROLL_AREA)

        content = QWidget()
        outer = mount_settings_column(content)

        self._devices_status = QLabel("")
        self._style_devices_status(self._devices_status)

        refresh_btn = make_ghost_button(tr("Refresh"), icon="refresh")
        refresh_btn.clicked.connect(self._render_devices)
        self._devices_refresh_btn = refresh_btn
        register = getattr(self, "_register_page_header", None)
        if callable(register):
            register(
                6,
                tr("Devices"),
                tr(
                    "Pass host USB / PCI devices through to the Windows guest. "
                    "USB hot-plugs live; PCI needs a guest restart and confirmation."
                ),
                actions=refresh_btn,
            )
        else:
            refresh_btn.setParent(page)
            refresh_btn.hide()

        columns = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        columns.setSpacing(SPACE_XL)
        self._devices_cols = columns

        filt = QLineEdit()
        filt.setObjectName("navSearch")
        filt.setStyleSheet(
            theme_mod.NAV_SEARCH
            + f"\nQLineEdit#navSearch {{ background: {theme_mod.C.SURFACE0}; }}"
        )
        filt.setFixedHeight(theme_mod.CONTROL_HEIGHT_W11)
        filt.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        filt.addAction(
            load_icon("search", theme_mod.C.SUBTEXT1, 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        filt.setClearButtonEnabled(True)
        filt.textChanged.connect(self._apply_device_filter)
        self._devices_filter = filt
        columns.addWidget(filt)

        self._dev_usb_col, self._usb_group = self._device_column(tr("USB"))
        self._dev_pci_col, self._pci_group = self._device_column(tr("PCI"))
        self._dev_guest_col, self._guest_group = self._device_column(
            tr("Assigned to guest"), status=True
        )
        self._dev_host_col = _HostColumnSeam(self._dev_usb_col, self._dev_pci_col)
        columns.addWidget(self._usb_group)
        columns.addWidget(self._pci_group)
        columns.addWidget(self._guest_group)
        outer.addLayout(columns)

        self._render_devices()
        self._reflow_devices()
        scroll.setWidget(content)
        shell.addWidget(scroll)
        self._devices_page = page
        return page

    def _reflow_devices(self) -> None:
        """Keep Host / Guest groups in one left-anchored column.

        Called from the window resizeEvent. Idempotent; DESIGN.md §4 forbids
        the 50/50 split.
        """
        cols = getattr(self, "_devices_cols", None)
        if cols is None:
            return
        if cols.direction() != QBoxLayout.Direction.TopToBottom:
            cols.setDirection(QBoxLayout.Direction.TopToBottom)

    # -- rendering --------------------------------------------------------

    def _clear_column(self, col: QVBoxLayout) -> None:
        while col.count():
            item = col.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def _render_devices(self) -> None:
        from winpodx.cli.device import _enumerate_host, _guest_running

        cfg = Config.load()
        assigned = {d.key: d for d in D.parse_entries(cfg.pod.devices)}
        try:
            hosts = _enumerate_host()
        except Exception:  # noqa: BLE001 -- a device yanked mid-enumeration can
            # fail transiently (sysfs/lsusb race); skip this render rather than
            # propagate to the Qt slot. The next refresh / op re-renders cleanly.
            return
        host_by_key = {h.to_device_config().key: h for h in hosts}
        running = _guest_running(cfg)

        # Batch the repaint: a full clear + rebuild of both columns otherwise
        # paints the empty intermediate state, so the panel visibly flickers
        # whenever the list re-renders (e.g. a USB device yanked while open).
        _panes = []
        for col in (self._dev_usb_col, self._dev_pci_col, self._dev_guest_col):
            parent = col.parentWidget()
            if parent is not None:
                _panes.append(parent)
                parent.setUpdatesEnabled(False)

        self._clear_column(self._dev_usb_col)
        self._clear_column(self._dev_pci_col)
        self._clear_column(self._dev_guest_col)

        hosts = D.sort_host_devices(hosts)
        usb_hosts: list[D.HostDevice] = []
        pci_hosts: list[D.HostDevice] = []
        for h in hosts:
            if h.to_device_config().key in assigned:
                continue
            if h.dtype == "usb":
                usb_hosts.append(h)
            else:
                pci_hosts.append(h)
        for h in usb_hosts:
            self._dev_usb_col.addWidget(self._device_row(h, assigned=False))
        for h in pci_hosts:
            self._dev_pci_col.addWidget(self._device_row(h, assigned=False))
        if not usb_hosts and not pci_hosts:
            self._dev_usb_col.addWidget(self._empty_label(tr("No unassigned devices.")))
        self._dev_usb_col.addStretch(1)
        self._dev_pci_col.addStretch(1)

        guest_hosts = [
            host_by_key.get(key) or D.HostDevice(dtype=dc.dtype, did=dc.did, label=dc.label)
            for key, dc in assigned.items()
        ]
        guest_hosts = D.sort_host_devices(guest_hosts)
        if not assigned:
            self._dev_guest_col.addWidget(self._empty_label(tr("Nothing assigned yet.")))
        for host in guest_hosts:
            self._dev_guest_col.addWidget(self._device_row(host, assigned=True))
        self._dev_guest_col.addStretch(1)

        self._set_group_count(self._usb_group, tr("USB"), len(usb_hosts))
        self._set_group_count(self._pci_group, tr("PCI"), len(pci_hosts))
        self._set_group_count(self._guest_group, tr("Assigned to guest"), len(guest_hosts))

        total = len(usb_hosts) + len(pci_hosts) + len(guest_hosts)
        filt = getattr(self, "_devices_filter", None)
        if filt is not None:
            filt.setVisible(total > 8)
            self._apply_device_filter()
            parent = filt.parentWidget()
            if parent is not None:
                parent.updateGeometry()

        self._devices_status.setText(tr("Guest running: ") + (tr("yes") if running else tr("no")))

        for _p in _panes:
            if _p is not None:
                _p.setUpdatesEnabled(True)

    def _apply_device_filter(self, _text: str = "") -> None:
        filt = getattr(self, "_devices_filter", None)
        needle = (filt.text() if filt is not None else "").strip().lower()
        for col in (self._dev_usb_col, self._dev_pci_col, self._dev_guest_col):
            for i in range(col.count()):
                item = col.itemAt(i)
                if item is None:
                    continue
                widget = item.widget()
                if widget is None or widget.objectName() == "emptyState":
                    continue
                title = getattr(widget, "title_label", None)
                desc = getattr(widget, "desc_label", None)
                title_txt = title.text() if title is not None else ""
                desc_txt = desc.text() if desc is not None else ""
                hay = f"{title_txt} {desc_txt}".lower()
                widget.setVisible(not needle or needle in hay)

    def _restyle_devices(self) -> None:
        root = getattr(self, "_devices_page", None) or getattr(self, "_page", None)
        if root is None:
            return
        restyle_settings_cards(root)
        status = getattr(self, "_devices_status", None)
        if status is not None:
            self._style_devices_status(status)
        filt = getattr(self, "_devices_filter", None)
        if filt is not None:
            filt.setStyleSheet(
                theme_mod.NAV_SEARCH
                + f"\nQLineEdit#navSearch {{ background: {theme_mod.C.SURFACE0}; }}"
            )
            filt.setFixedHeight(theme_mod.CONTROL_HEIGHT_W11)
        for btn in root.findChildren(QPushButton):
            role = btn.property("w11Role") or "secondary"
            if role == "ghost":
                btn.setStyleSheet(chevron_button_qss())
                continue
            qss = BTN_SECONDARY
            if role == "danger":
                qss = BTN_DANGER
            elif role == "primary":
                qss = BTN_PRIMARY
            apply_w11_button(btn, qss, role=role)

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
        # Feedback is a NON-blocking toast, not a modal dialog: the live op
        # already runs off the UI thread (QThreadPool worker + queued result
        # signal, #414) and a modal exec() here would re-block the launch
        # path and defeat that. The toast tells the user it's working + why a
        # pkexec prompt may appear; the status label carries the running text.
        toast_parent = self.window() if hasattr(self, "window") else self
        show_toast(toast_parent, busy, kind="info")
        op = _LiveOp(fn)
        op.signals.done.connect(self._on_live_op_finished)
        self._dev_op = op  # keep a reference so it isn't garbage-collected
        QThreadPool.globalInstance().start(op)

    def _on_live_op_finished(self, err: str) -> None:
        ok, fail_title, did = getattr(self, "_dev_op_ctx", ("", tr("Device op failed"), ""))
        self._dev_busy = False
        self._dev_op = None
        self._render_devices()
        toast_parent = self.window() if hasattr(self, "window") else self
        if err:
            self._devices_status.setText(tr("Failed: ") + did)
            show_toast(toast_parent, tr("Failed: ") + did, kind="error")
            QMessageBox.critical(self, fail_title, f"{did}:\n\n{err}")
        else:
            self._devices_status.setText(ok)
            show_toast(toast_parent, ok, kind="success")

    def _iommu_siblings(self, host: D.HostDevice) -> list[D.HostDevice]:
        """Other host PCI devices sharing ``host``'s IOMMU group.

        Empty when the group is unknown or the device sits alone — used to
        name what the host gives up alongside the chosen device.
        """
        if host.dtype != "pci" or host.iommu_group is None:
            return []
        try:
            peers = D.list_host_pci()
        except Exception:  # noqa: BLE001
            return []
        return [p for p in peers if p.iommu_group == host.iommu_group and p.did != host.did]

    def _confirm_risky_pci(self, host: D.HostDevice, safety: D.Safety) -> bool:
        """Confirm a risky PCI passthrough with a plain-language warning.

        Surfaces the IOMMU reasons *and* a one-line plain explanation of the
        host-side cost, naming the sibling devices that move with the group.
        Reuses ``make_warning_callout`` so the danger reads the same as the
        rest of the GUI. Returns True only when the user confirms.
        """
        siblings = self._iommu_siblings(host)
        device_name = host.label or host.did
        if siblings:
            names = ", ".join(s.label or s.did for s in siblings)
            lost = tr("{device} (and {extra} in the same IOMMU group: {names})").format(
                device=device_name, extra=len(siblings), names=names
            )
        else:
            lost = device_name
        plain = tr(
            "Passing this device unbinds it from the host: the host will lose "
            "{lost} until you detach + restart."
        ).format(lost=lost)

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Risky passthrough"))
        dlg.setModal(True)
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        lay.setSpacing(SPACE_M)

        lay.addWidget(make_warning_callout(plain, level="danger"))

        reasons = QLabel(tr("Why this is flagged:\n") + "\n".join(f"• {r}" for r in safety.reasons))
        reasons.setWordWrap(True)
        reasons.setStyleSheet(
            f"color: {C.SUBTEXT1}; font-size: {FONT_BODY}px; background: transparent;"
        )
        lay.addWidget(reasons)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Pass through anyway"))
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        return dlg.exec() == QDialog.DialogCode.Accepted

    def _on_attach(self, host: D.HostDevice) -> None:
        from winpodx.cli.device import _guest_running

        if getattr(self, "_dev_busy", False):
            return
        dc = host.to_device_config()
        safety = D.classify_safety(host)
        if not safety.safe:
            if not self._confirm_risky_pci(host, safety):
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
                    busy=tr(
                        "Attaching {device} to the guest — you may be prompted for your password."
                    ).format(device=dc.did),
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
                    busy=tr(
                        "Detaching {device} from the guest — you may be prompted for your password."
                    ).format(device=dc.did),
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
