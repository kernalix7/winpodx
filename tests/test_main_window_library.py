# SPDX-License-Identifier: MIT
"""Tests for the GUI "All apps" page: LibraryPageMixin, AppCrudMixin, app_dialog.

Complements (does NOT duplicate):
  - ``test_library_filter_reentrancy.py`` — the ``_filter_apps`` guard only.
  - ``test_app.py`` — ``_on_batch_remove`` / ``_on_batch_hide`` /
    ``_restore_deleted_slugs`` / ``set_custom_icon`` / ``preserve_app_icon``
    against ``core.app`` state.

Two harness flavours, both per the ``test_main_window_bringup.py`` pattern:
  1. ``_LogicHarness`` — a bare host mixing in ``LibraryPageMixin`` with only
     the attributes the pure-logic methods read. No QApplication.
  2. ``_PageHarness`` — a real ``QWidget`` mixing in both page mixins, used
     only where an actual widget tree is the thing under test. Nothing is
     ever shown and no event loop is ``exec()``-ed.

Every outward call (discovery, desktop entries, sessions, dialogs) is stubbed;
XDG roots come from the autouse ``conftest`` isolation fixture.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QWidget  # noqa: E402

from winpodx.core.app import AppInfo  # noqa: E402
from winpodx.gui._main_window_apps import AppCrudMixin  # noqa: E402
from winpodx.gui._main_window_library import LibraryPageMixin  # noqa: E402

# ----- fixtures / helpers -------------------------------------------------


def _app(
    name: str,
    full_name: str = "",
    *,
    categories: list[str] | None = None,
    hidden: bool = False,
    source: str = "user",
) -> AppInfo:
    return AppInfo(
        name=name,
        full_name=full_name or name.title(),
        executable=f"C:\\{name}.exe",
        categories=list(categories or []),
        hidden=hidden,
        source=source,
    )


class _StubLabel:
    """QLabel stand-in recording setText / setVisible."""

    def __init__(self, text: str = "") -> None:
        self._text = text
        self.visible = True

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:  # noqa: N802 - Qt signature
        self._text = text

    def setVisible(self, visible: bool) -> None:  # noqa: N802 - Qt signature
        self.visible = bool(visible)


class _StubButton(_StubLabel):
    """QPushButton stand-in with the checkable/enabled surface the mixin uses."""

    def __init__(self, text: str = "", *, checked: bool = False) -> None:
        super().__init__(text)
        self.checked = checked
        self.enabled = True

    def isChecked(self) -> bool:  # noqa: N802 - Qt signature
        return self.checked

    def setChecked(self, value: bool) -> None:  # noqa: N802 - Qt signature
        self.checked = bool(value)

    def setEnabled(self, value: bool) -> None:  # noqa: N802 - Qt signature
        self.enabled = bool(value)


class _StubLineEdit:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def text(self) -> str:
        return self._text

    def setText(self, value: str) -> None:  # noqa: N802 - Qt signature
        self._text = value


class _LogicHarness(LibraryPageMixin):
    """Bare host exposing only what the pure-logic library methods read.

    ``_visible_apps`` / ``_hidden_count`` / ``_filter_apps`` / ``_apps_by_names``
    / ``_running_display_name`` / ``_grid_cols`` / ``_refresh_hidden_button``
    run for real; the three widget-building collaborators record instead.
    """

    def __init__(self, apps: list[AppInfo], *, show_hidden: bool = False) -> None:
        self.apps = list(apps)
        self._show_hidden = show_hidden
        self._active_category = ""
        self.app_count_label = _StubLabel()
        self.btn_show_hidden = _StubButton()
        self.search_box = _StubLineEdit()
        self.populated: list[list[AppInfo]] = []
        self.launcher_sections: list[list[AppInfo]] = []
        self.command_queries: list[str] = []

    def _refresh_commands(self, q: str) -> None:
        self.command_queries.append(q)

    def _refresh_launcher_sections(self, filtered: list[AppInfo]) -> None:
        self.launcher_sections.append(list(filtered))

    def _populate_app_view(self, apps: list[AppInfo]) -> None:
        self.populated.append(list(apps))

    def names(self) -> list[str]:
        return [a.name for a in self.populated[-1]]


# ----- search / filter algorithm -----------------------------------------


def test_filter_matches_short_name_and_display_name() -> None:
    h = _LogicHarness([_app("word", "Microsoft Word"), _app("calc", "Calculator")])

    h._filter_apps("micro")  # matches full_name only
    assert h.names() == ["word"]

    h._filter_apps("calc")  # matches short name only
    assert h.names() == ["calc"]


def test_filter_is_case_insensitive_and_lowercases_the_command_query() -> None:
    h = _LogicHarness([_app("word", "Microsoft Word")])
    h._filter_apps("WORD")
    assert h.names() == ["word"]
    # _refresh_commands always receives the LOWERCASED query.
    assert h.command_queries == ["word"]


def test_filter_no_match_populates_an_empty_view() -> None:
    h = _LogicHarness([_app("word"), _app("calc")])
    h._filter_apps("nothing-here")
    assert h.names() == []


def test_filter_intersects_search_with_the_active_category() -> None:
    h = _LogicHarness(
        [
            _app("word", "Microsoft Word", categories=["Office"]),
            _app("excel", "Microsoft Excel", categories=["Office"]),
            _app("gimp", "Microsoft Paint-alike", categories=["Graphics"]),
        ]
    )
    h._active_category = "Office"
    h._filter_apps("microsoft")
    assert h.names() == ["word", "excel"]  # Graphics entry dropped by category


def test_filter_excludes_hidden_apps_until_the_toggle_is_on() -> None:
    h = _LogicHarness([_app("word"), _app("shim", hidden=True)])

    h._filter_apps("")
    assert h.names() == ["word"]

    h._show_hidden = True
    h._filter_apps("")
    assert h.names() == ["word", "shim"]


def test_filter_feeds_the_same_filtered_list_to_the_launcher_sections() -> None:
    h = _LogicHarness([_app("word", "Microsoft Word"), _app("calc", "Calculator")])
    h._filter_apps("word")
    assert [a.name for a in h.launcher_sections[-1]] == ["word"]


def test_filter_count_label_reports_shown_and_total() -> None:
    h = _LogicHarness([_app("word"), _app("calc"), _app("shim", hidden=True)])
    h._filter_apps("word")
    # tr() may translate the sentence, but both numbers must be in it: 1 of 3.
    assert "1" in h.app_count_label.text()
    assert "3" in h.app_count_label.text()


def test_visible_apps_and_hidden_count_track_the_toggle() -> None:
    apps = [_app("a"), _app("b", hidden=True), _app("c", hidden=True)]
    h = _LogicHarness(apps)
    assert [a.name for a in h._visible_apps()] == ["a"]
    assert h._hidden_count() == 2

    h._show_hidden = True
    assert [a.name for a in h._visible_apps()] == ["a", "b", "c"]


def test_refresh_hidden_button_hides_itself_when_nothing_is_hidden() -> None:
    h = _LogicHarness([_app("a")])
    h._refresh_hidden_button()
    assert h.btn_show_hidden.visible is False


def test_refresh_hidden_button_shows_the_hidden_count() -> None:
    h = _LogicHarness([_app("a"), _app("b", hidden=True), _app("c", hidden=True)])
    h._refresh_hidden_button()
    assert h.btn_show_hidden.visible is True
    assert "2" in h.btn_show_hidden.text()


def test_on_toggle_hidden_adopts_the_button_state_and_refilters() -> None:
    h = _LogicHarness([_app("a"), _app("b", hidden=True)])
    h.btn_show_hidden.setChecked(True)

    h._on_toggle_hidden()

    assert h._show_hidden is True
    assert h.names() == ["a", "b"]  # the rebuild ran with hidden included


def test_set_category_rechecks_chips_and_refilters() -> None:
    h = _LogicHarness([_app("word", categories=["Office"]), _app("gimp", categories=["Graphics"])])
    all_chip = _StubButton("All", checked=True)
    office_chip = _StubButton("Office")
    graphics_chip = _StubButton("Graphics")
    h._category_btns = [all_chip, office_chip, graphics_chip]

    h._set_category("Office")

    assert h._active_category == "Office"
    assert (all_chip.checked, office_chip.checked, graphics_chip.checked) == (False, True, False)
    assert h.names() == ["word"]


def test_set_category_back_to_all_rechecks_the_all_chip() -> None:
    h = _LogicHarness([_app("word", categories=["Office"]), _app("gimp", categories=["Graphics"])])
    all_chip = _StubButton("All")
    office_chip = _StubButton("Office", checked=True)
    h._category_btns = [all_chip, office_chip]

    h._set_category("")

    assert all_chip.checked is True
    assert office_chip.checked is False
    assert h.names() == ["word", "gimp"]


def test_set_category_keeps_the_overflow_chip_lit_for_a_collapsed_category() -> None:
    h = _LogicHarness([_app("vlc", categories=["Video"])])
    all_chip = _StubButton("All", checked=True)
    more_chip = _StubButton("+3 more")
    h._category_btns = [all_chip, more_chip]
    h._category_more_btn = more_chip
    h._overflow_categories = ["Video", "Network"]

    h._set_category("Video")
    assert more_chip.checked is True  # active filter lives inside the overflow menu

    h._set_category("")
    assert more_chip.checked is False


def test_apps_by_names_preserves_request_order_and_drops_unknowns() -> None:
    h = _LogicHarness([])
    candidates = [_app("a"), _app("b"), _app("c")]
    picked = h._apps_by_names(["c", "ghost", "a"], candidates)
    assert [a.name for a in picked] == ["c", "a"]


def test_running_display_name_prefers_a_known_app_then_cleans_the_stem() -> None:
    h = _LogicHarness([_app("word", "Microsoft Word")])
    assert h._running_display_name("word") == "Microsoft Word"
    # Unknown stem: strip the UWP prefix, cut at "_", de-dash, title-case.
    assert h._running_display_name("winpodx-uwp-sticky-notes_8wek") == "Sticky Notes"
    assert h._running_display_name("solitaire") == "Solitaire"


def test_grid_cols_is_clamped_between_three_and_six() -> None:
    class _W:
        def __init__(self, w: int) -> None:
            self._w = w

        def width(self) -> int:
            return self._w

    h = _LogicHarness([])
    h.pages = _W(200)  # far too narrow -> floor
    assert h._grid_cols() == 3
    h.pages = _W(4000)  # far too wide -> ceiling
    assert h._grid_cols() == 6
    h.pages = _W(812)  # (812-112)//140 == 5
    assert h._grid_cols() == 5


def test_grid_cols_falls_back_to_the_default_width_without_a_pages_widget() -> None:
    h = _LogicHarness([])
    assert h._grid_cols() == 6  # (1100-112)//140 == 7 -> clamped to 6


def test_reflow_library_only_refilters_when_the_column_count_changed() -> None:
    class _W:
        def __init__(self, w: int) -> None:
            self._w = w

        def width(self) -> int:
            return self._w

    h = _LogicHarness([_app("a")])
    h._view_mode = "grid"
    h.pages = _W(812)
    h._current_grid_cols = 5

    h._reflow_library()
    assert h.populated == []  # unchanged column count -> no rebuild

    h.pages = _W(1200)  # (1200-112)//140 == 7 -> clamped to 6, differs from 5
    h._reflow_library()
    assert len(h.populated) == 1


def test_reflow_library_is_a_noop_in_list_view() -> None:
    h = _LogicHarness([_app("a")])
    h._view_mode = "list"
    h._current_grid_cols = 1  # would differ if it were consulted
    h._reflow_library()
    assert h.populated == []


# ----- app_dialog: name validation is the file-write gate ----------------


@pytest.mark.parametrize(
    "name",
    [
        "../etc/passwd",
        "..",
        "../../root",
        "a/b",
        "/absolute",
        "with space",
        "semi;colon",
        "dot.name",
        "",
        "évil",
        "null\x00byte",
    ],
)
def test_validate_app_name_rejects_anything_outside_the_slug_charset(name: str) -> None:
    from winpodx.gui.app_dialog import _validate_app_name

    assert _validate_app_name(name) is False


@pytest.mark.parametrize("name", ["word", "vs_code", "app-2024", "A1", "_", "-"])
def test_validate_app_name_accepts_safe_slugs(name: str) -> None:
    from winpodx.gui.app_dialog import _validate_app_name

    assert _validate_app_name(name) is True


@pytest.mark.parametrize("name", ["../etc/passwd", "../../root", "a/b", "..", ""])
def test_save_app_profile_refuses_traversal_names_before_touching_disk(name: str) -> None:
    from winpodx.gui.app_dialog import save_app_profile
    from winpodx.utils.paths import data_dir

    payload = {"name": name, "full_name": "X", "executable": "C:\\x.exe"}
    with pytest.raises(ValueError):
        save_app_profile(payload)
    # Nothing was created anywhere: not the apps root, not a sibling of it.
    assert not (data_dir() / "apps").exists()
    assert not (data_dir().parent / "passwd").exists()
    assert not (data_dir().parent / "etc").exists()


def test_save_app_profile_round_trips_every_field_for_a_safe_name() -> None:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py39/py310
        import tomli as tomllib

    from winpodx.gui.app_dialog import save_app_profile
    from winpodx.utils.paths import data_dir

    data = {
        "name": "word",
        "full_name": "Microsoft Word",
        "executable": "C:\\Program Files\\Word\\winword.exe",
        "categories": ["Office", "WordProcessor"],
        "mime_types": ["application/msword"],
    }
    path = save_app_profile(data)

    assert path == data_dir() / "apps" / "word" / "app.toml"
    written = tomllib.loads(path.read_text(encoding="utf-8"))
    assert written["name"] == "word"
    assert written["full_name"] == "Microsoft Word"
    assert written["executable"] == "C:\\Program Files\\Word\\winword.exe"
    assert written["categories"] == ["Office", "WordProcessor"]
    assert written["mime_types"] == ["application/msword"]


def test_delete_app_profile_refuses_a_traversal_name() -> None:
    from winpodx.gui.app_dialog import delete_app_profile
    from winpodx.utils.paths import data_dir

    victim = data_dir() / "apps" / "word"
    victim.mkdir(parents=True)
    (victim / "app.toml").write_text('name = "word"\n', encoding="utf-8")

    assert delete_app_profile("../apps") is False
    assert delete_app_profile("word/../word") is False
    assert victim.exists()  # nothing was removed


def test_delete_app_profile_reports_false_for_an_unknown_slug() -> None:
    from winpodx.gui.app_dialog import delete_app_profile

    assert delete_app_profile("nosuchapp") is False


def test_preserve_app_icon_refuses_a_traversal_destination() -> None:
    from winpodx.gui.app_dialog import preserve_app_icon
    from winpodx.utils.paths import data_dir

    src = data_dir() / "src.svg"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("<svg/>", encoding="utf-8")

    preserve_app_icon(str(src), "../escape")

    assert not (data_dir().parent / "escape").exists()
    assert not (data_dir() / "apps").exists()


def test_preserve_app_icon_ignores_an_unsupported_extension() -> None:
    from winpodx.gui.app_dialog import preserve_app_icon
    from winpodx.utils.paths import data_dir

    src = data_dir() / "src.gif"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("gif", encoding="utf-8")
    (data_dir() / "apps" / "word").mkdir(parents=True)

    preserve_app_icon(str(src), "word")

    assert list((data_dir() / "apps" / "word").iterdir()) == []


def test_preserve_app_icon_does_not_clobber_an_existing_icon() -> None:
    from winpodx.gui.app_dialog import preserve_app_icon
    from winpodx.utils.paths import data_dir

    src = data_dir() / "new.svg"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("<svg>new</svg>", encoding="utf-8")
    dest = data_dir() / "apps" / "word"
    dest.mkdir(parents=True)
    (dest / "icon.png").write_text("existing", encoding="utf-8")

    preserve_app_icon(str(src), "word")

    assert not (dest / "icon.svg").exists()
    assert (dest / "icon.png").read_text(encoding="utf-8") == "existing"


def test_set_custom_icon_overwrites_even_when_a_discovered_twin_exists() -> None:
    from winpodx.gui.app_dialog import set_custom_icon
    from winpodx.utils.paths import data_dir

    (data_dir() / "discovered" / "word").mkdir(parents=True)
    (data_dir() / "discovered" / "word" / "icon.svg").write_text("<svg/>", encoding="utf-8")
    src = data_dir() / "pick.svg"
    src.write_text("<svg>picked</svg>", encoding="utf-8")

    assert set_custom_icon(str(src), "word") is True
    # preserve_app_icon would have SKIPPED here; a deliberate pick must not.
    written = (data_dir() / "apps" / "word" / "icon.svg").read_text(encoding="utf-8")
    assert written == "<svg>picked</svg>"


def test_executable_shape_warning_accepts_windows_paths() -> None:
    from winpodx.gui.app_dialog import AppProfileDialog

    warn = AppProfileDialog._executable_shape_warning
    assert warn("C:\\Program Files\\App\\app.exe") == ""
    assert warn("d:\\tools\\thing.EXE") == ""
    assert warn("\\\\server\\share\\app.exe") == ""


def test_executable_shape_warning_flags_non_windows_and_non_exe_paths() -> None:
    from winpodx.gui.app_dialog import AppProfileDialog

    warn = AppProfileDialog._executable_shape_warning
    assert warn("") != ""  # empty
    assert warn("   ") != ""  # whitespace-only is still empty
    assert warn("/usr/bin/gimp") != ""  # POSIX path
    assert warn("C:\\tools\\run.bat") != ""  # not .exe


# ----- app_dialog: the ".." guard also blocks the icon writers ------------


@pytest.mark.parametrize("name", ["../etc", "..", "a/b", ""])
def test_set_custom_icon_refuses_traversal_destinations(name: str) -> None:
    from winpodx.gui.app_dialog import set_custom_icon
    from winpodx.utils.paths import data_dir

    src = data_dir() / "pick.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("png", encoding="utf-8")

    assert set_custom_icon(str(src), name) is False
    assert not (data_dir() / "apps").exists()


# ----- Qt-backed surface --------------------------------------------------


def _ensure_qapp():
    """Return a QApplication, creating one if needed."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _pump(predicate, timeout: float = 3.0) -> bool:
    """Spin the event loop until ``predicate()`` or ``timeout``. Never sleeps."""
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        QApplication.processEvents()
    return predicate()


