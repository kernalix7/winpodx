# SPDX-License-Identifier: MIT
"""Tests for the RemoteApp session-interactive gate (#332)."""

from __future__ import annotations

import pytest

from winpodx.core import rdp
from winpodx.core.config import Config


class _Exec:
    def __init__(self, stdout: str) -> None:
        self.rc = 0
        self.stdout = stdout
        self.stderr = ""


def _cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    return cfg


def test_gate_ready_returns_true_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, cfg):  # noqa: ANN001
            pass

        def health(self):
            return {"ok": True}

        def exec(self, script, timeout=60):  # noqa: ANN001
            return _Exec("READY")

    import winpodx.core.agent as agent_mod

    monkeypatch.setattr(agent_mod, "AgentClient", FakeClient)
    assert rdp._wait_session_interactive(_cfg(), timeout=5) is True


def test_gate_agent_down_returns_false_no_block(monkeypatch: pytest.MonkeyPatch) -> None:
    import winpodx.core.agent as agent_mod

    class FakeClient:
        def __init__(self, cfg):  # noqa: ANN001
            pass

        def health(self):
            raise agent_mod.AgentUnavailableError("down")

    monkeypatch.setattr(agent_mod, "AgentClient", FakeClient)
    # Must return quickly (no polling) when the agent is unreachable.
    assert rdp._wait_session_interactive(_cfg(), timeout=5) is False


def test_gate_times_out_when_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    import winpodx.core.agent as agent_mod

    class FakeClient:
        def __init__(self, cfg):  # noqa: ANN001
            pass

        def health(self):
            return {"ok": True}

        def exec(self, script, timeout=60):  # noqa: ANN001
            return _Exec("LOCKED")

    monkeypatch.setattr(agent_mod, "AgentClient", FakeClient)
    monkeypatch.setattr(rdp, "log", rdp.log)  # keep
    # timeout=1 so the loop exits fast; LOCKED never becomes READY.
    assert rdp._wait_session_interactive(_cfg(), timeout=1) is False
