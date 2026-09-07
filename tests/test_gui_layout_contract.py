# SPDX-License-Identifier: MIT
"""Geometry contract for DESIGN.md §4 — measured on real offscreen widgets."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QWidget,
)

from winpodx.core.config import Config
from winpodx.gui.main_window import WinpodxWindow
from winpodx.gui.theme import (
    CONTENT_MAX_WIDTH,
    CONTROL_HEIGHT_W11,
    MIN_SHRINK_RATIO,
    NAV_PANE_COMPACT,
    NAV_PANE_WIDTH,
    PAGE_MARGIN_TOP,
    PAGE_MARGIN_X,
    SETTINGS_ROW_MIN,
    SPACE_L,
    SPACE_S,
    SPACE_XL,
    SPACE_XS,
    TITLE_BAR_H,
)

_PAGE_IDS = (
    (0, "dashboard"),
    (1, "applications"),
    (2, "settings"),
    (3, "tools"),
    (4, "logs"),
    (5, "info"),
    (6, "devices"),
    (7, "license"),
)
_CARD_NAMES = frozenset({"settingsCard", "sessionCard", "aboutDeviceCard", "settingsSection"})
_TITLE_X = NAV_PANE_WIDTH + PAGE_MARGIN_X
_TITLE_Y = TITLE_BAR_H + PAGE_MARGIN_TOP
_CONTENT_RIGHT = _TITLE_X + 708
_WINDOW_SIZE = (1100, 720)
_SCROLL_GUTTER_TOLERANCE = 12
_GROUP_GAP_SLACK = 2


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _stub_window_side_effects(monkeypatch: pytest.MonkeyPatch) -> Config:
    """Keep WinpodxWindow off host binaries, timers, and modal dialogs."""
    cfg = Config()
    cfg.pod.initialized = True
    cfg.pod.backend = "podman"
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr("winpodx.gui.main_window.list_available_apps", lambda: [])
    monkeypatch.setattr(
        "winpodx.gui.main_window.WinpodxWindow._start_status_timer",
        lambda self: None,
    )
    monkeypatch.setattr(
        "winpodx.gui.main_window.WinpodxWindow._on_follow_app_log",
        lambda self: None,
    )
    monkeypatch.setattr(
        "winpodx.gui.main_window.WinpodxWindow._maybe_run_first_launch_checks",
        lambda self: None,
    )
    monkeypatch.setattr(
        "winpodx.gui._main_window_info.InfoPageMixin._refresh_info",
        lambda self: None,
    )
    monkeypatch.setattr(
        "winpodx.gui._main_window_pod.PodStatusMixin._refresh_pod_status",
        lambda self: None,
    )
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: [])
    monkeypatch.setattr("winpodx.cli.device._enumerate_host", lambda: [])
    monkeypatch.setattr("winpodx.cli.device._guest_running", lambda _cfg: False)
    monkeypatch.setattr("winpodx.utils.locale.detect_timezone", lambda: "UTC")
    monkeypatch.setattr(
        "winpodx.utils.specs.detect_tuning_capability",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("probe disabled")),
    )
    monkeypatch.setattr("winpodx.desktop.autostart.is_autostart_enabled", lambda: False)
    monkeypatch.setattr("winpodx.reverse_open.lifecycle.is_listener_running", lambda: None)
    return cfg


def _stop_window_timers(window: WinpodxWindow) -> None:
    for attr in (
        "_dashboard_timer",
        "_sessions_timer",
        "status_timer",
        "_info_auto_timer",
    ):
        timer = getattr(window, attr, None)
        if timer is not None:
            timer.stop()


@pytest.fixture
def contract_window(monkeypatch: pytest.MonkeyPatch) -> WinpodxWindow:
    app = _ensure_qapp()
    _stub_window_side_effects(monkeypatch)
    window = WinpodxWindow()
    _stop_window_timers(window)
    window.resize(*_WINDOW_SIZE)
    window.show()
    app.processEvents()
    window.resize(*_WINDOW_SIZE)
    app.processEvents()
    yield window
    _stop_window_timers(window)
    window.close()
    window.deleteLater()
    app.processEvents()


def _show_page(window: WinpodxWindow, index: int) -> None:
    window._switch_page(index)
    _stop_window_timers(window)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()
    window.resize(*_WINDOW_SIZE)
    app.processEvents()


def _visible_page_title(window: WinpodxWindow) -> QLabel:
    labels = [
        lbl
        for lbl in window.findChildren(QLabel)
        if lbl.objectName() == "pageTitle" and lbl.isVisible()
    ]
    assert labels, "current page has no visible QLabel#pageTitle"
    labels.sort(key=lambda lbl: lbl.mapTo(window, QPoint(0, 0)).y())
    return labels[0]


def _title_origin(window: WinpodxWindow) -> tuple[int, int]:
    title = _visible_page_title(window)
    pos = title.mapTo(window, QPoint(0, 0))
    return pos.x(), pos.y()


def _visible_cards(window: WinpodxWindow) -> list[QFrame]:
    cards = [
        frame
        for frame in window.findChildren(QFrame)
        if frame.objectName() in _CARD_NAMES and frame.isVisible() and frame.width() > 0
    ]
    cards.sort(key=lambda frame: (frame.mapTo(window, QPoint(0, 0)).y(), frame.x()))
    return cards


def _card_origin(window: WinpodxWindow, card: QFrame) -> tuple[int, int, int]:
    pos = card.mapTo(window, QPoint(0, 0))
    return pos.x(), pos.y(), pos.x() + card.width()


def _sibling_card_groups(window: WinpodxWindow) -> list[list[QFrame]]:
    groups: dict[int, list[QFrame]] = {}
    order: list[int] = []
    for card in _visible_cards(window):
        if card.objectName() == "settingsSection":
            continue
        parent = card.parentWidget()
        if parent is None or parent.objectName() != "settingsCardStack":
            continue
        key = id(parent)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(card)
    return [groups[key] for key in order if len(groups[key]) >= 2]


def _group_headers(window: WinpodxWindow) -> list[QLabel]:
    headers = [
        lbl
        for lbl in window.findChildren(QLabel)
        if lbl.objectName() in {"settingsGroupTitle", "settingsGroupHeading"} and lbl.isVisible()
    ]
    headers.sort(key=lambda lbl: lbl.mapTo(window, QPoint(0, 0)).y())
    return headers


def test_all_pages_share_the_same_title_origin(contract_window: WinpodxWindow) -> None:
    origins: list[tuple[int, int]] = []
    for index, name in _PAGE_IDS:
        _show_page(contract_window, index)
        origin = _title_origin(contract_window)
        origins.append(origin)
        assert origin[0] == _TITLE_X, f"{name}: title x {origin[0]} != {_TITLE_X}"
        assert origin[1] == _TITLE_Y, f"{name}: title y {origin[1]} != {_TITLE_Y}"
    assert len(set(origins)) == 1, f"title origins differ across pages: {origins}"


def _page_scroll_areas(window: WinpodxWindow, index: int) -> list[QScrollArea]:
    page = window.pages.widget(index)
    return page.findChildren(QScrollArea)


def test_page_title_stays_pinned_after_scroll(contract_window: WinpodxWindow) -> None:
    app = QApplication.instance()
    assert app is not None
    for index, name in _PAGE_IDS:
        _show_page(contract_window, index)
        title = _visible_page_title(contract_window)
        origin = _title_origin(contract_window)
        assert origin == (_TITLE_X, _TITLE_Y), f"{name}: title origin {origin}"
        for scroll in _page_scroll_areas(contract_window, index):
            bar = scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        app.processEvents()
        after = _title_origin(contract_window)
        assert after == (_TITLE_X, _TITLE_Y), f"{name}: title moved after scroll {after}"
        assert title.isVisible(), f"{name}: pageTitle hidden after scroll"
        pos = title.mapTo(contract_window, QPoint(0, 0))
        assert pos.y() >= 0
        assert pos.y() + title.height() <= contract_window.height()


def test_page_actions_align_to_content_column_right_edge(
    contract_window: WinpodxWindow,
) -> None:
    app = QApplication.instance()
    assert app is not None
    for index, name in _PAGE_IDS:
        _show_page(contract_window, index)
        slot = contract_window.findChild(QWidget, "pageActions")
        assert slot is not None, f"{name}: missing #pageActions"
        children = [
            child
            for child in slot.findChildren(QWidget)
            if child is not slot and child.isVisible() and child.parentWidget() is slot
        ]
        if not children:
            continue
        right = slot.mapTo(contract_window, QPoint(slot.width(), 0)).x()
        assert right == _CONTENT_RIGHT, f"{name}: pageActions right {right} != {_CONTENT_RIGHT}"


def test_compact_rail_has_one_selection_visible_pill_and_no_mouse_focus(
    contract_window: WinpodxWindow,
) -> None:
    app = QApplication.instance()
    assert app is not None
    contract_window.resize(900, 720)
    app.processEvents()
    contract_window._apply_nav_compact()
    app.processEvents()

    pane = contract_window.sidebar
    assert pane.width() == NAV_PANE_COMPACT
    buttons = contract_window.nav_buttons
    assert sum(btn.isChecked() for btn in buttons) == 1
    checked = next(btn for btn in buttons if btn.isChecked())
    indicator = contract_window.nav_indicator
    assert indicator.isVisible()
    assert pane.rect().contains(indicator.geometry())
    mapped = checked.mapTo(pane, QPoint(0, 0))
    mid = indicator.y() + indicator.height() / 2
    assert mapped.y() - 2 <= mid <= mapped.y() + checked.height() + 2

    target = buttons[6]
    QTest.mouseClick(target, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert sum(btn.isChecked() for btn in buttons) == 1
    assert target.isChecked()
    assert not any(btn.hasFocus() for btn in buttons)
    for btn, label in zip(buttons, contract_window._nav_labels):
        assert btn.toolTip() == label


@pytest.mark.parametrize(("index", "name"), _PAGE_IDS)
def test_page_cards_share_a_left_anchored_column(
    contract_window: WinpodxWindow,
    index: int,
    name: str,
) -> None:
    _show_page(contract_window, index)
    cards = _visible_cards(contract_window)
    if not cards:
        return
    rights: list[int] = []
    max_right = contract_window.width() - PAGE_MARGIN_X + _SCROLL_GUTTER_TOLERANCE
    for card in cards:
        left, _top, right = _card_origin(contract_window, card)
        assert left == _TITLE_X, f"{name}: {card.objectName()} x {left} != {_TITLE_X}"
        assert right <= max_right, f"{name}: {card.objectName()} right {right} > {max_right}"
        if card.objectName() != "settingsSection":
            assert card.width() >= 600, f"{name}: {card.objectName()} width {card.width()} < 600"
            rights.append(right)
    if rights:
        assert len(set(rights)) == 1, f"{name}: card right edges differ: {rights}"
        assert rights[0] <= NAV_PANE_WIDTH + PAGE_MARGIN_X + CONTENT_MAX_WIDTH


@pytest.mark.parametrize(("index", "name"), _PAGE_IDS)
def test_page_card_stack_uses_contract_gaps(
    contract_window: WinpodxWindow,
    index: int,
    name: str,
) -> None:
    _show_page(contract_window, index)
    for group in _sibling_card_groups(contract_window):
        for prev, nxt in zip(group, group[1:]):
            gap = nxt.geometry().top() - prev.geometry().bottom() - 1
            assert gap == SPACE_XS, f"{name}: card gap {gap} != {SPACE_XS}"

    headers = _group_headers(contract_window)
    cards = [c for c in _visible_cards(contract_window) if c.objectName() != "settingsSection"]
    for header in headers:
        group = header.parentWidget()
        if group is None:
            continue
        captions = [
            lbl
            for lbl in group.findChildren(QLabel)
            if lbl.objectName() == "settingsGroupCaption" and lbl.isVisible()
        ]
        chrome = captions[-1] if captions else header
        chrome_bottom = chrome.mapTo(contract_window, QPoint(0, chrome.height())).y()
        below = [
            card
            for card in cards
            if card.mapTo(contract_window, QPoint(0, 0)).y() >= chrome_bottom - 1
            and (
                card.parentWidget() is group
                or (card.parentWidget() is not None and card.parentWidget().parentWidget() is group)
            )
        ]
        if not below:
            continue
        first = min(below, key=lambda card: card.mapTo(contract_window, QPoint(0, 0)).y())
        gap = first.mapTo(contract_window, QPoint(0, 0)).y() - chrome_bottom
        assert abs(gap - SPACE_S) <= _GROUP_GAP_SLACK, (
            f"{name}: header→card gap {gap} != {SPACE_S}±{_GROUP_GAP_SLACK}"
        )

    sections = [c for c in _visible_cards(contract_window) if c.objectName() == "settingsSection"]
    sections.sort(key=lambda frame: frame.mapTo(contract_window, QPoint(0, 0)).y())
    for prev, nxt in zip(sections, sections[1:]):
        parent = prev.parentWidget()
        if parent is None or parent is not nxt.parentWidget():
            continue
        layout = parent.layout()
        if layout is None:
            continue
        indices = []
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is prev or widget is nxt:
                indices.append(i)
        if len(indices) != 2 or indices[1] != indices[0] + 1:
            continue
        gap = nxt.geometry().top() - prev.geometry().bottom() - 1
        assert abs(gap - SPACE_XL) <= _GROUP_GAP_SLACK, (
            f"{name}: group gap {gap} != {SPACE_XL}±{_GROUP_GAP_SLACK}"
        )


@pytest.mark.parametrize(("index", "name"), _PAGE_IDS)
def test_page_cards_use_contract_padding_and_control_height(
    contract_window: WinpodxWindow,
    index: int,
    name: str,
) -> None:
    _show_page(contract_window, index)
    from winpodx.gui._toggle_switch import ToggleSwitch

    for card in _visible_cards(contract_window):
        if card.objectName() == "settingsSection":
            continue
        desc = getattr(card, "desc_label", None)
        if desc is not None and desc.isVisible() and desc.text():
            assert card.height() >= SETTINGS_ROW_MIN, (
                f"{name}: {card.objectName()} height {card.height()} < {SETTINGS_ROW_MIN}"
            )
        layout = card.layout()
        if layout is not None and card.objectName() in {"settingsCard", "sessionCard"}:
            margins = layout.contentsMargins()
            pad = (margins.left(), margins.top(), margins.right(), margins.bottom())
            if pad != (0, 0, 0, 0):
                assert pad == (SPACE_L, SPACE_L, SPACE_L, SPACE_L), (
                    f"{name}: {card.objectName()} padding {pad} != {(SPACE_L,) * 4}"
                )
        controls = (
            card.findChildren(QPushButton)
            + card.findChildren(QComboBox)
            + card.findChildren(QLineEdit)
        )
        for widget in controls:
            if not widget.isVisible():
                continue
            if isinstance(widget, ToggleSwitch):
                continue
            if widget.objectName() == "podPrimaryAction":
                continue
            assert widget.height() == CONTROL_HEIGHT_W11, (
                f"{name}: {type(widget).__name__} height {widget.height()} != {CONTROL_HEIGHT_W11}"
            )


@pytest.mark.parametrize(("index", "name"), _PAGE_IDS)
def test_page_has_no_horizontal_scrollbar(
    contract_window: WinpodxWindow,
    index: int,
    name: str,
) -> None:
    _show_page(contract_window, index)
    page = contract_window.pages.widget(index)
    for scroll in page.findChildren(QScrollArea):
        bar = scroll.horizontalScrollBar()
        assert bar.maximum() == 0, f"{name}: horizontal scrollbar range {bar.maximum()} > 0"


def test_nav_toggle_reflows_dashboard_and_keeps_cards_inside_the_window(
    contract_window: WinpodxWindow,
) -> None:
    app = QApplication.instance()
    assert app is not None
    contract_window.resize(844, 720)
    app.processEvents()
    contract_window._apply_nav_compact()
    app.processEvents()
    assert contract_window.sidebar.width() == NAV_PANE_COMPACT

    toggle = contract_window.sidebar.findChild(QPushButton, "navToggle")
    assert toggle is not None
    toggle.click()
    app.processEvents()
    app.processEvents()
    assert contract_window.sidebar.width() == NAV_PANE_WIDTH

    _show_page_no_resize(contract_window, 0)
    page = contract_window.pages.widget(0)
    for scroll in page.findChildren(QScrollArea):
        assert scroll.horizontalScrollBar().maximum() == 0
    for card in _visible_cards(contract_window):
        _left, _top, right = _card_origin(contract_window, card)
        assert right <= contract_window.width() - PAGE_MARGIN_X + _SCROLL_GUTTER_TOLERANCE, (
            f"{card.objectName()} right {right} spills past the window"
        )


def _show_page_no_resize(window: WinpodxWindow, index: int) -> None:
    window._switch_page(index)
    _stop_window_timers(window)
    app = QApplication.instance()
    assert app is not None
    app.processEvents()


def test_window_minimum_is_ratio_of_preferred_size_or_content_floor(
    contract_window: WinpodxWindow,
) -> None:
    pref_w, pref_h = contract_window._preferred_size
    pane_floor = contract_window.sidebar.minimumSizeHint().height() + TITLE_BAR_H
    assert contract_window.minimumHeight() == max(int(pref_h * MIN_SHRINK_RATIO), pane_floor)
    assert contract_window.minimumHeight() < pref_h
    assert contract_window.minimumWidth() >= int(pref_w * MIN_SHRINK_RATIO)
    assert contract_window.minimumWidth() < pref_w
    # The nav pane must never be shorter than its own stack (profile block,
    # search, items, footer) -- that is what squeezed the logo under the search box.
    contract_window.resize(contract_window.minimumSize())
    QApplication.instance().processEvents()
    assert contract_window.sidebar.height() >= contract_window.sidebar.minimumSizeHint().height()


@pytest.mark.parametrize("pane_open", [False, True], ids=["rail", "pane-open"])
def test_every_page_fits_at_the_minimum_window_size(
    contract_window: WinpodxWindow, pane_open: bool
) -> None:
    app = QApplication.instance()
    assert app is not None
    contract_window.resize(contract_window.minimumSize())
    app.processEvents()
    contract_window._apply_nav_compact()
    app.processEvents()
    assert contract_window.sidebar.width() == NAV_PANE_COMPACT
    if pane_open:
        toggle = contract_window.sidebar.findChild(QPushButton, "navToggle")
        assert toggle is not None
        toggle.click()
        app.processEvents()
        app.processEvents()
        assert contract_window.sidebar.width() == NAV_PANE_WIDTH
        assert contract_window._nav_slot.width() == NAV_PANE_COMPACT, "pane must overlay"
        assert contract_window.sidebar.x() == 0
        assert contract_window.sidebar.property("overlay") is True

    for index in range(contract_window.pages.count()):
        contract_window.pages.setCurrentIndex(index)
        contract_window._reflow_pages()
        app.processEvents()
        page = contract_window.pages.widget(index)
        for scroll in page.findChildren(QScrollArea):
            assert scroll.horizontalScrollBar().maximum() == 0, f"page {index} overflows"
        assert page.minimumSizeHint().width() <= contract_window.pages.width(), f"page {index}"


def test_overlaid_pane_light_dismisses_on_page_switch(contract_window: WinpodxWindow) -> None:
    app = QApplication.instance()
    assert app is not None
    contract_window.resize(contract_window.minimumSize())
    app.processEvents()
    contract_window._apply_nav_compact()
    toggle = contract_window.sidebar.findChild(QPushButton, "navToggle")
    assert toggle is not None
    toggle.click()
    app.processEvents()
    app.processEvents()
    assert contract_window._nav_overlay_active()

    contract_window._switch_page(3)
    app.processEvents()
    assert contract_window.sidebar.width() == NAV_PANE_COMPACT
    assert not contract_window._nav_overlay_active()
