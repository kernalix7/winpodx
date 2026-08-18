# SPDX-License-Identifier: MIT
"""Tests for the quick app launcher (PR #561, reworked to source from core).

The launcher used to re-parse ``.desktop`` files and grab raw keyboard input
via evdev/pynput. It now builds its list from ``core.app.list_available_apps``
and is opened via ``winpodx launch`` (DE shortcut), so these tests pin that the
reimplementation/hotkey machinery is gone and discovery is core-backed.
"""

import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QEnterEvent, QKeyEvent, QMouseEvent, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QWidget  # noqa: E402

from winpodx.core.app import AppInfo  # noqa: E402
from winpodx.gui import launcher, launcher_state  # noqa: E402

# Widgets need a live QApplication; a second one in the same process aborts.
_APP = QApplication.instance() or QApplication([])


def test_discover_apps_sources_from_core(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir
    from winpodx.gui import launcher

    for full, name in [("Word", "word"), ("Excel", "excel")]:
        d = data_dir() / "discovered" / name
        d.mkdir(parents=True)
        (d / "app.toml").write_text(
            f'name = "{name}"\nfull_name = "{full}"\n'
            f'executable = "C:\\\\x.exe"\ncategories = ["Office"]\n',
            encoding="utf-8",
        )
    # a hidden app must be skipped
    h = data_dir() / "discovered" / "noise1"
    h.mkdir(parents=True)
    (h / "app.toml").write_text(
        'name = "noise1"\nfull_name = "Noise"\nexecutable = "C:\\\\n.exe"\nhidden = true\n',
        encoding="utf-8",
    )

    entries = launcher.discover_apps()
    by_slug = {e.slug: e for e in entries}
    assert set(by_slug) == {"word", "excel"}  # hidden skipped, core-sourced
    assert by_slug["word"].name == "Word"
    # launches via the canonical winpodx path, not a bespoke Exec line
    assert by_slug["word"].exec_ == "winpodx app run word"


def test_no_global_hotkey_or_desktop_reparse(monkeypatch, tmp_path):
    """The evdev/pynput global-hotkey grab and .desktop re-parser are gone;
    activation is delegated to the DE via ``winpodx launch``."""
    from winpodx.gui import launcher

    for gone in (
        "_start_hotkey_listener",
        "_start_evdev_listener",
        "_start_pynput_listener",
        "parse_desktop_file",
        "HotkeySignals",
    ):
        assert not hasattr(launcher, gone), gone
    assert hasattr(launcher, "show_launcher")


def test_launch_command_dispatches_to_show_launcher(monkeypatch):
    """`winpodx launch` routes to the launcher entry point."""
    import argparse

    import winpodx.gui.launcher as launcher
    from winpodx.cli.main import _dispatch

    called = []
    monkeypatch.setattr(launcher, "show_launcher", lambda: called.append(True))
    _dispatch(argparse.Namespace(command="launch"))
    assert called == [True]


_LIVE_WINDOWS = []
_KEEPALIVE = []


class _FakeProc:
    pid = 4242


class _TrackedLauncherWindow(launcher.LauncherWindow):
    def __init__(self):
        super().__init__()
        _LIVE_WINDOWS.append(self)


class _NoExecMenu(QMenu):
    instances = []
    exec_points = []

    def __init__(self, parent=None):
        super().__init__(parent)
        _NoExecMenu.instances.append(self)

    def exec(self, *args):
        _NoExecMenu.exec_points.append(args)
        return None


@pytest.fixture(autouse=True)
def popen_calls(monkeypatch):
    calls = []

    def _fake(cmd, **kwargs):
        calls.append((list(cmd), kwargs))
        return _FakeProc()

    monkeypatch.setattr("winpodx.gui.launcher.subprocess.Popen", _fake)
    return calls


@pytest.fixture(autouse=True)
def _retire_launcher_windows():
    yield
    app = QApplication.instance()
    while _LIVE_WINDOWS:
        win = _LIVE_WINDOWS.pop()
        win._visible = False
        win._notification.hide_()
        win.hide()
        if app is not None:
            app.removeEventFilter(win)
        _KEEPALIVE.append(win)


def _keep(widget):
    _KEEPALIVE.append(widget)
    return widget


def _info(name, full_name, categories=None, **kwargs):
    return AppInfo(
        name=name,
        full_name=full_name,
        executable=f"C:\\Windows\\{name}.exe",
        categories=list(categories or []),
        **kwargs,
    )


def _default_infos():
    return [
        _info("word", "Word", ["Office"]),
        _info("excel", "Excel", ["Office"]),
        _info("powershell", "PowerShell", ["Development"]),
        _info("settings", "Settings", ["System"]),
        _info("vlc", "VLC Media Player", ["AudioVideo"]),
        _info("firefox", "Firefox", ["Network"]),
    ]


def _patch_config(monkeypatch, tmp_path, **options):
    path = tmp_path / "launcher-search.conf"
    if options:
        body = "\n".join(["[Launcher]"] + [f"{k} = {v}" for k, v in options.items()])
        path.write_text(body + "\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "CONFIG_PATH", str(path))
    return path


def _make_window(monkeypatch, tmp_path, apps=None, show=False, **options):
    _patch_config(monkeypatch, tmp_path, **options)
    infos = _default_infos() if apps is None else apps
    monkeypatch.setattr(launcher, "list_available_apps", lambda: list(infos))
    win = _TrackedLauncherWindow()
    if show:
        win.show_()
    return win


def _key(key):
    return QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)


def _click(pos, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(0.0, 0.0),
        QPointF(*pos),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def _widgets(layout, kind):
    out = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        widget = item.widget() if item else None
        if isinstance(widget, kind):
            out.append(widget)
    return out


def _tiles(win):
    return _widgets(win._grid_layout, launcher.RevealTile)


def _compact_items(win):
    return _widgets(win._compact_layout, launcher.CompactListItem)


def _names(widgets):
    return [w._entry.name for w in widgets]


@pytest.mark.parametrize(
    ("name", "categories", "expected"),
    [
        ("Microsoft Word", "", "Productivity"),
        ("Windows PowerShell", "", "Developer Tools"),
        ("Settings", "", "System"),
        ("VLC Media Player", "", "Media"),
        ("Mozilla Firefox", "", "Browser"),
        ("Acme Widget", "", "Other"),
        ("Acme Widget", "Office;Utility", "Productivity"),
    ],
)
def test_assign_category_maps_keywords_with_other_fallback(name, categories, expected):
    entry = launcher.AppEntry(name=name, categories=categories)
    assert launcher.assign_category(entry) == expected


def test_assign_category_returns_the_first_matching_group():
    entry = launcher.AppEntry(name="Office Terminal")
    assert launcher.assign_category(entry) == "Productivity"


def test_config_roundtrip_and_defaults_when_missing(monkeypatch, tmp_path):
    path = _patch_config(monkeypatch, tmp_path)
    cfg = launcher.load_config()
    assert not path.exists()
    assert cfg["Launcher"].getboolean("compact_mode", fallback=False) is False

    cfg["Launcher"]["compact_mode"] = "true"
    cfg["Launcher"]["reset_search_on_open"] = "false"
    launcher.save_config(cfg)

    reloaded = launcher.load_config()
    assert reloaded["Launcher"].getboolean("compact_mode") is True
    assert reloaded["Launcher"].getboolean("reset_search_on_open") is False


def test_load_config_recovers_from_an_unparsable_file(monkeypatch, tmp_path):
    path = _patch_config(monkeypatch, tmp_path)
    path.write_text("this is not ini at all\n", encoding="utf-8")
    cfg = launcher.load_config()
    assert "Launcher" in cfg
    assert cfg["Launcher"].getboolean("compact_mode", fallback=False) is False


def test_load_icon_prefers_an_absolute_path_over_the_theme(tmp_path):
    png = tmp_path / "icon.png"
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor(10, 200, 30))
    assert pixmap.save(str(png))

    from_file = launcher.load_icon(str(png))
    assert not from_file.isNull()
    assert from_file.pixmap(16, 16).toImage().pixelColor(8, 8) == QColor(10, 200, 30)

    missing = launcher.load_icon(str(tmp_path / "gone.png"))
    assert missing.pixmap(16, 16).toImage() != from_file.pixmap(16, 16).toImage()
    assert launcher.load_icon("", fallback="winpodx-no-such-icon").isNull() is True
    assert launcher.load_icon("winpodx-nope", fallback="winpodx-also-nope").isNull() is True


def test_discover_apps_maps_fields_dedups_and_skips_hidden(monkeypatch):
    infos = [
        _info(
            "word",
            "Microsoft Word",
            ["Office", "WordProcessor"],
            icon_path="/tmp/word.png",
            description="Word processor",
        ),
        _info("word", "Duplicate Word", ["Office"]),
        _info("ghost", "Ghost", ["Office"], hidden=True),
        _info("vlc", "", ["AudioVideo"]),
    ]
    monkeypatch.setattr(launcher, "list_available_apps", lambda: list(infos))

    entries = launcher.discover_apps()
    assert [e.slug for e in entries] == ["word", "vlc"]
    word = entries[0]
    assert word.name == "Microsoft Word"
    assert word.filename == "word.desktop"
    assert word.exec_ == "winpodx app run word"
    assert word.icon == "/tmp/word.png"
    assert word.categories == "Office;WordProcessor"
    assert word.comment == "Word processor"
    assert word.category == "Productivity"
    assert entries[1].name == "vlc"
    assert entries[1].category == "Media"


def test_reveal_tile_launches_on_left_click_only():
    entry = launcher.AppEntry(name="Word", slug="word")
    fired = []
    tile = _keep(launcher.RevealTile(entry, fired.append))

    tile.mousePressEvent(_click((1.0, 1.0), Qt.MouseButton.RightButton))
    assert fired == []
    tile.mousePressEvent(_click((1.0, 1.0)))
    assert fired == [entry]


def test_reveal_tile_tracks_hover_and_selection_while_painting():
    tile = _keep(launcher.RevealTile(launcher.AppEntry(name="Word", slug="word"), lambda _e: None))
    tile.resize(130, 108)
    canvas = QPixmap(tile.size())
    tile.render(canvas)
    assert tile._hovered is False

    tile.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert tile._hovered is True
    tile.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(20, 20),
            QPointF(20, 20),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert tile._cursor_pos == QPointF(20, 20)

    tile.hover_progress = 1.0
    assert tile.hover_progress == 1.0
    tile.set_selected(True)
    tile.render(canvas)
    assert tile._selected is True

    tile.leaveEvent(QEvent(QEvent.Type.Leave))
    assert tile._hovered is False
    assert tile._cursor_pos == QPointF(-1, -1)


def test_long_cjk_name_keeps_the_wrap_label_width_fixed(monkeypatch, tmp_path):
    long_name = "한글로 아주 긴 애플리케이션 이름 " * 8
    win = _make_window(monkeypatch, tmp_path, apps=[_info("cjk", long_name)], show=True)
    label = _tiles(win)[0]._name_label

    # #553: a variable-width word-wrap label inside a resizable QScrollArea
    # recurses through heightForWidth until it SIGSEGVs.
    assert label.wordWrap() is True
    assert label.minimumWidth() == label.maximumWidth() == 180
    assert label.maximumHeight() == 38

    win._scroll_area.resize(300, 300)
    win._scroll_area.render(QPixmap(win._scroll_area.size()))
    narrow = (label.width(), label.height())
    win._scroll_area.resize(640, 300)
    win._scroll_area.render(QPixmap(win._scroll_area.size()))
    assert (label.width(), label.height()) == narrow
    assert label.width() == 180


def test_compact_list_item_styles_and_activation():
    entry = launcher.AppEntry(name="Excel", slug="excel")
    fired = []
    item = _keep(launcher.CompactListItem(entry, fired.append))
    assert item.height() == 40
    assert "transparent" in item.styleSheet()

    item.set_selected(True)
    assert "96,205,255" in item.styleSheet()
    item.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert item._hovered is True
    item.set_selected(False)
    assert "#2B2B2B" in item.styleSheet()
    item.leaveEvent(QEvent(QEvent.Type.Leave))
    assert "transparent" in item.styleSheet()

    item.mousePressEvent(_click((1.0, 1.0), Qt.MouseButton.RightButton))
    item.keyPressEvent(_key(Qt.Key.Key_Escape))
    assert fired == []
    item.mousePressEvent(_click((1.0, 1.0)))
    item.keyPressEvent(_key(Qt.Key.Key_Return))
    assert fired == [entry, entry]


def test_pill_bar_exposes_every_category_and_reports_selection():
    picked = []
    bar = _keep(launcher.PillBar(launcher.CATEGORY_ORDER, picked.append))
    assert list(bar._buttons) == ["All"] + launcher.CATEGORY_ORDER
    assert bar._active == "All"
    assert "#60CDFF" in bar._buttons["All"].styleSheet()

    bar._buttons["Media"].click()
    assert picked == ["Media"]
    assert bar._active == "Media"
    assert "#60CDFF" in bar._buttons["Media"].styleSheet()
    assert "#2B2B2B" in bar._buttons["All"].styleSheet()


def test_launch_notification_renders_name_and_hides_once(tmp_path):
    png = tmp_path / "note.png"
    pixmap = QPixmap(20, 20)
    pixmap.fill(QColor(200, 10, 10))
    assert pixmap.save(str(png))

    note = _keep(launcher.LaunchNotification())
    note.show_for("Word", str(png))
    assert note._text_label.text() == "Opening Word…"
    assert not note._icon_label.pixmap().isNull()
    assert note._visible is True

    note.show_for("Excel", "winpodx-no-such-icon")
    assert note._text_label.text() == "Opening Excel…"

    note.hide_()
    assert note._visible is False
    assert note.isHidden() is True
    note.hide_()
    assert note._visible is False


def test_window_builds_one_tile_per_app_in_four_columns(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path)
    tiles = _tiles(win)
    assert _names(tiles) == [
        "Word",
        "Excel",
        "PowerShell",
        "Settings",
        "VLC Media Player",
        "Firefox",
    ]
    positions = [win._grid_layout.getItemPosition(i)[:2] for i in range(len(tiles))]
    assert positions == [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1)]
    assert win._compact_mode is False
    assert win._content_stack.currentIndex() == 0
    assert win.maximumHeight() == launcher.WINDOW_HEIGHT
    assert win.search_bar.placeholderText() == launcher.SEARCH_PLACEHOLDER


