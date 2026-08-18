# SPDX-License-Identifier: MIT
"""Unit tests for guest-sync host-side logic.

The guest-mutating steps (OEM delivery, urlacl, agent restart over /exec)
are covered by the real-Windows smoke gate, not here. These tests pin the
version-stamp comparison, the parse, and the trigger gating."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from winpodx.core.config import Config
from winpodx.core.guest_sync import (
    GuestVersion,
    guest_sync_needed,
    host_version,
    maybe_autosync,
)


def test_host_version_shape() -> None:
    hv = host_version()
    assert isinstance(hv, GuestVersion)
    assert hv.winpodx  # non-empty
    assert hv.oem_bundle  # non-empty


def test_guest_version_equality() -> None:
    a = GuestVersion(winpodx="0.5.8", oem_bundle="25")
    assert a == GuestVersion(winpodx="0.5.8", oem_bundle="25")
    assert a != GuestVersion(winpodx="0.5.9", oem_bundle="25")
    assert a != GuestVersion(winpodx="0.5.8", oem_bundle="26")


def _cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.guest_autosync = True
    return cfg


def test_guest_sync_needed_when_stamp_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("winpodx.core.guest_sync.read_guest_version", lambda cfg: None)
    assert guest_sync_needed(_cfg()) is True


def test_guest_sync_needed_when_older(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "winpodx.core.guest_sync.read_guest_version",
        lambda cfg: GuestVersion(winpodx="0.0.1", oem_bundle="1"),
    )
    assert guest_sync_needed(_cfg()) is True


def test_guest_sync_not_needed_when_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("winpodx.core.guest_sync.read_guest_version", lambda cfg: host_version())
    assert guest_sync_needed(_cfg()) is False


def _agent_up(monkeypatch: pytest.MonkeyPatch, *, up: bool = True) -> None:
    """Patch AgentClient so maybe_autosync's health gate passes (or fails)."""
    import winpodx.core.agent as agent_mod

    class _Client:
        def __init__(self, cfg):  # noqa: ANN001
            pass

        def health(self):
            if not up:
                raise agent_mod.AgentUnavailableError("down")
            return {"ok": True}

    monkeypatch.setattr(agent_mod, "AgentClient", _Client)


def test_maybe_autosync_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    cfg.pod.guest_autosync = False
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: pytest.fail("sync should not run when disabled"),
    )
    assert maybe_autosync(cfg) is False


def test_maybe_autosync_skips_when_current(monkeypatch: pytest.MonkeyPatch) -> None:
    _agent_up(monkeypatch)
    monkeypatch.setattr("winpodx.core.guest_sync.read_guest_version", lambda cfg: host_version())
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: pytest.fail("sync should not run when current"),
    )
    assert maybe_autosync(_cfg()) is False


def test_maybe_autosync_runs_when_stamp_older(monkeypatch: pytest.MonkeyPatch) -> None:
    _agent_up(monkeypatch)
    monkeypatch.setattr(
        "winpodx.core.guest_sync.read_guest_version",
        lambda cfg: GuestVersion(winpodx="0.0.1", oem_bundle="1"),
    )
    calls = {"n": 0}
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1),
    )
    assert maybe_autosync(_cfg()) is True
    assert calls["n"] == 1


def test_maybe_autosync_absent_stamp_records_no_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    _agent_up(monkeypatch)
    # Fresh install / pre-stamp pod: must NOT sync (would disrupt first-boot
    # agent bring-up) -- just record the version.
    monkeypatch.setattr("winpodx.core.guest_sync.read_guest_version", lambda cfg: None)
    wrote = {"n": 0}
    monkeypatch.setattr(
        "winpodx.core.guest_sync.write_guest_version",
        lambda *a, **k: wrote.__setitem__("n", wrote["n"] + 1) or True,
    )
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: pytest.fail("sync must not run when stamp absent (fresh install)"),
    )
    assert maybe_autosync(_cfg()) is False
    assert wrote["n"] == 1


def test_maybe_autosync_skips_unsupported_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    cfg.pod.backend = "manual"
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: pytest.fail("sync should not run on the manual backend"),
    )
    assert maybe_autosync(cfg) is False


def test_read_guest_version_parses_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    class _R:
        ok = True
        stdout = json.dumps({"winpodx": "0.5.8", "oem_bundle": "25"})
        stderr = ""
        rc = 0

    # read_guest_version goes through the agent-first run_via_transport
    # (imported inside the function); patch it on the source module.
    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", lambda *a, **k: _R())
    gv = guest_sync.read_guest_version(_cfg())
    assert gv == GuestVersion(winpodx="0.5.8", oem_bundle="25")


