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
