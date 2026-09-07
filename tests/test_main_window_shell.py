# SPDX-License-Identifier: MIT
"""NavigationView shell tests for HeaderMixin / NavigationMixin."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.gui import theme
from winpodx.gui._main_window_header import HeaderMixin
from winpodx.gui._main_window_nav import NavigationMixin
from winpodx.gui._main_window_navpane import NavPaneMixin


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make_cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.cpu_cores = 4
    cfg.pod.ram_gb = 8
    return cfg


class FakeTimer:
    def __init__(self) -> None:
        self.events: list[str] = []

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")


class ShellHost(NavPaneMixin, HeaderMixin, NavigationMixin, QWidget):
    """QWidget host for the NavigationView pane + page-switch side effects."""

    def __init__(self) -> None:
        QWidget.__init__(self)
        self.cfg = _make_cfg()
        self.apps: list = []
        self.pages = QStackedWidget(self)
        for _ in range(8):
            self.pages.addWidget(QWidget())
        self.started = 0
        self.stopped = 0
        self._dashboard_timer = FakeTimer()
        self._sessions_timer = FakeTimer()

    def _start_info_auto_refresh(self) -> None:
        return

    def _stop_info_auto_refresh(self) -> None:
        return

    def _on_start_pod(self) -> None:
        self.started += 1

    def _on_stop_pod(self) -> None:
        self.stopped += 1


def _mount_pane(host: ShellHost) -> QFrame:
    pane = host._build_sidebar()
    layout = QHBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(pane)
    return pane


@pytest.fixture(autouse=True)
def _restore_theme_scheme() -> None:
    previous = theme.current_scheme()
    yield
    theme.rebuild(previous)


def test_sidebar_returns_nav_pane_with_eight_checkable_rows() -> None:
    _ensure_qapp()
    host = ShellHost()
    pane = host._build_sidebar()

    assert isinstance(pane, QFrame)
    assert pane.objectName() == "navPane"
    assert len(host.nav_buttons) == 8
    assert all(btn.isCheckable() for btn in host.nav_buttons)
    assert all(btn.parentWidget() is pane for btn in host.nav_buttons)
    assert [i for i, btn in enumerate(host.nav_buttons) if btn.isChecked()] == [0]
    assert all(btn.minimumHeight() >= theme.NAV_ITEM_HEIGHT for btn in host.nav_buttons)


def test_logo_home_button_switches_to_dashboard() -> None:
    _ensure_qapp()
    host = ShellHost()
    pane = host._build_sidebar()

    logo = pane.findChild(QPushButton, "logoHomeButton")
    assert logo is not None
    host._switch_page(3)
    logo.click()
    assert host.pages.currentIndex() == 0


def test_nav_pane_expands_at_1100_and_compacts_below() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    pane = _mount_pane(host)

    host.resize(1100, 720)
    host.show()
    app.processEvents()
    host._apply_nav_compact()
    assert pane.width() == theme.NAV_PANE_WIDTH

    host.resize(900, 720)
    app.processEvents()
    host._apply_nav_compact()
    assert pane.width() == theme.NAV_PANE_COMPACT
    labels = list(host._nav_labels)
    for btn, label in zip(host.nav_buttons, labels):
        assert btn.toolTip() == label
        assert btn.text() in ("", label)


def _finish_indicator_anim(host: ShellHost) -> None:
    anim = host._nav_indicator_anim
    anim.stop()
    end = anim.endValue()
    if isinstance(end, QPoint):
        host.nav_indicator.move(end)


def _assert_indicator_on_row(host: ShellHost, index: int) -> None:
    indicator = host.nav_indicator
    btn = host.nav_buttons[index]
    parent = indicator.parentWidget()
    mapped = btn.mapTo(parent, QPoint(0, 0))
    mid = indicator.y() + indicator.height() / 2
    assert mapped.y() - 2 <= mid <= mapped.y() + btn.height() + 2
    assert indicator.x() <= theme.SPACE_S


def test_profile_block_is_fully_inside_the_pane() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    pane = _mount_pane(host)
    host.resize(1100, 720)
    host.show()
    app.processEvents()

    logo = pane.findChild(QPushButton, "logoHomeButton")
    search = pane.findChild(QLineEdit, "navSearch")
    assert logo is not None
    assert search is not None
    assert logo.y() >= 0
    assert logo.height() >= 32
    pane_rect = pane.rect()
    assert pane_rect.contains(logo.geometry())
    assert search.y() >= logo.geometry().bottom()


def test_shell_stays_1100_wide_after_show() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    _mount_pane(host)
    host.resize(1100, 720)
    host.show()
    app.processEvents()
    assert host.width() == 1100


def test_nav_indicator_geometry_tracks_selected_row() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    pane = _mount_pane(host)
    host.resize(1100, 720)
    host.show()
    app.processEvents()
    host._move_nav_indicator(0)
    _finish_indicator_anim(host)
    app.processEvents()

    indicator = pane.findChild(QFrame, "navIndicator")
    assert indicator is not None
    _assert_indicator_on_row(host, 0)

    host._switch_page(3)
    app.processEvents()
    _finish_indicator_anim(host)
    app.processEvents()
    _assert_indicator_on_row(host, 3)


def test_compact_nav_buttons_keep_accessible_names() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    _mount_pane(host)
    host.resize(900, 720)
    host.show()
    app.processEvents()
    host._apply_nav_compact()
    app.processEvents()

    labels = list(host._nav_labels)
    assert labels
    for btn, label in zip(host.nav_buttons, labels):
        assert btn.accessibleName() == label
        assert btn.text() == ""


def test_nav_indicator_tracks_selected_row_after_compact_resize() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    _mount_pane(host)
    host.resize(1100, 720)
    host.show()
    app.processEvents()
    host._switch_page(2)
    app.processEvents()
    _finish_indicator_anim(host)
    app.processEvents()
    _assert_indicator_on_row(host, 2)

    host.resize(900, 720)
    app.processEvents()
    host._apply_nav_compact()
    app.processEvents()
    _assert_indicator_on_row(host, 2)

    host._switch_page(2)
    app.processEvents()
    _finish_indicator_anim(host)
    app.processEvents()
    _assert_indicator_on_row(host, 2)


def test_pod_buttons_show_labels_when_expanded() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    _mount_pane(host)
    host.resize(1100, 720)
    host.show()
    app.processEvents()
    host._apply_nav_compact()

    assert host.btn_start.text() == tr("Start Pod")
    assert host.btn_stop.text() == tr("Stop Pod")

    host.resize(900, 720)
    app.processEvents()
    host._apply_nav_compact()
    assert host.btn_start.text() == ""
    assert host.btn_stop.text() == ""


def test_top_strip_reuses_pod_widgets_created_by_sidebar() -> None:
    _ensure_qapp()
    host = ShellHost()
    host._build_sidebar()

    for name in ("pod_dot", "pod_label", "agent_dot", "rdp_dot", "btn_start", "btn_stop"):
        assert getattr(host, name) is not None

    identities = {
        name: getattr(host, name)
        for name in (
            "pod_dot",
            "pod_label",
            "agent_dot",
            "rdp_dot",
            "btn_start",
            "btn_stop",
        )
    }
    strip = host._build_top_strip()
    assert strip.objectName() == "topStrip"
    assert strip.isHidden()
    for name, widget in identities.items():
        assert getattr(host, name) is widget


def test_page_header_tracks_registered_title_without_breadcrumb() -> None:
    _ensure_qapp()
    host = ShellHost()
    host._build_sidebar()

    assert host.page_header is not None
    assert host.page_header.findChild(QLabel, "breadcrumb") is None
    title = host.page_header.findChild(QLabel, "pageTitle")
    subtitle = host.page_header.findChild(QLabel, "pageSubtitle")
    actions = host.page_header.findChild(QWidget, "pageActions")
    assert title is not None
    assert subtitle is not None
    assert actions is not None
    host._register_page_header(2, tr("Settings"), tr("Configure RDP and container settings"))
    host._switch_page(2)
    assert title.text() == tr("Settings")
    assert subtitle.text() == tr("Configure RDP and container settings")
    assert not subtitle.isHidden()


def test_nav_group_divider_sits_between_settings_and_tools() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    pane = _mount_pane(host)
    host.resize(1100, 720)
    host.show()
    app.processEvents()

    divider = pane.findChild(QFrame, "navGroupDivider")
    assert divider is not None
    assert divider.height() == 1
    settings = host.nav_buttons[2]
    tools = host.nav_buttons[3]
    assert settings.geometry().bottom() < divider.geometry().top()
    assert divider.geometry().bottom() < tools.geometry().top()
    gap = tools.geometry().top() - divider.geometry().bottom() - 1
    assert gap >= theme.SPACE_S


def test_pod_footer_shows_one_toggle_row() -> None:
    _ensure_qapp()
    host = ShellHost()
    host._build_sidebar()

    assert host.btn_start is not None
    assert host.btn_stop is not None
    host._sync_pod_footer("stopped")
    assert not host.btn_start.isHidden()
    assert host.btn_stop.isHidden()
    host._sync_pod_footer("unknown")
    assert not host.btn_start.isHidden()
    assert host.btn_stop.isHidden()
    host._sync_pod_footer("running")
    assert host.btn_start.isHidden()
    assert not host.btn_stop.isHidden()
    host._sync_pod_footer("paused")
    assert host.btn_start.isHidden()
    assert not host.btn_stop.isHidden()
    host._sync_pod_footer("checking")
    assert host.btn_start.isHidden()
    assert not host.btn_stop.isHidden()


def test_compact_rail_click_checks_one_item_without_focus_ring() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    pane = _mount_pane(host)
    host.resize(900, 720)
    host.show()
    app.processEvents()
    host._apply_nav_compact()
    app.processEvents()

    assert sum(btn.isChecked() for btn in host.nav_buttons) == 1
    target = host.nav_buttons[6]
    QTest.mouseClick(target, Qt.MouseButton.LeftButton)
    app.processEvents()
    _finish_indicator_anim(host)
    app.processEvents()
    assert sum(btn.isChecked() for btn in host.nav_buttons) == 1
    assert target.isChecked()
    assert not any(btn.hasFocus() for btn in host.nav_buttons)
    indicator = host.nav_indicator
    assert indicator.isVisible()
    assert pane.rect().contains(indicator.geometry())
    _assert_indicator_on_row(host, 6)
    for btn, label in zip(host.nav_buttons, host._nav_labels):
        assert btn.toolTip() == label
        assert btn.focusPolicy() == Qt.FocusPolicy.TabFocus


def test_restyle_shell_applies_scheme_nav_background() -> None:
    _ensure_qapp()
    host = ShellHost()
    pane = host._build_sidebar()

    theme.rebuild("light")
    host._restyle_shell()
    assert "#EBEBEB" in pane.styleSheet()

    theme.rebuild("dark")
    host._restyle_shell()
    assert "#1A1A1A" in pane.styleSheet()


def test_nav_header_exposes_pod_state_pill() -> None:
    _ensure_qapp()
    host = ShellHost()
    pane = host._build_sidebar()

    pill = pane.findChild(QFrame, "podStatePill")
    assert pill is not None
    assert pill.height() == 22 or pill.minimumHeight() == 22
    assert host.pod_dot.parentWidget() is pill
    assert host.pod_label.parentWidget() is pill


@pytest.mark.parametrize(
    ("state", "token"),
    [
        ("running", "GREEN"),
        ("stopped", "SUBTEXT0"),
        ("paused", "YELLOW"),
        ("checking", "YELLOW"),
        ("unresponsive", "RED"),
    ],
)
def test_pod_state_pill_fill_tracks_state(state: str, token: str) -> None:
    _ensure_qapp()
    host = ShellHost()
    pane = host._build_sidebar()

    host._sync_pod_pill(state)

    color = getattr(theme.C, token)
    pill = pane.findChild(QFrame, "podStatePill")
    assert pill is not None
    assert theme.rgba(color, 0.16) in pill.styleSheet()


def test_agent_and_rdp_dots_remain_text_labels() -> None:
    _ensure_qapp()
    host = ShellHost()
    host._build_sidebar()

    assert host.agent_dot.text() == "A"
    assert host.rdp_dot.text() == "R"
    assert isinstance(host.agent_dot, QLabel)
    assert isinstance(host.rdp_dot, QLabel)
    assert host.agent_dot.pixmap() is None or host.agent_dot.pixmap().isNull()
    assert host.rdp_dot.pixmap() is None or host.rdp_dot.pixmap().isNull()


# ----- NavigationView: hamburger toggle + compact profile -----------------


def test_nav_toggle_button_pins_compact_and_expanded_across_resizes() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    pane = _mount_pane(host)
    host.resize(1100, 720)
    host.show()
    app.processEvents()
    host._apply_nav_compact()

    toggle = pane.findChild(QPushButton, "navToggle")
    assert toggle is not None
    assert toggle.toolTip()
    assert toggle.focusPolicy() == Qt.FocusPolicy.TabFocus

    toggle.click()
    app.processEvents()
    assert pane.width() == theme.NAV_PANE_COMPACT
    host.resize(1200, 720)
    app.processEvents()
    host._apply_nav_compact()
    assert pane.width() == theme.NAV_PANE_COMPACT, "user choice must survive a resize"

    toggle.click()
    app.processEvents()
    assert pane.width() == theme.NAV_PANE_WIDTH
    host.resize(900, 720)
    app.processEvents()
    host._apply_nav_compact()
    assert pane.width() == theme.NAV_PANE_COMPACT, (
        "crossing the breakpoint changes display mode: a pin made while expanded "
        "must not survive as a stuck overlay (WinUI adaptive trigger)"
    )
    host.resize(1200, 720)
    app.processEvents()
    host._apply_nav_compact()
    assert pane.width() == theme.NAV_PANE_WIDTH, "back above the breakpoint = auto expanded"


def test_compact_rail_keeps_avatar_and_toggle_inside_the_pane() -> None:
    app = _ensure_qapp()
    host = ShellHost()
    pane = _mount_pane(host)
    host.resize(900, 720)
    host.show()
    app.processEvents()
    host._apply_nav_compact()
    app.processEvents()

    assert pane.width() == theme.NAV_PANE_COMPACT
    avatar = host._profile_avatar
    toggle = pane.findChild(QPushButton, "navToggle")
    for widget in (avatar, toggle, host._profile_button):
        rect = widget.geometry()
        right = widget.mapTo(pane, rect.bottomRight() - rect.topLeft()).x()
        assert right <= theme.NAV_PANE_COMPACT, f"{widget.objectName()} overflows the rail"
    assert avatar.width() <= theme.NAV_PANE_COMPACT - 2 * theme.SPACE_S
    assert not avatar.pixmap().isNull()
    assert avatar.pixmap().width() <= avatar.width()

    host.resize(1100, 720)
    app.processEvents()
    host._apply_nav_compact()
    assert avatar.width() == 64
