# SPDX-License-Identifier: MIT
"""Tests for reverse_open.lifecycle's pid-file and signalling layer.

No process is ever forked or signalled for real: ``os.kill`` is patched at the
module's lookup site so the tests assert the exact (pid, signal) pairs the code
would have sent, and the sleep in the SIGTERM grace loop is stubbed so the
timeout paths run instantly.
"""

from __future__ import annotations

import errno
import os
import signal
from pathlib import Path

import pytest

from winpodx.reverse_open import lifecycle
from winpodx.reverse_open.lifecycle import (
    DaemonPaths,
    ListenerStartFailed,
    _daemon_main,
    _pid_alive,
    _read_pid_file,
    _write_pid_file,
    is_listener_running,
    reload_apps_db,
    start_listener,
    stop_listener,
)


@pytest.fixture
def paths(tmp_path: Path) -> DaemonPaths:
    return DaemonPaths(pid_file=tmp_path / "run" / "reverse-open.pid", log_file=tmp_path / "l.log")


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(lifecycle.time, "sleep", lambda _s: None)


def _fake_kill(recorder: list, *, alive: set[int] | None = None, errno_for_signal=None):
    alive = alive if alive is not None else set()

    def kill(pid: int, sig: int) -> None:
        recorder.append((pid, sig))
        if sig == 0:
            if pid not in alive:
                raise OSError(errno.ESRCH, "no such process")
            return
        if errno_for_signal is not None:
            raise OSError(errno_for_signal, "signal failed")

    return kill


# --- pid file primitives --------------------------------------------------


def test_pid_file_is_written_private_and_read_back(paths: DaemonPaths) -> None:
    _write_pid_file(paths.pid_file, 4321)

    assert _read_pid_file(paths.pid_file) == 4321
    assert oct(paths.pid_file.stat().st_mode & 0o777) == "0o600"


def test_pid_file_write_leaves_no_temp_behind(paths: DaemonPaths) -> None:
    _write_pid_file(paths.pid_file, 11)

    assert list(paths.pid_file.parent.iterdir()) == [paths.pid_file]


def test_pid_file_write_replaces_a_previous_value(paths: DaemonPaths) -> None:
    _write_pid_file(paths.pid_file, 11)
    _write_pid_file(paths.pid_file, 22)

    assert _read_pid_file(paths.pid_file) == 22


def test_missing_pid_file_reads_as_none(paths: DaemonPaths) -> None:
    assert _read_pid_file(paths.pid_file) is None


@pytest.mark.parametrize("body", ["", "   ", "not-a-pid", "12x", "0", "-4"])
def test_malformed_pid_file_reads_as_none(paths: DaemonPaths, body: str) -> None:
    paths.pid_file.parent.mkdir(parents=True)
    paths.pid_file.write_text(body, encoding="ascii")

    assert _read_pid_file(paths.pid_file) is None


# --- _pid_alive -----------------------------------------------------------


def test_current_process_is_alive() -> None:
    assert _pid_alive(os.getpid()) is True


@pytest.mark.parametrize("pid", [0, -1])
def test_non_positive_pid_is_never_alive(pid: int) -> None:
    assert _pid_alive(pid) is False


def test_esrch_means_dead(monkeypatch) -> None:
    monkeypatch.setattr(
        lifecycle.os, "kill", _fake_kill([], alive=set())
    )  # empty alive set -> ESRCH

    assert _pid_alive(999999) is False


def test_eperm_means_alive_but_foreign(monkeypatch) -> None:
    def kill(_pid, _sig):
        raise OSError(errno.EPERM, "not yours")

    monkeypatch.setattr(lifecycle.os, "kill", kill)

    assert _pid_alive(1) is True


def test_unexpected_oserror_propagates(monkeypatch) -> None:
    def kill(_pid, _sig):
        raise OSError(errno.EIO, "io error")

    monkeypatch.setattr(lifecycle.os, "kill", kill)

    with pytest.raises(OSError):
        _pid_alive(1)


# --- is_listener_running --------------------------------------------------