def _shown(widget) -> bool:
    """True when a widget is not explicitly hidden.

    ``isVisible()`` is always False here because nothing is ever mapped to a
    screen, so it can't distinguish an explicit ``setVisible(False)``.
    """
    return not widget.isHidden()


class _PageHarness(LibraryPageMixin, AppCrudMixin, QWidget):
    """Real-widget host: the two page mixins over a QWidget.

    Only the collaborators owned by *other* mixins are stubbed; everything in
    ``LibraryPageMixin`` / ``AppCrudMixin`` runs for real.
    """

    def __init__(self, apps: list[AppInfo]) -> None:
        from PySide6.QtWidgets import QLabel

        super().__init__()
        self.apps = list(apps)
        self.cfg = None
        self._view_mode = "grid"
        self._active_category = ""
        self._pod_state = "running"
        self.pages = None
        self.info_label = QLabel()
        self._refresh_state = "idle"
        self._refresh_thread = None
        self._refresh_worker = None
        self.launched: list[str] = []
        self.edited: list[str] = []
        self.deleted: list[str] = []
        self.reset_calls: list[str] = []
        self.hidden_toggles: list[str] = []
        self.added = 0
        self.started_pod = 0
        self.switched: list[int] = []
        self.suspends = 0
        self.resumes = 0
        self.desktops = 0

    # --- collaborators owned by sibling mixins -------------------------
    def _launch_app(self, app: AppInfo) -> None:
        self.launched.append(app.name)

    def _on_add_app(self) -> None:
        self.added += 1

    def _on_start_pod(self) -> None:
        self.started_pod += 1

    def _switch_page(self, index: int) -> None:
        self.switched.append(index)

    def _on_suspend(self) -> None:
        self.suspends += 1

    def _on_resume(self) -> None:
        self.resumes += 1

    def _on_open_desktop(self) -> None:
        self.desktops += 1


