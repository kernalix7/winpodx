# SPDX-License-Identifier: MIT

"""Regression guards for the tray Launch App submenu.

The tray is long-lived, so its app menu must reflect discovery / Hide-Show
changes made after tray startup. It must also honour AppInfo.hidden and pass
the complete AppInfo launch metadata to core.rdp.launch_app.

These are source-shape guards because CI has no display, matching the other
tray regression tests.
"""

from __future__ import annotations

from pathlib import Path

TRAY = Path(__file__).resolve().parent.parent / "src" / "winpodx" / "desktop" / "tray.py"


def _src() -> str:
    return TRAY.read_text(encoding="utf-8")


def _app_menu_block() -> str:
    src = _src()
    start = src.index('    apps_menu = QMenu(tr("Launch App"))')
    end = src.index("    menu.addMenu(apps_menu)", start)
    return src[start:end]


def test_app_menu_rebuilds_when_opened() -> None:
    block = _app_menu_block()
    assert "def _rebuild_apps_menu()" in block
    assert "apps_menu.aboutToShow.connect(_rebuild_apps_menu)" in block
    rebuild = block.index("def _rebuild_apps_menu()")
    listing = block.index("list_available_apps()", rebuild)
    assert listing > rebuild


def test_app_menu_filters_hidden_apps() -> None:
    block = _app_menu_block()
    assert "if not app_info.hidden" in block


def test_app_menu_does_not_arbitrarily_truncate_apps() -> None:
    block = _app_menu_block()
    assert "available_apps[:20]" not in block


def test_app_launcher_preserves_full_appinfo_contract() -> None:
    block = _app_menu_block()
    assert "app_info.executable" in block
    assert "launch_uri=app_info.launch_uri or None" in block
    assert "wm_class_hint=app_info.wm_class_hint or None" in block
    assert "default_args=app_info.args or None" in block
    assert "app_icon=app_info.icon_path or None" in block
    assert "rdp_overrides=app_info.rdp_overrides or None" in block


def test_app_actions_capture_appinfo_not_only_executable() -> None:
    block = _app_menu_block()
    assert "action.triggered.connect(make_launcher(app_info))" in block
    assert "make_launcher(app_info.executable" not in block


def test_full_desktop_remains_available() -> None:
    block = _app_menu_block()
    assert 'desktop_action = QAction(tr("Full Desktop"), apps_menu)' in block
    assert "desktop_action.triggered.connect(on_desktop)" in block


def test_app_menu_sorts_by_semantic_utility() -> None:
    block = _app_menu_block()
    assert "def _tray_app_sort_key(app_info)" in block
    assert "app_info.essential" in block
    assert "_TRAY_ADMIN_HINTS" in block
    assert "_TRAY_USER_WINDOWS_HINTS" in block
    assert "key=_tray_app_sort_key" in block


def test_app_menu_classifies_installed_apps_as_user_facing() -> None:
    block = _app_menu_block()
    assert '"/program files/"' in block
    assert '"/program files (x86)/"' in block
    assert '"/users/"' in block
    assert '"/windowsapps/"' in block


def test_app_menu_demotes_windows_system_utilities() -> None:
    block = _app_menu_block()
    assert '"/windows/system32/"' in block
    assert '"/windows/syswow64/"' in block
    assert "_TRAY_ADMIN_HINTS" in block


def test_app_menu_sort_has_stable_alphabetical_tiebreaker() -> None:
    block = _app_menu_block()
    assert "app_info.full_name.casefold()" in block
