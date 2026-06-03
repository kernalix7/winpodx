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
