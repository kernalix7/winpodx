"""Tests for transport.base — dataclass shapes, exception hierarchy, ABC."""

from __future__ import annotations

import dataclasses

import pytest

from winpodx.core.transport.base import (
    SPEC_VERSION,
    ExecResult,
    HealthStatus,
    Transport,
    TransportAuthError,
    TransportError,
    TransportTimeoutError,
    TransportUnavailable,
)


class TestSpecVersion:
    def test_is_v1(self):
        assert SPEC_VERSION == 1


class TestExecResult:
    def test_required_fields(self):
        r = ExecResult(rc=0, stdout="hi", stderr="")
        assert r.rc == 0
        assert r.stdout == "hi"
        assert r.stderr == ""

    def test_ok_property_true_for_zero_rc(self):
        assert ExecResult(rc=0, stdout="", stderr="").ok is True

    def test_ok_property_false_for_nonzero_rc(self):
        assert ExecResult(rc=1, stdout="", stderr="").ok is False
        assert ExecResult(rc=-1, stdout="", stderr="").ok is False

    def test_frozen(self):
        r = ExecResult(rc=0, stdout="", stderr="")
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.rc = 1  # type: ignore[misc]

    def test_field_names(self):
        names = {f.name for f in dataclasses.fields(ExecResult)}
        assert names == {"rc", "stdout", "stderr"}


class TestHealthStatus:
    def test_minimal_construction(self):
        h = HealthStatus(available=True)
        assert h.available is True
        assert h.version is None
        assert h.detail is None

    def test_full_construction(self):
        h = HealthStatus(available=False, version="1.2.3", detail="boom")
        assert h.available is False
        assert h.version == "1.2.3"
        assert h.detail == "boom"

    def test_frozen(self):
        h = HealthStatus(available=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            h.available = False  # type: ignore[misc]


class TestExceptionHierarchy:
    def test_base_inherits_runtime_error(self):
        assert issubclass(TransportError, RuntimeError)

    def test_unavailable_is_transport_error(self):
        assert issubclass(TransportUnavailable, TransportError)

    def test_auth_is_transport_error(self):
        assert issubclass(TransportAuthError, TransportError)

    def test_timeout_is_transport_error(self):
        assert issubclass(TransportTimeoutError, TransportError)

    def test_subclasses_are_distinct(self):
        # Catching TransportAuthError must NOT swallow other subclasses
        # — this is the foundation of the "do not silently fall back on
        # auth" rule.
        assert not issubclass(TransportUnavailable, TransportAuthError)
        assert not issubclass(TransportAuthError, TransportUnavailable)
        assert not issubclass(TransportTimeoutError, TransportAuthError)


class TestABCEnforcement:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            Transport()  # type: ignore[abstract]

    def test_partial_implementation_still_abstract(self):
        class Partial(Transport):
            name = "partial"

            def health(self):
                return HealthStatus(available=True)

            # exec + stream missing — instantiation must still fail.

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]

    def test_full_implementation_instantiates(self):
        class Full(Transport):
            name = "full"

            def health(self):
                return HealthStatus(available=True)

            def exec(self, script, *, timeout=60, description="winpodx-exec"):
                return ExecResult(rc=0, stdout="", stderr="")

            def stream(self, script, on_progress, *, timeout=600, description="winpodx-stream"):
                return ExecResult(rc=0, stdout="", stderr="")

        Full()  # no raise