def test_search_filters_case_insensitively_on_the_display_name(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path)
    win.search_bar.setText("  SET  ")
    assert win._search_text == "SET"
    assert _names(_tiles(win)) == ["Settings"]

    win.search_bar.setText("e")
    assert _names(_tiles(win)) == [
        "Excel",
        "PowerShell",
        "Settings",
        "VLC Media Player",
        "Firefox",
    ]

    win.search_bar.setText("")
    assert len(_tiles(win)) == 6


def test_category_filter_composes_with_the_search_text(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path)
    win._set_category("Productivity")
    assert win._active_category == "Productivity"
    assert _names(_tiles(win)) == ["Word", "Excel"]

    win.search_bar.setText("exc")
    assert _names(_tiles(win)) == ["Excel"]

    win._set_category("Browser")
    assert _tiles(win) == []
    win.search_bar.setText("")
    assert _names(_tiles(win)) == ["Firefox"]


def test_empty_result_renders_the_placeholder_across_the_grid(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path)
    win.search_bar.setText("zzzz")
    assert _tiles(win) == []
    labels = _widgets(win._grid_layout, QLabel)
    assert [lbl.text() for lbl in labels] == ["No apps found"]
    assert win._grid_layout.getItemPosition(0)[3] == 4


def test_search_resets_navigation_state_and_hides_the_gear(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    win._nav_index = 3
    win._nav_state = 2
    win._compact_index = 2
    win._pill_focus = 1
    assert win._gear_btn.isHidden() is False

    win.search_bar.setText("word")
    assert (win._nav_index, win._nav_state, win._compact_index, win._pill_focus) == (
        -1,
        0,
        -1,
        -1,
    )
    assert win._gear_btn.isHidden() is True

    win.search_bar.setText("")
    assert win._gear_btn.isHidden() is False


def test_launch_spawns_the_canonical_slug_argv(monkeypatch, tmp_path, popen_calls):
    win = _make_window(monkeypatch, tmp_path, show=True)
    win._launch_app(_tiles(win)[1]._entry)

    assert len(popen_calls) == 1
    cmd, kwargs = popen_calls[0]
    assert cmd == ["winpodx", "app", "run", "excel"]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] == launcher.subprocess.DEVNULL
    assert kwargs["stderr"] == launcher.subprocess.DEVNULL
    assert win._visible is False
    assert win._notification._text_label.text() == "Opening Excel…"


