# SPDX-License-Identifier: MIT
"""Dispatch for ``winpodx guest`` — guest-side operations (0.6.0 item G).

Handler bodies live in ``winpodx.cli.pod`` (the original home); this
module is a thin dispatcher that imports and calls them.  Keeping the
bodies in ``pod.py`` minimises merge-conflict surface: only the dispatch
routing changes, not the implementation.

Old ``pod <x>`` invocations keep working through 0.6.x via deprecation
aliases registered in ``handle_pod``.  Both paths call the same private
``_<name>`` functions in ``pod.py``.
"""

from __future__ import annotations

import argparse
import sys


def handle_guest(args: argparse.Namespace) -> None:
    """Route ``winpodx guest <subcommand>`` to the shared handler in pod.py."""
    # Import lazily so the whole pod module isn't loaded on every CLI call.
    from winpodx.cli import pod as _pod

    cmd = args.guest_command
    if cmd == "apply-fixes":
        _pod._apply_fixes()
    elif cmd == "sync":
        _pod._sync_guest(force=getattr(args, "force", False))
    elif cmd == "sync-password":
        _pod._sync_password(getattr(args, "non_interactive", False))
    elif cmd == "multi-session":
        _pod._multi_session(args.action)
    elif cmd == "recover-oem":
        _pod._recover_oem()
    elif cmd == "resync-token":
        _pod._resync_token()
    else:
        print(
            "Usage: winpodx guest "
            "{apply-fixes|sync|sync-password|multi-session|recover-oem|resync-token}",
            file=sys.stderr,
        )
        sys.exit(1)
