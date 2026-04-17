"""Tests for app management."""

from pathlib import Path

from winpodx.core.app import bundled_apps_dir, load_app


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


def test_load_app_missing(tmp_path):
    app = load_app(tmp_path / "nonexistent")
    assert app is None


def test_bundled_apps_dir_returns_source_when_repo_layout_exists():
    """When the repo-layout path exists, it should be preferred and exist."""
    result = bundled_apps_dir()
    # This project has data/apps present in the repo, so it must resolve.
    assert result.parts[-2:] == ("data", "apps")
    assert result.exists()


def test_bundled_apps_dir_falls_back_to_sys_prefix(tmp_path, monkeypatch):
    """When the repo layout is absent, ``sys.prefix/share/winpodx/data/apps`` must win.

    We patch ``__file__`` inside the app module so the first candidate
    (repo layout, 4 levels up) resolves to a non-existent path, and point
    ``sys.prefix`` at a fake wheel-install tree.
    """
    import winpodx.core.app as app_mod

    # Fake prefix with wheel-install layout.
    fake_prefix = tmp_path / "prefix"
    wheel_apps = fake_prefix / "share" / "winpodx" / "data" / "apps"
    wheel_apps.mkdir(parents=True)
    (wheel_apps / "sentinel").write_text("x")

    # Deep enough that parent**4 resolves to a non-existent dir.
    fake_module_file = tmp_path / "a" / "b" / "c" / "d" / "app.py"
    fake_module_file.parent.mkdir(parents=True)
    fake_module_file.write_text("")

    # Home that also does not contain the user-install layout.
    fake_home = tmp_path / "home-missing"

    monkeypatch.setattr(app_mod, "__file__", str(fake_module_file))
    monkeypatch.setattr(app_mod.sys, "prefix", str(fake_prefix))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    result = app_mod.bundled_apps_dir()
    assert result == wheel_apps


def test_bundled_apps_dir_falls_back_to_user_local(tmp_path, monkeypatch):
    """If neither repo nor sys.prefix hit, ~/.local/share/winpodx/data/apps must win."""
    import winpodx.core.app as app_mod

    fake_home = tmp_path / "home"
    user_apps = fake_home / ".local" / "share" / "winpodx" / "data" / "apps"
    user_apps.mkdir(parents=True)

    fake_prefix = tmp_path / "empty-prefix"
    fake_prefix.mkdir()

    fake_module_file = tmp_path / "a" / "b" / "c" / "d" / "app.py"
    fake_module_file.parent.mkdir(parents=True)
    fake_module_file.write_text("")

    monkeypatch.setattr(app_mod, "__file__", str(fake_module_file))
    monkeypatch.setattr(app_mod.sys, "prefix", str(fake_prefix))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    result = app_mod.bundled_apps_dir()
    assert result == user_apps


def test_bundled_apps_dir_returns_source_default_when_nothing_exists(tmp_path, monkeypatch):
    """If no candidate exists, return the source-layout path (callers check existence)."""
    import winpodx.core.app as app_mod

    fake_prefix = tmp_path / "empty-prefix"
    fake_prefix.mkdir()
    fake_home = tmp_path / "home-missing"

    fake_module_file = tmp_path / "a" / "b" / "c" / "d" / "app.py"
    fake_module_file.parent.mkdir(parents=True)
    fake_module_file.write_text("")

    monkeypatch.setattr(app_mod, "__file__", str(fake_module_file))
    monkeypatch.setattr(app_mod.sys, "prefix", str(fake_prefix))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    result = app_mod.bundled_apps_dir()
    assert not result.exists()
    # First candidate = repo layout (parent**4 of fake file + data/apps).
    assert result.parts[-2:] == ("data", "apps")
