# SPDX-License-Identifier: MIT
"""Tests for ``winpodx doctor`` (#255 PR 6)."""

from __future__ import annotations

import argparse

import pytest

from winpodx.cli.doctor import (
    Finding,
    _check_autostart_entry,
    _check_config_state,
    _check_initialized_flag,
    _check_kvm,
    _check_pending_setup,
    handle_doctor,
)
from winpodx.core.config import Config


class TestFindingFormatting:
    def test_severity_tags(self):
        assert Finding("ok", "x").severity_tag().strip() == "[OK]"
        assert Finding("warn", "x").severity_tag().strip() == "[WARN]"
        assert Finding("fail", "x").severity_tag().strip() == "[FAIL]"


class TestPendingCheck:
    def test_no_pending_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        f = _check_pending_setup()
        assert f.severity == "ok"

    def test_pending_marker_returns_warn(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cd = tmp_path / "winpodx"
        cd.mkdir(parents=True)
        (cd / ".pending_setup").write_text("wait_ready\ndiscovery\n")
        f = _check_pending_setup()
        assert f.severity == "warn"
        assert "2 item" in f.title


class TestAutostartCheck:
    def test_no_autostart_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        f = _check_autostart_entry()
        assert f.severity == "ok"

    def test_autostart_missing_binary_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        autostart = tmp_path / "autostart" / "winpodx-tray.desktop"
        autostart.parent.mkdir(parents=True)
        autostart.write_text("[Desktop Entry]\nExec=winpodx tray\n")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        f = _check_autostart_entry()
        assert f.severity == "fail"


class TestInitializedCheck:
    def test_initialized_true_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.pod.initialized = True
        cfg.save()
        f = _check_initialized_flag()
        assert f.severity == "ok"

    def test_initialized_false_returns_warn(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        Config().save()  # initialized = False
        f = _check_initialized_flag()
        assert f.severity == "warn"


class TestConfigStateCheck:
    def test_neither_present_warns(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("shutil.which", lambda _name: None)
        f = _check_config_state()
        assert f.severity == "warn"
        assert "not installed" in f.title

    def test_config_only_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        Config().save()
        monkeypatch.setattr("shutil.which", lambda _name: None)
        f = _check_config_state()
        assert f.severity == "fail"
        assert "binary not on PATH" in f.title

    def test_binary_only_warns(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("shutil.which", lambda _name: "/fake/winpodx")
        f = _check_config_state()
        assert f.severity == "warn"
        assert "config missing" in f.title

    def test_both_present_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        Config().save()
        monkeypatch.setattr("shutil.which", lambda _name: "/fake/winpodx")
        f = _check_config_state()
        assert f.severity == "ok"


class TestKvmCheck:
    def test_present_returns_ok(self, monkeypatch):
        monkeypatch.setattr("pathlib.Path.exists", lambda self: str(self) == "/dev/kvm")
        f = _check_kvm()
        assert f.severity == "ok"

    def test_missing_returns_fail(self, monkeypatch):
        monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
        f = _check_kvm()
        assert f.severity == "fail"


class TestHandleDoctor:
    def test_exits_zero_on_no_fail(self, tmp_path, monkeypatch, capsys):
        """All checks return OK or WARN -> exit 0."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # Stub every check to OK to keep the test independent of host state.
        ok = Finding("ok", "stub")
        for name in (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        ):
            monkeypatch.setattr(f"winpodx.cli.doctor.{name}", lambda: ok)
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])
        monkeypatch.setattr("winpodx.cli.doctor._check_container_health", lambda: [ok])

        handle_doctor(argparse.Namespace())  # no sys.exit -> success
        out = capsys.readouterr().out
        assert "all checks passed" in out

    def test_exits_one_on_fail(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        fail = Finding("fail", "stub fail", suggestion="do something")
        ok = Finding("ok", "stub ok")
        for name in (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        ):
            monkeypatch.setattr(f"winpodx.cli.doctor.{name}", lambda: fail)
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])
        monkeypatch.setattr("winpodx.cli.doctor._check_container_health", lambda: [ok])

        with pytest.raises(SystemExit) as exc:
            handle_doctor(argparse.Namespace())
        assert exc.value.code == 1
