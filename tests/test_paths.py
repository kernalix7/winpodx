# SPDX-License-Identifier: MIT
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
    """_find_oem_dir() / _prepare_oem_dir() always return the user OEM
    dir under ~/.config/winpodx/oem/, populated by copy from the bundle
    OEM tree. Pre-#254 the function had a fast-path that returned the
    bundle dir directly when it was user-writable, but #254 needs to
    drop per-config files (timezone.txt) into the OEM dir without
    polluting the source bundle, so the always-copy path is now the
    single regime. Both call shapes are covered."""

    def test_returns_user_oem_path_always(self, tmp_path, monkeypatch):
        """Even when the bundle is user-writable, the function returns
        a copy under ``~/.config/winpodx/oem/`` rather than the bundle
        path. Lets the compose generator safely drop ``timezone.txt``
        (and future per-config files) without touching the source
        checkout."""
        from winpodx.core.pod.compose import _find_oem_dir

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        result = Path(_find_oem_dir())

        user_oem = tmp_path / "winpodx" / "oem"
        assert result == user_oem, "expected always-copy path under user_oem"
        # Bundle OEM contents must be present under the user_oem copy.
        assert (result / "install.bat").exists(), "bundle OEM files should be copied"

    def test_copies_to_user_dir_when_bundle_readonly(self, tmp_path, monkeypatch):
        """Fedora/RPM case: bundle dir is root-owned + read-only to
        the current user. Fall back to a copy under
        ``~/.config/winpodx/oem/`` so Podman's ``:Z`` relabel can land.
        Regression test for GH-93 (pgarciaq's lsetxattr fail).
        """
        import os as _os

        from winpodx.core.pod import compose as compose_mod

        # Build a fake "bundle" dir we can mark as read-only-only.
        fake_bundle = tmp_path / "fake-bundle"
        (fake_bundle / "config" / "oem").mkdir(parents=True)
        (fake_bundle / "scripts").mkdir()
        (fake_bundle / "data").mkdir()
        (fake_bundle / "config" / "oem" / "install.bat").write_text("rem fake")

        # bundle_dir() is module-level, monkeypatch the import site.
        monkeypatch.setattr(compose_mod, "bundle_dir", lambda: fake_bundle)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))

        # Force the writability check to report False even though the
        # test process technically owns the dir. Mocking os.access is
        # cleaner than juggling sticky chmod bits in a tmp tree.
        real_access = _os.access

        def fake_access(path, mode):
            if str(path).startswith(str(fake_bundle)):
                return False
            return real_access(path, mode)

        monkeypatch.setattr(compose_mod.os, "access", fake_access)

        result = Path(compose_mod._find_oem_dir())
        user_oem = tmp_path / "user-config" / "winpodx" / "oem"

        assert result == user_oem, "expected fallback to user_oem when bundle is read-only"
        assert result.is_dir()
        assert (result / "install.bat").exists(), "bundle OEM files should be copied"
        # Files must end up world-readable so dockur's in-container
        # cp succeeds regardless of the host user's umask.
        copied_mode = (result / "install.bat").stat().st_mode & 0o777
        assert copied_mode & 0o044, f"copied file mode {oct(copied_mode)} not world-readable"

    def test_user_oem_not_under_usr_share(self, tmp_path, monkeypatch):
        """Regression test for GH-93: even when the fallback fires,
        the returned path never points at the system bundle path."""
        from winpodx.core.pod.compose import _find_oem_dir

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        result = _find_oem_dir()
        assert "/usr/share/" not in result, (
            "OEM dir must not point to system paths (SELinux :Z relabeling fails)"
        )
