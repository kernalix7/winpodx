"""Tests for `core.pod.recovery.try_recover_rdp` — agent-driven RDP recovery.

Covers the three terminal RecoveryAction outcomes plus the "RDP comes back
mid-window" success path. The agent transport + RDP probe + time.sleep are
mocked so the test runs in milliseconds even though the production flow
polls for up to 30 s.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from winpodx.core.config import Config
from winpodx.core.pod.recovery import (
    RecoveryAction,
    try_recover_rdp,
)


def _agent_health(available: bool, detail: str = "") -> MagicMock:
    h = MagicMock()
    h.available = available
    h.detail = detail
    return h


def _exec_result(rc: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.rc = rc
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_recovery_succeeds_when_rdp_comes_back():
    cfg = Config()

    fake_transport = MagicMock()
    fake_transport.health.return_value = _agent_health(True)
    fake_transport.exec.return_value = _exec_result(rc=0)

    with (
        patch("winpodx.core.transport.agent.AgentTransport", return_value=fake_transport),
        patch("winpodx.core.pod.recovery.check_rdp_port", return_value=True),
        patch("winpodx.core.pod.recovery.time.sleep"),
    ):
        result = try_recover_rdp(cfg)

    assert result.success is True
    assert result.action == RecoveryAction.RESTARTED_TERMSERVICE
    fake_transport.exec.assert_called_once()
    args, kwargs = fake_transport.exec.call_args
    assert "TermService" in args[0]


def test_recovery_fails_with_agent_unreachable_when_health_down():
    cfg = Config()

    fake_transport = MagicMock()
    fake_transport.health.return_value = _agent_health(False, detail="connection refused")

    with patch("winpodx.core.transport.agent.AgentTransport", return_value=fake_transport):
        result = try_recover_rdp(cfg)

    assert result.success is False
    assert result.action == RecoveryAction.AGENT_UNREACHABLE
    assert "connection refused" in result.detail
    fake_transport.exec.assert_not_called()


def test_recovery_fails_with_rdp_still_down_when_probe_never_clears():
    cfg = Config()

    fake_transport = MagicMock()
    fake_transport.health.return_value = _agent_health(True)
    fake_transport.exec.return_value = _exec_result(rc=0)

    with (
        patch("winpodx.core.transport.agent.AgentTransport", return_value=fake_transport),
        patch("winpodx.core.pod.recovery.check_rdp_port", return_value=False),
        patch("winpodx.core.pod.recovery.time.sleep"),
        patch(
            "winpodx.core.pod.recovery.time.monotonic",
            side_effect=[0.0, 5.0, 15.0, 31.0],
        ),
    ):
        result = try_recover_rdp(cfg)

    assert result.success is False
    assert result.action == RecoveryAction.RDP_STILL_DOWN
    assert "still unreachable" in result.detail


def test_recovery_propagates_agent_exec_failure_as_agent_unreachable():
    from winpodx.core.transport.base import TransportTimeoutError

    cfg = Config()

    fake_transport = MagicMock()
    fake_transport.health.return_value = _agent_health(True)
    fake_transport.exec.side_effect = TransportTimeoutError("exec hit 20s timeout")

    with patch("winpodx.core.transport.agent.AgentTransport", return_value=fake_transport):
        result = try_recover_rdp(cfg)

    assert result.success is False
    assert result.action == RecoveryAction.AGENT_UNREACHABLE
    assert "20s timeout" in result.detail
