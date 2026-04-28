"""Tests for winpodx.core.agent — HTTP client for the Windows guest agent.

The mock agent is a stdlib ``http.server.HTTPServer`` running on a
random localhost port. Each test installs a fresh routes dict that
selects the handler logic per (method, path), so we can focus on one
endpoint at a time without fighting a global mock.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from winpodx.core import agent as agent_mod
from winpodx.core.agent import (
    AgentAuthError,
    AgentClient,
    AgentTimeoutError,
    AgentUnavailableError,
    ExecResult,
    run_via_agent_or_freerdp,
)
from winpodx.core.config import Config
from winpodx.core.windows_exec import WindowsExecResult

# ---------------------------------------------------------------------------
# Mock server plumbing
# ---------------------------------------------------------------------------


HandlerFn = Callable[[BaseHTTPRequestHandler], None]


def _free_port() -> int:
    """Grab an OS-assigned free TCP port (race-tolerant for tests)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


class _RouteHandler(BaseHTTPRequestHandler):
    """Dispatch to a per-test routes dict keyed by ``(method, path)``."""

    routes: dict[tuple[str, str], HandlerFn] = {}

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Silence the default stderr access log so pytest output stays clean.
        return

    def _dispatch(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        fn = self.routes.get((method, path))
        if fn is None:
            # Allow prefix routes like "/apply/" to match "/apply/<step>".
            for (m, p), candidate in self.routes.items():
                if m == method and p.endswith("/") and path.startswith(p):
                    fn = candidate
                    break
        if fn is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
            return
        fn(self)

    def do_GET(self) -> None:  # noqa: N802 — stdlib name
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


class _MockAgent:
    """Background HTTPServer with helpers for installing routes per test."""

    def __init__(self) -> None:
        self.port = _free_port()
        # Fresh handler subclass per instance so routes don't leak between
        # parallel tests if pytest-xdist ever lands.
        self.handler_cls = type(
            "_RouteHandlerInstance",
            (_RouteHandler,),
            {"routes": {}},
        )
        self.server = HTTPServer(("127.0.0.1", self.port), self.handler_cls)
        self.server.timeout = 0.2
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.captured_requests: list[dict[str, Any]] = []

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def route(self, method: str, path: str, fn: HandlerFn) -> None:
        self.handler_cls.routes[(method, path)] = fn

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def mock_agent():
    """Spin up a clean mock HTTP agent on a free port, tear down after."""
    srv = _MockAgent()
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    """Place a valid token file where AgentClient expects it."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    token_path = cfg_dir / "agent_token.txt"
    token_path.write_text("test-token-abc\n", encoding="utf-8")
    monkeypatch.setattr(AgentClient, "_token_path", staticmethod(lambda: token_path))
    return token_path


@pytest.fixture
def cfg() -> Config:
    """Plain default Config — agent client doesn't read RDP fields."""
    return Config()


# ---------------------------------------------------------------------------
# Tiny helpers for the route handlers
# ---------------------------------------------------------------------------


def _send_json(h: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _read_json_body(h: BaseHTTPRequestHandler) -> dict:
    length = int(h.headers.get("Content-Length", "0"))
    raw = h.rfile.read(length) if length else b""
    return json.loads(raw.decode("utf-8")) if raw else {}


def _send_sse_chunks(h: BaseHTTPRequestHandler, chunks: list[tuple[str, str]]) -> None:
    """Write each (event, data) pair as an SSE event and flush."""
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream")
    h.send_header("Cache-Control", "no-cache")
    h.end_headers()
    for event, data in chunks:
        out = b""
        if event:
            out += f"event: {event}\n".encode("utf-8")
        out += f"data: {data}\n\n".encode("utf-8")
        h.wfile.write(out)
        h.wfile.flush()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_happy_path(mock_agent, token_file, cfg):
    """/health returns the parsed JSON dict from the agent."""
    payload = {"version": "0.2.2", "started": "2026-04-26T00:00:00Z", "uptime": 5}

    def handle(h):
        _send_json(h, 200, payload)

    mock_agent.route("GET", "/health", handle)
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    assert client.health() == payload


def test_health_no_token_succeeds(mock_agent, tmp_path, monkeypatch, cfg):
    """/health is no-auth: a missing token file does not block it."""
    missing = tmp_path / "absent" / "agent_token.txt"
    monkeypatch.setattr(AgentClient, "_token_path", staticmethod(lambda: missing))
    payload = {"version": "0.2.2", "started": "x", "uptime": 1}
    mock_agent.route("GET", "/health", lambda h: _send_json(h, 200, payload))
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    assert client.health() == payload


def test_health_connection_refused_raises_unavailable(token_file, cfg):
    """If nothing is listening, /health raises AgentUnavailableError."""
    dead_port = _free_port()  # nothing bound here
    client = AgentClient(cfg, base_url=f"http://127.0.0.1:{dead_port}")
    with pytest.raises(AgentUnavailableError):
        client.health()


def test_health_500_raises_unavailable(mock_agent, token_file, cfg):
    """A 5xx from /health is treated as the agent being unavailable."""

    def handle(h):
        _send_json(h, 500, {"error": "boom"})

    mock_agent.route("GET", "/health", handle)
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    with pytest.raises(AgentUnavailableError):
        client.health()


# ---------------------------------------------------------------------------
# /exec
# ---------------------------------------------------------------------------


def test_exec_happy_path(mock_agent, token_file, cfg):
    """/exec receives a base64-encoded script + Bearer auth, returns ExecResult."""
    captured: dict[str, Any] = {}

    def handle(h):
        captured["auth"] = h.headers.get("Authorization")
        captured["body"] = _read_json_body(h)
        _send_json(h, 200, {"rc": 0, "stdout": "ok", "stderr": ""})

    mock_agent.route("POST", "/exec", handle)
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    result = client.exec("Write-Output 'hi'")

    assert isinstance(result, ExecResult)
    assert result.rc == 0
    assert result.stdout == "ok"
    assert result.stderr == ""
    assert captured["auth"] == "Bearer test-token-abc"
    decoded = base64.b64decode(captured["body"]["script"]).decode("utf-8")
    assert decoded == "Write-Output 'hi'"


def test_exec_token_rejected(mock_agent, token_file, cfg):
    """A 401 from /exec surfaces as AgentAuthError, not unavailable."""

    def handle(h):
        _send_json(h, 401, {"error": "unauthorized"})

    mock_agent.route("POST", "/exec", handle)
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    with pytest.raises(AgentAuthError):
        client.exec("Write-Output 'x'")


def test_exec_timeout(mock_agent, token_file, cfg):
    """A server slower than the per-call timeout raises AgentTimeoutError."""
    release = threading.Event()

    def handle(h):
        # Block until the test releases us so we never leak this thread
        # past the test's lifetime.
        release.wait(timeout=5.0)
        _send_json(h, 200, {"rc": 0, "stdout": "", "stderr": ""})

    mock_agent.route("POST", "/exec", handle)
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    try:
        with pytest.raises(AgentTimeoutError):
            client.exec("Write-Output 'slow'", timeout=0.3)
    finally:
        release.set()


# ---------------------------------------------------------------------------
# /events (SSE)
# ---------------------------------------------------------------------------


def test_stream_events_happy_path(mock_agent, token_file, cfg):
    """SSE data lines are parsed into dicts and dispatched to on_line."""
    events = [
        ("", json.dumps({"ts": "t1", "level": "info", "msg": "one"})),
        ("", json.dumps({"ts": "t2", "level": "info", "msg": "two"})),
        ("", json.dumps({"ts": "t3", "level": "info", "msg": "three"})),
        ("", json.dumps({"ts": "t4", "level": "info", "msg": "four"})),
    ]

    def handle(h):
        _send_sse_chunks(h, events)

    mock_agent.route("GET", "/events", handle)
    received: list[dict] = []
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    client.stream_events(received.append)

    assert len(received) == 4
    assert [d["msg"] for d in received] == ["one", "two", "three", "four"]
    for d in received:
        assert {"ts", "level", "msg"} <= d.keys()


def test_stream_events_stop_event_breaks_loop(mock_agent, token_file, cfg):
    """A pre-set stop event short-circuits the SSE loop without consuming."""
    # Plant a route that would block forever if reached, so an
    # un-honoured stop event would also expose itself as a hang/timeout.
    forever = threading.Event()

    def handle(h):
        _send_sse_chunks(h, [("", json.dumps({"msg": "should-not-arrive"}))])
        forever.wait(timeout=2.0)

    mock_agent.route("GET", "/events", handle)
    stop = threading.Event()
    stop.set()
    received: list[dict] = []
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    # Should return effectively immediately without invoking on_line.
    start = time.monotonic()
    client.stream_events(received.append, stop=stop)
    elapsed = time.monotonic() - start
    forever.set()
    assert elapsed < 1.5
    assert received == []


# ---------------------------------------------------------------------------
# /apply/<step> + /discover
# ---------------------------------------------------------------------------


def test_post_apply_streams_progress_then_returns_rc(mock_agent, token_file, cfg):
    """post_apply dispatches each progress line, returns rc from done."""
    chunks = [
        ("", json.dumps({"ts": "t1", "level": "info", "msg": "step 1/2"})),
        ("", json.dumps({"ts": "t2", "level": "info", "msg": "step 2/2"})),
        ("done", json.dumps({"rc": 0})),
    ]

    def handle(h):
        _send_sse_chunks(h, chunks)

    mock_agent.route("POST", "/apply/", handle)
    progress: list[str] = []
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    rc = client.post_apply("max_sessions", on_progress=progress.append)
    assert rc == 0
    assert len(progress) == 2


def test_post_discover_streams_then_returns_rc_and_json_path(mock_agent, token_file, cfg):
    """post_discover returns the full done payload including json_file."""
    chunks = [
        ("", json.dumps({"ts": "t1", "level": "info", "msg": "scanning"})),
        ("", json.dumps({"ts": "t2", "level": "info", "msg": "found 12"})),
        (
            "done",
            json.dumps({"rc": 0, "json_file": "C:\\OEM\\agent-runs\\d.json"}),
        ),
    ]

    def handle(h):
        _send_sse_chunks(h, chunks)

    mock_agent.route("POST", "/discover", handle)
    progress: list[str] = []
    client = AgentClient(cfg, base_url=mock_agent.base_url)
    payload = client.post_discover(on_progress=progress.append)
    assert payload["rc"] == 0
    assert payload["json_file"] == "C:\\OEM\\agent-runs\\d.json"
    assert len(progress) == 2


# ---------------------------------------------------------------------------
# run_via_agent_or_freerdp
# ---------------------------------------------------------------------------


def test_run_via_agent_or_freerdp_uses_agent_when_available(
    mock_agent, token_file, cfg, monkeypatch
):
    """When the agent answers /health, /exec is used and freerdp is NOT called."""
    mock_agent.route(
        "GET",
        "/health",
        lambda h: _send_json(h, 200, {"version": "0.2.2"}),
    )
    mock_agent.route(
        "POST",
        "/exec",
        lambda h: _send_json(h, 200, {"rc": 0, "stdout": "via-agent", "stderr": ""}),
    )
    # ``run_via_agent_or_freerdp`` builds its own AgentClient(cfg) with
    # the module default; redirect that default at the mock port so we
    # exercise the real wiring without subclassing the helper.
    monkeypatch.setattr(agent_mod, "DEFAULT_BASE_URL", mock_agent.base_url)

    called = {"freerdp": False}

    def fake_run_in_windows(*args, **kwargs):
        called["freerdp"] = True
        return WindowsExecResult(rc=99, stdout="freerdp", stderr="")

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake_run_in_windows)

    result = run_via_agent_or_freerdp(cfg, "Write-Output 'x'")
    assert isinstance(result, WindowsExecResult)
    assert result.rc == 0
    assert result.stdout == "via-agent"
    assert called["freerdp"] is False


def test_run_via_agent_or_freerdp_falls_back_when_agent_down(tmp_path, monkeypatch, cfg):
    """A connection-refused agent triggers fallback to windows_exec.run_in_windows."""
    # Point the client at a port nothing is listening on so /health
    # raises AgentUnavailableError immediately.
    dead_port = _free_port()
    monkeypatch.setattr(agent_mod, "DEFAULT_BASE_URL", f"http://127.0.0.1:{dead_port}")
    # And give it a token file so token resolution doesn't dominate.
    cfg_dir = tmp_path / "cfg2"
    cfg_dir.mkdir()
    token_path = cfg_dir / "agent_token.txt"
    token_path.write_text("tok\n", encoding="utf-8")
    monkeypatch.setattr(AgentClient, "_token_path", staticmethod(lambda: token_path))

    sentinel = WindowsExecResult(rc=7, stdout="from-freerdp", stderr="")

    def fake_run_in_windows(_cfg, script, *, timeout=60, description="x"):
        return sentinel

    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake_run_in_windows)

    result = run_via_agent_or_freerdp(cfg, "Write-Output 'x'")
    assert result is sentinel
    assert isinstance(result, WindowsExecResult)
    assert result.rc == 7
    assert result.stdout == "from-freerdp"
