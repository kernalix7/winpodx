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

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QDialog,
    QDialogButtonBox,
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
from winpodx.gui._widget_helpers import (
    ElidingLabel,
    add_shadow,
    columns_want_stack,
    make_empty_panel,
    make_page_header,
    make_warning_callout,
    show_toast,
)
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    ACTION_ROW,
    BTN_GHOST,
    BTN_PRIMARY,
    BTN_SECONDARY,
    FONT_BODY,
    FONT_CAPTION,
    FONT_HEADER,
    RADIUS_M,
    RADIUS_S,
    SCROLL_AREA,
    SETTINGS_SECTION,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    SPACE_XXL,
    C,
    rgba,
)


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
        outer.setContentsMargins(SPACE_XXL, 0, SPACE_XXL, SPACE_XL)
        outer.setSpacing(SPACE_M)

        refresh = QPushButton(tr("Refresh"))
        refresh.setStyleSheet(BTN_SECONDARY)
        refresh.setIcon(load_icon("refresh", C.SUBTEXT1, 16))
        refresh.setIconSize(QSize(16, 16))
        refresh.clicked.connect(self._render_devices)
        self._devices_status = QLabel("")
        self._devices_status.setStyleSheet(
            f"color: {C.SUBTEXT1}; font-size: {FONT_CAPTION}px; font-weight: 500;"
            f" background: {rgba(C.SURFACE0, 0.72)};"
            f" border: 1px solid {rgba(C.SURFACE2, 0.38)};"
            f" border-radius: {RADIUS_M}px; padding: 7px 12px;"
        )

        actions = QWidget()
        actions_l = QHBoxLayout(actions)
        actions_l.setContentsMargins(0, 0, 0, 0)
        actions_l.setSpacing(SPACE_S)
        actions_l.addWidget(self._devices_status)
        actions_l.addWidget(refresh)

        outer.addWidget(
            make_page_header(
                tr("Devices"),
                tr(
                    "Pass host USB / PCI devices through to the Windows guest. "
                    "USB hot-plugs live; PCI needs a guest restart and confirmation."
                ),
                actions_widget=actions,
            )
        )

        # Host / guest columns side by side when wide; stacked vertically when
        # the page is too narrow (the device rows + Attach buttons clipped off
        # the right edge otherwise). Direction is toggled by _reflow_devices.
        columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        columns.setSpacing(SPACE_L)
        self._devices_cols = columns
        self._dev_host_col, host_card = self._device_column(tr("Host devices"))
        self._dev_guest_col, guest_card = self._device_column(tr("Assigned to guest"))
        columns.addWidget(host_card, 1)
        columns.addWidget(guest_card, 1)
        outer.addLayout(columns, 1)

        self._render_devices()
        self._reflow_devices()
        return page

    def _reflow_devices(self) -> None:
        """Stack the Host / Guest device columns when the page is too narrow
        for them side by side; restore the row when there's room. Called from
        the window resizeEvent so it tracks live resizing. Idempotent."""
        cols = getattr(self, "_devices_cols", None)
        pages = getattr(self, "pages", None)
        if cols is None or pages is None:
            return
        # Stack when the two columns can't both get their preferred (content)
        # width side by side -- measured from the cards' sizeHints, so it adapts
        # to the device row content + display scale instead of a fixed breakpoint.
        want = (
            QBoxLayout.Direction.TopToBottom
            if columns_want_stack(cols, pages.width())
            else QBoxLayout.Direction.LeftToRight
        )
        if cols.direction() != want:
            cols.setDirection(want)

    def _device_column(self, heading: str) -> tuple[QVBoxLayout, QWidget]:
        card = QFrame()
        card.setObjectName("settingsSection")
        card.setStyleSheet(SETTINGS_SECTION)
        add_shadow(card, blur=14, y=2, alpha=35)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        lay.setSpacing(SPACE_M)

        head_row = QWidget()
        head_l = QHBoxLayout(head_row)
        head_l.setContentsMargins(0, 0, 0, 0)
        head_l.setSpacing(SPACE_S)
        head_icon = QLabel()
        head_icon.setFixedSize(16, 16)
        head_icon.setPixmap(load_icon("hardware", C.SUBTEXT0, 16).pixmap(16, 16))
        head_l.addWidget(head_icon)
        head = QLabel(heading)
        head.setStyleSheet(f"color: {C.TEXT}; font-size: {FONT_HEADER}px; font-weight: 600;")
        head_l.addWidget(head)
        head_l.addStretch(1)
        lay.addWidget(head_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(SCROLL_AREA)
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_S)
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
        _panes = [c.parentWidget() for c in (self._dev_host_col, self._dev_guest_col)]
        for _p in _panes:
            if _p is not None:
                _p.setUpdatesEnabled(False)

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

        for _p in _panes:
            if _p is not None:
                _p.setUpdatesEnabled(True)

    def _empty_label(self, text: str) -> QWidget:
        return make_empty_panel(text)

    def _device_row(self, host: D.HostDevice, *, assigned: bool) -> QWidget:
        safety = D.classify_safety(host)
        row = QFrame()
        row.setObjectName("actionRow")
        row.setStyleSheet(ACTION_ROW)
        h = QHBoxLayout(row)
        h.setContentsMargins(SPACE_M, SPACE_M, SPACE_M, SPACE_M)
        h.setSpacing(SPACE_M)

        badge = QLabel(host.dtype.upper())
        badge_color = C.GREEN if safety.safe else C.PEACH
        badge.setStyleSheet(
            f"background: {rgba(badge_color, 0.16)}; color: {badge_color};"
            f" border: 1px solid {rgba(badge_color, 0.30)};"
            f" border-radius: {RADIUS_S}px; padding: 2px 8px;"
            f" font-size: {FONT_CAPTION}px; font-weight: 600;"
        )
        badge.setAlignment(Qt.AlignCenter)
        h.addWidget(badge, 0, Qt.AlignTop)

        full_label = host.label or tr("(unknown)")
        # Device id reads as the primary line; the (often long) label sits
        # below as calmer secondary text. Both elide to the available width so a
        # narrow column shrinks the text instead of pushing the Attach button
        # off the right edge.
        did_lbl = ElidingLabel(host.did)
        did_lbl.setStyleSheet(f"color: {C.TEXT}; font-size: {FONT_BODY}px; font-weight: 500;")
        label_lbl = ElidingLabel(full_label)
        label_lbl.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: {FONT_CAPTION}px;")

        text_host = QWidget()
        text_l = QVBoxLayout(text_host)
        text_l.setContentsMargins(0, 0, 0, 0)
        text_l.setSpacing(2)
        text_l.addWidget(did_lbl)
        text_l.addWidget(label_lbl)
        text_host.setToolTip(f"{host.did}\n{full_label}")
        h.addWidget(text_host, 1)

        if assigned:
            btn = QPushButton(tr("← Detach"))
            btn.setText(btn.text().removeprefix("← "))
            btn.setIcon(load_icon("chevron-left", C.TEXT, 16))
            btn.setIconSize(QSize(16, 16))
            btn.setStyleSheet(BTN_GHOST)
            btn.clicked.connect(lambda _=False, dev=host: self._on_detach(dev))
        else:
            btn = QPushButton(tr("Attach →"))
            btn.setText(btn.text().removesuffix(" →"))
            btn.setIcon(load_icon("chevron-right", C.CRUST, 16))
            btn.setIconSize(QSize(16, 16))
            btn.setStyleSheet(BTN_PRIMARY)
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
