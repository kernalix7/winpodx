"""Tests for dispatch() — pick agent if up, else freerdp."""

from __future__ import annotations

import pytest

from winpodx.core.config import Config
from winpodx.core.transport.agent import AgentTransport
from winpodx.core.transport.base import HealthStatus, TransportUnavailable
from winpodx.core.transport.dispatch import dispatch
from winpodx.core.transport.freerdp import FreerdpTransport


@pytest.fixture
def cfg() -> Config:
    return Config()


def _force_agent_health(monkeypatch, available: bool, detail: str | None = None):
    monkeypatch.setattr(
        AgentTransport,
        "health",
        lambda self: HealthStatus(available=available, detail=detail),
    )


class TestDefaultPolicy:
    def test_picks_agent_when_available(self, monkeypatch, cfg):
        _force_agent_health(monkeypatch, available=True, detail="agent up")
        t = dispatch(cfg)
        assert isinstance(t, AgentTransport)

    def test_falls_back_to_freerdp_when_agent_unavailable(self, monkeypatch, cfg):
        _force_agent_health(monkeypatch, available=False, detail="agent down")
        t = dispatch(cfg)
        assert isinstance(t, FreerdpTransport)

    def test_falls_back_when_agent_health_raises(self, monkeypatch, cfg):
        # Belt-and-braces: even if a buggy transport's health() raises
        # (against spec), dispatch must not propagate. The user gets the
        # fallback transport; the bug shows up in logs only.
        def boom(self):
            raise RuntimeError("agent health blew up")

        monkeypatch.setattr(AgentTransport, "health", boom)
        t = dispatch(cfg)
        assert isinstance(t, FreerdpTransport)


class TestPreferKwarg:
    def test_prefer_freerdp_forces_freerdp_even_when_agent_up(self, monkeypatch, cfg):
        _force_agent_health(monkeypatch, available=True)
        t = dispatch(cfg, prefer="freerdp")
        assert isinstance(t, FreerdpTransport)

    def test_prefer_agent_returns_agent_when_available(self, monkeypatch, cfg):
        _force_agent_health(monkeypatch, available=True)
        t = dispatch(cfg, prefer="agent")
        assert isinstance(t, AgentTransport)

    def test_prefer_agent_raises_when_unavailable(self, monkeypatch, cfg):
        _force_agent_health(monkeypatch, available=False, detail="stub")
        with pytest.raises(TransportUnavailable):
            dispatch(cfg, prefer="agent")

    def test_prefer_freerdp_does_not_call_agent_health(self, monkeypatch, cfg):
        called = {"n": 0}

        def counting_health(self):
            called["n"] += 1
            return HealthStatus(available=True)

        monkeypatch.setattr(AgentTransport, "health", counting_health)
        dispatch(cfg, prefer="freerdp")
        assert called["n"] == 0

    def test_unknown_prefer_kind_raises_value_error(self, cfg):
        with pytest.raises(ValueError):
            dispatch(cfg, prefer="wsman")  # type: ignore[arg-type]


class TestFreshInstances:
    def test_each_dispatch_returns_new_instance(self, monkeypatch, cfg):
        # Spec rule: dispatcher must NOT cache. Two calls return two
        # distinct objects so caller-mutated state never bleeds across
        # callers.
        _force_agent_health(monkeypatch, available=False)
        a = dispatch(cfg)
        b = dispatch(cfg)
        assert a is not b
