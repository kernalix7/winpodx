"""Tests for the host-side AgentClient (Phase 1: /health, Phase 2: /exec)."""

from __future__ import annotations

import base64
import io
import json
import socket
from urllib import error as urllib_error

import pytest

from winpodx.core.agent import (
    AgentAuthError,
    AgentClient,
    AgentError,
    AgentTimeoutError,
    AgentUnavailableError,
    ExecResult,
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


class TestExec:
    @pytest.fixture
    def authed_client(self, cfg: Config) -> AgentClient:
        """Client with a pre-cached token so _token() never touches disk."""
        return AgentClient(cfg, token="cafebabe" * 8)

    def test_exec_happy_path(self, monkeypatch, authed_client):
        body = json.dumps({"rc": 0, "stdout": "ok\n", "stderr": ""}).encode("utf-8")
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["timeout"] = timeout
            captured["headers"] = dict(req.header_items())
            captured["body"] = req.data
            return _FakeResponse(body, status=200)

        _patch_urlopen(monkeypatch, fake_urlopen)

        result = authed_client.exec("Write-Output ok", timeout=15)

        assert isinstance(result, ExecResult)
        assert result.rc == 0
        assert result.stdout == "ok\n"
        assert result.stderr == ""
        assert result.ok is True
        assert captured["url"] == "http://127.0.0.1:8765/exec"
        assert captured["method"] == "POST"
        assert captured["timeout"] == 14.0  # timeout - 1
        # Authorization header present.
        header_keys = {k.lower(): v for k, v in captured["headers"].items()}
        assert header_keys["authorization"] == "Bearer " + "cafebabe" * 8
        # Body is JSON with base64-encoded script.
        sent = json.loads(captured["body"].decode("utf-8"))
        assert sent["timeout_sec"] == 15
        assert base64.b64decode(sent["script"]).decode("utf-8") == "Write-Output ok"

    def test_exec_token_rejected(self, monkeypatch, authed_client):
        def fake_urlopen(req, timeout=None):
            raise urllib_error.HTTPError(
                req.full_url, 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b"")
            )

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentAuthError, match="401"):
            authed_client.exec("Write-Output ok")

    def test_exec_timeout(self, monkeypatch, authed_client):
        def fake_urlopen(req, timeout=None):
            raise socket.timeout("timed out")

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentTimeoutError):
            authed_client.exec("Start-Sleep 90", timeout=5)

    def test_exec_connection_refused(self, monkeypatch, authed_client):
        def fake_urlopen(req, timeout=None):
            raise urllib_error.URLError(ConnectionRefusedError("connection refused"))

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentUnavailableError, match="unreachable"):
            authed_client.exec("Write-Output ok")

    def test_exec_500(self, monkeypatch, authed_client):
        def fake_urlopen(req, timeout=None):
            raise urllib_error.HTTPError(
                req.full_url, 500, "Internal Server Error", hdrs=None, fp=io.BytesIO(b"")
            )

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentUnavailableError, match="500"):
            authed_client.exec("Write-Output ok")

    def test_exec_non_json_response(self, monkeypatch, authed_client):
        def fake_urlopen(req, timeout=None):
            return _FakeResponse(b"<html>boom</html>", status=200)

        _patch_urlopen(monkeypatch, fake_urlopen)

        with pytest.raises(AgentError, match="non-JSON"):
            authed_client.exec("Write-Output ok")


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
