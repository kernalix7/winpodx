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


def is_freerdp_pid(pid: int) -> bool:
    """Return True if the given PID is a live FreeRDP process we spawned.

    Single source of truth for PID-reuse detection. Accepts only ``freerdp``
    or ``xfreerdp`` in the cmdline (our own spawned binaries). Excludes
    ``winpodx`` because that substring can match unrelated CLI invocations.
    """
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False

    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().lower()
    except (OSError, PermissionError):
        return False

    return b"freerdp" in cmdline or b"xfreerdp" in cmdline


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
            proc = TrackedProcess(app_name=f.stem, pid=pid)
            if proc.is_alive:
                sessions.append(proc)
            else:
                f.unlink(missing_ok=True)
        except (ValueError, OSError):
            f.unlink(missing_ok=True)
    return sessions


def kill_session(app_name: str) -> bool:
    """Kill an active RDP session by app name."""
    pid_file = runtime_dir() / f"{app_name}.cproc"
    if not pid_file.exists():
        return False

    try:
        pid = int(pid_file.read_text().strip())

        # Verify it's actually a FreeRDP process before killing (PID reuse)
        if not is_freerdp_pid(pid):
            pid_file.unlink(missing_ok=True)
            return False

        os.kill(pid, signal.SIGTERM)
        pid_file.unlink(missing_ok=True)
        return True
    except (ValueError, ProcessLookupError, PermissionError) as e:
        log.warning("Failed to kill session %s: %s", app_name, e)
        pid_file.unlink(missing_ok=True)
        return False
