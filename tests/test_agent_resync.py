# SPDX-License-Identifier: MIT
"""Tests for the guest agent token resync self-heal (#615)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from winpodx.core import agent_resync
from winpodx.core.agent import AgentAuthError, ExecResult
from winpodx.core.windows_exec import WindowsExecError, WindowsExecResult

_TOKEN = "ab" * 32  # 64 hex chars, like secrets.token_hex(32)


@pytest.fixture(autouse=True)
def _no_token_side_effects(monkeypatch):
    """Stub token generation + OEM staging so tests don't touch disk."""
    monkeypatch.setattr(agent_resync, "ensure_agent_token", lambda: _TOKEN)
    monkeypatch.setattr(agent_resync, "stage_token_to_oem", lambda _p: None)
    # Skip the real verify-loop sleeps.
    monkeypatch.setattr(agent_resync.time, "sleep", lambda _s: None)
    # The lazy `from winpodx.core.pod.compose import _find_oem_dir` is wrapped in
    # try/except in resync_token; make it cheap and harmless.
    import winpodx.core.pod.compose as compose

    monkeypatch.setattr(compose, "_find_oem_dir", lambda: "/tmp/winpodx-oem", raising=False)


def _patch_freerdp(monkeypatch, *, rc=0, stdout="resynced+respawn", raises=None):
    """Patch the FreeRDP push (run_in_windows); return a payload-capture list."""
    captured: list[str] = []
    import winpodx.core.windows_exec as we

    def fake_run_in_windows(cfg, payload, *, description="", timeout=60):
        captured.append(payload)
        if raises is not None:
            raise raises
        return WindowsExecResult(rc=rc, stdout=stdout, stderr="")

    monkeypatch.setattr(we, "run_in_windows", fake_run_in_windows)
    return captured


def _patch_agent_exec(monkeypatch, behaviors):
    """Patch AgentClient so each .exec() pops the next behavior (result or exc)."""
    import winpodx.core.agent as agent

    seq = list(behaviors)

    class FakeClient:
        def __init__(self, _cfg):
            pass

        def exec(self, _script, *, timeout=30.0):
            item = seq.pop(0) if seq else ExecResult(rc=0, stdout="ok", stderr="")
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr(agent, "AgentClient", FakeClient)


def test_resync_success(monkeypatch):
    _patch_freerdp(monkeypatch, stdout="resynced+respawn")
    _patch_agent_exec(monkeypatch, [ExecResult(rc=0, stdout="ok", stderr="")])

    ok, detail = agent_resync.resync_token(SimpleNamespace(), verify_timeout=6.0)
    assert ok is True
    assert "OK" in detail or "ok" in detail.lower()


def test_resync_payload_carries_token_and_respawn(monkeypatch):
    captured = _patch_freerdp(monkeypatch)
    _patch_agent_exec(monkeypatch, [ExecResult(rc=0, stdout="ok", stderr="")])

    agent_resync.resync_token(SimpleNamespace(), verify_timeout=6.0)

    assert len(captured) == 1
    payload = captured[0]
    assert _TOKEN in payload
    assert "C:\\OEM\\agent_token.txt" in payload
    assert "agent-respawn.ps1" in payload
    assert "-NoNewline" in payload  # byte-for-byte match with the host write


def test_resync_norespawn_reports_manual_step(monkeypatch):
    _patch_freerdp(monkeypatch, stdout="resynced+norespawn")
    # Even if the agent would answer, norespawn short-circuits before verify.
    _patch_agent_exec(monkeypatch, [ExecResult(rc=0, stdout="ok", stderr="")])

    ok, detail = agent_resync.resync_token(SimpleNamespace(), verify_timeout=6.0)
    assert ok is False
    assert "apply-fixes" in detail or "log" in detail.lower()


def test_resync_freerdp_push_failure(monkeypatch):
    _patch_freerdp(monkeypatch, raises=WindowsExecError("no result file"))
    ok, detail = agent_resync.resync_token(SimpleNamespace(), verify_timeout=6.0)
    assert ok is False
    assert "FreeRDP push failed" in detail


def test_resync_guest_rewrite_nonzero_rc(monkeypatch):
    _patch_freerdp(monkeypatch, rc=1, stdout="")
    ok, detail = agent_resync.resync_token(SimpleNamespace(), verify_timeout=6.0)
    assert ok is False
    assert "rewrite failed" in detail


def test_resync_verify_never_authenticates(monkeypatch):
    _patch_freerdp(monkeypatch, stdout="resynced+respawn")
    # Push OK, respawn launched, but the agent keeps rejecting → not healed.
    _patch_agent_exec(monkeypatch, [AgentAuthError("/exec returned 401")] * 10)

    ok, detail = agent_resync.resync_token(SimpleNamespace(), verify_timeout=4.0)
    assert ok is False
    assert "not authenticated" in detail


def test_doctor_guest_exec_auto_resyncs_on_401(monkeypatch):
    """probe_guest_exec heals a 401 once, then reports the round-trip OK."""
    import winpodx.core.pod as pod
    from winpodx.core import checks
    from winpodx.core.pod import PodState

    monkeypatch.setattr(pod, "pod_status", lambda _cfg: SimpleNamespace(state=PodState.RUNNING))
    # First exec → 401; after resync, a fresh client → ok.
    import winpodx.core.agent as agent

    calls = {"n": 0}

    class FakeClient:
        def __init__(self, _cfg):
            pass

        def exec(self, _script, *, timeout=30.0):
            calls["n"] += 1
            if calls["n"] == 1:
                raise AgentAuthError("/exec returned 401")
            return ExecResult(rc=0, stdout="ok", stderr="")

    monkeypatch.setattr(agent, "AgentClient", FakeClient)
    monkeypatch.setattr(agent_resync, "resync_token", lambda _cfg: (True, "token resynced"))

    probe = checks.probe_guest_exec(SimpleNamespace())
    assert probe.status == "ok"
    assert "drifted" in probe.detail or "resync" in probe.detail


def test_doctor_guest_exec_reports_failed_resync(monkeypatch):
    import winpodx.core.pod as pod
    from winpodx.core import checks
    from winpodx.core.pod import PodState

    monkeypatch.setattr(pod, "pod_status", lambda _cfg: SimpleNamespace(state=PodState.RUNNING))
    import winpodx.core.agent as agent

    class FakeClient:
        def __init__(self, _cfg):
            pass

        def exec(self, _script, *, timeout=30.0):
            raise AgentAuthError("/exec returned 401")

    monkeypatch.setattr(agent, "AgentClient", FakeClient)
    monkeypatch.setattr(
        agent_resync, "resync_token", lambda _cfg: (False, "FreeRDP push failed: x")
    )

    probe = checks.probe_guest_exec(SimpleNamespace())
    assert probe.status == "fail"
    assert "auto-resync failed" in probe.detail
