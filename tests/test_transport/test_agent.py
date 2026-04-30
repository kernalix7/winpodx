"""Tests for AgentTransport — wraps AgentClient through Transport ABC."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from winpodx.core.agent import (
    AgentAuthError,
    AgentError,
    AgentTimeoutError,
    AgentUnavailableError,
)
from winpodx.core.agent import (
    ExecResult as AgentExecResult,
)
from winpodx.core.config import Config
from winpodx.core.transport.agent import AgentTransport
from winpodx.core.transport.base import (
    ExecResult,
    HealthStatus,
    TransportAuthError,
    TransportError,
    TransportTimeoutError,
    TransportUnavailable,
)


@pytest.fixture
def cfg() -> Config:
    return Config()


@pytest.fixture
def transport(cfg: Config) -> AgentTransport:
    return AgentTransport(cfg)


class TestName:
    def test_name_is_agent(self):
        assert AgentTransport.name == "agent"


class TestHealth:
    """health() never raises on transient state — see Transport ABC rule."""

    def test_returns_available_when_client_responds(self, transport):
        with patch.object(
            type(transport._client),
            "health",
            return_value={"version": "0.2.2-rev1", "ok": True},
        ):
            status = transport.health()
        assert isinstance(status, HealthStatus)
        assert status.available is True
        assert status.version == "0.2.2-rev1"

    def test_returns_unavailable_on_unavailable_error(self, transport):
        with patch.object(
            type(transport._client),
            "health",
            side_effect=AgentUnavailableError("connection refused"),
        ):
            status = transport.health()
        assert status.available is False
        assert "connection refused" in (status.detail or "")

    def test_returns_unavailable_on_unexpected_exception(self, transport):
        # Defensive: even unexpected exceptions must not propagate.
        with patch.object(type(transport._client), "health", side_effect=ValueError("boom")):
            status = transport.health()
        assert status.available is False
        assert "boom" in (status.detail or "")

    def test_handles_non_dict_payload_gracefully(self, transport):
        # AgentClient.health() returns a dict on success; if it ever
        # returns something else, AgentTransport must not crash.
        with patch.object(type(transport._client), "health", return_value=[]):
            status = transport.health()
        assert status.available is True
        assert status.version is None


class TestExec:
    def test_happy_path_maps_agent_result_to_exec_result(self, transport):
        agent_result = AgentExecResult(rc=0, stdout="hi\n", stderr="")
        with patch.object(type(transport._client), "exec", return_value=agent_result):
            result = transport.exec("Write-Output hi")
        assert isinstance(result, ExecResult)
        assert result.rc == 0
        assert result.stdout == "hi\n"
        assert result.stderr == ""
        assert result.ok

    def test_auth_error_maps_to_transport_auth_error(self, transport):
        with patch.object(
            type(transport._client),
            "exec",
            side_effect=AgentAuthError("/exec auth failed (401)"),
        ):
            with pytest.raises(TransportAuthError):
                transport.exec("anything")

    def test_timeout_error_maps_to_transport_timeout(self, transport):
        with patch.object(
            type(transport._client),
            "exec",
            side_effect=AgentTimeoutError("60s timeout"),
        ):
            with pytest.raises(TransportTimeoutError):
                transport.exec("Start-Sleep 120")

    def test_unavailable_error_maps_to_transport_unavailable(self, transport):
        with patch.object(
            type(transport._client),
            "exec",
            side_effect=AgentUnavailableError("connection refused"),
        ):
            with pytest.raises(TransportUnavailable):
                transport.exec("anything")

    def test_generic_agent_error_maps_to_transport_error(self, transport):
        with patch.object(type(transport._client), "exec", side_effect=AgentError("malformed")):
            with pytest.raises(TransportError):
                transport.exec("anything")

    def test_passes_timeout_through_as_float(self, transport):
        captured: dict[str, object] = {}

        def fake_exec(self_inner, script, *, timeout):
            captured["script"] = script
            captured["timeout"] = timeout
            return AgentExecResult(rc=0, stdout="", stderr="")

        with patch.object(type(transport._client), "exec", fake_exec):
            transport.exec("ok", timeout=42)
        assert captured["timeout"] == 42.0


class TestStream:
    def test_stream_raises_transport_unavailable(self, transport):
        with pytest.raises(TransportUnavailable):
            transport.stream("Get-Process", lambda _line: None)
