"""Tests for utils.pending — partial-install resume marker (v0.2.1)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from winpodx.utils import pending


@pytest.fixture
def patched_config_dir(tmp_path, monkeypatch):
    """Redirect pending._path() to tmp_path so each test is isolated."""
    monkeypatch.setattr("winpodx.utils.pending.config_dir", lambda: tmp_path)
    return tmp_path


class TestHasPending:
    def test_returns_false_when_file_missing(self, patched_config_dir):
        assert pending.has_pending() is False

    def test_returns_true_when_file_has_valid_step(self, patched_config_dir):
        (patched_config_dir / ".pending_setup").write_text("wait_ready\n", encoding="utf-8")
        assert pending.has_pending() is True

    def test_ignores_garbage_lines(self, patched_config_dir):
        # Empty / unknown step IDs are ignored — only valid step IDs count.
        (patched_config_dir / ".pending_setup").write_text("\n# comment\nbogus\n", encoding="utf-8")
        assert pending.has_pending() is False


class TestListPending:
    def test_returns_canonical_order(self, patched_config_dir):
        # Even if the file has them in random order, list_pending returns
        # them in the canonical (wait_ready, migrate, apply_fixes,
        # discovery) order. v0.2.2.1 added apply_fixes between migrate
        # and discovery.
        (patched_config_dir / ".pending_setup").write_text(
            "discovery\napply_fixes\nwait_ready\nmigrate\n", encoding="utf-8"
        )
        assert pending.list_pending() == [
            "wait_ready",
            "migrate",
            "apply_fixes",
            "discovery",
        ]

    def test_filters_unknown_steps(self, patched_config_dir):
        (patched_config_dir / ".pending_setup").write_text(
            "wait_ready\nbogus\ndiscovery\n", encoding="utf-8"
        )
        assert pending.list_pending() == ["wait_ready", "discovery"]


class TestRemoveStep:
    def test_removes_one_step_keeps_others(self, patched_config_dir):
        (patched_config_dir / ".pending_setup").write_text(
            "wait_ready\nmigrate\ndiscovery\n", encoding="utf-8"
        )
        pending.remove_step("migrate")
        assert pending.list_pending() == ["wait_ready", "discovery"]

    def test_deletes_file_when_last_step_removed(self, patched_config_dir):
        (patched_config_dir / ".pending_setup").write_text("wait_ready\n", encoding="utf-8")
        pending.remove_step("wait_ready")
        assert not (patched_config_dir / ".pending_setup").exists()


class TestResume:
    def test_no_op_when_no_pending(self, patched_config_dir):
        called = []
        pending.resume(printer=lambda s: called.append(s))
        assert called == []

    def test_runs_steps_in_canonical_order(self, patched_config_dir, monkeypatch):
        (patched_config_dir / ".pending_setup").write_text(
            "discovery\nwait_ready\nmigrate\n", encoding="utf-8"
        )

        with (
            patch(
                "winpodx.core.provisioner.wait_for_windows_responsive",
                return_value=True,
            ),
            patch(
                "winpodx.core.provisioner.apply_windows_runtime_fixes",
                return_value={
                    "max_sessions": "ok",
                    "rdp_timeouts": "ok",
                    "oem_runtime_fixes": "ok",
                    "multi_session": "ok",
                },
            ),
            patch("winpodx.core.discovery.discover_apps", return_value=[]),
            patch("winpodx.core.discovery.persist_discovered", return_value=[]),
        ):
            pending.resume(printer=lambda s: None)

        # All steps should be cleared on success.
        assert pending.list_pending() == []

    def test_stops_when_wait_ready_still_failing(self, patched_config_dir):
        (patched_config_dir / ".pending_setup").write_text(
            "wait_ready\nmigrate\ndiscovery\n", encoding="utf-8"
        )
        # Guest still booting → wait_ready returns False → migrate / discovery
        # should NOT run (they'd just fail too and waste time).
        with patch(
            "winpodx.core.provisioner.wait_for_windows_responsive",
            return_value=False,
        ):
            pending.resume(printer=lambda s: None)
        # All three steps still pending.
        assert pending.list_pending() == ["wait_ready", "migrate", "discovery"]