class _CrudHarness(_PageHarness):
    """``_PageHarness`` with the CRUD entry points left REAL (AppCrudMixin)."""

    def _on_add_app(self) -> None:
        AppCrudMixin._on_add_app(self)


@pytest.fixture
def page(monkeypatch: pytest.MonkeyPatch):
    """A built library page with every outward call stubbed."""
    _ensure_qapp()
    import winpodx.gui._main_window_library as lib

    monkeypatch.setattr(lib, "list_active_sessions", lambda: [])
    monkeypatch.setattr(lib, "kill_session", lambda name: True)

    def _make(apps: list[AppInfo], cls=_PageHarness):
        host = cls(apps)
        host._page = host._build_library_page()
        return host

    return _make


def _tile_count(host) -> int:
    """Widgets in the app-list layout minus the trailing stretch spacer."""
    layout = host.app_list_layout
    return sum(1 for i in range(layout.count()) if layout.itemAt(i).widget() is not None)


def test_build_library_page_wires_the_toolbar_and_populates_the_grid(page) -> None:
    host = page([_app("word", "Microsoft Word"), _app("calc", "Calculator")])

    assert host.search_box.text() == ""
    assert host.btn_grid.isChecked() is True
    assert host.btn_list.isChecked() is False
    assert _shown(host.btn_deleted) is False  # no tombstones
    assert _shown(host.refresh_progress) is False
    # Grid view packs every tile into ONE grid widget.
    assert _tile_count(host) == 1


