# SPDX-License-Identifier: MIT
from __future__ import annotations

from unittest.mock import patch

from winpodx.core.config import Config
from winpodx.core.updates import _exec_toggle, disable_updates, enable_updates, get_update_status
from winpodx.core.windows_exec import WindowsExecError, WindowsExecResult


def test_exec_toggle_rejects_unsupported_backend_without_guest_call() -> None:
    cfg = Config()
    cfg.pod.backend = "manual"

    with patch("winpodx.core.windows_exec.run_via_transport") as run:
        result = _exec_toggle(cfg, "status")

    assert result == (False, "Only supported for podman/docker backends")
    run.assert_not_called()


def test_exec_toggle_rejects_unknown_action_without_guest_call() -> None:
    cfg = Config()

    with patch("winpodx.core.windows_exec.run_via_transport") as run:
        result = _exec_toggle(cfg, "upgrade")

    assert result == (False, "unknown action 'upgrade'")
    run.assert_not_called()


def test_exec_toggle_sends_exact_payload_and_returns_trimmed_output() -> None:
    cfg = Config()
    result = WindowsExecResult(rc=0, stdout=" disabled\n", stderr="")

    with patch("winpodx.core.windows_exec.run_via_transport", return_value=result) as run:
        actual = _exec_toggle(cfg, "disable")

    assert actual == (True, "disabled")
    run.assert_called_once_with(
        cfg,
        "& 'C:\\OEM\\toggle_updates.ps1' -Action 'disable'\n"
        "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }\n",
        description="updates-disable",
        timeout=45,
    )


def test_exec_toggle_reports_transport_and_script_failures() -> None:
    cfg = Config()

    with patch(
        "winpodx.core.windows_exec.run_via_transport",
        side_effect=WindowsExecError("guest offline"),
    ):
        assert _exec_toggle(cfg, "status") == (False, "guest offline")

    failed = WindowsExecResult(rc=5, stdout="fallback", stderr=" denied \n")
    with patch("winpodx.core.windows_exec.run_via_transport", return_value=failed):
        assert _exec_toggle(cfg, "enable") == (False, "denied")

    empty = WindowsExecResult(rc=7, stdout="", stderr="")
    with patch("winpodx.core.windows_exec.run_via_transport", return_value=empty):
        assert _exec_toggle(cfg, "enable") == (False, "rc=7")


def test_public_update_actions_return_transport_outcome() -> None:
    cfg = Config()

    with patch("winpodx.core.updates._exec_toggle", return_value=(True, "done")) as toggle:
        assert disable_updates(cfg) is True
        assert enable_updates(cfg) is True

    assert toggle.call_args_list[0].args == (cfg, "disable")
    assert toggle.call_args_list[1].args == (cfg, "enable")

    with patch("winpodx.core.updates._exec_toggle", return_value=(False, "offline")):
        assert disable_updates(cfg) is False
        assert enable_updates(cfg) is False


def test_get_update_status_accepts_only_known_successful_statuses() -> None:
    cfg = Config()

    for result, expected in (
        ((True, "enabled"), "enabled"),
        ((True, "disabled"), "disabled"),
        ((True, "unknown"), None),
        ((False, "enabled"), None),
    ):
        with patch("winpodx.core.updates._exec_toggle", return_value=result):
            assert get_update_status(cfg) == expected