def test_write_guest_version_uses_agent_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    # The stamp write must go through the windowless agent transport, not the
    # FreeRDP run_in_windows path (which flashed a window + hit a 30s
    # RemoteApp-activation timeout on a fresh first boot). #341-followup.
    from winpodx.core import guest_sync

    class _R:
        ok = True
        stdout = ""
        stderr = ""
        rc = 0

    calls: list[str] = []

    def _fake_transport(cfg, payload, *, timeout=60, description="winpodx-exec"):
        calls.append(description)
        return _R()

    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", _fake_transport)
    # If anything reaches the FreeRDP path the test fails loudly.
    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_in_windows",
        lambda *a, **k: pytest.fail("stamp write must not use FreeRDP run_in_windows"),
    )
    assert guest_sync.write_guest_version(_cfg(), GuestVersion("0.5.9", "26")) is True
    assert calls == ["guest-version-write"]


def test_write_guest_version_defers_on_exec_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A transitioning agent fails clean -> False (best-effort, retried next
    # start); no exception escapes to the install flow.
    from winpodx.core import guest_sync
    from winpodx.core.windows_exec import WindowsExecError

    def _boom(*a, **k):
        raise WindowsExecError("agent transitioning")

    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", _boom)
    assert guest_sync.write_guest_version(_cfg(), GuestVersion("0.5.9", "26")) is False


def test_config_guest_autosync_default_and_coerce() -> None:
    cfg = Config()
    assert cfg.pod.guest_autosync is True
    cfg.pod.guest_autosync = "nope"  # type: ignore[assignment]
    cfg.pod.__post_init__()
    assert cfg.pod.guest_autosync is True


def test_maybe_autosync_skips_when_agent_down(monkeypatch: pytest.MonkeyPatch) -> None:
    # Agent not up (fresh install bringing it online): skip silently --
    # no read/write/sync, no FreeRDP fallback (#install-cleanliness).
    _agent_up(monkeypatch, up=False)
    monkeypatch.setattr(
        "winpodx.core.guest_sync.read_guest_version",
        lambda cfg: pytest.fail("must not touch guest when agent down"),
    )
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: pytest.fail("must not sync when agent down"),
    )
    assert maybe_autosync(_cfg()) is False


def test_sync_guest_uses_windowless_transport_not_freerdp(monkeypatch: pytest.MonkeyPatch) -> None:
    # All guest-mutating steps must ride the windowless agent channel; touching
    # FreeRDP run_in_windows pops a visible console (the flash regression that
    # appeared once guest-sync first fired on a 0.5.8 -> 0.5.9 upgrade).
    from winpodx.core import guest_sync, provisioner

    class _R:
        ok = True
        rc = 0
        stdout = "oem-refreshed"
        stderr = ""

    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", lambda *a, **k: _R())
    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_in_windows",
        lambda *a, **k: pytest.fail("sync_guest must not use FreeRDP run_in_windows (flash)"),
    )
    monkeypatch.setattr(guest_sync, "_serve_oem", lambda cfg: None)
    monkeypatch.setattr(guest_sync, "_stop_oem_server", lambda cfg: None)
    monkeypatch.setattr(guest_sync, "write_guest_version", lambda cfg, ver: True)
    monkeypatch.setattr(guest_sync, "_wait_agent_back", lambda cfg, **k: True)
    monkeypatch.setattr(provisioner, "apply_windows_runtime_fixes", lambda cfg: {"fix:x": "ok"})

    results = guest_sync.sync_guest(_cfg(), force=True)
    assert results.get("oem_delivery") == "ok"
    assert results.get("agent_restart") == "ok"
    assert results.get("agent_back") == "ok"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (MagicMock(ok=False, stdout="ignored"), None),
        (MagicMock(ok=True, stdout="  "), None),
        (MagicMock(ok=True, stdout="not-json"), None),
    ],
)
def test_read_guest_version_rejects_failed_or_hostile_output(
    monkeypatch: pytest.MonkeyPatch, result, expected
) -> None:
    from winpodx.core import guest_sync

    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", lambda *a, **k: result)
    assert guest_sync.read_guest_version(_cfg()) is expected


def test_read_guest_version_handles_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync
    from winpodx.core.windows_exec import WindowsExecError

    def fail(*_args, **_kwargs):
        raise WindowsExecError("offline")

    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", fail)
    assert guest_sync.read_guest_version(_cfg()) is None