def test_list_view_emits_one_tile_widget_per_app(page) -> None:
    host = page([_app("word"), _app("calc"), _app("paint")])

    host._set_view("list")

    assert host._view_mode == "list"
    assert host.btn_list.isChecked() is True
    assert host.btn_grid.isChecked() is False
    assert _tile_count(host) == 3


def test_typing_in_the_search_box_drives_the_rebuild(page) -> None:
    host = page([_app("word", "Microsoft Word"), _app("calc", "Calculator")])
    host._set_view("list")

    host.search_box.setText("calc")  # textChanged -> _filter_apps

    assert _tile_count(host) == 1


def test_category_chips_are_built_from_the_app_categories(page) -> None:
    host = page(
        [
            _app("word", categories=["Office"]),
            _app("gimp", categories=["Graphics"]),
            _app("vlc", categories=["Graphics", "Video"]),
        ]
    )
    labels = [b.text() for b in host._category_btns]
    assert labels[0] == "All"
    assert set(labels[1:]) == {"Graphics", "Office", "Video"}  # sorted + de-duped
    assert host._category_more_btn is None
    assert host._overflow_categories == []


def test_category_chips_collapse_the_overflow_into_a_more_menu(page) -> None:
    apps = [_app(f"a{i}", categories=[f"Cat{i:02d}"]) for i in range(11)]
    host = page(apps)

    # "All" + 8 inline chips + the overflow chip.
    assert len(host._category_btns) == 10
    assert host._category_more_btn is not None
    assert host._overflow_categories == ["Cat08", "Cat09", "Cat10"]
    # Nothing is silently dropped: every overflow category has a menu action.
    actions = [a.text() for a in host._category_more_btn.menu().actions()]
    assert actions == ["Cat08", "Cat09", "Cat10"]


def test_overflow_menu_action_applies_the_category_filter(page) -> None:
    apps = [_app(f"a{i}", categories=[f"Cat{i:02d}"]) for i in range(11)]
    host = page(apps)
    host._set_view("list")

    [action] = [a for a in host._category_more_btn.menu().actions() if a.text() == "Cat09"]
    action.trigger()

    assert host._active_category == "Cat09"
    assert _tile_count(host) == 1


def test_empty_state_when_a_search_matches_nothing(page, monkeypatch) -> None:
    import winpodx.gui._main_window_library as lib

    captured: list[tuple] = []
    real = lib.make_empty_panel

    def _spy(title, body="", **kw):
        captured.append((title, body, kw))
        return real(title, body, **kw)

    monkeypatch.setattr(lib, "make_empty_panel", _spy)

    host = page([_app("word", "Microsoft Word")])
    captured.clear()
    host.search_box.setText("zzz-no-match")

    assert _tile_count(host) == 1  # exactly the empty panel
    title, _body, kw = captured[-1]
    assert "zzz-no-match" in title  # the query is echoed back
    assert kw.get("action_cb") is None  # no Start-Windows affordance


def test_empty_state_offers_start_windows_when_the_pod_is_down(page) -> None:
    host = page([])
    host._pod_state = "stopped"

    panel = host._make_empty_state()

    from PySide6.QtWidgets import QPushButton

    buttons = panel.findChildren(QPushButton)
    assert len(buttons) == 1
    buttons[0].click()
    assert host.started_pod == 1


def test_empty_state_has_no_start_button_while_windows_is_installing(page) -> None:
    from PySide6.QtWidgets import QPushButton

    from winpodx.core.config import Config

    host = page([])
    host.cfg = Config()
    host.cfg.pod.initialized = False
    host._pod_state = "starting"

    panel = host._make_empty_state()

    assert panel.findChildren(QPushButton) == []
    assert host.started_pod == 0


