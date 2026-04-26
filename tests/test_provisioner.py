"""Tests for auto-provisioning engine."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from winpodx.core.provisioner import ProvisionError


def test_provision_error():
    err = ProvisionError("test error")
    assert str(err) == "test error"
    assert isinstance(err, Exception)


def test_ensure_config_creates_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from winpodx.core.provisioner import _ensure_config

    cfg = _ensure_config()

    assert cfg.rdp.user == "User"
    assert cfg.rdp.ip == "127.0.0.1"
    assert (tmp_path / "winpodx" / "winpodx.toml").exists()


# C3: password rotation rollback failure handling


@pytest.fixture()
def _rotation_cfg(tmp_path, monkeypatch):
    """Config set up to trigger _auto_rotate_password work."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from winpodx.core.config import Config

    cfg = Config()
    cfg.rdp.user = "User"
    cfg.rdp.password = "old-password"
    cfg.rdp.password_max_age = 1  # day
    cfg.rdp.password_updated = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    cfg.pod.backend = "podman"
    cfg.save()
    return cfg


def test_rotation_rollback_success_reverts_password(_rotation_cfg, monkeypatch):
    # When config.save fails but Windows rollback succeeds, config keeps the old password.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    monkeypatch.setattr(provisioner, "_change_windows_password", lambda cfg, pw: True)

    with patch.object(_rotation_cfg, "save", side_effect=OSError("disk full")):
        result = provisioner._auto_rotate_password(_rotation_cfg)

    assert result.rdp.password == "old-password"
    assert not provisioner._rotation_marker_path().exists()


def test_rotation_rollback_failure_writes_marker(_rotation_cfg, monkeypatch):
    # Config save and Windows rollback both fail: must log error and write .rotation_pending marker.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )

    calls: list[str] = []

    def fake_change(cfg, pw):
        calls.append(pw)
        return len(calls) == 1

    monkeypatch.setattr(provisioner, "_change_windows_password", fake_change)

    with patch.object(_rotation_cfg, "save", side_effect=OSError("disk full")):
        provisioner._auto_rotate_password(_rotation_cfg)

    assert len(calls) == 2
    marker = provisioner._rotation_marker_path()
    assert marker.exists()
    assert marker.stat().st_mode & 0o777 == 0o600


def test_check_rotation_pending_warns(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core import provisioner

    marker = provisioner._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    with caplog.at_level(logging.ERROR, logger="winpodx.core.provisioner"):
        provisioner._check_rotation_pending()

    assert any("Pending password rotation" in r.message for r in caplog.records)


def test_rotation_marker_cleared_on_success(_rotation_cfg, monkeypatch):
    # A successful rotation must clear any previously-written marker.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    marker = provisioner._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    monkeypatch.setattr(provisioner, "_change_windows_password", lambda cfg, pw: True)

    provisioner._auto_rotate_password(_rotation_cfg)

    assert not marker.exists()


# --- v0.1.8: _apply_max_sessions runtime registry sync ---


def test_apply_max_sessions_skips_libvirt_backend():
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    # Must return without raising and without calling subprocess.
    provisioner._apply_max_sessions(cfg)


def test_apply_max_sessions_noop_when_registry_matches(monkeypatch):
    from unittest.mock import MagicMock

    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.max_sessions = 20

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        # First call = read; stdout says current value is already 20.
        result = MagicMock()
        result.stdout = "20\n"
        result.stderr = ""
        result.returncode = 0
        return result

    monkeypatch.setattr(provisioner.subprocess, "run", fake_run)
    provisioner._apply_max_sessions(cfg)

    # Only the read call should have fired — no write, no TermService restart.
    assert len(calls) == 1
    assert "-Command" in calls[0]
    assert "Get-ItemProperty" in calls[0][-1]


def test_apply_max_sessions_writes_and_restarts_when_differs(monkeypatch):
    from unittest.mock import MagicMock

    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.max_sessions = 25

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        result = MagicMock()
        if "Get-ItemProperty" in cmd[-1]:
            result.stdout = "10\n"  # guest is still at install.bat's default
        else:
            result.stdout = ""
        result.stderr = ""
        result.returncode = 0
        return result

    monkeypatch.setattr(provisioner.subprocess, "run", fake_run)
    provisioner._apply_max_sessions(cfg)

    # Two subprocess calls: read, then apply.
    assert len(calls) == 2
    apply_cmd = calls[1][-1]
    assert "Set-ItemProperty" in apply_cmd
    assert "MaxInstanceCount" in apply_cmd
    assert "-Value 25" in apply_cmd
    assert "fSingleSessionPerUser" in apply_cmd
    assert "Restart-Service" in apply_cmd
    assert "TermService" in apply_cmd


def test_apply_max_sessions_survives_missing_registry_key(monkeypatch):
    """When the read returns empty stdout (key missing), apply still fires."""
    from unittest.mock import MagicMock

    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.max_sessions = 15

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        result = MagicMock()
        result.stdout = "" if "Get-ItemProperty" in cmd[-1] else ""
        result.stderr = ""
        result.returncode = 0
        return result

    monkeypatch.setattr(provisioner.subprocess, "run", fake_run)
    provisioner._apply_max_sessions(cfg)

    # Missing key -> current == None, which != desired, so apply fires.
    assert len(calls) == 2


def test_apply_max_sessions_tolerates_timeout(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()

    def fake_run(cmd, **kwargs):
        raise provisioner.subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

    monkeypatch.setattr(provisioner.subprocess, "run", fake_run)
    # Must not raise — timeout just logs and returns.
    provisioner._apply_max_sessions(cfg)


# --- v0.1.9.1: _apply_rdp_timeouts ---


def test_apply_rdp_timeouts_skips_libvirt():
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    provisioner._apply_rdp_timeouts(cfg)  # must not raise / not subprocess


def test_apply_rdp_timeouts_writes_all_keys(monkeypatch):
    from unittest.mock import MagicMock

    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    captured = []

    def fake_run(cmd, **kw):
        captured.append(cmd)
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        return m

    monkeypatch.setattr(provisioner.subprocess, "run", fake_run)
    provisioner._apply_rdp_timeouts(cfg)
    assert len(captured) == 1
    script = captured[0][-1]
    for token in (
        "MaxIdleTime",
        "MaxDisconnectionTime",
        "MaxConnectionTime",
        "KeepAliveEnable",
        "KeepAliveInterval",
        "KeepAliveTimeout",
        "RDP-Tcp",
        "Terminal Services",
    ):
        assert token in script


def test_apply_rdp_timeouts_tolerates_timeout(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()

    def boom(cmd, **kw):
        raise provisioner.subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))

    monkeypatch.setattr(provisioner.subprocess, "run", boom)
    # Must swallow the exception.
    provisioner._apply_rdp_timeouts(cfg)
