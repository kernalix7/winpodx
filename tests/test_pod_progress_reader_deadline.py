from __future__ import annotations

import threading
import time

import pytest

import winpodx.core.dockur_progress as dockur_progress
from winpodx.core.dockur_progress import DockurProgress, DockurProgressReader

VNC_PORT = 8006


def test_reader_enforces_total_wall_clock_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    released = threading.Event()
    closed = threading.Event()
    read_thread_ids: list[int] = []
    close_thread_ids: list[int] = []

    class _BlockingResponse:
        status = 200

        def getheader(self, name: str, default: str | None = None) -> str | None:
            return default

        def read(self, amount: int | None = None) -> bytes:
            read_thread_ids.append(threading.get_ident())
            released.wait(3.0)
            return b'<p class="loading">Installing Windows</p>'

    class _BlockingConnection:
        def __init__(self) -> None:
            self.closed = False

        def request(self, method: str, path: str) -> None:
            return None

        def getresponse(self) -> _BlockingResponse:
            return _BlockingResponse()

        def close(self) -> None:
            self.closed = True
            close_thread_ids.append(threading.get_ident())
            closed.set()

    connection = _BlockingConnection()
    monkeypatch.setattr(
        dockur_progress.http.client,
        "HTTPConnection",
        lambda host, port, timeout: connection,
    )

    # When
    started = time.monotonic()
    result = DockurProgressReader(vnc_port=VNC_PORT).poll()
    elapsed = time.monotonic() - started
    released.set()

    # Then
    assert result is None
    assert elapsed < 1.5
    assert closed.wait(1.0)
    assert connection.closed is True
    assert close_thread_ids == read_thread_ids


def test_reader_keeps_only_one_timed_out_request_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    released = threading.Event()
    read_started = threading.Event()
    closed = threading.Event()
    connections: list[_BlockingConnection] = []

    class _BlockingResponse:
        status = 200

        def getheader(self, name: str, default: str | None = None) -> str | None:
            return default

        def read(self, amount: int | None = None) -> bytes:
            read_started.set()
            released.wait(3.0)
            return b'<p class="loading">Late progress</p>'

    class _BlockingConnection:
        def request(self, method: str, path: str) -> None:
            return None

        def getresponse(self) -> _BlockingResponse:
            return _BlockingResponse()

        def close(self) -> None:
            closed.set()

    def connect(host: str, port: int, timeout: float) -> _BlockingConnection:
        connection = _BlockingConnection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(dockur_progress.http.client, "HTTPConnection", connect)
    reader = DockurProgressReader(vnc_port=VNC_PORT)

    # When
    assert reader.poll() is None
    assert read_started.is_set()
    started = time.monotonic()
    assert reader.poll() is None
    second_elapsed = time.monotonic() - started
    released.set()

    # Then
    assert second_elapsed < 0.2
    assert len(connections) == 1
    assert closed.wait(1.0)
    assert reader._last_progress is None
    assert reader.poll() == DockurProgress(text="Late progress", is_loading=True)
