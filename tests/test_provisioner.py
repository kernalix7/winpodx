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


# --- v0.1.9.4: runtime applies via FreeRDP RemoteApp (windows_exec.run_in_windows) ---
#
# The v0.1.9.0-v0.1.9.3 versions of these tests mocked podman-exec subprocess
# calls — but podman exec can't reach the Windows VM inside the dockur Linux
# container, so the helpers never actually applied anything (they just logged
# warnings). v0.1.9.4 routes them through windows_exec.run_in_windows, which
# launches PowerShell as a FreeRDP RemoteApp. These tests mock that helper.


def _mock_run_in_windows(monkeypatch, *, rc: int = 0, stdout: str = "", stderr: str = ""):
    """Return a list that captures every (description, payload) call."""
    from winpodx.core.windows_exec import WindowsExecResult

    captured: list[tuple[str, str]] = []

    def fake(cfg, payload, *, timeout=60, description="windows-exec"):
        captured.append((description, payload))
        return WindowsExecResult(rc=rc, stdout=stdout, stderr=stderr)

    import winpodx.core.provisioner as prov

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake)
    # Make sure the lazy-imported reference inside provisioner picks up the patched fn.
    monkeypatch.setattr(prov, "_apply_max_sessions", prov._apply_max_sessions)
    return captured


