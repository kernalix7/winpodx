"""Tests for the host-side AgentClient (Phase 1: /health only)."""

from __future__ import annotations

import io
import json
import socket
from urllib import error as urllib_error

import pytest

from winpodx.core.agent import (
    AgentAuthError,
    AgentClient,
    AgentUnavailableError,
)
from winpodx.core.config import Config


class _FakeResponse:
    """Minimal context-manager stand-in for urllib's HTTPResponse."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *a: object) -> None:
        pass


@pytest.fixture
def cfg() -> Config:
    return Config()


@pytest.fixture
def client(cfg: Config) -> AgentClient:
    return AgentClient(cfg)


def _patch_urlopen(monkeypatch, fake):
    monkeypatch.setattr("winpodx.core.agent.urllib_request.urlopen", fake)


class TestHealth:
    def test_happy_path_returns_parsed_json(self, monkeypatch, client):
        body = json.dumps({"ok": True, "version": "0.2.2"}).encode("utf-8")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["timeout"] = timeout
            captured["headers"] = dict(req.header_items())
            return _FakeResponse(body, status=200)

        _patch_urlopen(monkeypatch, fake_urlopen)

        result = client.health()

        assert result == {"ok": True, "version": "0.2.2"}
        assert captured["url"] == "http://127.0.0.1:8765/health"
        assert captured["method"] == "GET"
        assert captured["timeout"] == 2.0
        # No Authorization header on /health.
        header_keys = {k.lower() for k in captured["headers"]}
        assert "authorization" not in header_keys

    def test_no_token_succeeds(self, monkeypatch, tmp_path, cfg):
        """/health must work even when ~/.config/winpodx/agent_token.txt is absent."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        client = AgentClient(cfg)
        body = b'{"ok": true}'

        def fake_urlopen(req, timeout=None):
            return _FakeResponse(body, status=200)

        _patch_urlopen(monkeypatch, fake_urlopen)

        result = client.health()
        assert result == {"ok": True}

    def test_connection_refused_raises_unavailable(self, monkeypatch, client):
        def fake_urlopen(req, timeout=None):
            raise urllib_error.URLError(ConnectionRefusedError("connection refused"))

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentUnavailableError, match="unreachable"):
            client.health()

    def test_500_raises_unavailable(self, monkeypatch, client):
        def fake_urlopen(req, timeout=None):
            raise urllib_error.HTTPError(
                req.full_url, 500, "Internal Server Error", hdrs=None, fp=io.BytesIO(b"")
            )

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentUnavailableError, match="500"):
            client.health()

    def test_503_raises_unavailable(self, monkeypatch, client):
        def fake_urlopen(req, timeout=None):
            raise urllib_error.HTTPError(
                req.full_url, 503, "Service Unavailable", hdrs=None, fp=io.BytesIO(b"")
            )

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentUnavailableError, match="503"):
            client.health()

    def test_timeout_raises_unavailable(self, monkeypatch, client):
        def fake_urlopen(req, timeout=None):
            raise socket.timeout("timed out")

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentUnavailableError):
            client.health()

    def test_non_json_raises_unavailable(self, monkeypatch, client):
        def fake_urlopen(req, timeout=None):
            return _FakeResponse(b"<html>not json</html>", status=200)

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentUnavailableError, match="non-JSON"):
            client.health()

    def test_401_raises_auth_error(self, monkeypatch, client):
        """/health is unauthenticated, but if a misconfigured server returns
        401 anyway, surface it as AgentAuthError so callers can disambiguate."""

        def fake_urlopen(req, timeout=None):
            raise urllib_error.HTTPError(
                req.full_url, 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b"")
            )

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentAuthError):
            client.health()


class TestTokenLazyLoad:
    def test_token_missing_raises_unavailable(self, monkeypatch, tmp_path, cfg):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        client = AgentClient(cfg)

        with pytest.raises(AgentUnavailableError, match="missing"):
            client._token()

    def test_token_empty_raises_unavailable(self, monkeypatch, tmp_path, cfg):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = tmp_path / "winpodx" / "agent_token.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

        client = AgentClient(cfg)

        with pytest.raises(AgentUnavailableError, match="empty"):
            client._token()

    def test_token_loaded_and_cached(self, monkeypatch, tmp_path, cfg):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = tmp_path / "winpodx" / "agent_token.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("deadbeef" * 8)

        client = AgentClient(cfg)
        first = client._token()
        # Delete file — cache should still serve.
        path.unlink()
        second = client._token()

        assert first == second == "deadbeef" * 8


class TestRunViaAgentOrFreerdp:
    def test_phase1_always_falls_back_to_run_in_windows(self, monkeypatch, cfg):
        """Phase 1 stub must route everything through run_in_windows."""
        from winpodx.core.windows_exec import WindowsExecResult

        captured = {}

        def fake_run_in_windows(cfg_inner, payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            return WindowsExecResult(rc=0, stdout="ok", stderr="")

        monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake_run_in_windows)

        from winpodx.core.agent import run_via_agent_or_freerdp

        result = run_via_agent_or_freerdp(cfg, "Write-Output ok", description="test", timeout=15)

        assert result.rc == 0
        assert captured["payload"] == "Write-Output ok"
        assert captured["kwargs"]["timeout"] == 15
        assert captured["kwargs"]["description"] == "test"
