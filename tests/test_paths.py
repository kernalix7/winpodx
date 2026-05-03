"""Tests for XDG path management."""

from pathlib import Path

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
        for marker in ("scripts", "config", "data"):
            (tmp_path / marker).mkdir()
        monkeypatch.setenv("WINPODX_BUNDLE_DIR", str(tmp_path))
        assert bundle_dir() == tmp_path

    def test_partial_markers_skipped(self, tmp_path, monkeypatch):
        """A stale directory with only some marker dirs must not match.

        Regression test for GH-93: an RPM uninstall left behind
        /usr/share/winpodx/config/ (one marker), which hijacked
        bundle_dir() resolution away from the correct curl-install path.
        """
        stale = tmp_path / "stale-share" / "winpodx"
        stale.mkdir(parents=True)
        (stale / "config").mkdir()

        full = tmp_path / "full-install"
        full.mkdir()
        for marker in ("scripts", "config", "data"):
            (full / marker).mkdir()

        monkeypatch.setenv("WINPODX_BUNDLE_DIR", str(stale))
        result = bundle_dir()
        assert result != stale, "partial markers should not match"

    def test_resolves_shipped_scripts(self):
        result = bundle_dir()
        assert (result / "scripts" / "windows" / "discover_apps.ps1").exists()


class TestFindOemDir:
    """_find_oem_dir() must return a user-writable copy, not the bundle path."""

    def test_copies_bundle_oem_to_user_config(self, tmp_path, monkeypatch):
        from winpodx.core.pod.compose import _find_oem_dir

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        result = Path(_find_oem_dir())

        assert result == tmp_path / "winpodx" / "oem"
        assert result.is_dir()
        assert (result / "install.bat").exists(), "bundle OEM files should be copied"

    def test_user_oem_not_under_usr_share(self, tmp_path, monkeypatch):
        """Regression test for GH-93: compose mount path must be user-owned."""
        from winpodx.core.pod.compose import _find_oem_dir

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        result = _find_oem_dir()

        assert "/usr/share/" not in result, (
            "OEM dir must not point to system paths (SELinux :Z relabeling fails)"
        )
