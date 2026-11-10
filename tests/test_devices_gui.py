# SPDX-License-Identifier: MIT
"""Headless smoke tests for the GUI Devices tab (#286, _main_window_devices)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QThread, QThreadPool, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QDialogButtonBox,
    QPushButton,
    QWidget,
)

import winpodx.gui._main_window_devices as devices_mod  # noqa: E402
import winpodx.gui._main_window_devices_cards as devices_cards  # noqa: E402
from winpodx.cli import device as DC  # noqa: E402
from winpodx.core import devices as D  # noqa: E402
from winpodx.core.config import Config  # noqa: E402
from winpodx.gui._dialog_chrome import ChromeDialog  # noqa: E402
from winpodx.gui._main_window_devices import DevicesMixin  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def host(qapp, monkeypatch, tmp_path):
    # Isolate config to a temp XDG dir and stub the host enumeration / state.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = Config()
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(Config, "save", lambda self: None)
    monkeypatch.setattr(DC, "_guest_running", lambda _c: False)
    monkeypatch.setattr(
        DC,
        "_enumerate_host",
        lambda: [
            D.HostDevice(dtype="usb", did="1234:5678", label="ACME Dongle"),
            D.HostDevice(
                dtype="pci", did="0000:01:00.0", label="GPU", pci_class="03", iommu_group="15"
            ),
        ],
    )

    # Match the real window's QObject receiver and parent-owned widget tree.
    class _Host(DevicesMixin, QWidget):
        pass

    h = _Host()
    try:
        h._page = h._build_devices_page()
        h._page.setParent(h)
        h._cfg = cfg
        yield h
    finally:
        h.close()
        h.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_page_builds_and_populates(host):
    # Both devices unassigned -> 2 rows + a stretch in the host column.
    assert host._dev_host_col.count() == 3
    # Guest column empty -> placeholder + stretch.
    assert host._dev_guest_col.count() == 2


@pytest.mark.parametrize(
    ("pci_class", "class_label"),
    [("03", "Graphics"), ("ff", "PCI device")],
)
def test_pci_metadata_translates_ui_labels_only(host, monkeypatch, pci_class, class_label):
    calls: list[str] = []

    def fake_tr(text: str) -> str:
        calls.append(text)
        return f"T<{text}>"

    monkeypatch.setattr(devices_cards, "tr", fake_tr)
    row = host._device_row(
        D.HostDevice(
            dtype="pci",
            did="0000:01:00.0",
            label="Example Vendor Example Adapter",
            pci_class=pci_class,
            iommu_group="15",
        ),
        assigned=False,
    )
    texts = [label.text() for label in row.findChildren(devices_mod.QLabel)]

    assert any("T<IOMMU 15>" in text and f"T<{class_label}>" in text for text in texts)
    assert "IOMMU {group}" in calls
    assert class_label in calls
    assert "Example Vendor Example Adapter" not in calls
    assert "0000:01:00.0" not in calls


def test_usb_bus_metadata_is_translated_without_translating_the_device(host, monkeypatch):
    calls: list[str] = []

    def fake_tr(text: str) -> str:
        calls.append(text)
        return f"T<{text}>"

    monkeypatch.setattr(devices_cards, "tr", fake_tr)
    row = host._device_row(
        D.HostDevice(
            dtype="usb",
            did="1234:5678",
            label="Example Security Dongle",
            bus="003",
        ),
        assigned=False,
    )
    texts = [label.text() for label in row.findChildren(devices_mod.QLabel)]

    assert any("T<Bus 003>" in text for text in texts)
    assert "Bus {bus}" in calls
    assert "Example Security Dongle" not in calls
    assert "1234:5678" not in calls


def test_attach_usb_persists_and_moves_column(host):
    host._on_attach(D.HostDevice(dtype="usb", did="1234:5678", label="ACME Dongle"))
    assert host._cfg.pod.devices == ["usb|1234:5678|ACME Dongle"]
    # USB moved to guest column; only the PCI remains on the host side.
    assert host._dev_guest_col.count() == 2  # 1 row + stretch
    assert host._dev_host_col.count() == 2  # 1 row + stretch
    assert "Assigned" in host._devices_status.text()


def test_detach_persists(host):
    host._cfg.pod.devices = ["usb|1234:5678|ACME Dongle"]
    host._on_detach(D.HostDevice(dtype="usb", did="1234:5678", label="ACME Dongle"))
    assert host._cfg.pod.devices == []
    assert "Released" in host._devices_status.text()


def test_attach_usb_live_is_nonblocking(host, qapp, monkeypatch) -> None:
    from threading import Event

    started = Event()
    release = Event()
    rendered_on_gui_thread: list[bool] = []
    monkeypatch.setattr(DC, "_guest_running", lambda _c: True)
    monkeypatch.setattr(
        host,
        "_render_devices",
        lambda: rendered_on_gui_thread.append(QThread.currentThread() == qapp.thread()),
    )

    def _slow_attach(be, c, dc):
        started.set()
        assert release.wait(5.0), "test worker was not released"

    monkeypatch.setattr(D, "live_attach", _slow_attach)
    try:
        host._on_attach(D.HostDevice(dtype="usb", did="1234:5678", label="ACME"))
        assert started.wait(5.0)
        responsive: list[bool] = []
        QTimer.singleShot(0, lambda: responsive.append(host._dev_busy))
        qapp.processEvents()
        assert responsive == [True]
    finally:
        release.set()
        assert QThreadPool.globalInstance().waitForDone(5000)
        # Deliver the queued result before monkeypatch and host teardown.
        qapp.processEvents()

    assert host._dev_busy is False
    assert host._dev_op is None
    assert all(rendered_on_gui_thread)


def test_pci_attach_requires_confirmation(host, monkeypatch):
    # The risky-PCI confirm is now a custom dialog (_confirm_risky_pci, which
    # renders the plain-language "host will lose ..." warning callout), not a
    # bare QMessageBox.warning. Mock that method's verdict.
    # Decline -> nothing persisted.
    monkeypatch.setattr(host, "_confirm_risky_pci", lambda host_dev, safety: False)
    host._on_attach(D.HostDevice(dtype="pci", did="0000:01:00.0", label="GPU", pci_class="03"))
    assert host._cfg.pod.devices == []

    # Accept -> persisted.
    monkeypatch.setattr(host, "_confirm_risky_pci", lambda host_dev, safety: True)
    host._on_attach(D.HostDevice(dtype="pci", did="0000:01:00.0", label="GPU", pci_class="03"))
    assert host._cfg.pod.devices == ["pci|0000:01:00.0|GPU"]


# ----- the risky-PCI confirm dialog itself --------------------------------


class _ConfirmHost(DevicesMixin, QWidget):
    """Minimal QWidget owner for real modal confirmations."""


_RISKY_GPU = D.HostDevice(
    dtype="pci", did="0000:01:00.0", label="GPU", pci_class="03", iommu_group="15"
)
_UNSAFE = D.Safety(safe=False, reasons=["boot GPU", "IOMMU group 15 is shared"])


@pytest.fixture()
def confirm_host(qapp, monkeypatch):
    monkeypatch.setattr(D, "list_host_pci", lambda: [])
    h = _ConfirmHost()
    yield h
    h.deleteLater()


@pytest.fixture()
def driven_dialog(monkeypatch):
    """``act`` presses a real control from inside ``exec()``'s nested loop.

    The parented 4s guard rejects a dialog nothing closed, so a regression
    fails the test instead of hanging the suite.
    """
    monkeypatch.delenv("WINPODX_NATIVE_TITLEBAR", raising=False)

    class _Driven(ChromeDialog):
        seen: list = []

        @staticmethod
        def act(dlg) -> None:
            dlg.reject()

        def exec(self):  # noqa: A003 - mirrors QDialog.exec
            type(self).seen.append(self)
            QTimer.singleShot(0, lambda: type(self).act(self))
            guard = QTimer(self)
            guard.setSingleShot(True)
            guard.timeout.connect(self.reject)
            guard.start(4000)
            try:
                return super().exec()
            finally:
                guard.stop()

    monkeypatch.setattr(devices_mod, "ChromeDialog", _Driven)
    return _Driven


def _ok_button(dlg: QDialog) -> QPushButton:
    return dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)


def _cancel_button(dlg: QDialog) -> QPushButton:
    return dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel)


def test_confirm_risky_pci_accepts_explicit_confirmation(confirm_host, driven_dialog) -> None:
    driven_dialog.act = staticmethod(lambda dlg: _ok_button(dlg).click())

    assert confirm_host._confirm_risky_pci(_RISKY_GPU, _UNSAFE) is True

    (dlg,) = driven_dialog.seen
    assert dlg.result() == QDialog.DialogCode.Accepted
    assert not dlg.isVisible()


def test_confirm_risky_pci_cancel_declines(confirm_host, driven_dialog):
    driven_dialog.act = staticmethod(lambda dlg: _cancel_button(dlg).click())

    assert confirm_host._confirm_risky_pci(_RISKY_GPU, _UNSAFE) is False

    (dlg,) = driven_dialog.seen
    assert dlg.result() == QDialog.DialogCode.Rejected


def test_confirm_risky_pci_caption_close_declines(confirm_host, driven_dialog):
    driven_dialog.act = staticmethod(lambda dlg: dlg.title_bar.btn_close.click())

    assert confirm_host._confirm_risky_pci(_RISKY_GPU, _UNSAFE) is False

    (dlg,) = driven_dialog.seen
    assert dlg.result() == QDialog.DialogCode.Rejected
    assert not dlg.isVisible()
