"""Tests for AgentTransport — Sprint 2 stub."""

from __future__ import annotations

import pytest

from winpodx.core.config import Config
from winpodx.core.transport.agent import AgentTransport
from winpodx.core.transport.base import HealthStatus, TransportUnavailable


@pytest.fixture
def cfg() -> Config:
    return Config()


@pytest.fixture
def transport(cfg: Config) -> AgentTransport:
    return AgentTransport(cfg)


class TestStubBehaviour:
    def test_name_is_agent(self):
        assert AgentTransport.name == "agent"

    def test_health_returns_unavailable_without_raising(self, transport):
        # Spec rule: health() must not raise on transient state.
        # The Sprint 2 stub returns available=False so dispatch() falls
        # through to FreeRDP cleanly.
        status = transport.health()
        assert isinstance(status, HealthStatus)
        assert status.available is False
        assert status.detail and "stub" in status.detail.lower()

    def test_exec_raises_transport_unavailable(self, transport):
        with pytest.raises(TransportUnavailable):
            transport.exec("Get-Process")

    def test_stream_raises_transport_unavailable(self, transport):
        with pytest.raises(TransportUnavailable):
            transport.stream("Get-Process", lambda _line: None)
