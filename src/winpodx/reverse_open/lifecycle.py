# SPDX-License-Identifier: MIT
"""Daemonise the reverse-open listener via fork-fork-pipe.

The host-side listener runs as a background daemon per pod. We start
it from ``winpodx host-open start-listener`` (and, in Phase 2c+, from
``winpodx pod start``). Stop happens explicitly via
``winpodx host-open stop-listener`` or implicitly via the pid-file
becoming stale (process gone but file lingering).

The daemonisation pattern is classic Unix fork-fork-pipe:

1. Parent creates a pipe.
2. ``fork()``; child calls ``setsid()`` so it detaches from the
   parent's terminal session and never receives ``SIGHUP`` from the
   user's shell closing.
3. Child ``fork()``s again so the grandchild is no longer a session
   leader (can never acquire a controlling tty). First child exits
   immediately. Grandchild becomes the daemon.
4. Grandchild redirects stdin/stdout/stderr, writes its PID to the
   pid file, writes "OK\\n" to the pipe, and enters
   ``Listener.run_forever()``.
5. Parent reads from the pipe with a 5 s timeout. ``OK`` → success;
   anything else (EOF, timeout, error string) → failure.

The pid file lives under ``$XDG_RUNTIME_DIR/winpodx/reverse-open.pid``
with mode 0600. We use ``$XDG_RUNTIME_DIR`` because it's a tmpfs that
gets cleared on reboot, so a leftover pid file from a crash can't
falsely report a running daemon after the host reboots.

Signal handling inside the daemon:

- ``SIGTERM`` / ``SIGINT`` → graceful shutdown via
  :meth:`Listener.stop`; pid file is removed on exit.
- ``SIGHUP`` → reload the apps database from disk so a CLI ``refresh``
  takes effect without restarting the daemon.

See ``docs/design/REVERSE_OPEN_DESIGN.md`` §"Component contracts →
lifecycle.py".
"""

from __future__ import annotations

import errno
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from winpodx.reverse_open.apps_db import AppsDatabase
from winpodx.reverse_open.listener import Listener, ListenerConfig
from winpodx.reverse_open.seen_uuids import SeenUUIDs

logger = logging.getLogger(__name__)


_PID_FILENAME = "reverse-open.pid"
_LOG_FILENAME = "reverse-open.log"
_READY_TIMEOUT_SECS = 5.0


class ListenerStartFailed(RuntimeError):
    """Raised when the daemon couldn't start within the ready timeout."""


@dataclass(frozen=True)
class DaemonPaths:
    """Paths the lifecycle layer needs in one place — easier to mock in tests."""

    pid_file: Path
    log_file: Path

    @classmethod
    def default(cls) -> DaemonPaths:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        base = Path(runtime) / "winpodx"
        return cls(pid_file=base / _PID_FILENAME, log_file=base / _LOG_FILENAME)


# ----- pid-file primitives ----------------------------------------------------


def _write_pid_file(pid_file: Path, pid: int) -> None:
    """Atomically write ``pid`` to ``pid_file`` with mode 0600."""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = pid_file.with_suffix(pid_file.suffix + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, f"{pid}\n".encode("ascii"))
    finally:
        os.close(fd)
    os.replace(tmp, pid_file)


def _read_pid_file(pid_file: Path) -> int | None:
    """Return the PID from ``pid_file`` or ``None`` if absent / malformed."""
    try:
        text = pid_file.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID exists, regardless of owner.

    ``os.kill(pid, 0)`` is the canonical "does this PID exist" probe —
    sends no signal, just performs permission + existence checks. We
    treat ``EPERM`` as "alive but owned by someone else" (still a
    running process); ``ESRCH`` is "no such process".
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise
    return True


def is_listener_running(paths: DaemonPaths | None = None) -> int | None:
    """Return the running daemon's PID, or ``None`` if it's not up.

    Stale PID files (process gone but file lingering) are removed as
    a side effect so the next ``start_listener`` doesn't think a dead
    daemon is alive.
    """
    paths = paths or DaemonPaths.default()
    pid = _read_pid_file(paths.pid_file)
    if pid is None:
        return None
    if not _pid_alive(pid):
        try:
            paths.pid_file.unlink()
        except FileNotFoundError:
            pass
        return None
    return pid


# ----- daemon entry point -----------------------------------------------------


