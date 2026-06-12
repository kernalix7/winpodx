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