def test_running_daemon_reports_its_pid(paths: DaemonPaths, monkeypatch) -> None:
    _write_pid_file(paths.pid_file, 555)
    monkeypatch.setattr(lifecycle.os, "kill", _fake_kill([], alive={555}))

    assert is_listener_running(paths) == 555


def test_no_pid_file_means_not_running(paths: DaemonPaths) -> None:
    assert is_listener_running(paths) is None


def test_stale_pid_file_is_reported_down_and_removed(paths: DaemonPaths, monkeypatch) -> None:
    _write_pid_file(paths.pid_file, 4242)
    monkeypatch.setattr(lifecycle.os, "kill", _fake_kill([], alive=set()))

    assert is_listener_running(paths) is None
    assert paths.pid_file.exists() is False


# --- stop_listener --------------------------------------------------------


def test_stop_is_a_noop_when_nothing_runs(paths: DaemonPaths) -> None:
    sent: list = []
    assert stop_listener(paths) is False
    assert sent == []


def test_stop_sends_sigterm_and_clears_the_pid_file(
    paths: DaemonPaths, monkeypatch, no_sleep
) -> None:
    _write_pid_file(paths.pid_file, 777)
    sent: list = []
    alive = {777}

    def kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        if sig == 0:
            if pid not in alive:
                raise OSError(errno.ESRCH, "gone")
            return
        if sig == signal.SIGTERM:
            alive.discard(pid)

    monkeypatch.setattr(lifecycle.os, "kill", kill)

    assert stop_listener(paths) is True
    assert (777, signal.SIGTERM) in sent
    assert (777, signal.SIGKILL) not in sent
    assert paths.pid_file.exists() is False


def test_stop_escalates_to_sigkill_when_the_daemon_ignores_sigterm(
    paths: DaemonPaths, monkeypatch, no_sleep
) -> None:
    _write_pid_file(paths.pid_file, 888)
    sent: list = []
    monkeypatch.setattr(lifecycle.os, "kill", _fake_kill(sent, alive={888}))

    assert stop_listener(paths, grace_seconds=0.0) is True
    assert (888, signal.SIGKILL) in sent
    assert paths.pid_file.exists() is False


def test_stop_handles_a_daemon_that_died_between_probe_and_signal(
    paths: DaemonPaths, monkeypatch, no_sleep
) -> None:
    _write_pid_file(paths.pid_file, 999)
    first = {"probe": True}

    def kill(pid: int, sig: int) -> None:
        if sig == 0 and first["probe"]:
            first["probe"] = False
            return
        raise OSError(errno.ESRCH, "gone")

    monkeypatch.setattr(lifecycle.os, "kill", kill)

    assert stop_listener(paths) is False
    assert paths.pid_file.exists() is False


# --- reload_apps_db -------------------------------------------------------


def test_reload_sends_sighup_to_the_running_daemon(paths: DaemonPaths, monkeypatch) -> None:
    _write_pid_file(paths.pid_file, 4711)
    sent: list = []
    monkeypatch.setattr(lifecycle.os, "kill", _fake_kill(sent, alive={4711}))

    assert reload_apps_db(paths) is True
    assert (4711, signal.SIGHUP) in sent


def test_reload_is_a_noop_when_nothing_runs(paths: DaemonPaths) -> None:
    assert reload_apps_db(paths) is False


def test_reload_reports_false_when_the_daemon_vanished(paths: DaemonPaths, monkeypatch) -> None:
    _write_pid_file(paths.pid_file, 4711)
    monkeypatch.setattr(
        lifecycle.os, "kill", _fake_kill([], alive={4711}, errno_for_signal=errno.ESRCH)
    )

    assert reload_apps_db(paths) is False


def test_reload_propagates_an_unexpected_signal_error(paths: DaemonPaths, monkeypatch) -> None:
    _write_pid_file(paths.pid_file, 4711)
    monkeypatch.setattr(
        lifecycle.os, "kill", _fake_kill([], alive={4711}, errno_for_signal=errno.EPERM)
    )

    with pytest.raises(OSError):
        reload_apps_db(paths)


# --- DaemonPaths ----------------------------------------------------------


