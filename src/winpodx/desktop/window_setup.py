# SPDX-License-Identifier: MIT
"""Detached post-launch window setup for a RAIL app window.

Run as::

    python -m winpodx.desktop.window_setup <wm_class> [--icon PATH] [--uwp]

``rdp.launch_app`` spawns this **detached** (``start_new_session=True``) right
after starting the FreeRDP client, instead of running the same work on a daemon
thread. The daemon-thread approach only survived while the launching process
lived, so it silently did nothing for the common case -- an app started from
its ``.desktop`` menu entry (``winpodx app run``) is a short-lived process that
exits immediately, taking the daemon thread with it before the RAIL window ever
maps (#702, and the same reason #680's reaper moved into the long-lived tray).

As its own process this helper outlives the launcher and does two best-effort,
X11-only things once the window appears:

- ``_relist_uwp_taskbar`` (#472): clear FreeRDP's ``SKIP_TASKBAR`` / ``SKIP_PAGER``
  on a UWP RAIL window so it shows in the panel.
- ``_apply_window_icon`` (#702): stamp the app's own icon as ``_NET_WM_ICON`` so
  X11 DEs that don't match ``res_class`` -> ``.desktop`` (Cinnamon, GNOME-X11)
  stop showing FreeRDP's icon.

Both are clean no-ops off X11 / when their tools are missing.
"""

from __future__ import annotations

import argparse
import sys
import threading


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="winpodx.desktop.window_setup")
    parser.add_argument("wm_class", help="the /wm-class token (== .desktop StartupWMClass)")
    parser.add_argument("--icon", default=None, help="path to the app icon file (PNG/SVG)")
    parser.add_argument("--uwp", action="store_true", help="also clear UWP SKIP_TASKBAR")
    args = parser.parse_args(argv[1:])

    # Import lazily so `-m` startup stays cheap and this module carries no
    # import-time cost for the launcher.
    from winpodx.core.rdp import _apply_window_icon, _relist_uwp_taskbar

    threads: list[threading.Thread] = []
    if args.uwp:
        threads.append(threading.Thread(target=_relist_uwp_taskbar, args=(args.wm_class,)))
    if args.icon:
        threads.append(threading.Thread(target=_apply_window_icon, args=(args.wm_class, args.icon)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