def test_empty_state_when_everything_is_hidden(page, monkeypatch) -> None:
    import winpodx.gui._main_window_library as lib

    captured: list[str] = []
    real = lib.make_empty_panel
    monkeypatch.setattr(
        lib,
        "make_empty_panel",
        lambda title, body="", **kw: (captured.append(title), real(title, body, **kw))[1],
    )

    host = page([_app("shim", hidden=True)])
    captured.clear()
    host._make_empty_state()

    all_hidden_title = captured[-1]
    # Distinct from the "no apps yet" copy: hiding is the cause, not emptiness.
    host2 = page([])
    captured.clear()
    host2._make_empty_state()
    assert all_hidden_title != captured[-1]


def test_hidden_toggle_button_round_trips_through_the_real_widget(page) -> None:
    host = page([_app("word"), _app("shim", hidden=True)])
    host._set_view("list")
    assert _tile_count(host) == 1
    assert _shown(host.btn_show_hidden) is True

    host.btn_show_hidden.click()  # checkable -> toggles then fires the handler

    assert host._show_hidden is True
    assert _tile_count(host) == 2


def test_running_strip_renders_one_chip_per_live_session(page, monkeypatch) -> None:
    import winpodx.gui._main_window_library as lib
    from winpodx.core.process import TrackedProcess

    monkeypatch.setattr(
        lib,
        "list_active_sessions",
        lambda: [TrackedProcess(app_name="word", pid=1), TrackedProcess(app_name="calc", pid=2)],
    )
    host = page([_app("word", "Microsoft Word")])
    host._refresh_running_strip()

    assert _shown(host._running_section) is True
    chips = [
        host._running_row.itemAt(i).widget()
        for i in range(host._running_row.count())
        if host._running_row.itemAt(i).widget() is not None
    ]
    assert len(chips) == 2


def test_running_strip_survives_an_enumeration_failure(page, monkeypatch) -> None:
    import winpodx.gui._main_window_library as lib

    def _boom():
        raise OSError("runtime dir vanished")

    monkeypatch.setattr(lib, "list_active_sessions", _boom)
    host = page([_app("word")])

    host._refresh_running_strip()  # must not raise

    assert host._running_row.count() == 0


def test_terminate_session_kills_by_name_then_rebuilds_the_strip(page, monkeypatch) -> None:
    import winpodx.gui._main_window_library as lib

    killed: list[str] = []
    monkeypatch.setattr(lib, "kill_session", lambda name: killed.append(name) or True)
    host = page([_app("word")])

    host._terminate_session("word")

    assert killed == ["word"]


def test_terminate_session_ignores_a_kill_failure(page, monkeypatch) -> None:
    import winpodx.gui._main_window_library as lib

    def _boom(_name):
        raise RuntimeError("no such session")

    monkeypatch.setattr(lib, "kill_session", _boom)
    host = page([_app("word")])

    host._terminate_session("word")  # best-effort: must not propagate


def test_command_bar_surfaces_matching_quick_actions(page) -> None:
    host = page([_app("word")])
    label, _icon, _handler = host._command_specs()[0]

    host._refresh_commands(label.lower())

    assert _shown(host._commands_section) is True
    assert host._commands_layout.count() >= 1


def test_command_bar_stays_empty_for_a_blank_query(page) -> None:
    host = page([_app("word")])
    host._refresh_commands("")
    assert host._commands_layout.count() == 0
    assert _shown(host._commands_section) is False


def test_command_bar_caps_the_result_list_at_five(page) -> None:
    host = page([_app("word")])
    # Every spec label contains at least one of these; use the empty-ish match
    # by feeding a substring shared by all labels is not possible, so drive the
    # cap through a stubbed spec list instead.
    host._command_specs = lambda: [(f"zzcmd{i}", "gear", lambda: None) for i in range(9)]

    host._refresh_commands("zzcmd")

    assert host._commands_layout.count() == 5


def test_command_row_click_invokes_the_handler(page) -> None:
    host = page([_app("word")])
    fired: list[int] = []
    host._command_specs = lambda: [("zzjump", "gear", lambda: fired.append(1))]
    host._refresh_commands("zzjump")

    row = host._commands_layout.itemAt(0).widget()
    # _make_command_row replaces mousePressEvent with `lambda _e, fn=...: fn()`.
    row.mousePressEvent(None)
    assert fired == [1]


def test_select_mode_forces_list_view_and_reveals_the_batch_bar(page) -> None:
    host = page([_app("word"), _app("calc")])
    assert _shown(host._batch_bar) is False

    host.btn_select.click()

    assert host._select_mode is True
    assert host._view_mode == "list"  # grid can't host a checkbox
    assert host.btn_grid.isEnabled() is False
    assert _shown(host._batch_bar) is True
    assert host._batch_remove_btn.isEnabled() is False  # nothing selected yet


def test_tile_checkboxes_drive_the_batch_selection(page) -> None:
    from PySide6.QtWidgets import QCheckBox

    host = page([_app("word"), _app("calc")])
    host.btn_select.click()

    boxes = []
    for i in range(host.app_list_layout.count()):
        w = host.app_list_layout.itemAt(i).widget()
        if w is not None:
            boxes.extend(w.findChildren(QCheckBox))
    assert len(boxes) == 2

    boxes[0].setChecked(True)
    assert host._selected_names == {"word"}
    assert host._batch_remove_btn.isEnabled() is True
    assert host._batch_hide_btn.isEnabled() is True
    assert "1" in host._batch_label.text()

    boxes[0].setChecked(False)
    assert host._selected_names == set()
    assert host._batch_remove_btn.isEnabled() is False


def test_exit_select_mode_clears_the_selection_and_reenables_grid(page) -> None:
    host = page([_app("word")])
    host.btn_select.click()
    host._on_tile_checked("word", True)

    host._exit_select_mode()

    assert host._select_mode is False
    assert host._selected_names == set()
    assert host.btn_grid.isEnabled() is True
    assert _shown(host._batch_bar) is False


def test_batch_actions_are_noops_with_an_empty_selection(page, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    asked: list[int] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Yes),
    )
    host = page([_app("word")])
    host._selected_names = set()

    host._on_batch_remove()
    host._on_batch_hide()

    assert asked == []  # never even prompted


def test_deleted_button_appears_only_when_tombstones_exist(page) -> None:
    from winpodx.core.app import suppress_app_slug

    host = page([_app("word")])
    host._refresh_deleted_button()
    assert _shown(host.btn_deleted) is False

    suppress_app_slug("paint")
    suppress_app_slug("notepad")
    host._refresh_deleted_button()

    assert _shown(host.btn_deleted) is True
    assert "2" in host.btn_deleted.text()