def test_default_paths_follow_xdg_runtime_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    default = DaemonPaths.default()

    assert default.pid_file == tmp_path / "winpodx" / "reverse-open.pid"
    assert default.log_file == tmp_path / "winpodx" / "reverse-open.log"


def test_default_paths_fall_back_to_run_user_when_xdg_is_unset(monkeypatch) -> None:
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    default = DaemonPaths.default()

    assert default.pid_file == Path(f"/run/user/{os.getuid()}") / "winpodx" / "reverse-open.pid"


def test_daemon_main_runs_listener_and_handles_signals(
    paths: DaemonPaths, monkeypatch, tmp_path: Path
) -> None:
    writes: list[tuple[int, bytes]] = []
    handlers: dict[int, object] = {}
    initial_database = object()
    reloaded_database = object()
    databases = [initial_database, reloaded_database]
    listener_instances = []
    pid_writes: list[tuple[Path, int]] = []

    def record_pid(path: Path, pid: int) -> None:
        pid_writes.append((path, pid))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(pid))

    class FakeListener:
        def __init__(self, _config, apps_db, _seen) -> None:
            self._apps_db = apps_db
            self.stopped = False
            listener_instances.append(self)

        def preflight(self) -> None:
            return None

        def stop(self) -> None:
            self.stopped = True

        def run_forever(self) -> None:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            handlers[signal.SIGHUP](signal.SIGHUP, None)

    monkeypatch.setattr(lifecycle, "Listener", FakeListener)
    monkeypatch.setattr(lifecycle.AppsDatabase, "load", lambda _path: databases.pop(0))
    monkeypatch.setattr(lifecycle, "SeenUUIDs", lambda _path: object())
    monkeypatch.setattr(lifecycle, "_write_pid_file", record_pid)
    monkeypatch.setattr(lifecycle.os, "open", lambda *_args: 40)
    monkeypatch.setattr(lifecycle.os, "dup2", lambda *_args: None)
    monkeypatch.setattr(lifecycle.os, "close", lambda _fd: None)
    monkeypatch.setattr(lifecycle.os, "chdir", lambda _path: None)
    monkeypatch.setattr(lifecycle.os, "getpid", lambda: 2468)
    monkeypatch.setattr(lifecycle.os, "write", lambda fd, data: writes.append((fd, data)))
    monkeypatch.setattr(
        lifecycle.signal, "signal", lambda sig, handler: handlers.update({sig: handler})
    )
    monkeypatch.setattr(lifecycle.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    config = lifecycle.ListenerConfig(incoming_dir=tmp_path / "incoming", share_roots={})

    with pytest.raises(SystemExit) as exc_info:
        _daemon_main(config, tmp_path / "apps.json", tmp_path / "seen", paths, 12)

    assert exc_info.value.code == 0
    assert writes == [(12, b"OK\n")]
    assert pid_writes == [(paths.pid_file, 2468)]
    assert listener_instances[0].stopped is True
    assert listener_instances[0]._apps_db is reloaded_database
    assert paths.pid_file.exists() is False


def test_daemon_main_reports_preflight_failure(
    paths: DaemonPaths, monkeypatch, tmp_path: Path
) -> None:
    writes: list[bytes] = []

    class FailingListener:
        def __init__(self, *_args) -> None:
            return None

        def preflight(self) -> None:
            raise RuntimeError("unsafe incoming directory")

    monkeypatch.setattr(lifecycle, "Listener", FailingListener)
    monkeypatch.setattr(lifecycle.AppsDatabase, "load", lambda _path: object())
    monkeypatch.setattr(lifecycle, "SeenUUIDs", lambda _path: object())
    monkeypatch.setattr(lifecycle.os, "open", lambda *_args: 40)
    monkeypatch.setattr(lifecycle.os, "dup2", lambda *_args: None)
    monkeypatch.setattr(lifecycle.os, "close", lambda _fd: None)
    monkeypatch.setattr(lifecycle.os, "chdir", lambda _path: None)
    monkeypatch.setattr(lifecycle.os, "write", lambda _fd, data: writes.append(data))
    monkeypatch.setattr(lifecycle.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    config = lifecycle.ListenerConfig(incoming_dir=tmp_path / "incoming", share_roots={})

    with pytest.raises(SystemExit) as exc_info:
        _daemon_main(config, tmp_path / "apps.json", tmp_path / "seen", paths, 12)

    assert exc_info.value.code == 1
    assert writes == [b"FAIL preflight: RuntimeError: unsafe incoming directory\n"]


def test_start_listener_returns_pid_after_ready_sentinel(
    paths: DaemonPaths, monkeypatch, tmp_path: Path
) -> None:
    closed: list[int] = []
    running = iter((None, 3210))
    monkeypatch.setattr(lifecycle, "is_listener_running", lambda _paths: next(running))
    monkeypatch.setattr(lifecycle.os, "pipe", lambda: (10, 11))
    monkeypatch.setattr(lifecycle.os, "fork", lambda: 123)
    monkeypatch.setattr(lifecycle.os, "close", closed.append)
    monkeypatch.setattr(lifecycle.os, "waitpid", lambda pid, options: (pid, options))
    monkeypatch.setattr(lifecycle.os, "read", lambda _fd, _size: b"OK\n")
    config = lifecycle.ListenerConfig(incoming_dir=tmp_path / "incoming", share_roots={})

    assert start_listener(config, tmp_path / "apps.json", tmp_path / "seen", paths) == 3210
    assert closed == [11, 10]
    assert config.incoming_dir.is_dir()


def test_start_listener_closes_pipe_when_first_fork_fails(
    paths: DaemonPaths, monkeypatch, tmp_path: Path
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(lifecycle, "is_listener_running", lambda _paths: None)
    monkeypatch.setattr(lifecycle.os, "pipe", lambda: (10, 11))
    monkeypatch.setattr(
        lifecycle.os, "fork", lambda: (_ for _ in ()).throw(OSError(errno.EAGAIN, "busy"))
    )
    monkeypatch.setattr(lifecycle.os, "close", closed.append)
    config = lifecycle.ListenerConfig(incoming_dir=tmp_path / "incoming", share_roots={})

    with pytest.raises(ListenerStartFailed, match="first fork failed"):
        start_listener(config, tmp_path / "apps.json", tmp_path / "seen", paths)

    assert closed == [10, 11]


@pytest.mark.parametrize(
    ("sentinel", "message"),
    [(b"", "exited before signalling ready"), (b"FAIL denied\n", "FAIL denied")],
)
def test_start_listener_rejects_failed_ready_sentinel(
    paths: DaemonPaths, monkeypatch, tmp_path: Path, sentinel: bytes, message: str
) -> None:
    monkeypatch.setattr(lifecycle, "is_listener_running", lambda _paths: None)
    monkeypatch.setattr(lifecycle.os, "pipe", lambda: (10, 11))
    monkeypatch.setattr(lifecycle.os, "fork", lambda: 123)
    monkeypatch.setattr(lifecycle.os, "close", lambda _fd: None)
    monkeypatch.setattr(lifecycle.os, "waitpid", lambda pid, options: (pid, options))
    monkeypatch.setattr(lifecycle.os, "read", lambda _fd, _size: sentinel)
    config = lifecycle.ListenerConfig(incoming_dir=tmp_path / "incoming", share_roots={})

    with pytest.raises(ListenerStartFailed, match=message):
        start_listener(config, tmp_path / "apps.json", tmp_path / "seen", paths)


def test_stop_propagates_unexpected_sigterm_error(paths: DaemonPaths, monkeypatch) -> None:
    _write_pid_file(paths.pid_file, 765)
    monkeypatch.setattr(
        lifecycle.os, "kill", _fake_kill([], alive={765}, errno_for_signal=errno.EPERM)
    )

    with pytest.raises(OSError):
        stop_listener(paths)


def test_stop_ignores_sigkill_error(paths: DaemonPaths, monkeypatch, no_sleep) -> None:
    _write_pid_file(paths.pid_file, 876)
    sent: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        if sig == signal.SIGKILL:
            raise OSError(errno.ESRCH, "gone")

    monkeypatch.setattr(lifecycle.os, "kill", kill)

    assert stop_listener(paths, grace_seconds=0.0) is True
    assert sent[-1] == (876, signal.SIGKILL)
