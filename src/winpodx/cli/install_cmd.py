# SPDX-License-Identifier: MIT
"""Dispatch for ``winpodx install`` — install progress + storage state (0.6.0 item G).

Note: this is ``winpodx install <sub>`` (the top-level group), which is
distinct from ``winpodx app install`` (install a desktop entry for an app).
No collision: they are registered at different levels of the argparse tree.

Handler bodies live in their canonical locations:
  - ``install-status`` / ``install-resume`` : ``winpodx.cli.pod_install_status``
    and ``winpodx.cli.pod_install_resume``
  - ``grow-disk`` / ``disk-usage`` : ``winpodx.cli.pod`` (shared with the
    deprecated ``pod grow-disk`` / ``pod disk-usage`` aliases)

Keeping bodies in their original modules minimises churn.  This file is a
thin dispatcher only.
"""

from __future__ import annotations

import argparse
import sys


def handle_install_group(args: argparse.Namespace) -> None:
    """Route ``winpodx install <subcommand>`` to the appropriate handler."""
    cmd = args.install_command
    if cmd == "status":
        from winpodx.cli.pod_install_status import handle as _handle_status

        sys.exit(_handle_status(args))
    elif cmd == "resume":
        from winpodx.cli.pod_install_resume import handle as _handle_resume

        sys.exit(_handle_resume(args))
    elif cmd == "grow-disk":
        from winpodx.cli import pod as _pod

        _pod._grow_disk(
            target_size=getattr(args, "size", None),
            increment=getattr(args, "increment", None),
            extend_only=getattr(args, "extend_only", False),
            assume_yes=getattr(args, "yes", False),
        )
    elif cmd == "disk-usage":
        from winpodx.cli import pod as _pod

        _pod._disk_usage()
    else:
        print(
            "Usage: winpodx install {status|resume|grow-disk|disk-usage}",
            file=sys.stderr,
        )
        sys.exit(1)