def test_open_deleted_apps_is_a_noop_without_tombstones(page, monkeypatch) -> None:
    import winpodx.gui.deleted_apps_dialog as dlg_mod

    opened: list[int] = []
    monkeypatch.setattr(
        dlg_mod, "DeletedAppsDialog", lambda *a, **k: opened.append(1) or _NeverDialog()
    )
    host = page([_app("word")])

    host._on_open_deleted_apps()

    assert opened == []


class _NeverDialog:
    def exec(self) -> int:  # pragma: no cover - the test asserts it is unused
        raise AssertionError("dialog should not have been constructed")


def test_open_deleted_apps_passes_sorted_slugs_and_wires_restore(page, monkeypatch) -> None:
    import winpodx.gui.deleted_apps_dialog as dlg_mod
    from winpodx.core.app import suppress_app_slug, suppressed_app_slugs

    for slug in ("paint", "notepad", "wordpad"):
        suppress_app_slug(slug)

    seen: dict = {}

    class _FakeDialog:
        def __init__(self, parent, *, slugs, on_restore) -> None:
            seen["slugs"] = list(slugs)
            self._on_restore = on_restore

        def exec(self) -> int:
            self._on_restore(["notepad"])  # user restores exactly one
            return 1

    monkeypatch.setattr(dlg_mod, "DeletedAppsDialog", _FakeDialog)
    host = page([_app("word")])
    host._on_refresh_apps = lambda: None

    host._on_open_deleted_apps()

    assert seen["slugs"] == ["notepad", "paint", "wordpad"]  # sorted
    assert suppressed_app_slugs() == {"paint", "wordpad"}
    assert "2" in host.btn_deleted.text()  # button re-counted after the dialog


def test_app_context_menu_offers_reset_only_with_a_discovered_twin(page, monkeypatch) -> None:
    from PySide6.QtCore import QPoint

    import winpodx.gui._main_window_library as lib
    from winpodx.utils.paths import data_dir

    built: list[_FakeMenu] = []
    monkeypatch.setattr(lib, "QMenu", lambda _parent: _FakeMenu(built))

    host = page([_app("word", source="user")])

    host._show_app_menu(host.apps[0], QPoint(0, 0))
    labels_no_twin = [a.text() for a in built[-1].actions()]

    disc = data_dir() / "discovered" / "word"
    disc.mkdir(parents=True)
    (disc / "app.toml").write_text('name = "word"\n', encoding="utf-8")
    host._show_app_menu(host.apps[0], QPoint(0, 0))
    labels_with_twin = [a.text() for a in built[-1].actions()]

    assert len(labels_with_twin) == len(labels_no_twin) + 1
    extra = set(labels_with_twin) - set(labels_no_twin)
    assert len(extra) == 1


def test_app_context_menu_actions_route_to_the_crud_handlers(page, monkeypatch) -> None:
    from PySide6.QtCore import QPoint

    import winpodx.gui._main_window_library as lib

    built: list[_FakeMenu] = []
    monkeypatch.setattr(lib, "QMenu", lambda _parent: _FakeMenu(built))

    host = page([_app("word")])
    host._on_edit_app = lambda a: host.edited.append(a.name)
    host._on_delete_app = lambda a: host.deleted.append(a.name)
    host._on_toggle_app_hidden = lambda a: host.hidden_toggles.append(a.name)

    host._show_app_menu(host.apps[0], QPoint(0, 0))
    actions = built[-1].actions()
    # Order is fixed by the source: Pin, Edit, Hide, Delete.
    actions[1].trigger()
    actions[2].trigger()
    actions[3].trigger()

    assert host.edited == ["word"]
    assert host.hidden_toggles == ["word"]
    assert host.deleted == ["word"]


class _FakeAction:
    def __init__(self, text: str) -> None:
        self._text = text
        self._callback = lambda: None
        self.triggered = self

    def text(self) -> str:
        return self._text

    def setIcon(self, _icon) -> None:  # noqa: N802 - Qt signature
        pass

    def connect(self, callback) -> None:
        self._callback = callback

    def trigger(self) -> None:
        self._callback()


class _FakeMenu:
    def __init__(self, sink: list) -> None:
        self._actions: list[_FakeAction] = []
        self._sink = sink

    def addAction(self, text: str) -> _FakeAction:  # noqa: N802 - Qt signature
        action = _FakeAction(text)
        self._actions.append(action)
        return action

    def actions(self) -> list[_FakeAction]:
        return self._actions

    def exec(self, _global_pos) -> None:
        self._sink.append(self)


def test_toggle_pin_app_round_trips_launcher_state(page, monkeypatch) -> None:
    from winpodx.gui import launcher_state

    host = page([_app("word", "Microsoft Word")])

    host._on_toggle_pin_app(host.apps[0])
    assert launcher_state.get_pinned() == ["word"]

    host._on_toggle_pin_app(host.apps[0])
    assert launcher_state.get_pinned() == []


def test_pinned_row_shows_pinned_apps_and_hides_when_empty(page) -> None:
    from winpodx.gui import launcher_state

    host = page([_app("word", "Microsoft Word"), _app("calc", "Calculator")])
    assert _shown(host._pinned_section) is False

    launcher_state.pin("word")
    host._refresh_launcher_home()

    assert _shown(host._pinned_section) is True
    widgets = [
        host._pinned_row.itemAt(i).widget()
        for i in range(host._pinned_row.count())
        if host._pinned_row.itemAt(i).widget() is not None
    ]
    assert len(widgets) == 1


def _press(button):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(1, 1),
        QPointF(1, 1),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def test_app_tile_left_click_launches_the_app(page) -> None:
    from PySide6.QtCore import Qt

    host = page([_app("word", "Microsoft Word")])
    tile = host._make_app_card(host.apps[0])

    tile.mousePressEvent(_press(Qt.MouseButton.LeftButton))
    assert host.launched == ["word"]

    tile.mousePressEvent(_press(Qt.MouseButton.RightButton))
    assert host.launched == ["word"]  # right-click must NOT launch


