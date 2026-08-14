# SPDX-License-Identifier: MIT

from __future__ import annotations

import os
from collections.abc import Callable
from inspect import getsource

import pytest

from winpodx.core.app import AppInfo
from winpodx.desktop import tray


def _app(
    name: str,
    *,
    full_name: str | None = None,
    executable: str = r"C:\Windows\System32\notepad.exe",
    hidden: bool = False,
    essential: bool = False,
) -> AppInfo:
    return AppInfo(
        name=name,
        full_name=full_name or name,
        executable=executable,
        hidden=hidden,
        essential=essential,
    )


def test_visible_tray_apps_filters_hidden_entries_without_a_count_cap() -> None:
    apps = [_app(f"app-{index:02d}") for index in range(30)]
    apps.insert(5, _app("hidden", hidden=True))

    visible = tray._visible_tray_apps(apps)

    assert len(visible) == 30
    assert all(app.name != "hidden" for app in visible)


@pytest.mark.parametrize(
    ("app", "tier"),
    [
        (_app("essential", essential=True), 0),
        (_app("notepad"), 1),
        (_app("third-party", executable=r"C:\Program Files\Vendor\app.exe"), 1),
        (_app("windows-tool", executable=r"C:\Windows\System32\tool.exe"), 2),
        (_app("registry-editor"), 3),
    ],
)
def test_tray_app_sort_key_assigns_the_expected_tier(app: AppInfo, tier: int) -> None:
    assert tray._tray_app_sort_key(app)[0] == tier


def test_visible_tray_apps_uses_casefolded_names_as_the_tiebreaker() -> None:
    apps = [_app("zulu", full_name="Zulu"), _app("alpha", full_name="alpha")]

    visible = tray._visible_tray_apps(apps)

    assert [app.name for app in visible] == ["alpha", "zulu"]


def test_tray_launch_kwargs_preserve_the_full_app_contract() -> None:
    app = _app("calc")
    app.launch_uri = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
    app.wm_class_hint = "winpodx-calc"
    app.args = "/foo"
    app.icon_path = "/tmp/calc.svg"
    app.rdp_overrides = {"scale": 140}

    kwargs = tray._tray_launch_kwargs(app)

    assert kwargs == {
        "launch_uri": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
        "wm_class_hint": "winpodx-calc",
        "default_args": "/foo",
        "app_icon": "/tmp/calc.svg",
        "rdp_overrides": {"scale": 140},
    }


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QApplication = QtWidgets.QApplication
QMenu = QtWidgets.QMenu


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _launcher_factory(captured: list[AppInfo]) -> Callable[[AppInfo], Callable[[], None]]:
    def make_launcher(app_info: AppInfo) -> Callable[[], None]:
        def launch() -> None:
            captured.append(app_info)

        return launch

    return make_launcher


def test_refresh_tray_apps_menu_populates_sorted_parented_actions(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = [_app("registry-editor"), _app("notepad"), _app("hidden", hidden=True)]
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: apps)
    menu = QMenu()
    captured: list[AppInfo] = []

    refreshed = tray._refresh_tray_apps_menu(menu, _launcher_factory(captured), lambda: None)
    actions = [action for action in menu.actions() if not action.isSeparator()]

    assert refreshed is True
    assert [action.text() for action in actions] == ["notepad", "registry-editor", "Full Desktop"]
    assert all(action.parent() is menu for action in actions)
    actions[0].trigger()
    assert captured == [apps[1]]


def test_refresh_tray_apps_menu_keeps_the_previous_menu_when_listing_fails(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [_app("notepad")])
    menu = QMenu()
    launcher = _launcher_factory([])
    assert tray._refresh_tray_apps_menu(menu, launcher, lambda: None) is True
    previous_actions = list(menu.actions())

    def fail_listing() -> list[AppInfo]:
        raise OSError("catalog unavailable")

    monkeypatch.setattr("winpodx.core.app.list_available_apps", fail_listing)

    assert tray._refresh_tray_apps_menu(menu, launcher, lambda: None) is False
    assert menu.actions() == previous_actions


def test_refresh_tray_apps_menu_keeps_the_previous_menu_for_invalid_app_metadata(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [_app("notepad")])
    menu = QMenu()
    launcher = _launcher_factory([])
    assert tray._refresh_tray_apps_menu(menu, launcher, lambda: None) is True
    previous_actions = list(menu.actions())
    invalid = _app("invalid")
    invalid.full_name = 7
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [invalid])

    assert tray._refresh_tray_apps_menu(menu, launcher, lambda: None) is False
    assert menu.actions() == previous_actions


def test_refresh_tray_apps_menu_keeps_full_desktop_when_no_apps_exist(
    qt_app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [])
    menu = QMenu()

    assert tray._refresh_tray_apps_menu(menu, _launcher_factory([]), lambda: None) is True

    actions = [action for action in menu.actions() if not action.isSeparator()]
    assert [action.text() for action in actions] == [
        "(no apps - run 'winpodx setup')",
        "Full Desktop",
    ]
    assert actions[0].isEnabled() is False


def test_run_tray_wires_app_menu_to_open_and_timer_refresh() -> None:
    source = getsource(tray.run_tray)

    assert "apps_menu.aboutToShow.connect(_rebuild_apps_menu)" in source
    assert "_refresh_tray_apps_menu(apps_menu, make_launcher, on_desktop)" in source
    assert "(_rebuild_apps_menu, _rebuild_sessions_menu, _rebuild_devices_menu)" in source
