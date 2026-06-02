# SPDX-License-Identifier: MIT
"""Headless smoke tests for the GUI Devices tab (#286, _main_window_devices)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from winpodx.cli import device as DC  # noqa: E402
from winpodx.core import devices as D  # noqa: E402
from winpodx.core.config import Config  # noqa: E402
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

    class _Host(DevicesMixin):
        pass

    h = _Host()
    # Keep a reference to the page so its Qt widget tree (and the column
    # layouts) isn't garbage-collected out from under the test.
    h._page = h._build_devices_page()  # type: ignore[attr-defined]
    h._cfg = cfg  # type: ignore[attr-defined]
    return h


def test_page_builds_and_populates(host):
    # Both devices unassigned -> 2 rows + a stretch in the host column.
    assert host._dev_host_col.count() == 3
    # Guest column empty -> placeholder + stretch.
    assert host._dev_guest_col.count() == 2


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


def test_attach_usb_live_is_nonblocking(host, monkeypatch):
    # The slow live attach must run off the GUI thread — `_on_attach` returns
    # immediately with a "busy" status instead of freezing the window.
    import time

    monkeypatch.setattr(DC, "_guest_running", lambda _c: True)
    monkeypatch.setattr(host, "_render_devices", lambda: None)  # no off-thread widget rebuild
    started: list = []

    def _slow_attach(be, c, dc):
        started.append(dc.did)
        time.sleep(0.5)  # would freeze the GUI if run on the main thread

    monkeypatch.setattr(D, "live_attach", _slow_attach)

    t0 = time.monotonic()
    host._on_attach(D.HostDevice(dtype="usb", did="1234:5678", label="ACME"))
    elapsed = time.monotonic() - t0

    assert elapsed < 0.3  # returned without waiting for the 0.5s work
    assert host._dev_busy is True
    assert "Attaching" in host._devices_status.text()
    # drain the worker so it doesn't bleed into other tests
    for _ in range(100):
        if not host._dev_busy:
            break
        time.sleep(0.02)
    assert started == ["1234:5678"]


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
