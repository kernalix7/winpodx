# SPDX-License-Identifier: MIT
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QWidget

from winpodx.core.config import Config
from winpodx.gui.main_window import WinpodxWindow
from winpodx.gui.theme import (
    NAV_PANE_COMPACT,
    NAV_PANE_WIDTH,
    PAGE_MARGIN_TOP,
    PAGE_MARGIN_X,
    TITLE_BAR_H,
)

_PAGE_COUNT = 8
_TARGET_NAMES = frozenset(
    {
        "settingsCard",
        "sessionCard",
        "deviceCard",
        "aboutDeviceCard",
        "settingsSection",
        "quickActions",
        "workspaceSurface",
        "pageActions",
        "pageTitle",
    }
)


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _stub_window_side_effects(monkeypatch: pytest.MonkeyPatch) -> Config:
    cfg = Config()
    cfg.pod.initialized = True
    cfg.pod.backend = "podman"
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))
    monkeypatch.setattr("winpodx.gui.main_window.list_available_apps", lambda: [])
    monkeypatch.setattr(
        "winpodx.gui.main_window.WinpodxWindow._start_status_timer", lambda self: None
    )
    monkeypatch.setattr(
        "winpodx.gui.main_window.WinpodxWindow._on_follow_app_log", lambda self: None
    )
    monkeypatch.setattr(
        "winpodx.gui.main_window.WinpodxWindow._maybe_run_first_launch_checks", lambda self: None
    )
    monkeypatch.setattr(
        "winpodx.gui._main_window_info.InfoPageMixin._refresh_info", lambda self: None
    )
    monkeypatch.setattr(
        "winpodx.gui._main_window_pod.PodStatusMixin._refresh_pod_status", lambda self: None
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
    for attr in ("_dashboard_timer", "_sessions_timer", "status_timer", "_info_auto_timer"):
        timer = getattr(window, attr, None)
        if timer is not None:
            timer.stop()


@pytest.fixture
def contract_window(monkeypatch: pytest.MonkeyPatch) -> WinpodxWindow:
    app = _ensure_qapp()
    _stub_window_side_effects(monkeypatch)
    window = WinpodxWindow()
    _stop_window_timers(window)
    window.resize(1100, 720)
    window.show()
    _settle(window)
    yield window
    _stop_window_timers(window)
    window.close()
    window.deleteLater()
    app.processEvents()


def _settle(window: WinpodxWindow, width: int | None = None, height: int | None = None) -> None:
    app = QApplication.instance()
    assert app is not None
    if width is not None and height is not None:
        window.resize(width, height)
    window._apply_nav_compact()
    window._reflow_pages()
    app.processEvents()
    app.processEvents()


def _window_text(widget: QWidget) -> str:
    text = getattr(widget, "text", None)
    return text() if callable(text) else ""


def _widget_details(window: WinpodxWindow, widget: QWidget | None) -> str:
    if widget is None:
        return "widget=<missing> text='' geometry=<missing>"
    point = widget.mapTo(window, QPoint(0, 0))
    return (
        f"widget={widget.objectName() or type(widget).__name__!r} text={_window_text(widget)!r} "
        f"geometry=({point.x()},{point.y()},{widget.width()},{widget.height()})"
    )


def _context(window: WinpodxWindow, page: int, width: int, height: int, sequence: str) -> str:
    overlay = window._nav_overlay_active()
    state = "overlay" if overlay else ("compact" if window.sidebar.width() == 48 else "expanded")
    return (
        f"page={page} requested={width}x{height} actual={window.width()}x{window.height()} "
        f"sequence={sequence} pane={state}"
    )


def _visible_title(window: WinpodxWindow) -> QLabel | None:
    titles = [
        title
        for title in window.findChildren(QLabel)
        if title.objectName() == "pageTitle" and title.isVisible()
    ]
    return min(titles, key=lambda title: title.mapTo(window, QPoint()).y(), default=None)


def _check_invariants(
    window: WinpodxWindow,
    page_index: int,
    width: int,
    height: int,
    sequence: str,
    failures: list[str],
) -> None:
    context = _context(window, page_index, width, height, sequence)
    page = window.pages.widget(page_index)
    for scroll in page.findChildren(QScrollArea):
        maximum = scroll.horizontalScrollBar().maximum()
        if maximum != 0:
            failures.append(
                f"{context} | invariant=horizontal-scroll | "
                f"maximum={maximum} {_widget_details(window, scroll)}"
            )
    overlay = window._nav_overlay_active()
    right_limit = window.width() - PAGE_MARGIN_X + 12
    for widget in window.findChildren(QWidget):
        if not widget.isVisible() or widget.objectName() not in _TARGET_NAMES:
            continue
        point = widget.mapTo(window, widget.rect().topLeft())
        right = point.x() + widget.width()
        if point.x() < 0 or right > window.width() or (not overlay and right > right_limit):
            failures.append(
                f"{context} | invariant=page-bounds | right={right} limit={right_limit} "
                f"{_widget_details(window, widget)}"
            )
    title = _visible_title(window)
    if title is None:
        failures.append(
            f"{context} | invariant=page-title-visible | {_widget_details(window, None)}"
        )
    else:
        point = title.mapTo(window, QPoint())
        expected = (window._nav_slot.width() + PAGE_MARGIN_X, TITLE_BAR_H + PAGE_MARGIN_TOP)
        if (point.x(), point.y()) != expected:
            failures.append(
                f"{context} | invariant=page-title-origin | "
                f"actual={(point.x(), point.y())} expected={expected} "
                f"{_widget_details(window, title)}"
            )
    sidebar = window.sidebar
    if sidebar.width() not in (NAV_PANE_COMPACT, NAV_PANE_WIDTH):
        failures.append(f"{context} | invariant=sidebar-width | {_widget_details(window, sidebar)}")
    if sidebar.x() != 0 or sidebar.height() != window.height() - TITLE_BAR_H:
        failures.append(
            f"{context} | invariant=sidebar-geometry | {_widget_details(window, sidebar)}"
        )
    checked = sum(button.isChecked() for button in window.nav_buttons)
    if checked != 1:
        failures.append(
            f"{context} | invariant=nav-selection | checked={checked} "
            f"{_widget_details(window, sidebar)}"
        )
    indicator = window.nav_indicator
    if not indicator.isVisible() or not sidebar.rect().contains(indicator.geometry()):
        failures.append(
            f"{context} | invariant=nav-indicator | {_widget_details(window, indicator)}"
        )
    if overlay and (
        window._nav_slot.width() != NAV_PANE_COMPACT or sidebar.width() != NAV_PANE_WIDTH
    ):
        failures.append(
            f"{context} | invariant=nav-overlay-geometry | {_widget_details(window, sidebar)}"
        )
    if window.width() >= 1100 and overlay:
        failures.append(
            f"{context} | invariant=nav-overlay-breakpoint | {_widget_details(window, sidebar)}"
        )
    if window.width() < window.minimumWidth():
        failures.append(f"{context} | invariant=window-minimum | {_widget_details(window, window)}")


def _widths(minimum: int) -> tuple[int, ...]:
    # The breakpoint neighbours (1099/1100/1101) plus the two extremes are what
    # actually exercise the adaptive shell; the intermediate widths only re-ran
    # the same code path and dominated CI runtime, so they are not swept.
    return (minimum, 866, 1099, 1100, 1101, 1500)


@pytest.mark.parametrize("page_index", range(_PAGE_COUNT))
def test_main_window_resize_stress(contract_window: WinpodxWindow, page_index: int) -> None:
    window = contract_window
    app = QApplication.instance()
    assert app is not None
    window._switch_page(page_index)
    _stop_window_timers(window)
    _settle(window)
    failures: list[str] = []
    widths = _widths(window.minimumWidth())
    # Height only feeds the vertical layout guard; the minimum is the tight case
    # and one roomy value covers the rest.
    heights = (window.minimumHeight(), 900)
    for width in widths:
        for height in heights:
            window._nav_user_compact = None
            _settle(window, width, height)
            _check_invariants(window, page_index, width, height, "direct", failures)
            if width < 1100:
                window._nav_user_compact = None
                _settle(window, 1300, height)
                seq = f"wide-narrow-wide:{width}"
                _check_invariants(window, page_index, 1300, height, f"{seq}:wide-before", failures)
                _settle(window, width, height)
                _check_invariants(window, page_index, width, height, f"{seq}:narrow", failures)
                _settle(window, 1300, height)
                _check_invariants(window, page_index, 1300, height, f"{seq}:wide-after", failures)
            window._nav_user_compact = None
            _settle(window, 1300, height)
            toggle = window.sidebar.findChild(QPushButton, "navToggle")
            assert toggle is not None
            toggle.click()
            _settle(window)
            _check_invariants(window, page_index, 1300, height, "pin-compact:wide", failures)
            _settle(window, width, height)
            _check_invariants(window, page_index, width, height, "pin-compact:shrink", failures)
            if width < 1100:
                window._nav_user_compact = None
                _settle(window, width, height)
                toggle.click()
                _settle(window)
                _check_invariants(window, page_index, width, height, "overlay-open", failures)
                _settle(window, width + 40, height)
                _check_invariants(
                    window, page_index, width + 40, height, "overlay-open:grow", failures
                )
            window._nav_user_compact = None
            _settle(window, width, height)
            window.showMaximized()
            _settle(window)
            _check_invariants(window, page_index, width, height, "maximize", failures)
            window.showNormal()
            _settle(window)
            _check_invariants(window, page_index, width, height, "restore", failures)
    if failures:
        pytest.fail("Resize stress violations:\n" + "\n".join(failures))
