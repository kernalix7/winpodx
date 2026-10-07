# SPDX-License-Identifier: MIT
"""Shared helper for launching the winpodx tray as a detached subprocess.

The tray icon is the only place where UNRESPONSIVE auto-recovery runs
(see ``desktop/tray.py``), so any winpodx entry point the user actually
exercises -- GUI window, ``winpodx`` CLI subcommand, the OEM-token tap
inside install.sh -- should make sure the tray is up. This module
centralises the spawn so the GUI + CLI dispatch + future entry points
all share the same pgrep / flock pattern.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


def _install_in_progress() -> bool:
    """Return True while ``install.sh`` or GUI setup is running.

    install.sh writes its own PID into the marker and removes it via
    EXIT/INT/TERM trap. We treat the marker as "live" only when both
    the file exists AND the recorded PID is still alive AND the marker
    isn't older than 2 h. A botched install that lost its trap (kernel
    panic, SIGKILL, etc.) would otherwise pin the marker forever and
    suppress recovery for every future winpodx invocation.
    """
    import time as _time

    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    marker = Path(xdg) / "winpodx" / ".install_in_progress"
    try:
        st = marker.stat()
    except OSError:
        return False
    # Stale after 2 h. Install.sh's slowest leg is the Windows ISO
    # download (~5-15 min on typical connections); 120 min is plenty
    # of headroom for the longest plausible run.
    if (_time.time() - st.st_mtime) > 7200:
        return False
    try:
        recorded_pid = int(marker.read_text().strip())
    except (OSError, ValueError):
        # Treat unreadable / malformed marker as alive to stay
        # conservative -- recovery suppression is the safer default
        # while install.sh might still be running.
        return True
    try:
        # signal 0 = "does this PID exist + are we allowed to signal?".
        os.kill(recorded_pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return True
    return True


def _remove_install_marker(marker: Path, owner: os.stat_result, contents: str) -> None:
    """Remove only the recorded file and contents, never a replacement owner."""
    try:
        if (
            os.path.samestat(owner, marker.stat())
            and marker.read_text(encoding="utf-8") == contents
        ):
            marker.unlink()
    except FileNotFoundError:
        # Another owner already removed it; there is nothing left to release.
        return


@contextmanager
def track_installation() -> Iterator[None]:
    """Publish setup activity, borrowing a live marker or owning a fresh one."""
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    marker = Path(xdg) / "winpodx" / ".install_in_progress"
    marker.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            owned = marker.open("x", encoding="utf-8")
        except FileExistsError:
            if _install_in_progress():
                yield
                return
            try:
                with marker.open(encoding="utf-8") as stale:
                    owner = os.fstat(stale.fileno())
                    contents = stale.read()
                    if not _install_in_progress():
                        _remove_install_marker(marker, owner, contents)
            except FileNotFoundError:
                continue
        else:
            break

    # Keep the file open so its inode cannot be reused before ownership cleanup.
    with owned:
        owner = os.fstat(owned.fileno())
        contents = str(os.getpid())
        try:
            owned.write(contents)
            owned.flush()
            yield
        finally:
            try:
                _remove_install_marker(marker, owner, contents)
            except OSError as exc:
                log.warning("Could not remove installation marker: %s", exc)


def _tray_already_running() -> bool:
    """Cheap pre-check via pgrep. Returns False when pgrep isn't usable."""
    try:
        result = subprocess.run(
            ["pgrep", "-fa", "winpodx tray"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def maybe_spawn_tray() -> bool:
    """Launch ``winpodx tray`` detached if not already running.

    Returns True on a fresh spawn, False when the tray was already up or
    the spawn failed. The caller is expected to be best-effort: a missing
    tray downgrades to "no auto-recovery on idle stall" but never breaks
    the GUI / CLI itself.
    """
    # Caller explicitly opted out of tray spawn via env var. uninstall.sh
    # sets this so the ``winpodx host-open stop-listener`` /
    # ``unregister-guest`` calls it runs to tear down reverse-open don't
    # auto-respawn a fresh tray right after the pkill in section 0a.
    if os.environ.get("WINPODX_NO_TRAY_SPAWN"):
        log.debug("WINPODX_NO_TRAY_SPAWN set; skipping tray auto-spawn")
        return False

    if _install_in_progress():
        log.debug("install.sh in progress; skipping tray auto-spawn")
        return False

    if _tray_already_running():
        return False

    cmd = shutil.which("winpodx")
    if cmd is None:
        # Source checkout fallback: ``python -m winpodx tray`` works as
        # long as PYTHONPATH already includes ``src/``.
        cmd = sys.executable
        args = [cmd, "-m", "winpodx", "tray"]
    else:
        args = [cmd, "tray"]

    try:
        subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=os.environ.copy(),
        )
    except (FileNotFoundError, OSError) as e:
        log.debug("Could not spawn tray subprocess: %s", e)
        return False
    return True
