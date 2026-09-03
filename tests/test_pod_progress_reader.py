from __future__ import annotations

import socket

import pytest

import winpodx.core.dockur_progress as dockur_progress
from winpodx.core.dockur_progress import DockurProgress, DockurProgressReader

MAX_BODY_BYTES = 64 * 1024
VNC_PORT = 8006


class _FakeResponse:
    def __init__(
        self,
        status: int = 200,
        body: bytes = b'<p class="loading">Installing Windows</p>',
        content_encoding: str | None = None,
        read_error: OSError | None = None,
        read_amounts: list[int | None] | None = None,
    ) -> None:
        self.status = status
        self._body = body
        self._content_encoding = content_encoding
        self._read_error = read_error
        self.read_amounts = read_amounts if read_amounts is not None else []

    def read(self, amount: int | None = None) -> bytes:
        self.read_amounts.append(amount)
        if self._read_error is not None:
            raise self._read_error
        return self._body if amount is None else self._body[:amount]

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._content_encoding if name.lower() == "content-encoding" else default


class _FakeConnection:
    def __init__(
        self,
        response: _FakeResponse,
        response_error: OSError | None,
        request_error: OSError | None,
        close_error: OSError | None,
    ) -> None:
        self._response = response
        self._response_error = response_error
        self._request_error = request_error
        self._close_error = close_error
        self.requests: list[tuple[str, str]] = []
        self.closed = False

    def request(self, method: str, path: str) -> None:
        if self._request_error is not None:
            raise self._request_error
        self.requests.append((method, path))

    def getresponse(self) -> _FakeResponse:
        if self._response_error is not None:
            raise self._response_error
        return self._response

    def close(self) -> None:
        if self._close_error is not None:
            raise self._close_error
        self.closed = True


class _ConnectionHarness:
    def __init__(self) -> None:
        self.response = _FakeResponse()
        self.constructor_error: OSError | None = None
        self.response_error: OSError | None = None
        self.request_error: OSError | None = None
        self.close_error: OSError | None = None
        self.calls: list[tuple[str, int, float]] = []
        self.connections: list[_FakeConnection] = []

    def __call__(self, host: str, port: int, timeout: float) -> _FakeConnection:
        self.calls.append((host, port, timeout))
        if self.constructor_error is not None:
            raise self.constructor_error
        connection = _FakeConnection(
            self.response, self.response_error, self.request_error, self.close_error
        )
        self.connections.append(connection)
        return connection


@pytest.fixture
def http_harness(monkeypatch: pytest.MonkeyPatch) -> _ConnectionHarness:
    harness = _ConnectionHarness()
    monkeypatch.setattr(dockur_progress.http.client, "HTTPConnection", harness)
    return harness


def test_reader_uses_fixed_loopback_request_and_closes(http_harness: _ConnectionHarness) -> None:
    reader = DockurProgressReader(vnc_port=VNC_PORT)
    result = reader.poll()
    assert result == DockurProgress(text="Installing Windows", is_loading=True)
    assert http_harness.calls == [("127.0.0.1", VNC_PORT, 1.0)]
    assert http_harness.connections[0].requests == [("GET", "/msg.html")]
    assert http_harness.connections[0].closed is True


def test_reader_reads_at_most_64_kibibytes_plus_one(http_harness: _ConnectionHarness) -> None:
    DockurProgressReader(vnc_port=VNC_PORT).poll()
    assert http_harness.response.read_amounts == [64 * 1024 + 1]


def test_reader_passes_configured_non_default_port(http_harness: _ConnectionHarness) -> None:
    DockurProgressReader(vnc_port=18006).poll()
    assert http_harness.calls == [("127.0.0.1", 18006, 1.0)]


@pytest.mark.parametrize("status", [201, 302, 404])
def test_reader_accepts_only_http_200_without_redirects(
    status: int, http_harness: _ConnectionHarness
) -> None:
    http_harness.response = _FakeResponse(status=status)
    result = DockurProgressReader(vnc_port=VNC_PORT).poll()
    assert result is None
    assert http_harness.connections[0].requests == [("GET", "/msg.html")]
    assert http_harness.connections[0].closed is True


