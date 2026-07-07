# SPDX-License-Identifier: MIT
"""Tests for app management.

v0.1.9 dropped the bundled profile set entirely; the loader now sources
only from `discovered_apps_dir` and `user_apps_dir`. The previous suite's
`bundled_apps_dir` tests were removed alongside that helper.
"""

from winpodx.core.app import discovered_apps_dir, list_available_apps, load_app, user_apps_dir


def test_load_app(tmp_path):
    app_dir = tmp_path / "test-app"
    app_dir.mkdir()

    toml = app_dir / "app.toml"
    toml.write_text(
        'name = "test"\n'
        'full_name = "Test App"\n'
        'executable = "C:\\\\test\\\\app.exe"\n'
        'categories = ["Utility"]\n'
        'mime_types = ["text/plain"]\n'
    )

    app = load_app(app_dir)
    assert app is not None
    assert app.name == "test"
    assert app.full_name == "Test App"
    assert app.executable == "C:\\test\\app.exe"
    assert app.categories == ["Utility"]
    assert app.mime_types == ["text/plain"]
    # v0.1.9: source defaults to "user" when the TOML doesn't declare one.
    assert app.source == "user"
    # #421/#694: url_schemes defaults to [] when absent.
    assert app.url_schemes == []


def test_load_app_reads_url_schemes(tmp_path):
    app_dir = tmp_path / "outlook"
    app_dir.mkdir()
    (app_dir / "app.toml").write_text(
        'name = "outlook"\n'
        'full_name = "Outlook"\n'
        'executable = "C:\\\\o.exe"\n'
        'url_schemes = ["mailto", "webcal"]\n'
    )
    app = load_app(app_dir)
    assert app is not None
    assert app.url_schemes == ["mailto", "webcal"]


def test_load_app_default_source_can_be_overridden(tmp_path):
    app_dir = tmp_path / "discovered-app"
    app_dir.mkdir()
    (app_dir / "app.toml").write_text(
        'name = "found"\nfull_name = "Found"\nexecutable = "C:\\\\f.exe"\n'
    )
    app = load_app(app_dir, default_source="discovered")
    assert app is not None
    assert app.source == "discovered"


def test_load_app_missing(tmp_path):
    app = load_app(tmp_path / "nonexistent")
    assert app is None


def test_user_and_discovered_dirs_under_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    user = user_apps_dir()
    discovered = discovered_apps_dir()
    assert "winpodx" in str(user)
    assert "winpodx" in str(discovered)
    assert user != discovered


def test_list_available_apps_empty_when_no_dirs(monkeypatch, tmp_path):
    """Fresh install: discovered + user dirs missing -> empty list, not crash."""
    import winpodx.core.app as app_mod

    monkeypatch.setattr(app_mod, "discovered_apps_dir", lambda: tmp_path / "missing-d")
    monkeypatch.setattr(app_mod, "user_apps_dir", lambda: tmp_path / "missing-u")
    assert list_available_apps() == []


def test_list_available_apps_user_overrides_discovered(monkeypatch, tmp_path):
    """When the same slug appears in both dirs the user-authored entry wins."""
    import winpodx.core.app as app_mod

    discovered = tmp_path / "discovered"
    user = tmp_path / "user"
    discovered.mkdir()
    user.mkdir()

    for root, full_name in [(discovered, "Discovered Word"), (user, "User Word")]:
        d = root / "word"
        d.mkdir()
        (d / "app.toml").write_text(
            f'name = "word"\nfull_name = "{full_name}"\nexecutable = "C:\\\\w.exe"\n'
        )

    monkeypatch.setattr(app_mod, "discovered_apps_dir", lambda: discovered)
    monkeypatch.setattr(app_mod, "user_apps_dir", lambda: user)
    apps = list_available_apps()
    assert len(apps) == 1
    assert apps[0].full_name == "User Word"
    assert apps[0].source == "user"


# -- hide / show (set_app_hidden) -----------------------------------------


def _write_app(d, name="myapp"):
    (d / name).mkdir(parents=True)
    (d / name / "app.toml").write_text(
        f'name = "{name}"\nfull_name = "My App"\nexecutable = "C:\\\\a.exe"\n'
    )


