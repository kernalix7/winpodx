# SPDX-License-Identifier: MIT
"""Tests for RDP process tracking."""

from __future__ import annotations

import os

from winpodx.core.process import (
    TrackedProcess,
    _cmdline_is_freerdp,
    kill_session,
    list_active_sessions,
)


class TestCmdlineIsFreerdp:
    def test_argv0_xfreerdp(self):
        assert _cmdline_is_freerdp(b"/usr/bin/xfreerdp3\0/v:host\0")

    def test_argv0_flatpak_freerdp(self):
        assert _cmdline_is_freerdp(b"flatpak\0run\0com.freerdp.FreeRDP\0/v:host\0")

    def test_argv0_bwrap_wrapped_xfreerdp(self):
        # `flatpak run com.freerdp.FreeRDP` re-execs (same PID) into
        # `bwrap ... -- xfreerdp ...`; the wrapped client must still count or
        # list_active_sessions() unlinks a live session's .cproc.
        assert _cmdline_is_freerdp(
            b"/usr/bin/bwrap\0--args\0072\0--\0xfreerdp\0/v:127.0.0.1:3390\0"
        )

    def test_argv0_bwrap_wrapped_flatpak_appid(self):
        assert _cmdline_is_freerdp(b"bwrap\0--\0com.freerdp.FreeRDP\0/v:host\0")

    def test_bwrap_without_freerdp_rejected(self):
        # A bwrap sandbox around something else must not be adopted.
        assert not _cmdline_is_freerdp(b"/usr/bin/bwrap\0--\0sleep\0900\0")

    def test_freerdp_only_in_later_argv_rejected(self):
        # Regression: "freerdp" in a later arg must not match.
        assert not _cmdline_is_freerdp(
            b"/usr/bin/python3\0-m\0pytest\0--deselect=test_freerdp_pid\0"
        )

    def test_freerdp_only_in_argv0_path_component_rejected(self):
        assert not _cmdline_is_freerdp(b"/home/user/freerdp-notes/run.sh\0")


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

    def test_lists_live_freerdp(self, tmp_path, monkeypatch):
        monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
        monkeypatch.setattr("winpodx.core.process._pid_alive", lambda pid: True)
        monkeypatch.setattr("winpodx.core.process.is_freerdp_pid", lambda pid: True)
        cproc = tmp_path / "Notepad.cproc"
        cproc.write_text("4242")
        sessions = list_active_sessions()
        assert [(s.app_name, s.pid) for s in sessions] == [("Notepad", 4242)]
        assert cproc.exists()

    def test_keeps_live_but_unrecognized_cproc(self, tmp_path, monkeypatch):
        # Regression: a live PID we fail to recognise as FreeRDP (a new sandbox
        # wrapper, or an old reader meeting a newly-wrapped client) must NOT be
        # listed -- but its .cproc must NOT be deleted either. A reader deleting
        # a live session's tracking file was the root cause of sessions
        # vanishing / Terminate finding nothing.
        monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
        monkeypatch.setattr("winpodx.core.process._pid_alive", lambda pid: True)
        monkeypatch.setattr("winpodx.core.process.is_freerdp_pid", lambda pid: False)
        cproc = tmp_path / "app.cproc"
        cproc.write_text("4242")
        sessions = list_active_sessions()
        assert sessions == []
        assert cproc.exists()  # NOT deleted -- live PID, just unrecognised


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

    def test_signals_live_freerdp_and_removes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
        monkeypatch.setattr("winpodx.core.process._pid_alive", lambda pid: True)
        monkeypatch.setattr("winpodx.core.process.is_freerdp_pid", lambda pid: True)
        signalled = {}
        monkeypatch.setattr(
            "winpodx.core.process.os.kill",
            lambda pid, sig: signalled.update(pid=pid, sig=sig),
        )
        cproc = tmp_path / "app.cproc"
        cproc.write_text("4242")
        assert kill_session("app") is True
        assert signalled["pid"] == 4242
        assert not cproc.exists()

    def test_reused_pid_not_signalled(self, tmp_path, monkeypatch):
        # PID-reuse guard: a live PID that is NOT our FreeRDP client must never
        # be SIGTERM'd (we'd kill an innocent process). Just drop the stale file.
        monkeypatch.setattr("winpodx.core.process.runtime_dir", lambda: tmp_path)
        monkeypatch.setattr("winpodx.core.process._pid_alive", lambda pid: True)
        monkeypatch.setattr("winpodx.core.process.is_freerdp_pid", lambda pid: False)
        calls = []
        monkeypatch.setattr("winpodx.core.process.os.kill", lambda pid, sig: calls.append(pid))
        cproc = tmp_path / "app.cproc"
        cproc.write_text("4242")
        assert kill_session("app") is False
        assert calls == []
        assert not cproc.exists()