def test_list_tile_buttons_launch_edit_hide_and_delete(page) -> None:
    from PySide6.QtWidgets import QPushButton

    host = page([_app("word", "Microsoft Word")])
    host._on_edit_app = lambda a: host.edited.append(a.name)
    host._on_delete_app = lambda a: host.deleted.append(a.name)
    host._on_toggle_app_hidden = lambda a: host.hidden_toggles.append(a.name)

    tile = host._make_app_tile(host.apps[0])
    buttons = tile.findChildren(QPushButton)
    # Launch, Edit, Hide, Delete -- no Reset without a discovered twin.
    assert len(buttons) == 4
    for btn in buttons:
        btn.click()

    assert host.launched == ["word"]
    assert host.edited == ["word"]
    assert host.hidden_toggles == ["word"]
    assert host.deleted == ["word"]


def test_list_tile_grows_a_reset_button_with_a_discovered_twin(page) -> None:
    from PySide6.QtWidgets import QPushButton

    from winpodx.utils.paths import data_dir

    disc = data_dir() / "discovered" / "word"
    disc.mkdir(parents=True)
    (disc / "app.toml").write_text('name = "word"\n', encoding="utf-8")

    host = page([_app("word", "Microsoft Word", source="user")])
    host._on_reset_app = lambda a: host.reset_calls.append(a.name)

    tile = host._make_app_tile(host.apps[0])
    buttons = tile.findChildren(QPushButton)
    assert len(buttons) == 5  # Launch, Edit, Reset, Hide, Delete

    buttons[2].click()
    assert host.reset_calls == ["word"]


def test_focus_session_prefers_wmctrl_then_xdotool(page, monkeypatch) -> None:
    import shutil
    import subprocess

    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(list(cmd)))
    host = page([_app("word")])

    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/wmctrl" if tool == "wmctrl" else "")
    host._focus_session("word")
    assert calls[-1] == ["wmctrl", "-x", "-a", "winpodx-word"]

    monkeypatch.setattr(
        shutil, "which", lambda tool: "/usr/bin/xdotool" if tool == "xdotool" else ""
    )
    host._focus_session("word")
    assert calls[-1] == [
        "xdotool",
        "search",
        "--class",
        "winpodx-word",
        "windowactivate",
    ]

    monkeypatch.setattr(shutil, "which", lambda tool: None)
    before = len(calls)
    host._focus_session("word")
    assert len(calls) == before  # neither tool present -> no subprocess at all


def test_focus_session_swallows_a_subprocess_failure(page, monkeypatch) -> None:
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda tool: "/usr/bin/wmctrl")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    host = page([_app("word")])

    host._focus_session("word")  # best-effort: must never break the UI