def test_set_app_hidden_toggles_toml_and_desktop(monkeypatch, tmp_path):
    import winpodx.core.app as app_mod
    import winpodx.desktop.entry as entry_mod
    import winpodx.desktop.icons as icons_mod

    user = tmp_path / "user"
    _write_app(user)
    monkeypatch.setattr(app_mod, "user_apps_dir", lambda: user)
    monkeypatch.setattr(app_mod, "discovered_apps_dir", lambda: tmp_path / "discovered")
    calls: list = []
    monkeypatch.setattr(entry_mod, "remove_desktop_entry", lambda n: calls.append(("rm", n)))
    monkeypatch.setattr(entry_mod, "install_desktop_entry", lambda a: calls.append(("add", a.name)))
    monkeypatch.setattr(icons_mod, "update_icon_cache", lambda: None)

    app = app_mod.set_app_hidden("myapp", True)
    assert app is not None and app.hidden is True
    assert "hidden = true" in (user / "myapp" / "app.toml").read_text()
    assert ("rm", "myapp") in calls  # dropped from the Linux menu

    app = app_mod.set_app_hidden("myapp", False)
    assert app is not None and app.hidden is False
    assert "hidden = false" in (user / "myapp" / "app.toml").read_text()
    assert ("add", "myapp") in calls  # re-added to the Linux menu


def test_set_app_hidden_not_found(monkeypatch, tmp_path):
    import winpodx.core.app as app_mod

    monkeypatch.setattr(app_mod, "user_apps_dir", lambda: tmp_path / "u")
    monkeypatch.setattr(app_mod, "discovered_apps_dir", lambda: tmp_path / "d")
    assert app_mod.set_app_hidden("ghost", True) is None


def test_set_app_hidden_rejects_bad_name(monkeypatch, tmp_path):
    import winpodx.core.app as app_mod

    monkeypatch.setattr(app_mod, "user_apps_dir", lambda: tmp_path / "u")
    monkeypatch.setattr(app_mod, "discovered_apps_dir", lambda: tmp_path / "d")
    assert app_mod.set_app_hidden("../etc", True) is None
    assert app_mod.set_app_hidden("a/b", True) is None
    assert app_mod._find_app_dir("..") is None


def test_cli_app_hide_dispatch(monkeypatch):
    import argparse

    from winpodx.cli import app as app_cli

    calls: list = []
    monkeypatch.setattr(
        "winpodx.core.app.set_app_hidden",
        lambda name, hidden: (
            calls.append((name, hidden))
            or type("A", (), {"hidden": hidden, "full_name": name, "name": name})()
        ),
    )
    app_cli.handle_app(argparse.Namespace(app_command="hide", name="myapp"))
    app_cli.handle_app(argparse.Namespace(app_command="show", name="myapp"))
    assert calls == [("myapp", True), ("myapp", False)]


# --- #514: deleting a discovered profile (delete worked only on user/) -------


def test_suppress_slug_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import (
        suppress_app_slug,
        suppressed_app_slugs,
        unsuppress_app_slug,
    )

    assert suppressed_app_slugs() == set()
    suppress_app_slug("notepad")
    suppress_app_slug("notepad")  # idempotent
    suppress_app_slug("calc")
    assert suppressed_app_slugs() == {"notepad", "calc"}
    # path-traversal / junk slugs are ignored
    suppress_app_slug("../evil")
    assert "../evil" not in suppressed_app_slugs()
    unsuppress_app_slug("notepad")
    assert suppressed_app_slugs() == {"calc"}