def test_launch_without_a_slug_falls_back_to_the_exec_line(monkeypatch, tmp_path, popen_calls):
    win = _make_window(monkeypatch, tmp_path, apps=[])
    entry = launcher.AppEntry(name="Legacy", exec_="winpodx app run legacy", slug="")
    win._launch_app(entry)
    assert popen_calls[0][0] == ["winpodx", "app", "run", "legacy"]


def test_spawn_failures_are_reported_without_raising(monkeypatch, tmp_path, capsys):
    win = _make_window(monkeypatch, tmp_path)

    def _boom(cmd, **kwargs):
        raise OSError("winpodx not on PATH")

    monkeypatch.setattr("winpodx.gui.launcher.subprocess.Popen", _boom)
    win._launch_app(_tiles(win)[0]._entry)
    err = capsys.readouterr().err
    assert "Failed to launch Word" in err
    assert "winpodx not on PATH" in err
    assert win._notification._text_label.text() == ""

    win._launch_winpodx()
    assert "Failed to launch WinPodX" in capsys.readouterr().err


def test_the_brand_button_opens_the_main_gui(monkeypatch, tmp_path, popen_calls):
    win = _make_window(monkeypatch, tmp_path, show=True)
    win._winpodx_btn.click()
    assert popen_calls[0][0] == ["winpodx", "gui"]
    assert win._visible is False
    assert win._notification._text_label.text() == "Opening WinPodX…"


