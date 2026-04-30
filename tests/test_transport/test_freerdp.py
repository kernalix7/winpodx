"""Tests for FreerdpTransport — wraps run_in_windows + check_rdp_port."""

from __future__ import annotations

import pytest

from winpodx.core.config import Config
from winpodx.core.transport.base import (
    ExecResult,
    HealthStatus,
    TransportAuthError,
    TransportError,
    TransportTimeoutError,
    TransportUnavailable,
)
from winpodx.core.transport.freerdp import FreerdpTransport
from winpodx.core.windows_exec import WindowsExecError, WindowsExecResult


@pytest.fixture
def cfg() -> Config:
    return Config()


@pytest.fixture
def transport(cfg: Config) -> FreerdpTransport:
    return FreerdpTransport(cfg)


@pytest.fixture
def patch_freerdp_present(monkeypatch):
    """find_freerdp() returns a fake binary."""
    monkeypatch.setattr(
        "winpodx.core.transport.freerdp.find_freerdp",
        lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
    )


@pytest.fixture
def patch_freerdp_missing(monkeypatch):
    """find_freerdp() returns None — config error path."""
    monkeypatch.setattr("winpodx.core.transport.freerdp.find_freerdp", lambda: None)


class TestHealth:
    def test_returns_available_when_binary_and_port_up(
        self, transport, monkeypatch, patch_freerdp_present
    ):
        monkeypatch.setattr(
            "winpodx.core.transport.freerdp.check_rdp_port",
            lambda ip, port, timeout=5.0: True,
        )
        status = transport.health()
        assert isinstance(status, HealthStatus)
        assert status.available is True

    def test_returns_unavailable_when_port_closed(
        self, transport, monkeypatch, patch_freerdp_present
    ):
        monkeypatch.setattr(
            "winpodx.core.transport.freerdp.check_rdp_port",
            lambda ip, port, timeout=5.0: False,
        )
        status = transport.health()
        assert status.available is False
        assert status.detail and "not accepting" in status.detail

    def test_raises_when_freerdp_binary_missing(self, transport, patch_freerdp_missing):
        # Configuration error — must raise per spec rule.
        with pytest.raises(TransportUnavailable):
            transport.health()

    def test_returns_unavailable_when_port_probe_raises(
        self, transport, monkeypatch, patch_freerdp_present
    ):
        # Spec rule: health() must NOT raise on transient state.
        def boom(*a, **kw):
            raise OSError("network unreachable")

        monkeypatch.setattr("winpodx.core.transport.freerdp.check_rdp_port", boom)
        status = transport.health()
        assert status.available is False
        assert status.detail and "RDP port probe failed" in status.detail


class TestExec:
    def test_happy_path_returns_exec_result(self, transport, monkeypatch):
        captured: dict = {}

        def fake_run(cfg, script, *, timeout=60, description="winpodx-exec", progress_callback=None):
            captured["script"] = script
            captured["timeout"] = timeout
            captured["description"] = description
            captured["progress_callback"] = progress_callback
            return WindowsExecResult(rc=0, stdout="hello", stderr="")

        monkeypatch.setattr("winpodx.core.transport.freerdp.run_in_windows", fake_run)

        result = transport.exec("Get-Process", timeout=30, description="probe")

        assert isinstance(result, ExecResult)
        assert result.rc == 0
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.ok is True
        assert captured["script"] == "Get-Process"
        assert captured["timeout"] == 30
        assert captured["description"] == "probe"
        # exec() does not stream — callback must be None.
        assert captured["progress_callback"] is None

    def test_nonzero_rc_returned_not_raised(self, transport, monkeypatch):
        # Per spec: a script's rc != 0 is a script-level outcome, not a
        # transport-level error — callers see ExecResult, not an exception.
        monkeypatch.setattr(
            "winpodx.core.transport.freerdp.run_in_windows",
            lambda *a, **k: WindowsExecResult(rc=2, stdout="", stderr="err"),
        )
        result = transport.exec("$x")
        assert result.rc == 2
        assert result.ok is False

    def test_timeout_maps_to_transport_timeout_error(self, transport, monkeypatch):
        def raise_timeout(*a, **k):
            raise WindowsExecError(
                "FreeRDP timed out after 60s waiting for the script to complete"
            )

        monkeypatch.setattr("winpodx.core.transport.freerdp.run_in_windows", raise_timeout)
        with pytest.raises(TransportTimeoutError):
            transport.exec("$x")

    def test_auth_failure_maps_to_transport_auth_error(self, transport, monkeypatch):
        def raise_auth(*a, **k):
            raise WindowsExecError("RDP password not set in config — cannot authenticate")

        monkeypatch.setattr("winpodx.core.transport.freerdp.run_in_windows", raise_auth)
        with pytest.raises(TransportAuthError):
            transport.exec("$x")

    def test_freerdp_missing_maps_to_transport_unavailable(self, transport, monkeypatch):
        def raise_missing(*a, **k):
            raise WindowsExecError("FreeRDP not found on $PATH")

        monkeypatch.setattr("winpodx.core.transport.freerdp.run_in_windows", raise_missing)
        with pytest.raises(TransportUnavailable):
            transport.exec("$x")

    def test_no_result_file_maps_to_transport_unavailable(self, transport, monkeypatch):
        def raise_no_result(*a, **k):
            raise WindowsExecError(
                "No result file written (FreeRDP rc=1). stderr tail: 'connection failed'"
            )

        monkeypatch.setattr("winpodx.core.transport.freerdp.run_in_windows", raise_no_result)
        with pytest.raises(TransportUnavailable):
            transport.exec("$x")

    def test_unknown_failure_maps_to_generic_transport_error(self, transport, monkeypatch):
        def raise_unknown(*a, **k):
            raise WindowsExecError("result file unparseable: something weird")

        monkeypatch.setattr("winpodx.core.transport.freerdp.run_in_windows", raise_unknown)
        with pytest.raises(TransportError) as excinfo:
            transport.exec("$x")
        # Must be the BASE TransportError, not a subclass.
        assert type(excinfo.value) is TransportError


class TestStream:
    def test_passes_progress_callback_through(self, transport, monkeypatch):
        captured: dict = {}

        def fake_run(cfg, script, *, timeout=60, description="winpodx-stream", progress_callback=None):
            captured["progress_callback"] = progress_callback
            captured["timeout"] = timeout
            captured["description"] = description
            # Simulate the wrapped helper invoking the callback.
            if progress_callback:
                progress_callback("step 1")
                progress_callback("step 2")
            return WindowsExecResult(rc=0, stdout="done", stderr="")

        monkeypatch.setattr("winpodx.core.transport.freerdp.run_in_windows", fake_run)

        seen: list[str] = []
        result = transport.stream("$x", seen.append, timeout=120, description="stream-probe")

        assert seen == ["step 1", "step 2"]
        assert result.rc == 0
        assert result.stdout == "done"
        assert captured["progress_callback"] is not None
        assert captured["timeout"] == 120
        assert captured["description"] == "stream-probe"

    def test_stream_error_maps_same_as_exec(self, transport, monkeypatch):
        def raise_timeout(*a, **k):
            raise WindowsExecError("FreeRDP timed out after 600s")

        monkeypatch.setattr("winpodx.core.transport.freerdp.run_in_windows", raise_timeout)
        with pytest.raises(TransportTimeoutError):
            transport.stream("$x", lambda _line: None)


class TestName:
    def test_class_name_is_freerdp(self):
        assert FreerdpTransport.name == "freerdp"