@pytest.mark.parametrize(
    "response",
    [_FakeResponse(content_encoding="gzip"), _FakeResponse(body=b"x" * (MAX_BODY_BYTES + 1))],
    ids=["compressed", "oversized"],
)
def test_reader_rejects_unsafe_bodies_and_closes(
    response: _FakeResponse, http_harness: _ConnectionHarness
) -> None:
    http_harness.response = response
    result = DockurProgressReader(vnc_port=VNC_PORT).poll()
    assert result is None
    assert http_harness.connections[0].closed is True


def test_reader_safely_handles_connection_refusal(http_harness: _ConnectionHarness) -> None:
    http_harness.constructor_error = ConnectionRefusedError()
    assert DockurProgressReader(vnc_port=VNC_PORT).poll() is None


def test_reader_safely_handles_request_error(http_harness: _ConnectionHarness) -> None:
    http_harness.request_error = OSError("request failed")
    assert DockurProgressReader(vnc_port=VNC_PORT).poll() is None


@pytest.mark.parametrize("error", [TimeoutError("read timeout"), OSError("read failed")])
def test_reader_safely_handles_body_read_errors(
    error: OSError, http_harness: _ConnectionHarness
) -> None:
    http_harness.response = _FakeResponse(read_error=error)
    assert DockurProgressReader(vnc_port=VNC_PORT).poll() is None
    assert http_harness.connections[0].closed is True


def test_reader_safely_handles_close_error(http_harness: _ConnectionHarness) -> None:
    http_harness.close_error = OSError("close failed")
    assert DockurProgressReader(vnc_port=VNC_PORT).poll() is None


def test_close_error_does_not_suppress_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    class _InterruptingConnection:
        def request(self, method: str, path: str) -> None:
            raise KeyboardInterrupt

        def close(self) -> None:
            raise OSError("close failed")

    connection = _InterruptingConnection()

    def connect(host: str, port: int, timeout: float) -> _InterruptingConnection:
        return connection

    monkeypatch.setattr(dockur_progress.http.client, "HTTPConnection", connect)

    with pytest.raises(KeyboardInterrupt):
        DockurProgressReader(vnc_port=VNC_PORT).poll()


def test_reader_rejects_malformed_body(http_harness: _ConnectionHarness) -> None:
    http_harness.response = _FakeResponse(body=b'<p class="loading">unfinished')
    assert DockurProgressReader(vnc_port=VNC_PORT).poll() is None


def test_reader_safely_handles_timeout_and_closes(http_harness: _ConnectionHarness) -> None:
    http_harness.response_error = socket.timeout()
    assert DockurProgressReader(vnc_port=VNC_PORT).poll() is None
    assert http_harness.connections[0].closed is True


def _set_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [0.0]

    def monotonic() -> float:
        return now[0]

    monkeypatch.setattr(dockur_progress.time, "monotonic", monotonic)
    return now


def test_unchanged_normalized_progress_becomes_stale_at_60_seconds(
    monkeypatch: pytest.MonkeyPatch, http_harness: _ConnectionHarness
) -> None:
    now = _set_clock(monkeypatch)
    reader = DockurProgressReader(vnc_port=VNC_PORT)
    assert reader.poll() == DockurProgress(text="Installing Windows", is_loading=True)
    http_harness.response = _FakeResponse(
        body=b'<p class="loading"> Installing <b>Windows</b> </p>'
    )
    now[0] = 60.0
    assert reader.poll() is None


def test_changed_progress_reactivates_after_staleness(
    monkeypatch: pytest.MonkeyPatch, http_harness: _ConnectionHarness
) -> None:
    now = _set_clock(monkeypatch)
    reader = DockurProgressReader(vnc_port=VNC_PORT)
    assert reader.poll() is not None
    now[0] = 60.0
    assert reader.poll() is None
    http_harness.response = _FakeResponse(body=b'<div id="info">Windows ready</div>')
    now[0] = 61.0
    assert reader.poll() == DockurProgress(text="Windows ready", is_loading=False)


def test_unavailable_poll_does_not_refresh_stale_age(
    monkeypatch: pytest.MonkeyPatch, http_harness: _ConnectionHarness
) -> None:
    now = _set_clock(monkeypatch)
    reader = DockurProgressReader(vnc_port=VNC_PORT)
    assert reader.poll() is not None
    now[0] = 30.0
    http_harness.response = _FakeResponse(status=404)
    assert reader.poll() is None
    now[0] = 60.0
    http_harness.response = _FakeResponse()
    assert reader.poll() is None
