"""Agent-first behaviour for ``winpodx pod sync-password``.

Same drive-redirect bug that affected ``rotate-password`` blocked the
recovery path; ``_sync_password`` now prefers AgentTransport so a user
whose rotation broke isn't stuck without a recovery path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _ok_result():
    from winpodx.core.transport.base import ExecResult

    return ExecResult(rc=0, stdout="password reset\n", stderr="")


def _fail_result(rc: int = 1, stderr: str = "boom"):
    from winpodx.core.transport.base import ExecResult

    return ExecResult(rc=rc, stdout="", stderr=stderr)


@pytest.fixture()
def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core.config import Config

    cfg = Config()
    cfg.rdp.user = "User"
    cfg.rdp.password = "target-pw"
    cfg.pod.backend = "podman"
    cfg.save()
    return cfg


def test_agent_ok_skips_freerdp_and_recovery_prompt(_cfg, monkeypatch):
    """Happy path: agent reachable, no recovery password prompt, no FreeRDP."""
    from winpodx.cli import pod as cli_pod

    transport = MagicMock()
    transport.exec.return_value = _ok_result()
    dispatch = MagicMock(return_value=transport)
    monkeypatch.setattr("winpodx.core.transport.dispatch", dispatch)

    rin = MagicMock()
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    # Hard fail if either prompt path runs.
    def _no_prompt(*_a, **_kw):
        raise AssertionError("getpass should not be called when agent is healthy")

    monkeypatch.setattr("getpass.getpass", _no_prompt)

    cli_pod._sync_password(non_interactive=False)

    transport.exec.assert_called_once()
    _, kwargs = transport.exec.call_args
    assert kwargs.get("description") == "sync-password"
    assert kwargs.get("timeout") == 30
    rin.assert_not_called()


def test_agent_unavailable_falls_back_to_freerdp(_cfg, monkeypatch):
    """Agent down → prompt for recovery pw → FreeRDP path with rescue cfg."""
    from winpodx.cli import pod as cli_pod
    from winpodx.core.transport.base import TransportUnavailable

    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        MagicMock(side_effect=TransportUnavailable("agent down")),
    )

    rin = MagicMock(return_value=_ok_result())
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    monkeypatch.setattr("getpass.getpass", lambda *_a, **_kw: "recovery-pw")

    cli_pod._sync_password(non_interactive=False)

    rin.assert_called_once()
    args, kwargs = rin.call_args
    rescue_cfg = args[0]
    assert rescue_cfg.rdp.password == "recovery-pw"
    assert "net user" in args[1]
    assert "target-pw" in args[1]  # the value from cfg, not the recovery
    assert kwargs.get("description") == "sync-password"
    assert kwargs.get("timeout") == 45


def test_agent_auth_error_does_not_fall_back(_cfg, monkeypatch, capsys):
    """TransportAuthError on the agent path is fatal, no FreeRDP retry."""
    from winpodx.cli import pod as cli_pod
    from winpodx.core.transport.base import TransportAuthError

    transport = MagicMock()
    transport.exec.side_effect = TransportAuthError("401")
    monkeypatch.setattr("winpodx.core.transport.dispatch", MagicMock(return_value=transport))

    rin = MagicMock()
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    def _no_prompt(*_a, **_kw):
        raise AssertionError("recovery prompt should not run on agent auth failure")

    monkeypatch.setattr("getpass.getpass", _no_prompt)

    with pytest.raises(SystemExit) as exc:
        cli_pod._sync_password(non_interactive=False)

    assert exc.value.code == 3
    rin.assert_not_called()
    captured = capsys.readouterr()
    assert "auth" in captured.out.lower()


def test_agent_nonzero_rc_returns_failure_without_fallback(_cfg, monkeypatch):
    """Script-level failure on the agent path is reported, not retried via FreeRDP."""
    from winpodx.cli import pod as cli_pod

    transport = MagicMock()
    transport.exec.return_value = _fail_result(rc=2, stderr="net user failed")
    monkeypatch.setattr("winpodx.core.transport.dispatch", MagicMock(return_value=transport))

    rin = MagicMock()
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    with pytest.raises(SystemExit) as exc:
        cli_pod._sync_password(non_interactive=False)

    assert exc.value.code == 3
    rin.assert_not_called()


def test_agent_unavailable_non_interactive_uses_env_recovery_pw(_cfg, monkeypatch):
    """Fallback path in non-interactive mode reads WINPODX_RECOVERY_PASSWORD."""
    from winpodx.cli import pod as cli_pod
    from winpodx.core.transport.base import TransportUnavailable

    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        MagicMock(side_effect=TransportUnavailable("agent down")),
    )
    monkeypatch.setenv("WINPODX_RECOVERY_PASSWORD", "env-recovery")

    rin = MagicMock(return_value=_ok_result())
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    cli_pod._sync_password(non_interactive=True)

    rin.assert_called_once()
    rescue_cfg = rin.call_args.args[0]
    assert rescue_cfg.rdp.password == "env-recovery"


def test_agent_unavailable_non_interactive_without_env_exits(_cfg, monkeypatch):
    from winpodx.cli import pod as cli_pod
    from winpodx.core.transport.base import TransportUnavailable

    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        MagicMock(side_effect=TransportUnavailable("agent down")),
    )
    monkeypatch.delenv("WINPODX_RECOVERY_PASSWORD", raising=False)

    rin = MagicMock()
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    with pytest.raises(SystemExit) as exc:
        cli_pod._sync_password(non_interactive=True)

    assert exc.value.code == 2
    rin.assert_not_called()


def test_agent_unavailable_freerdp_channel_failure_exits_3(_cfg, monkeypatch):
    """If both transports fail, surface an exit-3 error with the recovery hint."""
    from winpodx.cli import pod as cli_pod
    from winpodx.core.transport.base import TransportUnavailable
    from winpodx.core.windows_exec import WindowsExecError

    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        MagicMock(side_effect=TransportUnavailable("agent down")),
    )
    monkeypatch.setattr("getpass.getpass", lambda *_a, **_kw: "recovery-pw")

    rin = MagicMock(side_effect=WindowsExecError("freerdp died"))
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rin)

    with pytest.raises(SystemExit) as exc:
        cli_pod._sync_password(non_interactive=False)

    assert exc.value.code == 3
