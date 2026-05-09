"""Tests for ``winpodx.core.install_state``.

See ``docs/design/AGENT_FIRST_INSTALL_DESIGN.md`` §"Guest install state
(host-side mirror)" for the contract this exercises.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from winpodx.core.agent import (
    AgentError,
    AgentUnavailableError,
    ExecResult,
)
from winpodx.core.config import Config
from winpodx.core.install_state import (
    PHASE_ORDER,
    GuestInstallState,
    _parse_markers_json,
    _state_cache_path,
    fetch_install_state,
)


@pytest.fixture
def cfg() -> Config:
    return Config()


class _FakeAgent:
    """Stand-in for AgentClient that scripts ``exec()`` responses.

    ``responses`` is a list of either ExecResult objects (returned in
    order) or Exception instances (raised in order).
    """

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    def exec(self, script: str, *, timeout: int = 60) -> ExecResult:
        self.calls.append(script)
        if not self.responses:
            raise AssertionError("FakeAgent exhausted: extra exec() call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_agent(monkeypatch: pytest.MonkeyPatch, fake: _FakeAgent) -> None:
    # AgentClient is imported lazily inside install_state's helpers, so
    # patch on the agent module itself rather than the import alias.
    monkeypatch.setattr(
        "winpodx.core.agent.AgentClient",
        lambda cfg: fake,
    )


# ---------------------------------------------------------------------------
# _parse_markers_json
# ---------------------------------------------------------------------------


class TestParseMarkersJson:
    def test_array_of_strings(self) -> None:
        raw = '["defender_exclusion.done","agent_ready.done"]'
        assert _parse_markers_json(raw) == ["agent_ready.done", "defender_exclusion.done"]

    def test_single_string_becomes_singleton(self) -> None:
        # PowerShell ConvertTo-Json on a 1-element pipeline emits a bare string.
        assert _parse_markers_json('"agent_ready.done"') == ["agent_ready.done"]

    def test_empty_array(self) -> None:
        assert _parse_markers_json("[]") == []

    def test_empty_string(self) -> None:
        assert _parse_markers_json("") == []

    def test_whitespace_only(self) -> None:
        assert _parse_markers_json("   \n") == []

    def test_malformed_json_returns_empty(self) -> None:
        assert _parse_markers_json("{not json") == []

    def test_dedupes_and_sorts(self) -> None:
        raw = '["b.done","a.done","b.done"]'
        assert _parse_markers_json(raw) == ["a.done", "b.done"]


# ---------------------------------------------------------------------------
# fetch_install_state — happy path / parsing
# ---------------------------------------------------------------------------


class TestFetchInstallStateAgent:
    def test_parses_markers_into_steps(self, monkeypatch: pytest.MonkeyPatch, cfg: Config) -> None:
        markers = ["defender_exclusion.done", "agent_ready.done"]
        fake = _FakeAgent(
            [
                ExecResult(rc=0, stdout=json.dumps(markers), stderr=""),
                ExecResult(rc=0, stdout="", stderr=""),  # no install_failure.json
            ]
        )
        _patch_agent(monkeypatch, fake)

        state = fetch_install_state(cfg)

        assert state.agent_reachable is True
        assert state.marker_state_cached is False
        assert state.overall_status == "running"
        assert len(state.steps) == len(PHASE_ORDER)

        by_name = {s.name: s for s in state.steps}
        assert by_name["defender_exclusion"].status == "done"
        assert by_name["agent_ready"].status == "done"

        # state_dir_ready is the next un-done step (in PHASE_ORDER) -> running.
        # defender_exclusion (done), state_dir_ready (running), token_staged (pending), ...
        assert by_name["state_dir_ready"].status == "running"
        assert by_name["token_staged"].status == "pending"
        assert by_name["install_complete"].status == "pending"

    def test_all_done_yields_overall_done(
        self, monkeypatch: pytest.MonkeyPatch, cfg: Config
    ) -> None:
        markers = [f"{name}.done" for _phase, name, _label in PHASE_ORDER]
        fake = _FakeAgent(
            [
                ExecResult(rc=0, stdout=json.dumps(markers), stderr=""),
                ExecResult(rc=0, stdout="", stderr=""),
            ]
        )
        _patch_agent(monkeypatch, fake)

        state = fetch_install_state(cfg)

        assert state.overall_status == "done"
        assert all(s.status == "done" for s in state.steps)
        assert state.failure is None

    def test_failure_dict_passthrough(self, monkeypatch: pytest.MonkeyPatch, cfg: Config) -> None:
        markers = [
            "defender_exclusion.done",
            "state_dir_ready.done",
            "token_staged.done",
            "agent_ready.done",
            "rdprrap_installed.done",
        ]
        failure = {
            "session_id": "abcd1234-5678",
            "failed_step": "rdprrap_installed",
            "phase": 2,
            "attempt": 3,
            "max_attempts": 3,
            "exit_code": 1,
            "error_class": "rdprrap_install_failed",
            "error_summary": "rdprrap-install.exe exited 1",
            "timestamp_utc": "2026-05-08T09:17:51Z",
        }
        fake = _FakeAgent(
            [
                ExecResult(rc=0, stdout=json.dumps(markers), stderr=""),
                ExecResult(rc=0, stdout=json.dumps(failure), stderr=""),
            ]
        )
        _patch_agent(monkeypatch, fake)

        state = fetch_install_state(cfg)

        assert state.failure == failure
        assert state.overall_status == "failed"
        by_name = {s.name: s for s in state.steps}
        assert by_name["rdprrap_installed"].status == "failed"
        assert by_name["rdprrap_installed"].attempt == 3

    def test_malformed_failure_json_drops_silently(
        self, monkeypatch: pytest.MonkeyPatch, cfg: Config
    ) -> None:
        markers = ["defender_exclusion.done"]
        fake = _FakeAgent(
            [
                ExecResult(rc=0, stdout=json.dumps(markers), stderr=""),
                ExecResult(rc=0, stdout="{not valid json", stderr=""),
            ]
        )
        _patch_agent(monkeypatch, fake)

        state = fetch_install_state(cfg)

        assert state.failure is None
        # Without a failure dict, overall_status follows marker logic.
        assert state.overall_status == "running"


# ---------------------------------------------------------------------------
# fetch_install_state — cache fallback / cache write
# ---------------------------------------------------------------------------


class TestFetchInstallStateCache:
    def test_agent_unreachable_reads_cache(
        self, monkeypatch: pytest.MonkeyPatch, cfg: Config
    ) -> None:
        # Pre-populate cache.
        cache = _state_cache_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {
                    "session_id": "cached-session-1",
                    "overall_status": "running",
                    "elapsed_seconds": 42.0,
                    "agent_reachable": True,
                    "marker_state_cached": False,
                    "steps": [
                        {
                            "phase": 0,
                            "name": "defender_exclusion",
                            "status": "done",
                            "elapsed_seconds": 12.0,
                            "attempt": 1,
                        },
                    ],
                    "failure": None,
                }
            ),
            encoding="utf-8",
        )

        fake = _FakeAgent([AgentUnavailableError("connect refused")])
        _patch_agent(monkeypatch, fake)

        state = fetch_install_state(cfg)

        assert state.agent_reachable is False
        assert state.marker_state_cached is True
        assert state.session_id == "cached-session-1"
        assert len(state.steps) == 1
        assert state.steps[0].name == "defender_exclusion"

    def test_agent_unreachable_no_cache_returns_unknown(
        self, monkeypatch: pytest.MonkeyPatch, cfg: Config
    ) -> None:
        # Cache file deliberately absent (XDG dirs are isolated by conftest).
        assert not _state_cache_path().exists()

        fake = _FakeAgent([AgentUnavailableError("connect refused")])
        _patch_agent(monkeypatch, fake)

        state = fetch_install_state(cfg)

        assert state.overall_status == "unknown"
        assert state.agent_reachable is False
        assert state.marker_state_cached is False
        assert state.steps == []
        assert state.failure is None

    def test_unexpected_exception_falls_back_to_cache(
        self, monkeypatch: pytest.MonkeyPatch, cfg: Config
    ) -> None:
        # Non-AgentError exception still must not crash the call.
        cache = _state_cache_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps(
                {
                    "session_id": None,
                    "overall_status": "running",
                    "elapsed_seconds": 0.0,
                    "agent_reachable": True,
                    "marker_state_cached": False,
                    "steps": [],
                    "failure": None,
                }
            ),
            encoding="utf-8",
        )

        fake = _FakeAgent([RuntimeError("kaboom")])
        _patch_agent(monkeypatch, fake)

        state = fetch_install_state(cfg)

        assert state.agent_reachable is False
        assert state.marker_state_cached is True

    def test_corrupt_cache_treated_as_missing(
        self, monkeypatch: pytest.MonkeyPatch, cfg: Config
    ) -> None:
        cache = _state_cache_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("not even close to json", encoding="utf-8")

        fake = _FakeAgent([AgentUnavailableError("nope")])
        _patch_agent(monkeypatch, fake)

        state = fetch_install_state(cfg)

        assert state.overall_status == "unknown"
        assert state.marker_state_cached is False

    def test_successful_fetch_writes_cache_with_mode_0600(
        self, monkeypatch: pytest.MonkeyPatch, cfg: Config
    ) -> None:
        markers = ["defender_exclusion.done"]
        fake = _FakeAgent(
            [
                ExecResult(rc=0, stdout=json.dumps(markers), stderr=""),
                ExecResult(rc=0, stdout="", stderr=""),
            ]
        )
        _patch_agent(monkeypatch, fake)

        cache = _state_cache_path()
        assert not cache.exists()

        fetch_install_state(cfg)

        assert cache.exists()
        mode = stat.S_IMODE(cache.stat().st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

        # Round-trip the persisted JSON to confirm it's parseable.
        payload = json.loads(cache.read_text(encoding="utf-8"))
        assert payload["agent_reachable"] is True
        assert payload["marker_state_cached"] is False
        assert any(
            s["name"] == "defender_exclusion" and s["status"] == "done" for s in payload["steps"]
        )

    def test_cache_path_respects_xdg_state_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        custom = tmp_path / "custom_state"
        monkeypatch.setenv("XDG_STATE_HOME", str(custom))

        path = _state_cache_path()
        assert path == custom / "winpodx" / "last_install_state.json"


# ---------------------------------------------------------------------------
# fetch_install_state — never raises
# ---------------------------------------------------------------------------


class TestFetchNeverRaises:
    @pytest.mark.parametrize(
        "exc",
        [
            AgentUnavailableError("refused"),
            AgentError("generic agent failure"),
            ValueError("unexpected"),
            OSError("disk error"),
        ],
    )
    def test_first_exec_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        cfg: Config,
        exc: Exception,
    ) -> None:
        fake = _FakeAgent([exc])
        _patch_agent(monkeypatch, fake)
        # Call must not propagate.
        state = fetch_install_state(cfg)
        assert isinstance(state, GuestInstallState)
        assert state.agent_reachable is False

    def test_garbage_marker_stdout(self, monkeypatch: pytest.MonkeyPatch, cfg: Config) -> None:
        fake = _FakeAgent(
            [
                ExecResult(rc=0, stdout="<<<not json>>>", stderr=""),
                ExecResult(rc=0, stdout="", stderr=""),
            ]
        )
        _patch_agent(monkeypatch, fake)
        state = fetch_install_state(cfg)
        # No markers parsed -> all steps pending -> first step is "running".
        assert state.agent_reachable is True
        assert state.steps[0].status == "running"
