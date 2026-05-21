# SPDX-License-Identifier: MIT
"""Tests for ``winpodx.reverse_open.lifecycle``.

Daemonisation involves ``fork()`` + ``setsid()`` + writing to
``$XDG_RUNTIME_DIR``. The conftest's autouse fixture redirects
``XDG_RUNTIME_DIR`` to a per-test tmpdir, so these tests don't leak
processes or files across runs. Each test that spawns a daemon
registers a teardown that calls :func:`stop_listener` even on test
failure — leaving a daemon running across tests would surface as a
ghost FD inheritance bug in unrelated suites.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest

from winpodx.reverse_open.lifecycle import (
    DaemonPaths,
    ListenerStartFailed,
    _pid_alive,
    _read_pid_file,
    _write_pid_file,
    is_listener_running,
    reload_apps_db,
    start_listener,
    stop_listener,
)
from winpodx.reverse_open.listener import ListenerConfig

# --- pid file primitives ----------------------------------------------------


def test_write_pid_file_atomic_and_mode_0600(tmp_path: Path) -> None:
    pid_file = tmp_path / "x.pid"
    _write_pid_file(pid_file, 4242)
    assert _read_pid_file(pid_file) == 4242
    # Should be readable / writable only by the owner.
    mode = pid_file.stat().st_mode & 0o777
    assert mode == 0o600


def test_read_pid_file_missing_returns_none(tmp_path: Path) -> None:
    assert _read_pid_file(tmp_path / "nope") is None


def test_read_pid_file_malformed_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "x"
    p.write_text("not-a-number", encoding="ascii")
    assert _read_pid_file(p) is None


def test_pid_alive_for_current_process() -> None:
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_for_nonexistent_pid() -> None:
    # PID 0x7fffffff is almost certainly not alive on any Linux box.
    assert _pid_alive(0x7FFFFFFF) is False


def test_pid_alive_for_invalid_pid() -> None:
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False


# --- is_listener_running ----------------------------------------------------


def test_is_listener_running_clears_stale_pid(tmp_path: Path) -> None:
    paths = DaemonPaths(pid_file=tmp_path / "rev.pid", log_file=tmp_path / "rev.log")
    _write_pid_file(paths.pid_file, 0x7FFFFFFF)
    assert is_listener_running(paths) is None
    # And the stale file is removed as a side effect.
    assert not paths.pid_file.exists()


def test_is_listener_running_returns_pid_for_self(tmp_path: Path) -> None:
    paths = DaemonPaths(pid_file=tmp_path / "rev.pid", log_file=tmp_path / "rev.log")
    _write_pid_file(paths.pid_file, os.getpid())
    assert is_listener_running(paths) == os.getpid()


# --- daemon spawn / stop ----------------------------------------------------


@pytest.fixture
def daemon_paths(tmp_path: Path) -> DaemonPaths:
    return DaemonPaths(
        pid_file=tmp_path / "reverse-open.pid",
        log_file=tmp_path / "reverse-open.log",
    )


@pytest.fixture
def listener_cfg(tmp_path: Path) -> ListenerConfig:
    inc = tmp_path / "incoming"
    inc.mkdir()
    inc.chmod(0o700)
    return ListenerConfig(
        incoming_dir=inc,
        share_roots={"home": Path.home()},
        poll_interval=0.1,
    )


@pytest.fixture
def apps_db_path(tmp_path: Path) -> Path:
    p = tmp_path / "apps.json"
    p.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-05-11T00:00:00Z",
                "host": {"xdg_current_desktop": ""},
                "apps": [],
            }
        ),
        encoding="utf-8",
    )
    return p


def _wait_for_pid_disappears(paths: DaemonPaths, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_listener_running(paths) is None:
            return True
        time.sleep(0.05)
    return False


def test_start_and_stop_daemon(
    daemon_paths: DaemonPaths,
    listener_cfg: ListenerConfig,
    apps_db_path: Path,
    tmp_path: Path,
) -> None:
    seen = tmp_path / "seen.json"
    try:
        pid = start_listener(listener_cfg, apps_db_path, seen, daemon_paths)
        assert pid > 0
        assert is_listener_running(daemon_paths) == pid
        # PID file lives at the configured location.
        assert daemon_paths.pid_file.is_file()
        # Stop the daemon and confirm cleanup.
        sent = stop_listener(daemon_paths)
        assert sent is True
        assert _wait_for_pid_disappears(daemon_paths)
    finally:
        # Defensive: even if an assertion failed mid-way, make sure we
        # don't leak a process across tests.
        stop_listener(daemon_paths)


def test_start_listener_is_idempotent(
    daemon_paths: DaemonPaths,
    listener_cfg: ListenerConfig,
    apps_db_path: Path,
    tmp_path: Path,
) -> None:
    seen = tmp_path / "seen.json"
    try:
        pid1 = start_listener(listener_cfg, apps_db_path, seen, daemon_paths)
        pid2 = start_listener(listener_cfg, apps_db_path, seen, daemon_paths)
        assert pid1 == pid2
    finally:
        stop_listener(daemon_paths)


def test_start_listener_raises_on_preflight_failure(
    daemon_paths: DaemonPaths,
    tmp_path: Path,
    apps_db_path: Path,
) -> None:
    # Group-writable incoming dir → preflight refuses → daemon exits
    # with FAIL on the pipe → parent raises ListenerStartFailed.
    inc = tmp_path / "incoming"
    inc.mkdir()
    inc.chmod(0o770)
    cfg = ListenerConfig(
        incoming_dir=inc,
        share_roots={"home": Path.home()},
        poll_interval=0.1,
    )
    seen = tmp_path / "seen.json"
    with pytest.raises(ListenerStartFailed):
        start_listener(cfg, apps_db_path, seen, daemon_paths)
    # No pid file should have been written.
    assert not daemon_paths.pid_file.exists()


def test_stop_listener_returns_false_when_not_running(
    daemon_paths: DaemonPaths,
) -> None:
    assert stop_listener(daemon_paths) is False


def test_reload_apps_db_returns_false_when_not_running(
    daemon_paths: DaemonPaths,
) -> None:
    assert reload_apps_db(daemon_paths) is False


def test_reload_apps_db_sends_sighup_when_running(
    daemon_paths: DaemonPaths,
    listener_cfg: ListenerConfig,
    apps_db_path: Path,
    tmp_path: Path,
) -> None:
    seen = tmp_path / "seen.json"
    try:
        pid = start_listener(listener_cfg, apps_db_path, seen, daemon_paths)
        sent = reload_apps_db(daemon_paths)
        assert sent is True
        # Daemon stays alive after SIGHUP.
        assert _pid_alive(pid)
    finally:
        stop_listener(daemon_paths)


def test_daemon_processes_request_after_start(
    daemon_paths: DaemonPaths,
    apps_db_path: Path,
    tmp_path: Path,
) -> None:
    # Wire a kate-handler that points at /bin/true so spawn just succeeds.
    inc = tmp_path / "incoming"
    inc.mkdir()
    inc.chmod(0o700)
    home = tmp_path / "home"
    home.mkdir()
    target = home / "f.txt"
    target.write_text("x", encoding="utf-8")

    cfg = ListenerConfig(
        incoming_dir=inc,
        share_roots={"home": home},
        poll_interval=0.1,
    )

    apps_db_path.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-05-11T00:00:00Z",
                "host": {},
                "apps": [
                    {
                        "slug": "true",
                        "name": "true",
                        "comment": "",
                        "exec_argv": ["/bin/true", "%f"],
                        "icon_name": "",
                        "mime_types": ["text/plain"],
                        "desktop_file": "/x.desktop",
                        "is_default_for": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    seen = tmp_path / "seen.json"

    try:
        pid = start_listener(cfg, apps_db_path, seen, daemon_paths)
        # Write a request the daemon should accept and spawn.
        uid = uuid.uuid4().hex
        rel = target.resolve().relative_to(home.resolve())
        unc = "\\\\tsclient\\home\\" + str(rel).replace("/", "\\")
        (inc / f"{uid}.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "app": "true",
                    "path": unc,
                    "ts": "2026-05-11T00:00:00Z",
                    "pod_id": None,
                }
            ),
            encoding="utf-8",
        )
        # The daemon polls at 100 ms; give it up to 5 s to consume.
        deadline = time.time() + 5
        while time.time() < deadline:
            if not (inc / f"{uid}.json").exists():
                break
            time.sleep(0.1)
        assert not (inc / f"{uid}.json").exists(), "daemon did not process request"
        # Daemon still alive.
        assert _pid_alive(pid)
    finally:
        stop_listener(daemon_paths)