def test_apply_max_sessions_skips_libvirt_backend(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    captured = _mock_run_in_windows(monkeypatch)
    provisioner._apply_max_sessions(cfg)
    assert captured == []


def test_apply_max_sessions_runs_via_windows_exec(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.max_sessions = 25
    captured = _mock_run_in_windows(monkeypatch, rc=0, stdout="max_sessions: 10 -> 25")
    provisioner._apply_max_sessions(cfg)
    assert len(captured) == 1
    description, payload = captured[0]
    assert description == "apply-max-sessions"
    assert "MaxInstanceCount" in payload
    assert "$desired = 25" in payload
    assert "fSingleSessionPerUser" in payload
    # v0.1.9.5: Restart-Service intentionally removed — restarting the
    # TermService that hosts our own RDP session kills the session
    # before the wrapper can write its result file.
    assert "Restart-Service" not in payload


def test_apply_max_sessions_raises_on_nonzero_rc(monkeypatch):
    """v0.1.9.4: helpers no longer silently swallow non-zero rc."""
    import pytest

    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    _mock_run_in_windows(monkeypatch, rc=2, stderr="permission denied")
    with pytest.raises(RuntimeError, match="rc=2"):
        provisioner._apply_max_sessions(cfg)


def test_apply_max_sessions_propagates_channel_error(monkeypatch):
    import pytest

    from winpodx.core import provisioner
    from winpodx.core.config import Config
    from winpodx.core.windows_exec import WindowsExecError

    cfg = Config()

    def fake(*a, **k):
        raise WindowsExecError("FreeRDP not found")

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake)
    with pytest.raises(WindowsExecError, match="FreeRDP not found"):
        provisioner._apply_max_sessions(cfg)


def test_apply_rdp_timeouts_skips_libvirt(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    captured = _mock_run_in_windows(monkeypatch)
    provisioner._apply_rdp_timeouts(cfg)
    assert captured == []


def test_apply_rdp_timeouts_payload_contains_all_keys(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    captured = _mock_run_in_windows(monkeypatch, rc=0, stdout="rdp_timeouts applied")
    provisioner._apply_rdp_timeouts(cfg)
    assert len(captured) == 1
    description, payload = captured[0]
    assert description == "apply-rdp-timeouts"
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
        assert token in payload, f"missing {token!r} in payload"


def test_apply_oem_runtime_fixes_skips_libvirt(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    captured = _mock_run_in_windows(monkeypatch)
    provisioner._apply_oem_runtime_fixes(cfg)
    assert captured == []


def test_apply_oem_runtime_fixes_payload_contains_nic_and_termservice(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    captured = _mock_run_in_windows(monkeypatch, rc=0, stdout="oem v7 baseline applied")
    provisioner._apply_oem_runtime_fixes(cfg)
    assert len(captured) == 1
    description, payload = captured[0]
    assert description == "apply-oem"
    assert "Set-NetAdapterPowerManagement" in payload
    assert "AllowComputerToTurnOffDevice" in payload
    assert "sc.exe failure TermService" in payload
    assert "restart/5000/restart/5000/restart/5000" in payload


def test_ensure_ready_runs_apply_before_early_return_when_rdp_alive(monkeypatch):
    """v0.1.9.2: existing healthy pods must still get runtime fixes applied."""
    from winpodx.core import provisioner
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, PodStatus

    cfg = Config()
    cfg.pod.backend = "podman"

    monkeypatch.setattr(provisioner, "_check_rotation_pending", lambda: None)
    monkeypatch.setattr(provisioner, "_auto_rotate_password", lambda c: c)
    monkeypatch.setattr(provisioner, "_ensure_config", lambda: cfg)
    monkeypatch.setattr(provisioner, "pod_status", lambda c: PodStatus(state=PodState.RUNNING))
    # RDP alive -> must trigger early return AFTER runtime apply.
    monkeypatch.setattr(provisioner, "check_rdp_port", lambda *a, **k: True)

    calls = {"max_sessions": 0, "rdp_timeouts": 0, "oem_runtime_fixes": 0}

    def make_recorder(name):
        def f(c):
            calls[name] += 1

        return f

    monkeypatch.setattr(provisioner, "_apply_max_sessions", make_recorder("max_sessions"))
    monkeypatch.setattr(provisioner, "_apply_rdp_timeouts", make_recorder("rdp_timeouts"))
    monkeypatch.setattr(provisioner, "_apply_oem_runtime_fixes", make_recorder("oem_runtime_fixes"))

    result = provisioner.ensure_ready(cfg, timeout=1)
    assert result is cfg
    # All three idempotent applies fired exactly once even though the
    # function early-returned at the RDP-port check.
    assert calls == {"max_sessions": 1, "rdp_timeouts": 1, "oem_runtime_fixes": 1}


def test_ensure_ready_skips_apply_when_pod_not_running(monkeypatch):
    """When pod isn't running, the early-apply branch is skipped (later branch handles)."""
    from winpodx.core import provisioner
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, PodStatus

    cfg = Config()
    cfg.pod.backend = "podman"

    monkeypatch.setattr(provisioner, "_check_rotation_pending", lambda: None)
    monkeypatch.setattr(provisioner, "_auto_rotate_password", lambda c: c)
    monkeypatch.setattr(provisioner, "_ensure_config", lambda: cfg)
    monkeypatch.setattr(provisioner, "pod_status", lambda c: PodStatus(state=PodState.STOPPED))
    monkeypatch.setattr(provisioner, "check_rdp_port", lambda *a, **k: True)
    early_calls = {"n": 0}

    def recorder(c):
        early_calls["n"] += 1

    monkeypatch.setattr(provisioner, "_apply_oem_runtime_fixes", recorder)
    monkeypatch.setattr(provisioner, "_apply_max_sessions", recorder)
    monkeypatch.setattr(provisioner, "_apply_rdp_timeouts", recorder)

    provisioner.ensure_ready(cfg, timeout=1)
    # Stopped pod -> the early-branch `pod_status==RUNNING` guard prevents
    # the apply calls from firing on the early return path.
    assert early_calls["n"] == 0


# --- v0.1.9.3: apply_windows_runtime_fixes public API ---


def test_apply_windows_runtime_fixes_skips_libvirt():
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "libvirt"
    result = provisioner.apply_windows_runtime_fixes(cfg)
    assert "backend" in result
    assert "skipped" in result["backend"]


def test_apply_windows_runtime_fixes_returns_per_helper_status(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config
    from winpodx.core.windows_exec import WindowsExecResult

    cfg = Config()

    def fake(cfg_inner, payload, *, timeout=60, description="windows-exec"):
        return WindowsExecResult(rc=0, stdout="ok", stderr="")

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake)
    result = provisioner.apply_windows_runtime_fixes(cfg)
    assert set(result.keys()) == {"max_sessions", "rdp_timeouts", "oem_runtime_fixes"}
    for v in result.values():
        assert v == "ok"


def test_apply_windows_runtime_fixes_records_individual_failures(monkeypatch):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    cfg = Config()

    def fake_max_sessions(c):
        raise RuntimeError("boom max_sessions")

    monkeypatch.setattr(provisioner, "_apply_max_sessions", fake_max_sessions)
    monkeypatch.setattr(provisioner, "_apply_rdp_timeouts", lambda c: None)
    monkeypatch.setattr(provisioner, "_apply_oem_runtime_fixes", lambda c: None)
    result = provisioner.apply_windows_runtime_fixes(cfg)
    assert result["max_sessions"].startswith("failed: ")
    assert result["rdp_timeouts"] == "ok"
    assert result["oem_runtime_fixes"] == "ok"
