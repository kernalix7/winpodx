# SPDX-License-Identifier: MIT
"""Agent-first password rotation with FreeRDP fallback.

Covers ``_change_windows_password`` after rule #6 was superseded
(2026-05-07): rotation now prefers AgentTransport and falls back to
``run_in_windows`` only when the agent is unavailable.
"""

from __future__ import annotations

import logging
import re
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
    assert kwargs.get("timeout") == 120


def test_agent_disconnect_after_submission_is_unknown_without_fallback(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.transport.base import TransportUnavailable

    transport = MagicMock()
    transport.exec.side_effect = TransportUnavailable("connection lost")
    monkeypatch.setattr("winpodx.core.transport.dispatch", MagicMock(return_value=transport))
    fallback = MagicMock()
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fallback)

    with pytest.raises(rotation.RotationError, match="outcome is unknown"):
        rotation._change_windows_password(_rotate_cfg, "new-pw")

    fallback.assert_not_called()


def test_agent_server_timeout_result_is_unknown(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation

    transport = MagicMock()
    transport.exec.return_value = _fail_result(rc=124, stderr="execution timed out")
    monkeypatch.setattr("winpodx.core.transport.dispatch", MagicMock(return_value=transport))

    with pytest.raises(rotation.RotationError, match="outcome is unknown"):
        rotation._change_windows_password(_rotate_cfg, "new-pw")


def test_agent_server_timeout_keeps_transaction_pending(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.config import Config

    _rotate_cfg.save()
    transport = MagicMock()
    transport.exec.return_value = _fail_result(rc=124, stderr="execution timed out")
    monkeypatch.setattr("winpodx.core.transport.dispatch", MagicMock(return_value=transport))

    with pytest.raises(rotation.RotationError, match="outcome is unknown"):
        rotation.rotate_password(_rotate_cfg, "new-pw")

    assert Config.load().rdp.password == "old-password"
    assert rotation._rotation_marker_path().exists()


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


def test_agent_payload_checks_lastexitcode(_rotate_cfg, monkeypatch):
    """The PowerShell payload must exit with net user's rc, not just continue."""
    from winpodx.core import rotation

    transport = MagicMock()
    transport.exec.return_value = _ok_result()
    dispatch = MagicMock(return_value=transport)
    monkeypatch.setattr("winpodx.core.transport.dispatch", dispatch)

    rotation._change_windows_password(_rotate_cfg, "new-pw")

    args, _ = transport.exec.call_args
    payload = args[0]
    assert "$LASTEXITCODE" in payload
    assert "exit $LASTEXITCODE" in payload


def test_freerdp_fallback_channel_failure_is_unknown(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.transport.base import TransportUnavailable
    from winpodx.core.windows_exec import WindowsExecError

    dispatch = MagicMock(side_effect=TransportUnavailable("agent down"))
    monkeypatch.setattr("winpodx.core.transport.dispatch", dispatch)

    rin = MagicMock(side_effect=WindowsExecError("freerdp died"))
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    with pytest.raises(rotation.RotationError, match="outcome is unknown"):
        rotation._change_windows_password(_rotate_cfg, "new-pw")


def test_auto_rotation_with_pending_marker_does_not_call_agent(_rotate_cfg, monkeypatch):
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

    with pytest.raises(rotation.RotationError, match="unresolved"):
        rotation._auto_rotate_password(_rotate_cfg)

    transport.exec.assert_not_called()
    assert marker.exists()


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
    transport.exec.side_effect = [
        _ok_result(),
        _ok_result(),
        _fail_result(rc=1, stderr="rollback failed"),
    ]
    monkeypatch.setattr("winpodx.core.transport.dispatch", MagicMock(return_value=transport))

    with patch.object(_rotate_cfg, "save", side_effect=OSError("disk full")):
        with pytest.raises(rotation.RotationError, match="rollback is incomplete"):
            rotation._auto_rotate_password(_rotate_cfg)

    marker = rotation._rotation_marker_path()
    assert marker.exists()
    assert marker.stat().st_mode & 0o777 == 0o600


# PSCredential construction does not authenticate; require a real local logon.


def _agent_transport(monkeypatch) -> MagicMock:
    transport = MagicMock()
    monkeypatch.setattr("winpodx.core.transport.dispatch", MagicMock(return_value=transport))
    return transport


def _submitted_payload(transport: MagicMock) -> str:
    args, _ = transport.exec.call_args
    return args[0]


def _success_sentinel(payload: str) -> str:
    matches = re.findall(r"""Write-Output\s+(?P<q>['"])(?P<s>.+?)(?P=q)""", payload)
    assert matches, f"payload prints no success sentinel:\n{payload}"
    return matches[-1][1]


def test_verify_password_authenticates_with_logonuserw(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation

    transport = _agent_transport(monkeypatch)
    transport.exec.return_value = _ok_result()

    rotation._verify_windows_password(_rotate_cfg, "new-pw")

    payload = _submitted_payload(transport)
    assert "LogonUserW" in payload, "verification must call the Win32 LogonUserW API"
    assert "advapi32" in payload, "LogonUserW must be imported from advapi32.dll"
    assert "PSCredential" not in payload, (
        "constructing a PSCredential proves nothing about the password"
    )


def test_verify_password_uses_local_domain_network_logon_default_provider(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation

    transport = _agent_transport(monkeypatch)
    transport.exec.return_value = _ok_result()

    rotation._verify_windows_password(_rotate_cfg, "new-pw")

    payload = _submitted_payload(transport)
    assert re.search(r"""["']\.["']""", payload), (
        "LogonUserW must authenticate against the local computer domain '.'"
    )
    assert re.search(r"LOGON32_LOGON_NETWORK\s*=\s*3", payload), (
        "network logon type must be 3 (LOGON32_LOGON_NETWORK)"
    )
    assert re.search(r"LOGON32_PROVIDER_DEFAULT\s*=\s*0", payload), (
        "logon provider must default to 0 (LOGON32_PROVIDER_DEFAULT)"
    )


def test_verify_password_closes_token_handle(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation

    transport = _agent_transport(monkeypatch)
    transport.exec.return_value = _ok_result()

    rotation._verify_windows_password(_rotate_cfg, "new-pw")

    payload = _submitted_payload(transport)
    assert "CloseHandle" in payload, "the logon token must be released with CloseHandle"
    assert "kernel32" in payload, "CloseHandle must be imported from kernel32.dll"


def test_verify_password_true_on_zero_rc_with_sentinel(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.transport.base import ExecResult

    transport = _agent_transport(monkeypatch)

    def fake_exec(script, **_kwargs):
        return ExecResult(rc=0, stdout=_success_sentinel(script) + "\n", stderr="")

    transport.exec.side_effect = fake_exec

    assert rotation._verify_windows_password(_rotate_cfg, "new-pw") is True


def test_verify_password_false_on_nonzero_auth_rejection(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation

    transport = _agent_transport(monkeypatch)
    transport.exec.return_value = _fail_result(
        rc=1326, stderr="Logon failure: unknown user name or bad password"
    )

    assert rotation._verify_windows_password(_rotate_cfg, "wrong-pw") is False


# The FreeRDP fallback authenticates with ``cfg.rdp.password``, so verification
# must temporarily swap in the candidate and restore the caller's config on
# every exit path (success, rejection, channel exception).


def _verify_accepted_result():
    from winpodx.core.transport.base import ExecResult

    return ExecResult(rc=0, stdout="password accepted\n", stderr="")


def _freerdp_unavailable(monkeypatch):
    from winpodx.core.transport.base import TransportUnavailable

    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        MagicMock(side_effect=TransportUnavailable("agent down")),
    )


def test_verify_freerdp_fallback_authenticates_with_candidate(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation

    _freerdp_unavailable(monkeypatch)
    seen: list[str] = []

    def rin(cfg, payload, **_kwargs):
        seen.append(cfg.rdp.password)
        assert "LogonUserW" in payload
        return _verify_accepted_result()

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    assert rotation._verify_windows_password(_rotate_cfg, "candidate-password") is True
    assert seen == ["candidate-password"]
    assert _rotate_cfg.rdp.password == "old-password"


def test_verify_freerdp_fallback_restores_config_on_rejection(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation

    _freerdp_unavailable(monkeypatch)
    seen: list[str] = []

    def rin(cfg, _payload, **_kwargs):
        seen.append(cfg.rdp.password)
        return _fail_result(rc=1326, stderr="Logon failure: bad password")

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    assert rotation._verify_windows_password(_rotate_cfg, "candidate-password") is False
    assert seen == ["candidate-password"]
    assert _rotate_cfg.rdp.password == "old-password"


def test_verify_freerdp_fallback_restores_config_on_channel_failure(_rotate_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.windows_exec import WindowsExecError

    _freerdp_unavailable(monkeypatch)
    seen: list[str] = []

    def rin(cfg, _payload, **_kwargs):
        seen.append(cfg.rdp.password)
        raise WindowsExecError("freerdp died")

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    with pytest.raises(rotation.RotationError, match="outcome is unknown"):
        rotation._verify_windows_password(_rotate_cfg, "candidate-password")

    assert seen == ["candidate-password"]
    assert _rotate_cfg.rdp.password == "old-password"
