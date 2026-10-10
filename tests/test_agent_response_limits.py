# SPDX-License-Identifier: MIT
"""Regression tests: guest-agent HTTP replies must be byte-capped (RS-01).

``AgentClient.health`` / ``.exec`` (``src/winpodx/core/agent.py:211-213`` and
``:264-266``) currently call ``resp.read()`` with no argument and then decode
and ``json.loads`` the *entire* body. ``AgentClient`` is used on the normal
loopback topology and on the supported ``manual`` remote-VM topology, so a
compromised or spoofed guest can make the host allocate an unbounded buffer
before the reply is ever validated (CWE-400 / CWE-770).

These tests pin the behavioural contract of the intended fix — a byte ceiling
before decode — without asserting on any particular message string:

* an oversized ``/health`` or ``/exec`` body must raise the existing
  ``AgentError`` family (``AgentUnavailableError`` included) instead of being
  parsed;
* the oversized body must not be consumed whole (the client stops reading at
  the cap, not after the peer's full write);
* normal replies, the unauthenticated ``/health`` contract, and the
  bearer-authenticated ``/exec`` contract stay intact.

The intended implementation exposes ``AgentClient.HEALTH_RESPONSE_LIMIT``
(64 KiB) and ``AgentClient.EXEC_RESPONSE_LIMIT`` (64 MiB). They do not exist
yet, so every test installs them with ``raising=False`` and shrinks them to
tiny thresholds. On the current uncapped code the bigger-than-limit body is
accepted, so the oversize cases are RED by construction. ``urllib`` is stubbed
with an in-memory response that honours ``read(size)`` — no socket, no guest.
"""

from __future__ import annotations

import json

import pytest

from winpodx.core.agent import (
    AgentAuthError,
    AgentClient,
    AgentError,
    AgentUnavailableError,
)
from winpodx.core.config import Config

# Small, unambiguous thresholds. The production constants are 64 KiB /
# 64 MiB; the behaviour under test is identical at any positive cap.
_HEALTH_LIMIT = 256
_EXEC_LIMIT = 1024

# Deliberately larger than either limit so an uncapped client parses the
# whole thing (and thus fails the rejection assertion).
_OVERSIZE_BYTES = 4096


class _SizedResponse:
    """Context-manager stand-in for ``HTTPResponse`` honouring ``read(size)``.

    Records how many bytes were actually handed to the caller so a test can
    prove the client stopped at the cap instead of draining the peer.
    """

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status
        self.total_returned = 0
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size is None or size < 0:
            chunk = self._body
        else:
            chunk = self._body[:size]
        self.total_returned += len(chunk)
        return chunk

    def __enter__(self) -> _SizedResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


@pytest.fixture
def cfg() -> Config:
    return Config()


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, resp: object) -> None:
    monkeypatch.setattr(
        "winpodx.core.agent.urllib_request.urlopen",
        lambda req, timeout=None: resp,
    )


class TestHealthResponseCap:
    def test_oversize_body_is_rejected_before_parse(self, monkeypatch, cfg: Config) -> None:
        # Given: a valid-JSON /health reply larger than the cap.
        monkeypatch.setattr(AgentClient, "HEALTH_RESPONSE_LIMIT", _HEALTH_LIMIT, raising=False)
        body = json.dumps({"pad": "x" * _OVERSIZE_BYTES}).encode("utf-8")
        assert len(body) > _HEALTH_LIMIT
        resp = _SizedResponse(body, status=200)
        _patch_urlopen(monkeypatch, resp)

        # When / Then: the body is refused with the AgentError family, not parsed.
        client = AgentClient(cfg)
        with pytest.raises((AgentError, AgentUnavailableError)):
            client.health()

        # And the full oversized body was never consumed.
        assert resp.total_returned < len(body)

    def test_body_at_or_under_cap_still_parses(self, monkeypatch, cfg: Config) -> None:
        monkeypatch.setattr(AgentClient, "HEALTH_RESPONSE_LIMIT", _HEALTH_LIMIT, raising=False)
        body = json.dumps({"ok": True, "version": "0.2.2"}).encode("utf-8")
        assert len(body) <= _HEALTH_LIMIT
        _patch_urlopen(monkeypatch, _SizedResponse(body, status=200))

        assert AgentClient(cfg).health() == {"ok": True, "version": "0.2.2"}

    def test_normal_reply_keeps_no_rdp_and_no_auth_contract(
        self, monkeypatch, tmp_path, cfg: Config
    ) -> None:
        """/health stays reachable with no token and sends no Authorization.

        This is the no-RDP/agent-ready contract: the cap must not add a token
        requirement or an RDP-port dependency to the readiness probe.
        """
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # no token file
        monkeypatch.setattr(AgentClient, "HEALTH_RESPONSE_LIMIT", _HEALTH_LIMIT, raising=False)
        body = json.dumps({"ok": True}).encode("utf-8")
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            return _SizedResponse(body, status=200)

        monkeypatch.setattr("winpodx.core.agent.urllib_request.urlopen", fake_urlopen)

        assert AgentClient(cfg).health() == {"ok": True}
        assert captured["url"].endswith("/health")
        assert "authorization" not in {k.lower() for k in captured["headers"]}


class TestExecResponseCap:
    @pytest.fixture
    def authed_client(self, cfg: Config) -> AgentClient:
        return AgentClient(cfg, token="cafebabe" * 4)

    def test_oversize_body_is_rejected_before_parse(self, monkeypatch, authed_client) -> None:
        monkeypatch.setattr(AgentClient, "EXEC_RESPONSE_LIMIT", _EXEC_LIMIT, raising=False)
        body = json.dumps({"rc": 0, "stdout": "x" * _OVERSIZE_BYTES, "stderr": ""}).encode("utf-8")
        assert len(body) > _EXEC_LIMIT
        resp = _SizedResponse(body, status=200)
        _patch_urlopen(monkeypatch, resp)

        with pytest.raises((AgentError, AgentUnavailableError)):
            authed_client.exec("Write-Output ok")

        assert resp.total_returned < len(body)

    def test_normal_reply_parses_and_keeps_auth_contract(self, monkeypatch, authed_client) -> None:
        monkeypatch.setattr(AgentClient, "EXEC_RESPONSE_LIMIT", _EXEC_LIMIT, raising=False)
        body = json.dumps({"rc": 0, "stdout": "ok\n", "stderr": ""}).encode("utf-8")
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["headers"] = {k.lower(): v for k, v in req.header_items()}
            return _SizedResponse(body, status=200)

        monkeypatch.setattr("winpodx.core.agent.urllib_request.urlopen", fake_urlopen)

        result = authed_client.exec("Write-Output ok", timeout=5)

        assert result.rc == 0
        assert result.stdout == "ok\n"
        assert captured["headers"]["authorization"] == "Bearer " + "cafebabe" * 4

    def test_oversize_reply_still_propagates_auth_failure(self, monkeypatch, cfg: Config) -> None:
        # A 401 must remain an AgentAuthError even with the cap installed —
        # the limit is a transport concern, not an auth bypass.
        import io
        from urllib import error as urllib_error

        monkeypatch.setattr(AgentClient, "EXEC_RESPONSE_LIMIT", _EXEC_LIMIT, raising=False)

        def fake_urlopen(req, timeout=None):
            raise urllib_error.HTTPError(
                req.full_url, 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b"")
            )

        monkeypatch.setattr("winpodx.core.agent.urllib_request.urlopen", fake_urlopen)

        with pytest.raises(AgentAuthError):
            AgentClient(cfg, token="cafebabe" * 4).exec("Write-Output ok")
