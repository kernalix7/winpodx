"""Tests for XDG path management."""

from winpodx.utils.paths import applications_dir, bundle_dir, config_dir, data_dir


def test_config_dir(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-config")
    assert str(config_dir()) == "/tmp/test-config/winpodx"


def test_data_dir(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/test-data")
    assert str(data_dir()) == "/tmp/test-data/winpodx"


def test_applications_dir(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/test-data")
    assert str(applications_dir()) == "/tmp/test-data/applications"


class TestBundleDir:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        (tmp_path / "scripts").mkdir()
        monkeypatch.setenv("WINPODX_BUNDLE_DIR", str(tmp_path))
        assert bundle_dir() == tmp_path

    def test_resolves_shipped_scripts(self):
        # Source checkout (parents[3]) or packager-set $WINPODX_BUNDLE_DIR --
        # either way the discover script must be reachable.
        result = bundle_dir()
        assert (result / "scripts" / "windows" / "discover_apps.ps1").exists()
