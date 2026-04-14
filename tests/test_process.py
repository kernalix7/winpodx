"""Tests for RDP process tracking."""

from __future__ import annotations

import os

from winpodx.core.process import TrackedProcess, kill_session, list_active_sessions


class TestTrackedProcess:
    def test_dead_pid_not_alive(self):
        proc = TrackedProcess(app_name="test", pid=99999999)
        assert not proc.is_alive

    def test_current_process_not_freerdp(self):
        proc = TrackedProcess(app_name="test", pid=os.getpid())
        assert not proc.is_alive


class TestListActiveSessions:
    def test_empty_when_no_runtime_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "winpodx.core.process.runtime_dir",
            lambda: tmp_path / "nonexistent",
        )
        assert list_active_sessions() == []

    def test_cleans_stale_cproc(self, tmp_path, monkeypatch):
        monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
        cproc = tmp_path / "stale-app.cproc"
        cproc.write_text("99999999")
        sessions = list_active_sessions()
        assert len(sessions) == 0
        assert not cproc.exists()

    def test_cleans_invalid_cproc(self, tmp_path, monkeypatch):
        monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
        cproc = tmp_path / "bad.cproc"
        cproc.write_text("not-a-number")
        sessions = list_active_sessions()
        assert len(sessions) == 0
        assert not cproc.exists()


class TestKillSession:
    def test_returns_false_no_pidfile(self, tmp_path, monkeypatch):
        monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
        assert kill_session("nonexistent") is False

    def test_returns_false_dead_pid(self, tmp_path, monkeypatch):
        monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
        cproc = tmp_path / "dead-app.cproc"
        cproc.write_text("99999999")
        assert kill_session("dead-app") is False
        assert not cproc.exists()
