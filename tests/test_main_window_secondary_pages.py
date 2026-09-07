# SPDX-License-Identifier: MIT
"""Win11 Settings anatomy for Devices / Info / License secondary pages."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Qt, Signal, Slot  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QBoxLayout,
    QCheckBox,
    QFrame,
    QLabel,
    QPushButton,
    QTextEdit,
    QWidget,
)

from winpodx.cli import device as DC  # noqa: E402
from winpodx.core import devices as D  # noqa: E402
from winpodx.core.config import Config  # noqa: E402
from winpodx.gui import theme  # noqa: E402
from winpodx.gui._main_window_devices import DevicesMixin  # noqa: E402
from winpodx.gui._main_window_info import InfoPageMixin  # noqa: E402
from winpodx.gui._main_window_license import LicensePageMixin  # noqa: E402
from winpodx.gui._main_window_maintenance import MaintenanceMixin  # noqa: E402
from winpodx.gui.theme import HIT_TARGET  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _cfg() -> Config:
    return Config()


class _MaintHost(MaintenanceMixin, QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = _cfg()
        self.info_label = QLabel("")
        self.app_launched = type("S", (), {"emit": staticmethod(lambda *_a: None)})()
        self.app_launch_failed = self.app_launched
        self.pod_status_updated = self.app_launched
        self.log_signal = self.app_launched
        self.page = None

    def _refresh_pod_status(self) -> None:
        return None


class _DevicesHost(DevicesMixin):
    pass


class _InfoHost(InfoPageMixin, QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = _cfg()
        self.page = None

    def drain(self) -> None:
        app = QApplication.instance()
        self._stop_info_auto_refresh()
        for _ in range(5):
            app.processEvents()
        thread = self.__dict__.pop("_info_thread", None)
        if thread is not None:
            try:
                thread.quit()
                thread.wait(2000)
            except RuntimeError:
                pass
        self._info_busy = False
        for _ in range(5):
            app.processEvents()


class _LicenseHost(LicensePageMixin, QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.cfg = _cfg()
        self.page = None


@pytest.fixture()
def devices_host(qapp, monkeypatch, tmp_path):
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
        ],
    )
    host = _DevicesHost()
    host._page = host._build_devices_page()
    return host


class _StalledInfoWorker(QObject):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, cfg) -> None:
        super().__init__()
        self.cfg = cfg

    @Slot()
    def run(self) -> None:
        return None


@pytest.fixture()
def info_host(qapp, monkeypatch):
    monkeypatch.setattr(
        "winpodx.gui._main_window_info.InfoWorker",
        _StalledInfoWorker,
    )
    host = _InfoHost()
    host.page = host._build_info_page()
    yield host
    host.drain()


def test_devices_page_has_two_settings_card_columns(devices_host) -> None:
    titles = {lbl.text() for lbl in devices_host._page.findChildren(QLabel)}
    assert "USB · 1" in titles
    assert "PCI · 0" in titles
    assert "Assigned to guest · 0" in titles
    assert "Host devices" not in titles
    for btn in devices_host._page.findChildren(QPushButton):
        assert btn.minimumHeight() >= 32
    for toggle in devices_host._page.findChildren(QCheckBox):
        if type(toggle).__name__ == "ToggleSwitch":
            continue
        assert "::indicator" in toggle.styleSheet()
        assert toggle.minimumHeight() >= HIT_TARGET
    filt = devices_host._devices_filter
    assert filt.isHidden()
    assert filt.objectName() == "navSearch"
    assert filt.minimumHeight() >= 32


def test_device_row_is_compact_48px_with_secondary_attach(devices_host) -> None:
    row = devices_host._device_row(
        D.HostDevice(dtype="usb", did="1234:5678", label="ACME Dongle", bus="003"),
        assigned=False,
    )
    assert row.minimumHeight() == 48
    assert row.title_label.text() == "ACME Dongle"
    assert "1234:5678" in row.desc_label.text()
    btn = row.action_widget
    assert btn.property("w11Role") == "secondary"
    assert "Attach" in btn.text()
    assert btn.minimumHeight() >= 32
    icon = next(
        lbl
        for lbl in row.findChildren(QLabel)
        if not lbl.pixmap().isNull() and lbl.objectName() == "settingsCardIcon"
    )
    assert icon.width() == 24
    assert icon.property("iconName") == "usb"
    assert row.findChild(QLabel, "riskGlyph") is None


def test_risky_device_row_shows_warning_glyph_with_flag_tooltip(devices_host) -> None:
    row = devices_host._device_row(
        D.HostDevice(
            dtype="pci",
            did="0000:01:00.0",
            label="GPU",
            pci_class="03",
            iommu_group="15",
        ),
        assigned=True,
    )
    warn = row.findChild(QLabel, "riskGlyph")
    assert warn is not None
    assert "Why this is flagged" in (warn.toolTip() or "")
    assert "Restart the pod to apply." in row.desc_label.text()
    icon = next(
        lbl
        for lbl in row.findChildren(QLabel)
        if not lbl.pixmap().isNull() and lbl.objectName() == "settingsCardIcon"
    )
    assert icon.property("iconName") == "hardware"
    btn = row.action_widget
    assert btn.property("w11Role") == "secondary"
    assert "Detach" in btn.text()


def test_devices_refresh_is_a_ghost_in_the_group_header(devices_host) -> None:
    # Refresh moved to the pinned shell header (UX_PLAN A1); mixin hosts keep
    # the same ghost button as `_devices_refresh_btn`.
    refresh = devices_host._devices_refresh_btn
    assert refresh.property("w11Role") == "ghost"
    assert refresh.text() == "Refresh"
    assert "Guest running:" in devices_host._devices_status.text()
    assert isinstance(devices_host._devices_status, QLabel)


def test_devices_filter_matches_substring_across_groups(qapp, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg = Config()
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr(Config, "save", lambda self: None)
    monkeypatch.setattr(DC, "_guest_running", lambda _c: False)
    hosts = [
        D.HostDevice(dtype="usb", did=f"1234:{i:04x}", label=f"Stick {i}") for i in range(6)
    ] + [
        D.HostDevice(dtype="pci", did=f"0000:0{i}:00.0", label=f"NIC {i}", pci_class="02")
        for i in range(4)
    ]
    monkeypatch.setattr(DC, "_enumerate_host", lambda: hosts)
    host = _DevicesHost()
    host._page = host._build_devices_page()
    filt = host._devices_filter
    assert not filt.isHidden()
    assert filt.minimumHeight() >= 32
    filt.setText("Stick 2")
    qapp.processEvents()
    visible = [
        frame for frame in host._page.findChildren(QFrame, "deviceCard") if not frame.isHidden()
    ]
    assert len(visible) == 1
    assert visible[0].title_label.text() == "Stick 2"


def test_reflow_devices_stays_a_single_column(devices_host) -> None:
    pages = QWidget()
    pages.resize(1100, 600)
    devices_host.pages = pages
    devices_host._reflow_devices()
    assert devices_host._devices_cols.direction() == QBoxLayout.Direction.TopToBottom

    pages.resize(740, 600)
    devices_host._reflow_devices()
    # DESIGN.md §4: Host / Guest groups stay stacked; no 50/50 split at 1100.
    assert devices_host._devices_cols.direction() == QBoxLayout.Direction.TopToBottom


def test_info_page_has_device_card_and_health_rows(info_host, qapp) -> None:
    about = info_host.page.findChild(QFrame, "aboutDeviceCard")
    assert about is not None
    snapshot = {
        "health": [
            {"name": "agent_health", "status": "ok", "detail": "agent replied", "duration_ms": 12},
            {"name": "disk_free", "status": "warn", "detail": "6 GiB left", "duration_ms": 3},
            {"name": "guest_exec", "status": "fail", "detail": "pod down", "duration_ms": 41},
        ],
        "health_overall": "warn",
        "system": {"winpodx": "0.10.4"},
        "display": {},
        "dependencies": {},
        "pod": {"state": "running", "rdp_port": 3390, "vnc_port": 8006},
        "config": {"path": "/tmp/x", "backend": "podman", "ip": "127.0.0.1", "port": 3390},
    }
    info_host._apply_info_snapshot(snapshot)
    qapp.processEvents()
    body = info_host._info_card_bodies["health"]
    probes = []
    icons = {}
    for i in range(body.count()):
        widget = body.itemAt(i).widget()
        if widget is None:
            continue
        action = getattr(widget, "action_widget", None)
        title = getattr(widget, "title_label", None)
        if action is None or title is None:
            continue
        if title.text() in {"agent_health", "disk_free", "guest_exec"}:
            probes.append((title.text(), action.text(), action.styleSheet()))
            icons[title.text()] = next(
                label.pixmap().toImage()
                for label in widget.findChildren(QLabel)
                if not label.pixmap().isNull()
            )
    names = [p[0] for p in probes]
    assert names == ["agent_health", "disk_free", "guest_exec"]
    by_name = {p[0]: p for p in probes}
    assert theme.C.GREEN.lower() in by_name["agent_health"][2].lower()
    assert theme.C.YELLOW.lower() in by_name["disk_free"][2].lower()
    assert theme.C.RED.lower() in by_name["guest_exec"][2].lower()
    assert icons["disk_free"] != icons["agent_health"]
    assert icons["guest_exec"] != icons["agent_health"]


def _health_probe_cards(body) -> dict[str, QFrame]:
    cards: dict[str, QFrame] = {}
    for i in range(body.count()):
        widget = body.itemAt(i).widget()
        if widget is None:
            continue
        title = getattr(widget, "title_label", None)
        if title is None:
            continue
        cards[title.text()] = widget
    return cards


def _row_icon_label(card: QFrame) -> QLabel:
    return next(label for label in card.findChildren(QLabel) if not label.pixmap().isNull())


def test_health_row_icons_use_status_colour(info_host, qapp) -> None:
    info_host._apply_info_snapshot(
        {
            "health": [
                {"name": "agent_health", "status": "ok", "detail": "agent replied"},
                {"name": "disk_free", "status": "warn", "detail": "6 GiB left"},
                {"name": "guest_exec", "status": "fail", "detail": "pod down"},
            ],
            "health_overall": "warn",
            "system": {},
            "display": {},
            "dependencies": {},
            "pod": {},
            "config": {},
        }
    )
    qapp.processEvents()
    cards = _health_probe_cards(info_host._info_card_bodies["health"])
    expected = {
        "agent_health": theme.C.GREEN,
        "disk_free": theme.C.YELLOW,
        "guest_exec": theme.C.RED,
    }
    pixmaps = {}
    for name, color in expected.items():
        icon = _row_icon_label(cards[name])
        assert icon.property("iconColor") == color
        pixmaps[name] = icon.pixmap().toImage()
    assert pixmaps["agent_health"] != pixmaps["disk_free"]
    assert pixmaps["agent_health"] != pixmaps["guest_exec"]
    assert pixmaps["disk_free"] != pixmaps["guest_exec"]


def test_health_row_icons_restyle_from_theme_tokens(info_host, qapp) -> None:
    info_host._apply_info_snapshot(
        {
            "health": [
                {"name": "agent_health", "status": "ok", "detail": "ok"},
                {"name": "disk_free", "status": "warn", "detail": "warn"},
                {"name": "guest_exec", "status": "fail", "detail": "fail"},
            ],
            "health_overall": "ok",
            "system": {},
            "display": {},
            "dependencies": {},
            "pod": {},
            "config": {},
        }
    )
    qapp.processEvents()
    theme.rebuild("dark")
    info_host._restyle_info()
    cards = _health_probe_cards(info_host._info_card_bodies["health"])
    assert _row_icon_label(cards["agent_health"]).property("iconColor") == theme.C.GREEN
    assert _row_icon_label(cards["disk_free"]).property("iconColor") == theme.C.YELLOW
    assert _row_icon_label(cards["guest_exec"]).property("iconColor") == theme.C.RED
    theme.rebuild("light")
    info_host._restyle_info()
    cards = _health_probe_cards(info_host._info_card_bodies["health"])
    assert _row_icon_label(cards["agent_health"]).property("iconColor") == theme.C.GREEN
    assert _row_icon_label(cards["disk_free"]).property("iconColor") == theme.C.YELLOW
    assert _row_icon_label(cards["guest_exec"]).property("iconColor") == theme.C.RED


def test_license_page_has_summary_rows_and_viewer_in_settings_card(qapp) -> None:
    host = _LicenseHost()
    host.page = host._build_license_page()
    cards = host.page.findChildren(QFrame, "settingsCard")
    assert len(cards) >= 2
    titles = []
    for card in cards:
        title = getattr(card, "title_label", None)
        if title is not None:
            titles.append(title.text())
    assert any("License" in t or "Licence" in t for t in titles)
    viewer = host.page.findChildren(QTextEdit)
    assert viewer
    parent_cards = []
    w = viewer[0].parentWidget()
    while w is not None:
        if isinstance(w, QFrame) and w.objectName() == "settingsCard":
            parent_cards.append(w)
        w = w.parentWidget()
    assert parent_cards


def test_scheme_rebuild_restyles_settings_card_fill(qapp, monkeypatch) -> None:
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: [])
    host = _MaintHost()
    host.page = host._build_maintenance_page()
    card = host.page.findChildren(QFrame, "settingsCard")[0]
    theme.rebuild("light")
    host._restyle_tools()
    assert "#FFFFFF" in card.styleSheet()
    theme.rebuild("dark")
    host._restyle_tools()
    assert "#2B2B2B" in card.styleSheet()
    theme.rebuild("light")


def test_info_loading_state_is_one_card(info_host) -> None:
    body = info_host._info_card_bodies["health"]
    cards = [
        body.itemAt(i).widget() for i in range(body.count()) if body.itemAt(i).widget() is not None
    ]
    assert len(cards) == 1
    title = getattr(cards[0], "title_label", None)
    assert title is not None
    assert title.text() == "Loading..."


def test_info_refresh_lives_on_the_about_card(info_host) -> None:
    # Refresh Info moved to the pinned shell header (UX_PLAN A1); mixin hosts
    # keep the same ghost button as `_info_refresh_btn` instead of embedding it
    # in the About card (which would scroll away).
    assert info_host._info_refresh_btn.text() == "Refresh Info"
    about = info_host.page.findChild(QFrame, "aboutDeviceCard")
    assert about is not None
    assert not any(btn.text() == "Refresh Info" for btn in about.findChildren(QPushButton))


def test_info_missing_and_unknown_values_use_status_colour(info_host, qapp) -> None:
    info_host._apply_info_snapshot(
        {
            "health": [],
            "health_overall": "",
            "system": {"winpodx": "UNKNOWN"},
            "display": {},
            "dependencies": {
                "freerdp": {"found": "false", "path": ""},
                "podman": {"found": "true", "path": "/usr/bin/podman"},
            },
            "pod": {},
            "config": {},
        }
    )
    qapp.processEvents()
    deps = info_host._info_card_bodies["dependencies"]
    colours = {}
    for i in range(deps.count()):
        widget = deps.itemAt(i).widget()
        if widget is None:
            continue
        title = getattr(widget, "title_label", None)
        action = getattr(widget, "action_widget", None)
        if title is None or action is None:
            continue
        colours[title.text()] = action.styleSheet()
    assert theme.C.RED.lower() in colours["freerdp"].lower()
    assert theme.C.RED.lower() not in colours["podman"].lower()
    system = info_host._info_card_bodies["system"]
    winpodx_ss = ""
    for i in range(system.count()):
        widget = system.itemAt(i).widget()
        if widget is None:
            continue
        title = getattr(widget, "title_label", None)
        action = getattr(widget, "action_widget", None)
        if title is not None and title.text() == "WinPodX" and action is not None:
            winpodx_ss = action.styleSheet()
    assert theme.C.YELLOW.lower() in winpodx_ss.lower()


def test_license_summary_uses_upstream_and_third_party_keys(qapp) -> None:
    host = _LicenseHost()
    host.page = host._build_license_page()
    titles = []
    for card in host.page.findChildren(QFrame, "settingsCard"):
        title = getattr(card, "title_label", None)
        if title is not None:
            titles.append(title.text())
    joined = " ".join(titles)
    assert "License" in joined
    assert "Upstream" in joined
    assert "Third-party" in joined
    viewer = host.page.findChildren(QTextEdit)[0]
    assert theme.C.MANTLE in viewer.styleSheet()
    assert viewer.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_info_copy_diagnostics_writes_snapshot_to_clipboard(info_host, qapp) -> None:
    snapshot = {
        "health": [],
        "health_overall": "warn",
        "system": {"winpodx": "0.10.4"},
        "display": {},
        "dependencies": {},
        "pod": {},
        "config": {},
    }
    info_host._apply_info_snapshot(snapshot)
    qapp.processEvents()
    about = info_host.page.findChild(QFrame, "aboutDeviceCard")
    assert about is not None
    btn = next(b for b in about.findChildren(QPushButton) if "Copy" in b.text())
    assert btn.property("w11Role") == "primary"
    assert btn.minimumHeight() >= 32
    btn.click()
    qapp.processEvents()
    text = QApplication.clipboard().text()
    assert "health_overall: warn" in text
    assert "winpodx" in text.lower() or "0.10.4" in text


def test_info_key_rows_are_compact_48px(info_host, qapp) -> None:
    info_host._apply_info_snapshot(
        {
            "health": [],
            "health_overall": "",
            "system": {"winpodx": "0.10.4", "kernel": "6.8"},
            "display": {"session_type": "wayland"},
            "dependencies": {},
            "pod": {"state": "running"},
            "config": {"backend": "podman"},
        }
    )
    qapp.processEvents()
    for key in ("system", "display", "pod", "config"):
        body = info_host._info_card_bodies[key]
        for i in range(body.count()):
            widget = body.itemAt(i).widget()
            if widget is None:
                continue
            assert widget.minimumHeight() == 48
            desc = getattr(widget, "desc_label", None)
            assert desc is None or not desc.isVisible() or not desc.text()


def test_license_summary_rows_are_compact_48px(qapp) -> None:
    host = _LicenseHost()
    host.page = host._build_license_page()
    titles = {"License", "Upstream", "Third-party components"}
    found = 0
    for card in host.page.findChildren(QFrame, "settingsCard"):
        title = getattr(card, "title_label", None)
        if title is None or title.text() not in titles:
            continue
        assert card.minimumHeight() == 48
        found += 1
    assert found == 3


def test_license_ack_rows_keep_purpose_and_url_visible(qapp) -> None:
    host = _LicenseHost()
    host.page = host._build_license_page()
    texts = [lbl.text() for lbl in host.page.findChildren(QLabel)]
    joined = "\n".join(texts)
    assert "Windows-in-Docker" in joined
    assert "https://github.com/dockur/windows" in joined
    assert "dockur/windows" in joined
