"""Tests for app management."""

from winpodx.core.app import load_app


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