def _daemon_main(
    listener_config: ListenerConfig,
    apps_db_path: Path,
    seen_uuids_path: Path,
    paths: DaemonPaths,
    ready_fd: int,
) -> None:
    """Body of the grandchild process.

    The fork-fork-pipe parent reads from ``ready_fd``; this function
    must write ``"OK\\n"`` to it once the listener has finished
    pre-flight, and must close it on any startup error. Inside
    :meth:`Listener.run_forever` we treat any uncaught exception as a
    crash — exit non-zero so the pid file gets removed.
    """
    log_file = paths.log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)

    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    os.chdir("/")

    apps_db = AppsDatabase.load(apps_db_path)
    seen = SeenUUIDs(seen_uuids_path)
    listener = Listener(listener_config, apps_db, seen)

    try:
        listener.preflight()
    except Exception as exc:  # noqa: BLE001 - report and bail
        msg = f"FAIL preflight: {exc.__class__.__name__}: {exc}\n"
        try:
            os.write(ready_fd, msg.encode("utf-8", errors="replace"))
        except OSError:
            pass
        os.close(ready_fd)
        # os._exit bypasses Python's exception machinery (and any
        # pytest hooks the forked process inherited). sys.exit raises
        # SystemExit which can propagate back through the parent's
        # call stack in fork-without-exec scenarios.
        os._exit(1)

    _write_pid_file(paths.pid_file, os.getpid())

    # Wire signal handlers AFTER preflight + pid file write so a
    # signal between fork and these calls can't catch the daemon in a
    # half-initialised state.
    def _sigterm(_signum, _frame):
        logger.info("listener: SIGTERM received")
        listener.stop()

    def _sighup(_signum, _frame):
        logger.info("listener: SIGHUP — reloading apps database")
        new_db = AppsDatabase.load(apps_db_path)
        # Atomic swap — the listener holds a reference and reads it
        # at the top of every per-request handler.
        listener._apps_db = new_db  # noqa: SLF001 — controlled internal access

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)
    signal.signal(signal.SIGHUP, _sighup)

    try:
        os.write(ready_fd, b"OK\n")
    except OSError:
        pass
    finally:
        os.close(ready_fd)

    rc = 0
    try:
        listener.run_forever()
    except Exception:  # noqa: BLE001
        logger.exception("listener: unhandled exception in run_forever")
        rc = 2
    finally:
        try:
            paths.pid_file.unlink()
        except FileNotFoundError:
            pass
    # os._exit avoids dragging the grandchild's SystemExit back
    # through any inherited pytest / framework excepthooks. Anything
    # the daemon needed to persist already wrote to disk before this
    # point.
    os._exit(rc)


# ----- public API -------------------------------------------------------------


def start_listener(
    listener_config: ListenerConfig,
    apps_db_path: Path,
    seen_uuids_path: Path,
    paths: DaemonPaths | None = None,
) -> int:
    """Start the daemon. Returns its PID; raises on startup failure.

    Idempotent: if a live daemon is already recorded in the pid file,
    its PID is returned unchanged.
    """
    paths = paths or DaemonPaths.default()
    running = is_listener_running(paths)
    if running is not None:
        return running

    listener_config.incoming_dir.mkdir(parents=True, exist_ok=True)
    paths.pid_file.parent.mkdir(parents=True, exist_ok=True)

    read_fd, write_fd = os.pipe()
    try:
        first_pid = os.fork()
    except OSError as exc:
        os.close(read_fd)
        os.close(write_fd)
        raise ListenerStartFailed(f"first fork failed: {exc}") from exc

    if first_pid == 0:
        # First child — detach session, second fork, then exit.
        os.close(read_fd)
        try:
            os.setsid()
        except OSError:
            os.close(write_fd)
            os._exit(1)
        try:
            second_pid = os.fork()
        except OSError:
            os.close(write_fd)
            os._exit(1)
        if second_pid == 0:
            # Grandchild — the daemon body.
            _daemon_main(listener_config, apps_db_path, seen_uuids_path, paths, write_fd)
            os._exit(0)
        # First child exits immediately; the kernel re-parents the
        # grandchild to init/PID 1 so the parent doesn't have to wait
        # on it.
        os.close(write_fd)
        os._exit(0)

    # Parent — close the write end so the read EOFs if the grandchild
    # dies before sending the sentinel.
    os.close(write_fd)
    try:
        os.waitpid(first_pid, 0)
    except OSError:
        pass

    deadline = time.time() + _READY_TIMEOUT_SECS
    buf = b""
    while time.time() < deadline:
        try:
            chunk = os.read(read_fd, 4096)
        except OSError as exc:
            os.close(read_fd)
            raise ListenerStartFailed(f"pipe read failed: {exc}") from exc
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            break
        time.sleep(0.05)
    os.close(read_fd)

    if buf.startswith(b"OK"):
        # Wait briefly for the daemon to actually write its pid file.
        # The grandchild writes it BEFORE the OK sentinel, so a single
        # immediate read is enough in the happy path; the loop just
        # absorbs the rare scheduler delay.
        for _ in range(50):
            pid = is_listener_running(paths)
            if pid is not None:
                return pid
            time.sleep(0.02)
        raise ListenerStartFailed("daemon signalled OK but pid file never appeared")

    if not buf:
        raise ListenerStartFailed("daemon exited before signalling ready")
    raise ListenerStartFailed(buf.decode("utf-8", errors="replace").strip())


def stop_listener(
    paths: DaemonPaths | None = None,
    *,
    grace_seconds: float = 5.0,
) -> bool:
    """Stop the daemon if running. Returns ``True`` if we sent a signal.

    Sends ``SIGTERM`` first and waits up to ``grace_seconds`` for the
    pid file to disappear. If the daemon is still alive after the
    grace window, sends ``SIGKILL`` and gives it another second.
    """
    paths = paths or DaemonPaths.default()
    pid = is_listener_running(paths)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            try:
                paths.pid_file.unlink()
            except FileNotFoundError:
                pass
            return False
        raise

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if not _pid_alive(pid):
            try:
                paths.pid_file.unlink()
            except FileNotFoundError:
                pass
            return True
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.5)
    try:
        paths.pid_file.unlink()
    except FileNotFoundError:
        pass
    return True


def reload_apps_db(paths: DaemonPaths | None = None) -> bool:
    """Signal the running daemon to reload ``apps.json`` via SIGHUP.

    Returns ``True`` if a signal was sent. Used by ``host-open
    refresh`` to make a fresh manifest take effect without the user
    having to restart the daemon. No-op if the daemon isn't running.
    """
    paths = paths or DaemonPaths.default()
    pid = is_listener_running(paths)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGHUP)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        raise
    return True