def test_return_launches_the_first_filtered_app(monkeypatch, tmp_path, popen_calls):
    win = _make_window(monkeypatch, tmp_path)
    win.search_bar.setText("e")
    win.search_bar.returnPressed.emit()
    assert popen_calls[0][0] == ["winpodx", "app", "run", "excel"]

    popen_calls.clear()
    win.search_bar.setText("zzzz")
    win.search_bar.returnPressed.emit()
    assert popen_calls == []


def test_return_in_a_collapsed_compact_window_does_nothing(monkeypatch, tmp_path, popen_calls):
    win = _make_window(monkeypatch, tmp_path, compact_mode="true")
    assert win._compact_mode is True
    assert win._content_stack.currentIndex() == 1
    assert win._pill_bar.isHidden() is True
    assert win.maximumHeight() == 52
    win._on_return()
    assert popen_calls == []


def test_down_and_up_from_the_search_bar_enter_the_pill_row(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    assert win._pill_bar.isVisible() is True

    assert win.eventFilter(win.search_bar, _key(Qt.Key.Key_Down)) is True
    assert win._nav_state == 1
    assert win._pill_focus == 0

    win._clear_pill_focus()
    win._nav_state = 0
    assert win.eventFilter(win.search_bar, _key(Qt.Key.Key_Up)) is True
    assert win._pill_focus == len(win._pill_bar._buttons) - 1


def test_pill_row_arrows_wrap_nowhere_and_enter_applies_the_category(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    button = win._pill_bar._buttons["All"]
    keys = list(win._pill_bar._buttons)
    win.eventFilter(win.search_bar, _key(Qt.Key.Key_Down))

    assert win.eventFilter(button, _key(Qt.Key.Key_Right)) is True
    assert win._pill_focus == 1
    assert win.eventFilter(button, _key(Qt.Key.Key_Left)) is True
    assert win._pill_focus == 0
    assert win.eventFilter(button, _key(Qt.Key.Key_Left)) is True
    assert win._pill_focus == -1
    assert win._nav_state == 0
    assert win.focusWidget() is win.search_bar

    win._nav_state = 1
    win._pill_focus = len(keys) - 1
    assert win.eventFilter(button, _key(Qt.Key.Key_Right)) is True
    assert win._pill_focus == len(keys) - 1

    win._pill_focus = keys.index("Media")
    assert win.eventFilter(button, _key(Qt.Key.Key_Return)) is True
    assert win._active_category == "Media"
    assert win._pill_focus == -1
    assert _names(_tiles(win)) == ["VLC Media Player"]


def test_pill_row_down_enters_the_grid_and_up_returns_to_search(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    button = win._pill_bar._buttons["All"]
    win.eventFilter(win.search_bar, _key(Qt.Key.Key_Down))

    assert win.eventFilter(button, _key(Qt.Key.Key_Down)) is True
    assert win._nav_state == 2
    assert win._nav_index == 0
    assert _tiles(win)[0]._selected is True

    win._nav_state = 1
    win._pill_focus = 2
    assert win.eventFilter(button, _key(Qt.Key.Key_Up)) is True
    assert win._nav_state == 0
    assert win._pill_focus == -1
    assert win.focusWidget() is win.search_bar

    win._nav_state = 1
    win._pill_focus = 1
    assert win.eventFilter(button, _key(Qt.Key.Key_A)) is True
    assert win._nav_state == 0
    assert win._pill_focus == -1


def test_grid_arrow_navigation_and_enter_launch(monkeypatch, tmp_path, popen_calls):
    win = _make_window(monkeypatch, tmp_path, show=True)
    tiles = _tiles(win)
    win._nav_state = 2
    win._focus_tile(0)
    assert tiles[0]._selected is True

    assert win.eventFilter(tiles[0], _key(Qt.Key.Key_Right)) is True
    assert win._nav_index == 1
    assert tiles[1]._selected is True
    assert tiles[0]._selected is False

    assert win.eventFilter(tiles[1], _key(Qt.Key.Key_Down)) is True
    assert win._nav_index == 5
    assert win.eventFilter(tiles[5], _key(Qt.Key.Key_Up)) is True
    assert win._nav_index == 1
    assert win.eventFilter(tiles[1], _key(Qt.Key.Key_Left)) is True
    assert win._nav_index == 0

    assert win.eventFilter(tiles[0], _key(Qt.Key.Key_Left)) is True
    assert win._nav_index == 0
    win._focus_tile(5)
    assert win.eventFilter(tiles[5], _key(Qt.Key.Key_Right)) is True
    assert win._nav_index == 5
    assert win.eventFilter(tiles[5], _key(Qt.Key.Key_Down)) is True
    assert win._nav_index == 5

    assert win.eventFilter(tiles[5], _key(Qt.Key.Key_Return)) is True
    assert popen_calls[0][0] == ["winpodx", "app", "run", "firefox"]


def test_grid_up_from_the_top_row_returns_to_the_pills(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    tiles = _tiles(win)
    win._nav_state = 2
    win._focus_tile(2)

    assert win.eventFilter(tiles[2], _key(Qt.Key.Key_Up)) is True
    assert win._nav_state == 1
    assert win._pill_focus == 0
    assert all(not tile._selected for tile in tiles)


def test_an_unhandled_grid_key_hands_focus_back_to_search(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    tiles = _tiles(win)
    win._nav_state = 2
    win._focus_tile(1)
    assert win.focusWidget() is tiles[1]

    assert win.eventFilter(tiles[1], _key(Qt.Key.Key_A)) is True
    assert win._nav_index == -1
    assert all(not tile._selected for tile in tiles)
    assert win.focusWidget() is win.search_bar


def test_escape_and_an_outside_click_hide_the_window(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    assert win.eventFilter(win.search_bar, _key(Qt.Key.Key_Escape)) is True
    assert win._visible is False

    win.show_()
    far = win.geometry().bottomRight() + QPoint(500, 500)
    win.eventFilter(_keep(QWidget()), _click((float(far.x()), float(far.y()))))
    assert win._visible is False

    win.show_()
    inside = win.geometry().center()
    win.eventFilter(win.search_bar, _click((float(inside.x()), float(inside.y()))))
    assert win._visible is True


def test_window_key_press_escape_hides_and_f5_toggles(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    win.keyPressEvent(_key(Qt.Key.Key_Escape))
    assert win._visible is False
    win.keyPressEvent(_key(Qt.Key.Key_F5))
    assert win._visible is True
    win.keyPressEvent(_key(Qt.Key.Key_F5))
    assert win._visible is False


def test_toggle_and_hide_are_idempotent(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path)
    assert win._visible is False
    win.hide_()
    assert win._visible is False
    win.toggle()
    assert win._visible is True
    win.toggle()
    assert win._visible is False


def test_reset_search_on_open_persists_and_clears_the_query(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path)
    config_path = tmp_path / "launcher-search.conf"
    assert win._reset_search_on_open is False

    win.search_bar.setText("word")
    win.show_()
    assert win.search_bar.text() == "word"

    win._toggle_reset_search(True)
    assert "reset_search_on_open = true" in config_path.read_text(encoding="utf-8")
    win.show_()
    assert win.search_bar.text() == ""
    assert win._search_text == ""


def test_toggle_compact_switches_the_view_and_persists(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path)
    config_path = tmp_path / "launcher-search.conf"

    win._toggle_compact(True)
    assert win._compact_mode is True
    assert win._content_stack.currentIndex() == 1
    assert win._pill_bar.isHidden() is True
    assert win._outer_layout.spacing() == 0
    assert "compact_mode = true" in config_path.read_text(encoding="utf-8")

    win._toggle_compact(False)
    assert win._content_stack.currentIndex() == 0
    assert win._outer_layout.spacing() == 8
    assert "compact_mode = false" in config_path.read_text(encoding="utf-8")
    assert len(_tiles(win)) == 6


def test_compact_mode_lists_at_most_five_matches(monkeypatch, tmp_path):
    infos = [_info(f"a{i}", f"App {i}") for i in range(8)]
    win = _make_window(monkeypatch, tmp_path, apps=infos, compact_mode="true")
    assert _compact_items(win) == []

    win.search_bar.setText("app")
    assert _names(_compact_items(win)) == ["App 0", "App 1", "App 2", "App 3", "App 4"]
    assert win.maximumHeight() == 52 + 5 * 42 + 8

    win.search_bar.setText("app 7")
    assert _names(_compact_items(win)) == ["App 7"]
    assert win.maximumHeight() == 52 + 42 + 8


def test_compact_mode_shows_a_placeholder_then_collapses(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, compact_mode="true")
    assert win.maximumHeight() == 52

    win.search_bar.setText("zzzz")
    assert _compact_items(win) == []
    assert [lbl.text() for lbl in _widgets(win._compact_layout, QLabel)] == ["No apps found"]
    assert win._compact_item_count() == 1
    assert win.maximumHeight() == 52 + 42 + 8

    win.search_bar.setText("")
    assert win._compact_layout.count() == 0
    assert win.maximumHeight() == 52


def test_compact_settings_page_lists_toggles_and_applies_them(monkeypatch, tmp_path):
    infos = _default_infos()
    win = _make_window(monkeypatch, tmp_path, apps=infos, compact_mode="true")
    win.search_bar.setText("word")

    def _rows():
        return [win._compact_layout.itemAt(i).widget() for i in range(win._compact_layout.count())]

    win._on_gear_clicked()
    assert win._settings_mode is True
    assert win.search_bar.text() == ""
    assert win._search_text == ""
    assert [row.findChildren(QLabel)[0].text() for row in _rows()] == [
        "Clear search on open",
        "Compact mode",
        "Refresh apps",
    ]
    assert _rows()[1].findChildren(QLabel)[1].text() == "✓"
    assert _rows()[0].findChildren(QLabel)[1].text() == ""
    assert win.maximumHeight() == 52 + 3 * 42 + 8

    _rows()[0].mousePressEvent(None)
    assert win._reset_search_on_open is True
    assert _rows()[0].findChildren(QLabel)[1].text() == "✓"

    infos[:] = [_info("newapp", "New App")]
    _rows()[2].mousePressEvent(None)
    assert [entry.slug for entry in win._apps] == ["newapp"]

    _rows()[1].mousePressEvent(None)
    assert win._compact_mode is False
    assert win._settings_mode is False
    assert win._content_stack.currentIndex() == 0
    assert _names(_tiles(win)) == ["New App"]


def test_escape_in_compact_mode_clears_search_then_settings_then_hides(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, compact_mode="true", show=True)
    win.search_bar.setText("word")

    assert win.eventFilter(win.search_bar, _key(Qt.Key.Key_Escape)) is True
    assert win.search_bar.text() == ""
    assert win._visible is True

    win._on_gear_clicked()
    assert win._settings_mode is True
    assert win.eventFilter(win.search_bar, _key(Qt.Key.Key_Escape)) is True
    assert win._settings_mode is False
    assert win._visible is True

    assert win.eventFilter(win.search_bar, _key(Qt.Key.Key_Escape)) is True
    assert win._visible is False


def test_compact_keyboard_navigation_and_enter_launch(monkeypatch, tmp_path, popen_calls):
    infos = [_info(f"a{i}", f"App {i}") for i in range(4)]
    win = _make_window(monkeypatch, tmp_path, apps=infos, compact_mode="true", show=True)
    assert win._pill_bar.isVisible() is False

    win.search_bar.setText("app")
    items = _compact_items(win)
    assert len(items) == 4

    assert win.eventFilter(win.search_bar, _key(Qt.Key.Key_Down)) is True
    assert win._compact_index == 0
    assert items[0]._selected is True

    assert win.eventFilter(items[0], _key(Qt.Key.Key_Down)) is True
    assert win._compact_index == 1
    assert items[1]._selected is True
    assert items[0]._selected is False

    assert win.eventFilter(items[1], _key(Qt.Key.Key_Up)) is True
    assert win._compact_index == 0
    assert win.eventFilter(items[0], _key(Qt.Key.Key_Up)) is True
    assert win._compact_index == 0

    assert win.eventFilter(items[0], _key(Qt.Key.Key_A)) is True
    assert win._compact_index == -1
    assert win.focusWidget() is win.search_bar

    assert win.eventFilter(win.search_bar, _key(Qt.Key.Key_Up)) is True
    assert win._compact_index == 3
    assert win.eventFilter(items[3], _key(Qt.Key.Key_Return)) is True
    assert popen_calls[0][0] == ["winpodx", "app", "run", "a3"]
    assert win._visible is False


def test_settings_menu_reflects_state_and_its_actions_apply(monkeypatch, tmp_path):
    infos = _default_infos()
    win = _make_window(monkeypatch, tmp_path, apps=infos, show=True)
    monkeypatch.setattr(launcher, "QMenu", _NoExecMenu)
    _NoExecMenu.instances.clear()
    _NoExecMenu.exec_points.clear()

    win._on_gear_clicked()
    assert len(_NoExecMenu.exec_points) == 1
    actions = [a for a in _NoExecMenu.instances[-1].actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == [
        "Clear search on open",
        "Compact mode",
        "Refresh apps",
    ]
    assert actions[0].isChecked() is False
    assert actions[1].isChecked() is False

    actions[0].trigger()
    assert win._reset_search_on_open is True

    infos[:] = [_info("newapp", "New App")]
    actions[2].trigger()
    assert _names(_tiles(win)) == ["New App"]

    actions[1].trigger()
    assert win._compact_mode is True
    assert win._content_stack.currentIndex() == 1


def test_container_membership_helpers(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    tile = _tiles(win)[0]
    pill = win._pill_bar._buttons["All"]

    assert win._is_in_grid(tile) is True
    assert win._is_in_pills(pill) is True
    assert win._is_in_grid(pill) is False
    assert win._is_in_pills(tile) is False
    assert win._is_in_compact(tile) is False
    assert win._is_in_grid(_keep(QWidget())) is False


def test_pill_focus_ring_ignores_out_of_range_indexes(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path)
    keys = list(win._pill_bar._buttons)

    win._pill_focus = -1
    win._update_pill_styles()
    win._pill_focus = len(keys) + 5
    win._update_pill_styles()
    win._focus_pill_button(len(keys) + 5)
    assert win._pill_bar._active == "All"
    assert all(
        "2px solid #60CDFF" not in btn.styleSheet() for btn in win._pill_bar._buttons.values()
    )

    win._pill_focus = len(keys) - 1
    win._update_pill_styles()
    win._focus_pill_button(win._pill_focus)
    assert "2px solid #60CDFF" in win._pill_bar._buttons[keys[-1]].styleSheet()
    assert "2px solid #60CDFF" not in win._pill_bar._buttons["All"].styleSheet()


def test_focus_leaving_the_window_hides_it(monkeypatch, tmp_path):
    win = _make_window(monkeypatch, tmp_path, show=True)
    handle = win.windowHandle()
    assert handle is not None

    win._on_focus_changed(handle)
    assert win._visible is True

    other = _keep(QWidget())
    other.show()
    win._on_focus_changed(other.windowHandle())
    assert win._visible is False
    other.hide()

    win._on_focus_changed(None)
    assert win._visible is False


def test_show_launcher_builds_the_window_and_runs_the_loop(monkeypatch, tmp_path):
    _patch_config(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "list_available_apps", _default_infos)
    monkeypatch.setattr(launcher, "LauncherWindow", _TrackedLauncherWindow)
    monkeypatch.setattr(launcher.QApplication, "exec", lambda self: 17)

    app = QApplication.instance()
    org, name = app.organizationName(), app.applicationName()
    try:
        assert launcher.show_launcher() == 17
        assert app.organizationName() == "WinPodX"
        assert app.applicationName() == "WinPodX Launcher"
    finally:
        app.setOrganizationName(org)
        app.setApplicationName(name)

    win = _LIVE_WINDOWS[-1]
    assert win._visible is True
    assert _names(_tiles(win)) == [
        "Word",
        "Excel",
        "PowerShell",
        "Settings",
        "VLC Media Player",
        "Firefox",
    ]


def test_main_exits_with_the_launcher_status(monkeypatch):
    monkeypatch.setattr(launcher, "show_launcher", lambda: 3)
    with pytest.raises(SystemExit) as excinfo:
        launcher.main()
    assert excinfo.value.code == 3


def test_launcher_state_creates_an_empty_file_on_first_load(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert launcher_state.load() == {"pinned": [], "recent": []}
    path = tmp_path / launcher_state.APP_NAME / "launcher_state.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"pinned": [], "recent": []}


def test_launcher_state_pin_unpin_and_membership(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    launcher_state.pin("word")
    launcher_state.pin("excel")
    launcher_state.pin("word")
    assert launcher_state.get_pinned() == ["word", "excel"]
    assert launcher_state.is_pinned("word") is True
    assert launcher_state.is_pinned(" excel ") is True
    assert launcher_state.is_pinned("vlc") is False

    launcher_state.pin("   ")
    assert launcher_state.get_pinned() == ["word", "excel"]

    launcher_state.unpin("word")
    launcher_state.unpin("  ")
    launcher_state.unpin("never-pinned")
    assert launcher_state.get_pinned() == ["excel"]


def test_launcher_state_recent_is_newest_first_and_capped(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    for i in range(10):
        launcher_state.record_recent(f"app{i}")
    assert launcher_state.get_recent() == [
        "app9",
        "app8",
        "app7",
        "app6",
        "app5",
        "app4",
        "app3",
        "app2",
    ]

    launcher_state.record_recent("app5")
    recent = launcher_state.get_recent()
    assert recent[0] == "app5"
    assert recent.count("app5") == 1
    assert len(recent) == 8

    launcher_state.record_recent("   ")
    assert launcher_state.get_recent()[0] == "app5"


def test_launcher_state_normalizes_hostile_payloads(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = tmp_path / launcher_state.APP_NAME / "launcher_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pinned": ["  word  ", "word", "", 17, None, "excel"],
                "recent": [f"r{i}" for i in range(12)],
                "junk": "dropped",
            }
        ),
        encoding="utf-8",
    )

    state = launcher_state.load()
    assert state == {"pinned": ["word", "excel"], "recent": [f"r{i}" for i in range(8)]}
    assert json.loads(path.read_text(encoding="utf-8")) == state


def test_launcher_state_recovers_from_corrupt_json(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = tmp_path / launcher_state.APP_NAME / "launcher_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text("{not json", encoding="utf-8")
    assert launcher_state.load() == {"pinned": [], "recent": []}
    assert json.loads(path.read_text(encoding="utf-8")) == {"pinned": [], "recent": []}

    path.write_text('["a bare list"]', encoding="utf-8")
    assert launcher_state.load() == {"pinned": [], "recent": []}


def test_launcher_state_falls_back_to_home_without_xdg_state_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    launcher_state.pin("word")
    path = tmp_path / ".local" / "state" / launcher_state.APP_NAME / "launcher_state.json"
    assert json.loads(path.read_text(encoding="utf-8"))["pinned"] == ["word"]
