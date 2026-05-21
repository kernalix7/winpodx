# SPDX-License-Identifier: MIT
"""Tests for ``core/agent_install_state.py``.

Covers marker primitives, ``RetryCounter``, the redactor, and
``write_install_failure``. See ``docs/design/AGENT_FIRST_INSTALL_DESIGN.md``
Section "Schemas" + "Security threat model" for the contract under test.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from winpodx.core.agent_install_state import (
    RetryCounter,
    atomic_write_marker,
    list_completed_steps,
    read_marker,
    redact_log_line,
    redact_payload,
    write_install_failure,
)

# ---------------------------------------------------------------------------
# Marker primitives
# ---------------------------------------------------------------------------


def test_atomic_write_marker_produces_empty_file(tmp_path: Path) -> None:
    target = tmp_path / "step1.done"
    atomic_write_marker(target)
    assert target.exists()
    assert target.is_file()
    assert target.stat().st_size == 0


def test_read_marker_true_for_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "ok.done"
    target.write_text("")
    assert read_marker(target) is True


def test_read_marker_false_for_missing_file(tmp_path: Path) -> None:
    assert read_marker(tmp_path / "nope.done") is False


def test_list_completed_steps_returns_sorted_names(tmp_path: Path) -> None:
    for name in ("zeta", "alpha", "beta"):
        (tmp_path / f"{name}.done").write_text("")
    (tmp_path / "ignored.txt").write_text("nope")
    (tmp_path / "subdir").mkdir()
    assert list_completed_steps(tmp_path) == ["alpha", "beta", "zeta"]


def test_list_completed_steps_missing_dir(tmp_path: Path) -> None:
    assert list_completed_steps(tmp_path / "does-not-exist") == []


def test_concurrent_atomic_writes_do_not_corrupt(tmp_path: Path) -> None:
    target = tmp_path / "race.done"
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(20):
                atomic_write_marker(target)
        except BaseException as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert target.is_file()
    assert target.stat().st_size == 0
    leftovers = [p for p in tmp_path.iterdir() if p.name != "race.done"]
    assert leftovers == []


# ---------------------------------------------------------------------------
# RetryCounter
# ---------------------------------------------------------------------------


def test_retry_counter_get_missing_file_returns_zero(tmp_path: Path) -> None:
    counter = RetryCounter(tmp_path / "retry_counts.json")
    assert counter.get("rdprrap_install") == 0
    assert counter.all() == {}


def test_retry_counter_increment_persists_across_reload(tmp_path: Path) -> None:
    path = tmp_path / "retry_counts.json"
    counter = RetryCounter(path)
    assert counter.increment("step_a") == 1
    assert counter.increment("step_a") == 2
    assert counter.increment("step_b") == 1

    reloaded = RetryCounter(path)
    assert reloaded.get("step_a") == 2
    assert reloaded.get("step_b") == 1
    assert reloaded.all() == {"step_a": 2, "step_b": 1}


def test_retry_counter_atomic_update_no_partial_state(tmp_path: Path) -> None:
    """Concurrent increments must always leave a fully-formed JSON file."""
    path = tmp_path / "retry_counts.json"
    counter = RetryCounter(path)
    counter.increment("seed")  # ensure file exists

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(50):
                counter.increment("hot")
        except BaseException as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # File must always be valid JSON; final count is at least seed entry.
    parsed = json.loads(path.read_text())
    assert isinstance(parsed, dict)
    assert parsed.get("seed") == 1
    assert parsed.get("hot", 0) >= 1  # racy increments may collapse, but no corruption
    leftovers = [p for p in tmp_path.iterdir() if p.name != "retry_counts.json"]
    assert leftovers == []


def test_retry_counter_corrupt_json_treated_as_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "retry_counts.json"
    path.write_text("{not json")
    counter = RetryCounter(path)
    with caplog.at_level("WARNING"):
        assert counter.get("anything") == 0
        assert counter.all() == {}
    assert any("corrupt JSON" in rec.message for rec in caplog.records)


def test_retry_counter_reset_only_one_step(tmp_path: Path) -> None:
    counter = RetryCounter(tmp_path / "retry_counts.json")
    counter.increment("a")
    counter.increment("a")
    counter.increment("b")
    counter.reset("a")
    assert counter.get("a") == 0
    assert counter.get("b") == 1


# ---------------------------------------------------------------------------
# Redactor
# ---------------------------------------------------------------------------


def test_redact_net_user_pattern() -> None:
    out = redact_log_line("net user kernalix7 SecretP@ss")
    assert out.endswith("<REDACTED>")
    assert "SecretP@ss" not in out
    assert "kernalix7" in out  # username is fine to keep


def test_redact_authorization_bearer_header() -> None:
    out = redact_log_line("Authorization: Bearer abc123def456")
    assert out.endswith("<REDACTED>")
    assert "abc123def456" not in out


def test_redact_authorization_bearer_case_insensitive() -> None:
    out = redact_log_line("authorization: bearer ZZZTOKENZZZ")
    assert "ZZZTOKENZZZ" not in out
    assert "<REDACTED>" in out


def test_redact_password_kv_pattern() -> None:
    out = redact_log_line("foo password=p@ssw0rd&other=keep")
    assert "p@ssw0rd" not in out
    assert "password=<REDACTED>" in out
    # We deliberately leave 'other=keep' alone.
    assert "other=keep" in out


def test_redact_token_kv_case_insensitive() -> None:
    out = redact_log_line("TOKEN=xyz extra")
    assert "xyz" not in out
    assert "TOKEN=<REDACTED>" in out


def test_redact_apikey_variants() -> None:
    for raw in ("apikey=hunter2", "ApiKey=hunter2", "API_KEY=hunter2"):
        out = redact_log_line(raw)
        assert "hunter2" not in out, raw


def test_redact_base64_blob_60_chars() -> None:
    blob = "A" * 60
    out = redact_log_line(f"prefix {blob} suffix")
    assert blob not in out
    assert "<BASE64-REDACTED>" in out


def test_redact_base64_boundary_40_chars() -> None:
    blob = "B" * 40
    out = redact_log_line(blob)
    assert out == "<BASE64-REDACTED>"


def test_redact_base64_below_threshold_passes_through() -> None:
    blob = "C" * 39
    assert redact_log_line(blob) == blob


def test_redact_multi_pattern_line() -> None:
    line = "Authorization: Bearer abc123 password=hunter2"
    out = redact_log_line(line)
    assert "abc123" not in out
    assert "hunter2" not in out
    assert out.count("<REDACTED>") == 2


def test_redact_empty_string() -> None:
    assert redact_log_line("") == ""


def test_redact_non_string_input_coerced() -> None:
    # Caller's contract says this shouldn't happen; we coerce defensively
    # rather than crash a logging path.
    out = redact_log_line(12345)  # type: ignore[arg-type]
    assert out == "12345"


def test_redact_payload_deep_walks_dict() -> None:
    payload = {
        "error_summary": "Authorization: Bearer abc123def456ghi789",
        "nested": {"hint": "password=hunter2"},
        "exit_code": 1,
    }
    out = redact_payload(payload)
    assert "abc123def456ghi789" not in out["error_summary"]
    assert "<REDACTED>" in out["nested"]["hint"]
    assert out["exit_code"] == 1
    # original is not mutated
    assert "abc123def456ghi789" in payload["error_summary"]


def test_redact_payload_deep_walks_list() -> None:
    payload = {
        "last_log_lines": [
            "net user kernalix7 P@ss",
            "ok line",
            {"deep": "TOKEN=xxx"},
        ],
    }
    out = redact_payload(payload)
    assert "P@ss" not in out["last_log_lines"][0]
    assert out["last_log_lines"][1] == "ok line"
    assert "xxx" not in out["last_log_lines"][2]["deep"]


# ---------------------------------------------------------------------------
# write_install_failure
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_FAILURE_SCHEMA = _REPO_ROOT / "docs" / "design" / "install_failure.schema.json"


def _valid_payload() -> dict:
    return {
        "session_id": "abcd1234-1111-2222-3333-444455556666",
        "failed_step": "multi_session_activate",
        "phase": 2,
        "attempt": 3,
        "max_attempts": 3,
        "exit_code": 1,
        "error_class": "rdprrap_activate_failed",
        "error_summary": "Authorization: Bearer abc123def456ghi789jkl012mno345",
        "timestamp_utc": "2026-05-08T09:17:51Z",
        "environment": {
            "windows_build": "10.0.26100.0",
            "disk_fs": "ntfs",
            "free_bytes": 12345678901,
            "ram_total_mb": 8192,
        },
        "last_log_lines": ["net user kernalix7 SecretP@ss"],
    }


def test_write_install_failure_happy_path(tmp_path: Path) -> None:
    out_path = tmp_path / "install_failure.json"
    write_install_failure(out_path, _valid_payload())
    written = json.loads(out_path.read_text())
    assert "abc123def456ghi789jkl012mno345" not in written["error_summary"]
    assert "SecretP@ss" not in written["last_log_lines"][0]
    assert written["session_id"] == _valid_payload()["session_id"]


def test_write_install_failure_rejects_missing_required_field(tmp_path: Path) -> None:
    payload = _valid_payload()
    del payload["session_id"]
    out_path = tmp_path / "install_failure.json"
    with pytest.raises(ValueError, match="session_id"):
        write_install_failure(out_path, payload)
    assert not out_path.exists()


def test_write_install_failure_no_schema_still_redacts(tmp_path: Path) -> None:
    out_path = tmp_path / "install_failure.json"
    payload = _valid_payload()
    write_install_failure(out_path, payload, schema_path=None)
    written = json.loads(out_path.read_text())
    assert "<REDACTED>" in written["error_summary"]


def test_write_install_failure_rejects_non_dict(tmp_path: Path) -> None:
    out_path = tmp_path / "install_failure.json"
    with pytest.raises(ValueError):
        write_install_failure(out_path, "not a dict")  # type: ignore[arg-type]
    assert not out_path.exists()


def test_write_install_failure_full_schema_happy_path(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    if not _INSTALL_FAILURE_SCHEMA.is_file():
        pytest.skip("install_failure.schema.json not present in this checkout")
    out_path = tmp_path / "install_failure.json"
    write_install_failure(out_path, _valid_payload(), schema_path=_INSTALL_FAILURE_SCHEMA)
    written = json.loads(out_path.read_text())
    assert "<REDACTED>" in written["error_summary"]


def test_write_install_failure_full_schema_rejects_unknown_field(tmp_path: Path) -> None:
    pytest.importorskip("jsonschema")
    if not _INSTALL_FAILURE_SCHEMA.is_file():
        pytest.skip("install_failure.schema.json not present in this checkout")
    payload = _valid_payload()
    payload["surprise_field"] = "boom"
    out_path = tmp_path / "install_failure.json"
    with pytest.raises(ValueError):
        write_install_failure(out_path, payload, schema_path=_INSTALL_FAILURE_SCHEMA)
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# Hypothesis property test
# ---------------------------------------------------------------------------


def test_redact_round_trip_invariant() -> None:
    pytest.importorskip("hypothesis")
    from hypothesis import given, settings  # type: ignore[import-not-found]
    from hypothesis import strategies as st  # type: ignore[import-not-found]

    # Substring patterns whose presence would mean the redactor missed a
    # leak. We deliberately do NOT include the "kv secret" pattern here:
    # the `password=`/`token=`/`apikey=` rule replaces only the value, so
    # the literal "password=" still appears in output by design.
    leak_patterns = [
        re.compile(r"net user\s+\S+\s+(?!<REDACTED>)\S+", re.IGNORECASE),
        re.compile(r"Authorization:\s*Bearer\s+(?!<REDACTED>)\S+", re.IGNORECASE),
        re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])"),
    ]

    @given(st.text(max_size=400))
    @settings(max_examples=200, deadline=None)
    def _check(line: str) -> None:
        out = redact_log_line(line)
        for pat in leak_patterns:
            # Allow the pattern only if it matches a literal redaction marker
            # we put in ourselves.
            for match in pat.finditer(out):
                assert "<REDACTED>" in match.group(0) or "<BASE64-REDACTED>" in match.group(0), (
                    f"leak {match.group(0)!r} survived redaction of {line!r} -> {out!r}"
                )

    _check()