def test_delete_app_profile_removes_discovered(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir
    from winpodx.gui.app_dialog import delete_app_profile

    disc = data_dir() / "discovered" / "ghostapp"
    disc.mkdir(parents=True)
    (disc / "app.toml").write_text('name = "ghostapp"\n', encoding="utf-8")

    assert delete_app_profile("ghostapp") is True  # found in discovered/
    assert not disc.exists()


def test_persist_discovered_skips_suppressed(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core import discovery as disc_mod
    from winpodx.core.app import suppress_app_slug

    root = tmp_path / "discovered"  # passed explicitly via target_dir
    suppress_app_slug("junkapp")

    apps = [
        disc_mod.DiscoveredApp(name="realapp", full_name="Real App", executable="C:\\r.exe"),
        disc_mod.DiscoveredApp(name="junkapp", full_name="Junk", executable="C:\\j.exe"),
    ]
    disc_mod.persist_discovered(apps, target_dir=root)

    assert (root / "realapp" / "app.toml").exists()
    assert not (root / "junkapp").exists()  # suppressed → not re-created


# -- icon preservation across edits (#530) --------------------------------


def test_user_override_inherits_discovered_icon(monkeypatch, tmp_path):
    """A user override with no icon file inherits the discovered profile's icon
    (so editing name/MIME doesn't reset it to the fallback letter glyph) (#530)."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir, list_available_apps

    disc = data_dir() / "discovered" / "word"
    disc.mkdir(parents=True)
    (disc / "app.toml").write_text(
        'name = "word"\nfull_name = "Word"\nexecutable = "C:\\\\w.exe"\n', encoding="utf-8"
    )
    (disc / "icon.svg").write_text("<svg/>", encoding="utf-8")

    user = data_dir() / "apps" / "word"
    user.mkdir(parents=True)
    (user / "app.toml").write_text(
        'name = "word"\nfull_name = "Word (edited)"\nexecutable = "C:\\\\w.exe"\n', encoding="utf-8"
    )  # NO icon file in the override

    apps = list_available_apps()
    assert len(apps) == 1
    assert apps[0].full_name == "Word (edited)"  # user metadata wins
    assert apps[0].icon_path == str(disc / "icon.svg")  # inherited the discovered icon


def test_user_override_keeps_its_own_icon(monkeypatch, tmp_path):
    """An override that DOES carry its own icon keeps it (no inherit)."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir, list_available_apps

    disc = data_dir() / "discovered" / "word"
    disc.mkdir(parents=True)
    (disc / "app.toml").write_text(
        'name = "word"\nfull_name = "Word"\nexecutable = "C:\\\\w.exe"\n', encoding="utf-8"
    )
    (disc / "icon.svg").write_text("<svg/>", encoding="utf-8")
    user = data_dir() / "apps" / "word"
    user.mkdir(parents=True)
    (user / "app.toml").write_text(
        'name = "word"\nfull_name = "Word"\nexecutable = "C:\\\\w.exe"\n', encoding="utf-8"
    )
    (user / "icon.png").write_text("png", encoding="utf-8")

    apps = list_available_apps()
    assert apps[0].icon_path == str(user / "icon.png")  # its own icon, not the discovered one


def test_preserve_app_icon_copies_on_rename(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir
    from winpodx.gui.app_dialog import preserve_app_icon

    old = data_dir() / "apps" / "oldname"
    old.mkdir(parents=True)
    (old / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (data_dir() / "apps" / "newname").mkdir(parents=True)  # save_app_profile made this

    preserve_app_icon(str(old / "icon.svg"), "newname")
    assert (data_dir() / "apps" / "newname" / "icon.svg").exists()  # carried across the rename


def test_preserve_app_icon_skips_when_discovered_twin(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir
    from winpodx.gui.app_dialog import preserve_app_icon

    disc = data_dir() / "discovered" / "word"
    disc.mkdir(parents=True)
    (disc / "icon.svg").write_text("<svg/>", encoding="utf-8")
    (data_dir() / "apps" / "word").mkdir(parents=True)

    preserve_app_icon(str(disc / "icon.svg"), "word")
    # NOT copied -- the loader inherits the (fresh) discovered icon instead of
    # freezing a stale copy that a guest-side app update couldn't refresh.
    assert not (data_dir() / "apps" / "word" / "icon.svg").exists()


# -- reset-to-detected + custom icon (#530) -------------------------------


def _patch_desktop_sync(monkeypatch):
    """Stub the best-effort desktop-entry sync and return the call log."""
    import winpodx.desktop.entry as entry_mod
    import winpodx.desktop.icons as icons_mod

    calls: list = []
    monkeypatch.setattr(entry_mod, "remove_desktop_entry", lambda n: calls.append(("rm", n)))
    monkeypatch.setattr(entry_mod, "install_desktop_entry", lambda a: calls.append(("add", a.name)))
    monkeypatch.setattr(icons_mod, "update_icon_cache", lambda: None)
    return calls


def test_reset_app_profile_restores_discovered(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir, reset_app_profile

    calls = _patch_desktop_sync(monkeypatch)

    disc = data_dir() / "discovered" / "word"
    disc.mkdir(parents=True)
    (disc / "app.toml").write_text(
        'name = "word"\nfull_name = "Word"\nexecutable = "C:\\\\w.exe"\n', encoding="utf-8"
    )
    (disc / "icon.svg").write_text("<svg/>", encoding="utf-8")

    user = data_dir() / "apps" / "word"
    user.mkdir(parents=True)
    (user / "app.toml").write_text(
        'name = "word"\nfull_name = "Word (edited)"\nexecutable = "C:\\\\w.exe"\n', encoding="utf-8"
    )

    app = reset_app_profile("word")
    assert app is not None
    assert app.full_name == "Word"  # discovered metadata restored
    assert app.source == "discovered"
    assert app.icon_path == str(disc / "icon.svg")  # discovered icon returns
    assert not user.exists()  # the override is gone
    assert disc.exists()  # discovered profile untouched
    assert ("add", "word") in calls  # menu re-synced


def test_reset_app_profile_none_without_discovered_twin(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir, reset_app_profile

    _patch_desktop_sync(monkeypatch)
    user = data_dir() / "apps" / "solo"
    user.mkdir(parents=True)
    (user / "app.toml").write_text('name = "solo"\n', encoding="utf-8")

    assert reset_app_profile("solo") is None  # nothing to fall back to
    assert user.exists()  # left untouched (caller should Delete instead)


def test_reset_app_profile_none_without_override(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir, reset_app_profile

    _patch_desktop_sync(monkeypatch)
    disc = data_dir() / "discovered" / "word"
    disc.mkdir(parents=True)
    (disc / "app.toml").write_text('name = "word"\n', encoding="utf-8")

    assert reset_app_profile("word") is None  # no user override to discard


def test_reset_app_profile_rejects_bad_name(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import reset_app_profile

    _patch_desktop_sync(monkeypatch)
    assert reset_app_profile("../etc") is None
    assert reset_app_profile("a/b") is None


def test_discovered_profile_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir, discovered_profile_exists

    assert discovered_profile_exists("word") is False
    disc = data_dir() / "discovered" / "word"
    disc.mkdir(parents=True)
    (disc / "app.toml").write_text('name = "word"\n', encoding="utf-8")
    assert discovered_profile_exists("word") is True
    assert discovered_profile_exists("../etc") is False


def test_set_custom_icon_copies_and_drops_other_ext(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir
    from winpodx.gui.app_dialog import set_custom_icon

    user = data_dir() / "apps" / "word"
    user.mkdir(parents=True)
    (user / "icon.svg").write_text("<svg/>", encoding="utf-8")  # an old icon to be replaced

    src = tmp_path / "pick.png"
    src.write_text("png", encoding="utf-8")

    assert set_custom_icon(str(src), "word") is True
    assert (user / "icon.png").exists()  # new icon copied in
    assert not (user / "icon.svg").exists()  # stale svg removed (svg shadows png)


def test_set_custom_icon_rejects_bad_input(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.gui.app_dialog import set_custom_icon

    good = tmp_path / "i.png"
    good.write_text("png", encoding="utf-8")
    assert set_custom_icon(str(good), "../etc") is False  # path traversal
    assert set_custom_icon(str(tmp_path / "missing.png"), "word") is False  # src absent
    bad_ext = tmp_path / "i.gif"
    bad_ext.write_text("gif", encoding="utf-8")
    assert set_custom_icon(str(bad_ext), "word") is False  # unsupported extension


# -- multi-select bulk remove (#530) --------------------------------------


def test_batch_remove_deletes_and_tombstones(monkeypatch, tmp_path):
    """The bulk-remove loop deletes each profile and tombstones discovered ones
    so a sweep can't resurrect them -- driven through the real mixin method on a
    minimal fake host (no full window needed)."""
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from PySide6.QtWidgets import QMessageBox

    from winpodx.core.app import data_dir, suppressed_app_slugs
    from winpodx.gui._main_window_library import LibraryPageMixin

    # excel: discovered-only. word: user override over a discovered twin.
    for root, name in [("discovered", "excel"), ("discovered", "word"), ("apps", "word")]:
        d = data_dir() / root / name
        d.mkdir(parents=True)
        (d / "app.toml").write_text(
            f'name = "{name}"\nfull_name = "{name}"\nexecutable = "C:\\\\x.exe"\n', encoding="utf-8"
        )

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    )
    import winpodx.desktop.entry as entry_mod

    monkeypatch.setattr(entry_mod, "remove_desktop_entry", lambda n: None)

    host = type("H", (), {})()
    host._selected_names = {"excel", "word"}
    host._select_mode = True
    host.btn_select = type("B", (), {"setChecked": lambda self, v: None})()
    host.info_label = type("L", (), {"setText": lambda self, t: None})()
    host._reload_apps = lambda: None
    host._update_batch_bar = lambda: None

    LibraryPageMixin._on_batch_remove(host)

    assert not (data_dir() / "discovered" / "excel").exists()
    assert not (data_dir() / "apps" / "word").exists()
    assert host._selected_names == set()
    assert host._select_mode is False
    # Parity with single-delete (#514): only an app whose listed source is
    # "discovered" gets tombstoned. "word" resolves to the user override
    # (source="user"), so it is NOT tombstoned -- same as _on_delete_app.
    tomb = suppressed_app_slugs()
    assert "excel" in tomb
    assert "word" not in tomb


def test_batch_remove_aborts_on_no(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from PySide6.QtWidgets import QMessageBox

    from winpodx.core.app import data_dir
    from winpodx.gui._main_window_library import LibraryPageMixin

    d = data_dir() / "apps" / "keep"
    d.mkdir(parents=True)
    (d / "app.toml").write_text('name = "keep"\n', encoding="utf-8")

    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    )
    host = type("H", (), {})()
    host._selected_names = {"keep"}
    host._select_mode = True
    host.btn_select = type("B", (), {"setChecked": lambda self, v: None})()
    host.info_label = type("L", (), {"setText": lambda self, t: None})()
    host._reload_apps = lambda: None
    host._update_batch_bar = lambda: None

    LibraryPageMixin._on_batch_remove(host)
    assert d.exists()  # declined -> nothing removed
    assert host._selected_names == {"keep"}  # selection preserved


def test_batch_hide_hides_selected(monkeypatch, tmp_path):
    """Bulk 'Hide selected' flips every chosen app to hidden=true (#530)."""
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir, find_app
    from winpodx.gui._main_window_library import LibraryPageMixin

    _patch_desktop_sync(monkeypatch)
    for name in ("word", "excel"):
        d = data_dir() / "apps" / name
        d.mkdir(parents=True)
        (d / "app.toml").write_text(
            f'name = "{name}"\nfull_name = "{name}"\nexecutable = "C:\\\\x.exe"\n', encoding="utf-8"
        )

    host = type("H", (), {})()
    host._selected_names = {"word", "excel"}
    host._select_mode = True
    host.btn_select = type("B", (), {"setChecked": lambda self, v: None})()
    host.btn_grid = type("G", (), {"setEnabled": lambda self, v: None})()
    host.info_label = type("L", (), {"setText": lambda self, t: None})()
    host._reload_apps = lambda: None
    host._update_batch_bar = lambda: None

    LibraryPageMixin._on_batch_hide(host)
    assert find_app("word").hidden is True
    assert find_app("excel").hidden is True
    assert host._selected_names == set()
    assert host._select_mode is False


# -- restore deleted apps (#530 follow-up) --------------------------------


def test_clear_suppressed_slugs(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import (
        clear_suppressed_slugs,
        suppress_app_slug,
        suppressed_app_slugs,
    )

    assert clear_suppressed_slugs() == 0  # nothing to clear
    suppress_app_slug("paint")
    suppress_app_slug("notepad")
    assert suppressed_app_slugs() == {"paint", "notepad"}
    assert clear_suppressed_slugs() == 2  # both tombstones dropped
    assert suppressed_app_slugs() == set()


def test_restore_deleted_slugs_partial_vs_all(monkeypatch, tmp_path):
    """The GUI restore handler clears all tombstones for a full restore, else
    unsuppresses just the chosen slugs -- driven on a minimal fake host."""
    import pytest

    pytest.importorskip("PySide6")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import suppress_app_slug, suppressed_app_slugs
    from winpodx.gui._main_window_library import LibraryPageMixin

    for s in ("paint", "notepad", "wordpad"):
        suppress_app_slug(s)

    refreshed = []
    host = type("H", (), {})()
    host._on_refresh_apps = lambda: refreshed.append(True)
    host.info_label = type("L", (), {"setText": lambda self, t: None})()

    # partial restore -> only that slug comes off the tombstone list
    LibraryPageMixin._restore_deleted_slugs(host, ["notepad"])
    assert suppressed_app_slugs() == {"paint", "wordpad"}
    assert refreshed == [True]  # re-scan triggered

    # restoring the remaining set (== full list) clears everything
    LibraryPageMixin._restore_deleted_slugs(host, ["paint", "wordpad"])
    assert suppressed_app_slugs() == set()


# -- checksum-gated re-extraction (periodic icon refresh) ------------------

_H1 = "a" * 64
_H2 = "b" * 64


def _persist_one(root, *, exe_hash):
    from winpodx.core import discovery as d

    app = d.DiscoveredApp(
        name="word",
        full_name="Word",
        executable="C:\\w.exe",
        exe_hash=exe_hash,
        icon_bytes=b"<svg/>",
    )
    d.persist_discovered([app], target_dir=root, add_essentials=False)


def test_persist_skips_unchanged_exe_hash(tmp_path):
    root = tmp_path / "discovered"
    _persist_one(root, exe_hash=_H1)
    sentinel = root / "word" / "SENTINEL"
    sentinel.write_text("x", encoding="utf-8")

    _persist_one(root, exe_hash=_H1)  # same hash -> unchanged -> left as-is
    assert sentinel.exists()  # dir not rmtree'd = re-extraction skipped


def test_persist_reextracts_on_changed_exe_hash(tmp_path):
    root = tmp_path / "discovered"
    _persist_one(root, exe_hash=_H1)
    sentinel = root / "word" / "SENTINEL"
    sentinel.write_text("x", encoding="utf-8")

    _persist_one(root, exe_hash=_H2)  # changed hash -> re-extract
    assert not sentinel.exists()  # dir rmtree'd = rewritten


def test_persist_always_rewrites_when_no_hash(tmp_path):
    root = tmp_path / "discovered"
    _persist_one(root, exe_hash="")  # UWP / hashless -> no gating
    sentinel = root / "word" / "SENTINEL"
    sentinel.write_text("x", encoding="utf-8")

    _persist_one(root, exe_hash="")
    assert not sentinel.exists()  # always rewritten (gate disabled without a hash)


def test_persist_rewrites_unchanged_hash_if_icon_missing(tmp_path):
    root = tmp_path / "discovered"
    _persist_one(root, exe_hash=_H1)
    # Drop the icon: an icon-less entry must rewrite even on an unchanged hash.
    for ext in ("svg", "png"):
        (root / "word" / f"icon.{ext}").unlink(missing_ok=True)
    sentinel = root / "word" / "SENTINEL"
    sentinel.write_text("x", encoding="utf-8")

    _persist_one(root, exe_hash=_H1)
    assert not sentinel.exists()  # rewritten to recover the missing icon


def test_exe_hash_roundtrips_through_toml(tmp_path):
    from winpodx.core.app import load_app
    from winpodx.core.discovery import DiscoveredApp, _render_app_toml

    app = DiscoveredApp(name="word", full_name="Word", executable="C:\\w.exe", exe_hash=_H1)
    d = tmp_path / "word"
    d.mkdir()
    (d / "app.toml").write_text(_render_app_toml(app), encoding="utf-8")
    loaded = load_app(d)
    assert loaded is not None
    assert loaded.exe_hash == _H1
