# SPDX-License-Identifier: MIT
"""Detach ``winpodx gui`` from the controlling terminal (#549).

Running ``winpodx gui`` from a terminal used to block the prompt for the
whole lifetime of the window (``app.exec()`` is a blocking event loop), so
the user couldn't keep using the shell until they closed the dashboard.

When launched interactively we instead re-spawn ``winpodx gui --foreground``
in a new session (``start_new_session=True``, stdio to /dev/null) and return
immediately — the same detach pattern the tray already uses
(``desktop/tray_spawn.maybe_spawn_tray``). The ``--foreground`` child runs the
real Qt loop and does NOT re-detach, so there is exactly one GUI process.

No PySide6 import here: this runs in the parent before Qt is touched.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


def should_detach_gui(*, foreground: bool) -> bool:
    """True when ``winpodx gui`` should re-spawn itself detached.

    Only detaches for an interactive terminal launch: ``--foreground`` (the
    re-spawned child, or an explicit debug run) and non-tty launches (.desktop
    autostart, the quick launcher's subprocess) run the Qt loop in place — they
    don't tie up a shell and their caller already owns the process lifecycle.
    """
    if foreground:
        return False
    try:
        return sys.stdout.isatty() or sys.stdin.isatty()
    except (ValueError, OSError):
        return False


def spawn_gui_detached() -> bool:
    """Re-spawn ``winpodx gui --foreground`` in a new session, detached.

    Returns True on a successful spawn, False otherwise. Best-effort: on
    failure the caller falls back to running the GUI in the foreground.
    """
    cmd = shutil.which("winpodx")
    if cmd is None:
        # Source-checkout fallback: ``python -m winpodx`` works as long as
        # PYTHONPATH already includes ``src/``.
        args = [sys.executable, "-m", "winpodx", "gui", "--foreground"]
    else:
        args = [cmd, "gui", "--foreground"]

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
        log.debug("Could not spawn detached GUI: %s", e)
        return False
    return True