def test_backend_exec_validates_backend_before_running(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    cfg = _cfg()
    cfg.pod.backend = "manual"
    run = MagicMock()
    monkeypatch.setattr(guest_sync.subprocess, "run", run)
    with pytest.raises(guest_sync.GuestSyncError, match="not available"):
        guest_sync._backend_exec(cfg, ["ps"])
    run.assert_not_called()


def test_backend_exec_uses_specific_backend_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    monkeypatch.setattr(guest_sync.shutil, "which", lambda name: f"/usr/bin/{name}")
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(guest_sync.subprocess, "run", run)
    result = guest_sync._backend_exec(_cfg(), ["exec", "winpodx", "true"], timeout=7)
    assert result.returncode == 0
    run.assert_called_once_with(
        ["podman", "exec", "winpodx", "true"],
        capture_output=True,
        text=True,
        timeout=7,
    )


def test_serve_oem_rejects_missing_install_script(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    monkeypatch.setattr(
        guest_sync, "_backend_exec", lambda *a, **k: MagicMock(returncode=1, stderr="missing")
    )
    with pytest.raises(guest_sync.GuestSyncError, match="install.bat missing"):
        guest_sync._serve_oem(_cfg())


def test_serve_oem_rejects_failed_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    responses = iter(
        [MagicMock(returncode=0, stderr=""), MagicMock(returncode=2, stderr="tar denied")]
    )
    monkeypatch.setattr(guest_sync, "_backend_exec", lambda *a, **k: next(responses))
    with pytest.raises(guest_sync.GuestSyncError, match="tar /oem failed: tar denied"):
        guest_sync._serve_oem(_cfg())


def test_serve_oem_stages_and_serves_without_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    calls = []

    def fake_exec(_cfg, args, *, timeout=60):
        calls.append((args, timeout))
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(guest_sync, "_backend_exec", fake_exec)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    guest_sync._serve_oem(_cfg())
    assert len(calls) == 4
    assert calls[0][0][-1] == "test -f /oem/install.bat"
    assert "tar czf /tmp/winpodx-sync/oem.tar.gz oem" in calls[1][0][-1]
    assert calls[3][0][1] == "-d"


def test_stop_oem_server_swallows_backend_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    def fail(*_args, **_kwargs):
        raise guest_sync.GuestSyncError("gone")

    monkeypatch.setattr(guest_sync, "_backend_exec", fail)
    assert guest_sync._stop_oem_server(_cfg()) is None


def test_stamp_retry_stops_after_success_without_real_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpodx.core import guest_sync

    writes = iter([False, False, True])
    sleeps = []
    monkeypatch.setattr(guest_sync, "write_guest_version", lambda *a, **k: next(writes))
    monkeypatch.setattr(guest_sync, "host_version", lambda: GuestVersion("1", "2"))
    monkeypatch.setattr("time.sleep", sleeps.append)
    assert guest_sync._write_stamp_with_retry(_cfg(), attempts=4, wait=0.25) is True
    assert sleeps == [0.25, 0.25]


def test_sync_guest_skips_unsupported_and_current(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    cfg = _cfg()
    cfg.pod.backend = "manual"
    assert guest_sync.sync_guest(cfg) == {"backend": "skipped (backend=manual not supported)"}
    monkeypatch.setattr(guest_sync, "guest_sync_needed", lambda cfg: False)
    assert guest_sync.sync_guest(_cfg()) == {"sync": "skipped (guest already current)"}


def test_sync_guest_aborts_after_failed_oem_pull(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    stopped = []
    monkeypatch.setattr(guest_sync, "_serve_oem", lambda cfg: None)
    monkeypatch.setattr(guest_sync, "_stop_oem_server", lambda cfg: stopped.append(cfg))
    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_via_transport",
        lambda *a, **k: MagicMock(ok=False, stderr="hostile response", stdout=""),
    )
    results = guest_sync.sync_guest(_cfg(), force=True)
    assert results == {"oem_delivery": "failed: guest OEM pull failed: hostile response"}
    assert len(stopped) >= 1


def test_sync_guest_records_step_errors_and_skips_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync, provisioner
    from winpodx.core.windows_exec import WindowsExecError

    calls = {"n": 0}

    def transport(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return MagicMock(ok=True, rc=0, stdout="ok", stderr="")
        raise WindowsExecError("agent dropped")

    monkeypatch.setattr(guest_sync, "_serve_oem", lambda cfg: None)
    monkeypatch.setattr(guest_sync, "_stop_oem_server", lambda cfg: None)
    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", transport)
    monkeypatch.setattr(provisioner, "apply_windows_runtime_fixes", lambda cfg: {"rdp": "failed"})
    results = guest_sync.sync_guest(_cfg(), force=True)
    assert results["urlacl"] == "failed: agent dropped"
    assert results["stamp"] == "skipped (a step failed; will retry next sync)"
    assert results["agent_restart"] == "failed: agent dropped"


def test_wait_agent_back_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import agent, guest_sync

    attempts = {"n": 0}

    class _Client:
        def __init__(self, cfg):
            pass

        def health(self):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("settling")

    times = iter([0.0, 0.0, 0.1])
    monkeypatch.setattr(agent, "AgentClient", _Client)
    monkeypatch.setattr("time.monotonic", lambda: next(times))
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    assert guest_sync._wait_agent_back(_cfg(), timeout=15, interval=0) is True
    assert attempts["n"] == 2


def test_maybe_autosync_handles_sync_precondition_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    _agent_up(monkeypatch)
    monkeypatch.setattr(guest_sync, "read_guest_version", lambda cfg: GuestVersion("old", "old"))
    monkeypatch.setattr(guest_sync, "host_version", lambda: GuestVersion("new", "new"))

    def fail(_cfg):
        raise guest_sync.GuestSyncError("oem missing")

    monkeypatch.setattr(guest_sync, "sync_guest", fail)
    assert guest_sync.maybe_autosync(_cfg()) is False
