# SPDX-License-Identifier: MIT
"""Process tracking for active RDP sessions.

Maintains .cproc files in the runtime directory to track which
Windows applications are currently running, compatible with winapps.
"""

from __future__ import annotations

import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path

from winpodx.utils.paths import runtime_dir

log = logging.getLogger(__name__)

# argv[0] basenames find_freerdp() may launch.
_FREERDP_ARGV0 = (
    b"xfreerdp",
    b"xfreerdp3",
    b"wlfreerdp",
    b"wlfreerdp3",
    b"sdl-freerdp",
    b"sdl-freerdp3",
)


def _cmdline_is_freerdp(cmdline: bytes) -> bool:
    """Return True if a ``/proc/<pid>/cmdline`` blob is a FreeRDP client.

    Only argv[0]'s basename (or ``flatpak run com.freerdp.FreeRDP``) counts;
    a blanket ``b"freerdp" in cmdline`` would adopt unrelated processes that
    merely mention freerdp in an argument or path.
    """
    if not cmdline:
        return False
    argv = cmdline.split(b"\0")
    prog = argv[0].rsplit(b"/", 1)[-1].lower()
    if prog in _FREERDP_ARGV0:
        return True
    if prog == b"flatpak":
        tail = [a.lower() for a in argv[1:] if a]
        return b"com.freerdp.freerdp" in tail
    if prog in (b"bwrap", b"bubblewrap"):
        # `flatpak run com.freerdp.FreeRDP` re-execs (same PID) into
        # `bwrap ... -- xfreerdp ...`, so the tracked PID's argv[0] becomes
        # bwrap. Recognise a freerdp client basename (or the Flatpak app id)
        # among bwrap's args so session tracking survives the sandbox --
        # otherwise list_active_sessions() unlinks a live session's .cproc
        # and the tray / GUI report no active sessions while apps are running.
        for a in argv[1:]:
            base = a.rsplit(b"/", 1)[-1].lower()
            if base in _FREERDP_ARGV0 or base == b"com.freerdp.freerdp":
                return True
        return False
    return False


def is_freerdp_pid(pid: int) -> bool:
    """Return True if the given PID is a live FreeRDP process we spawned.

    Single source of truth for PID-reuse detection.
    """
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False

    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, PermissionError):
        return False

    return _cmdline_is_freerdp(cmdline)


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID currently exists (any identity).

    Unlike :func:`is_freerdp_pid` this does NOT inspect the cmdline -- it is
    the test for "is the tracked PID gone?". Keeping the two separate is
    deliberate: a live PID we merely fail to *recognise* as FreeRDP (a new
    sandbox wrapper, or version skew where an old reader meets a newly-wrapped
    client) must never cause a live session's ``.cproc`` to be deleted. Only a
    genuinely dead PID may be cleaned up.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another uid (defensive; shouldn't happen)
    except OSError:
        return False
    return True


@dataclass
class TrackedProcess:
    app_name: str
    pid: int

    @property
    def is_alive(self) -> bool:
        return is_freerdp_pid(self.pid)


def list_active_sessions() -> list[TrackedProcess]:
    """List all tracked active RDP sessions."""
    rd = runtime_dir()
    if not rd.exists():
        return []

    sessions: list[TrackedProcess] = []
    for f in rd.glob("*.cproc"):
        try:
            pid = int(f.read_text().strip())
        except (ValueError, OSError):
            f.unlink(missing_ok=True)  # corrupt / unreadable -> drop
            continue
        proc = TrackedProcess(app_name=f.stem, pid=pid)
        if proc.is_alive:
            sessions.append(proc)  # live FreeRDP -> list it
        elif not _pid_alive(pid):
            f.unlink(missing_ok=True)  # genuinely dead -> clean up
        # else: PID alive but not recognised as FreeRDP (PID reuse, or a
        # wrapper this version doesn't know yet) -> keep the file, don't list.
        # A reader must NEVER destroy a live session's tracking state; only a
        # dead PID is cleaned up here.
    return sessions


def kill_session(app_name: str) -> bool:
    """Terminate an active RDP session by app name (SIGTERM the tracked PID)."""
    pid_file = runtime_dir() / f"{app_name}.cproc"
    if not pid_file.exists():
        return False

    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return False

    if not _pid_alive(pid):
        pid_file.unlink(missing_ok=True)  # already gone -> clean up
        return False

    # PID-reuse guard: if the live PID was recycled into something that is not
    # our FreeRDP client, don't SIGTERM a stranger -- the real session is gone.
    if not is_freerdp_pid(pid):
        pid_file.unlink(missing_ok=True)
        return False

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as e:
        log.warning("Failed to kill session %s: %s", app_name, e)
        pid_file.unlink(missing_ok=True)
        return False

    pid_file.unlink(missing_ok=True)
    return True
