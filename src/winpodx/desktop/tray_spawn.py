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
from pathlib import Path

log = logging.getLogger(__name__)


def _install_in_progress() -> bool:
    """Return True while ``install.sh`` is running.

    install.sh writes / removes the marker so the tray doesn't spawn
    during first-boot install -- ``[3/4]`` and ``[4/4]`` legitimately
    have RDP down for several minutes while Windows is in Sysprep and
    the OEM-scheduled reboot, and the tray's UNRESPONSIVE classifier
    would otherwise misread that as a stalled long-running guest and
    fire spurious recovery notifications.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return (Path(xdg) / "winpodx" / ".install_in_progress").exists()


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