def test_reset_app_decline_has_no_effect(page, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    import winpodx.core.app as app_mod

    calls: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    monkeypatch.setattr(app_mod, "reset_app_profile", lambda name: calls.append(name))
    host = page([_app("word")], cls=_CrudHarness)

    host._on_reset_app(host.apps[0])

    assert calls == []


def test_reset_app_reports_missing_detected_profile(page, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    import winpodx.core.app as app_mod

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(app_mod, "reset_app_profile", lambda _name: None)
    host = page([_app("word", "Microsoft Word")], cls=_CrudHarness)

    host._on_reset_app(host.apps[0])

    assert "Microsoft Word" in host.info_label.text()


def test_reset_app_reloads_after_success(page, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    import winpodx.core.app as app_mod

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(app_mod, "reset_app_profile", lambda _name: _app("word", "Detected Word"))
    host = page([_app("word")], cls=_CrudHarness)
    reloads: list[int] = []
    host._reload_apps = lambda: reloads.append(1)

    host._on_reset_app(host.apps[0])

    assert reloads == [1]
    assert "Detected Word" in host.info_label.text()


def test_delete_discovered_app_stubs_every_outward_effect(page, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    import winpodx.core.app as app_mod
    import winpodx.desktop.entry as entry_mod
    import winpodx.gui.app_dialog as dialog_mod

    deleted: list[str] = []
    desktop_removed: list[str] = []
    suppressed: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    monkeypatch.setattr(dialog_mod, "delete_app_profile", lambda name: deleted.append(name))
    monkeypatch.setattr(
        entry_mod, "remove_desktop_entry", lambda name: desktop_removed.append(name)
    )
    monkeypatch.setattr(app_mod, "suppress_app_slug", lambda name: suppressed.append(name))
    host = page([_app("word", source="discovered")], cls=_CrudHarness)
    host._reload_apps = lambda: None

    host._on_delete_app(host.apps[0])

    assert deleted == ["word"]
    assert desktop_removed == ["word"]
    assert suppressed == ["word"]


def test_toggle_hidden_passes_inverse_state_and_reloads(page, monkeypatch) -> None:
    import winpodx.core.app as app_mod

    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        app_mod,
        "set_app_hidden",
        lambda name, hidden: calls.append((name, hidden)) or _app(name, hidden=hidden),
    )
    host = page([_app("word")], cls=_CrudHarness)
    reloads: list[int] = []
    host._reload_apps = lambda: reloads.append(1)

    host._on_toggle_app_hidden(host.apps[0])

    assert calls == [("word", True)]
    assert reloads == [1]
    assert "word" in host.info_label.text().lower()


class _FakeDialogResult:
    def __init__(self, *_args, **_kwargs) -> None:
        self.data = {
            "name": "word",
            "full_name": "Microsoft Word",
            "executable": "C:\\Word\\word.exe",
            "categories": ["Office"],
            "mime_types": [],
        }

    def exec(self) -> int:
        return 1

    def get_result(self):
        return self.data

    def chosen_icon_path(self) -> str:
        return "/picked/icon.svg"


def test_add_app_saves_profile_and_custom_icon(page, monkeypatch) -> None:
    import winpodx.gui.app_dialog as dialog_mod

    saved: list[dict] = []
    icons: list[tuple[str, str]] = []
    monkeypatch.setattr(dialog_mod, "AppProfileDialog", _FakeDialogResult)
    monkeypatch.setattr(dialog_mod, "save_app_profile", lambda data: saved.append(data))
    monkeypatch.setattr(
        dialog_mod, "set_custom_icon", lambda path, name: icons.append((path, name))
    )
    host = page([], cls=_CrudHarness)
    reloads: list[int] = []
    host._reload_apps = lambda: reloads.append(1)

    host._on_add_app()

    assert saved[0]["name"] == "word"
    assert icons == [("/picked/icon.svg", "word")]
    assert reloads == [1]


def test_edit_app_saves_profile_and_custom_icon(page, monkeypatch) -> None:
    import winpodx.gui.app_dialog as dialog_mod

    saved: list[dict] = []
    icons: list[tuple[str, str]] = []
    preserved: list[tuple[str, str]] = []
    monkeypatch.setattr(dialog_mod, "AppProfileDialog", _FakeDialogResult)
    monkeypatch.setattr(dialog_mod, "save_app_profile", lambda data: saved.append(data))
    monkeypatch.setattr(
        dialog_mod, "set_custom_icon", lambda path, name: icons.append((path, name))
    )
    monkeypatch.setattr(
        dialog_mod, "preserve_app_icon", lambda path, name: preserved.append((path, name))
    )
    host = page([_app("word")], cls=_CrudHarness)
    host._reload_apps = lambda: None

    host._on_edit_app(host.apps[0])

    assert saved[0]["name"] == "word"
    assert icons == [("/picked/icon.svg", "word")]
    assert preserved == []


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)


class _FakeThread:
    def __init__(self, _parent) -> None:
        self.started = _FakeSignal()
        self.finished = _FakeSignal()
        self.started_count = 0
        self.waited = 0

    def start(self) -> None:
        self.started_count += 1

    def quit(self) -> None:
        pass

    def deleteLater(self) -> None:  # noqa: N802 - Qt signature
        pass

    def wait(self) -> None:
        self.waited += 1


class _FakeWorker:
    def __init__(self) -> None:
        self.succeeded = _FakeSignal()
        self.failed = _FakeSignal()
        self.finished = _FakeSignal()
        self.thread = None

    def moveToThread(self, thread) -> None:  # noqa: N802 - Qt signature
        self.thread = thread

    def run(self) -> None:
        pass


def test_refresh_apps_wires_worker_and_keeps_both_references(page, monkeypatch) -> None:
    import winpodx.gui._main_window_apps as apps_mod

    monkeypatch.setattr(apps_mod, "QThread", _FakeThread)
    monkeypatch.setattr(apps_mod, "DiscoveryWorker", _FakeWorker)
    host = page([_app("word")], cls=_CrudHarness)

    host._on_refresh_apps()

    assert host._refresh_state == "scanning"
    assert host._refresh_worker.thread is host._refresh_thread
    assert host._refresh_thread.started_count == 1
    assert host.refresh_btn.isEnabled() is False


def test_refresh_apps_ignores_reclick_while_thread_reference_is_live(page, monkeypatch) -> None:
    import winpodx.gui._main_window_apps as apps_mod

    created: list[_FakeThread] = []
    monkeypatch.setattr(
        apps_mod, "QThread", lambda parent: created.append(_FakeThread(parent)) or created[-1]
    )
    monkeypatch.setattr(apps_mod, "DiscoveryWorker", _FakeWorker)
    host = page([_app("word")], cls=_CrudHarness)

    host._on_refresh_apps()
    host._set_refresh_state("idle")
    host._on_refresh_apps()

    assert len(created) == 1


def test_cleanup_refresh_worker_waits_before_dropping_references(page) -> None:
    host = page([_app("word")], cls=_CrudHarness)
    thread = _FakeThread(host)
    host._refresh_thread = thread
    host._refresh_worker = _FakeWorker()

    host._cleanup_refresh_worker()

    assert thread.waited == 1
    assert host._refresh_thread is None
    assert host._refresh_worker is None


def test_refresh_success_reloads_and_reports_count(page, monkeypatch) -> None:
    import winpodx.gui._main_window_apps as apps_mod

    toasts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        apps_mod, "show_toast", lambda _host, msg, *, kind: toasts.append((msg, kind))
    )
    host = page([_app("word")], cls=_CrudHarness)
    reloads: list[int] = []
    host._reload_apps = lambda: reloads.append(1)

    host._on_refresh_succeeded(3)

    assert reloads == [1]
    assert toasts[0][1] == "success"
    assert "3" in host.info_label.text()


def test_refresh_failure_defers_dialog_to_timer(page, monkeypatch) -> None:
    import winpodx.gui._main_window_apps as apps_mod

    queued: list = []
    monkeypatch.setattr(
        apps_mod.QTimer, "singleShot", lambda _delay, callback: queued.append(callback)
    )
    host = page([_app("word")], cls=_CrudHarness)

    host._on_refresh_failed("timeout", "slow guest")

    assert host._refresh_state == "idle"
    assert len(queued) == 1


def test_app_dialog_result_trims_fields_and_splits_lists() -> None:
    from winpodx.gui.app_dialog import AppProfileDialog

    _ensure_qapp()
    dlg = AppProfileDialog(
        name=" word ",
        full_name=" Microsoft Word ",
        executable=" C:\\Word\\word.exe ",
        categories=" Office, Productivity, ",
        mime_types=" application/msword, text/plain ",
    )

    result = dlg.get_result()

    assert result["name"] == "word"
    assert result["categories"] == ["Office", "Productivity"]
    assert result["mime_types"] == ["application/msword", "text/plain"]


def test_app_dialog_rejects_traversal_name_before_accept(page, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from winpodx.gui.app_dialog import AppProfileDialog

    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, _message: warnings.append(title)),
    )
    dlg = AppProfileDialog(
        name="../etc/passwd",
        full_name="Exploit",
        executable="C:\\Windows\\notepad.exe",
    )

    dlg._on_accept()

    assert warnings
    assert dlg.result() == 0


def test_app_dialog_missing_required_field_never_accepts(monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from winpodx.gui.app_dialog import AppProfileDialog

    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _parent, title, _message: warnings.append(title)),
    )
    dlg = AppProfileDialog(name="word", full_name="", executable="C:\\word.exe")

    dlg._on_accept()

    assert warnings
    assert dlg.result() == 0


def test_app_dialog_valid_fields_accept_without_warning() -> None:
    from PySide6.QtWidgets import QDialog

    from winpodx.gui.app_dialog import AppProfileDialog

    dlg = AppProfileDialog(
        name="word",
        full_name="Microsoft Word",
        executable="C:\\Word\\word.exe",
    )

    dlg._on_accept()

    assert dlg.result() == QDialog.DialogCode.Accepted


def test_app_dialog_custom_icon_picker_records_supported_file(monkeypatch, tmp_path) -> None:
    from PySide6.QtWidgets import QFileDialog

    from winpodx.gui.app_dialog import AppProfileDialog

    icon = tmp_path / "picked.svg"
    icon.write_text("<svg/>", encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(icon), "Images")),
    )
    dlg = AppProfileDialog(name="word", full_name="Word", executable="C:\\word.exe")

    dlg._on_choose_icon()

    assert dlg.chosen_icon_path() == str(icon)
