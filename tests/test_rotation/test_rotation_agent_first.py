"""Agent-first password rotation with FreeRDP fallback.

Covers ``_change_windows_password`` after rule #6 was superseded
(2026-05-07): rotation now prefers AgentTransport and falls back to
``run_in_windows`` only when the agent is unavailable.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def _rotate_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core.config import Config

    cfg = Config()
    cfg.rdp.user = "User"
    cfg.rdp.password = "old-password"
    cfg.pod.backend = "podman"
    return cfg


def _ok_result():
    from winpodx.core.transport.base import ExecResult

    return ExecResult(rc=0, stdout="password set\n", stderr="")


def _fail_result(rc: int = 1, stderr: str = "boom"):
    from winpodx.core.transport.base import ExecResult

    return ExecResult(rc=rc, stdout="", stderr=stderr)


def test_agent_ok_skips_freerdp(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation

    transport = MagicMock()
    transport.exec.return_value = _ok_result()
    dispatch = MagicMock(return_value=transport)
    monkeypatch.setattr("winpodx.core.transport.dispatch", dispatch)

    rin = MagicMock()
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    assert rotation._change_windows_password(_rotate_cfg, "new-pw") is True

    dispatch.assert_called_once()
    _, kwargs = dispatch.call_args
    assert kwargs.get("prefer") == "agent"
    transport.exec.assert_called_once()
    rin.assert_not_called()


def test_agent_unavailable_falls_back_to_freerdp(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.transport.base import TransportUnavailable

    dispatch = MagicMock(side_effect=TransportUnavailable("agent down"))
    monkeypatch.setattr("winpodx.core.transport.dispatch", dispatch)

    rin = MagicMock(return_value=_ok_result())
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    assert rotation._change_windows_password(_rotate_cfg, "new-pw") is True

    rin.assert_called_once()
    args, kwargs = rin.call_args
    # signature: run_in_windows(cfg, payload, description=..., timeout=...)
    assert args[0] is _rotate_cfg
    assert "net user" in args[1]
    assert kwargs.get("description") == "rotate-password"
    assert kwargs.get("timeout") == 45


def test_agent_auth_error_does_not_fall_back(_rotate_cfg, monkeypatch, caplog):
    from winpodx.core import rotation
    from winpodx.core.transport.base import TransportAuthError

    dispatch = MagicMock(side_effect=TransportAuthError("bad token"))
    monkeypatch.setattr("winpodx.core.transport.dispatch", dispatch)

    rin = MagicMock()
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    with caplog.at_level(logging.WARNING, logger="winpodx.core.rotation"):
        ok = rotation._change_windows_password(_rotate_cfg, "new-pw")

    assert ok is False
    rin.assert_not_called()
    assert any("auth failure" in r.message and "bad token" in r.message for r in caplog.records)


def test_agent_auth_error_from_exec_does_not_fall_back(_rotate_cfg, monkeypatch):
    """TransportAuthError raised by transport.exec (not dispatch) also blocks fallback."""
    from winpodx.core import rotation
    from winpodx.core.transport.base import TransportAuthError

    transport = MagicMock()
    transport.exec.side_effect = TransportAuthError("401")
    dispatch = MagicMock(return_value=transport)
    monkeypatch.setattr("winpodx.core.transport.dispatch", dispatch)

    rin = MagicMock()
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    assert rotation._change_windows_password(_rotate_cfg, "new-pw") is False
    rin.assert_not_called()


def test_agent_nonzero_rc_returns_false(_rotate_cfg, monkeypatch):
    """Script-level failure on the agent path is reported, not retried via FreeRDP."""
    from winpodx.core import rotation

    transport = MagicMock()
    transport.exec.return_value = _fail_result(rc=2, stderr="net user failed")
    dispatch = MagicMock(return_value=transport)
    monkeypatch.setattr("winpodx.core.transport.dispatch", dispatch)

    rin = MagicMock()
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    assert rotation._change_windows_password(_rotate_cfg, "new-pw") is False
    rin.assert_not_called()


def test_freerdp_fallback_channel_failure_returns_false(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.transport.base import TransportUnavailable
    from winpodx.core.windows_exec import WindowsExecError

    dispatch = MagicMock(side_effect=TransportUnavailable("agent down"))
    monkeypatch.setattr("winpodx.core.transport.dispatch", dispatch)

    rin = MagicMock(side_effect=WindowsExecError("freerdp died"))
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    assert rotation._change_windows_password(_rotate_cfg, "new-pw") is False


def test_marker_cleared_on_success_via_agent(_rotate_cfg, monkeypatch):
    """End-to-end via _auto_rotate_password: pre-existing marker is cleared
    when the agent path succeeds."""
    from datetime import datetime, timedelta, timezone

    from winpodx.core import rotation
    from winpodx.core.pod import PodState, PodStatus

    _rotate_cfg.rdp.password_max_age = 1
    _rotate_cfg.rdp.password_updated = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    _rotate_cfg.save()

    marker = rotation._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    monkeypatch.setattr(
        "winpodx.core.rotation.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    transport = MagicMock()
    transport.exec.return_value = _ok_result()
    monkeypatch.setattr("winpodx.core.transport.dispatch", MagicMock(return_value=transport))

    rotation._auto_rotate_password(_rotate_cfg)

    assert not marker.exists()


def test_marker_kept_when_rotation_partially_applied(_rotate_cfg, monkeypatch):
    """When config save fails AND the rollback Windows-side change fails,
    the partial-rotation marker must be written so ensure_ready can warn
    on next launch — independent of which transport was used."""
    from datetime import datetime, timedelta, timezone

    from winpodx.core import rotation
    from winpodx.core.pod import PodState, PodStatus

    _rotate_cfg.rdp.password_max_age = 1
    _rotate_cfg.rdp.password_updated = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    _rotate_cfg.save()

    monkeypatch.setattr(
        "winpodx.core.rotation.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )

    # First exec (apply new pw) succeeds; rollback exec (set old pw back) fails.
    transport = MagicMock()
    transport.exec.side_effect = [_ok_result(), _fail_result(rc=1, stderr="rollback failed")]
    monkeypatch.setattr("winpodx.core.transport.dispatch", MagicMock(return_value=transport))

    with patch.object(_rotate_cfg, "save", side_effect=OSError("disk full")):
        rotation._auto_rotate_password(_rotate_cfg)

    marker = rotation._rotation_marker_path()
    assert marker.exists()
    assert marker.stat().st_mode & 0o777 == 0o600
