"""Stub for `winpodx pod install-status`. Phase 1 ships the CLI
surface; Phase 3 fills in the implementation. See
docs/design/AGENT_FIRST_INSTALL_DESIGN.md §"Host side: winpodx pod install-status"."""

from __future__ import annotations

import argparse
import sys


def add_subcommand(pod_subparsers: argparse._SubParsersAction) -> None:
    """Register the install-status subcommand on the pod parser."""
    p = pod_subparsers.add_parser(
        "install-status",
        help="Show install step progress and last log lines",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Machine-parseable output (implied by --non-interactive)",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="Suppress ANSI colors",
    )
    p.add_argument(
        "--logs",
        action="store_true",
        help="Tail install log + container log interleaved",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="No prompts; implies --json",
    )


def handle(args: argparse.Namespace) -> int:
    """Phase 3 implementation will read GuestInstallState and format output."""
    if args.non_interactive:
        args.json = True
    print(
        "install-status: Phase 3 implementation pending. "
        "Design: docs/design/AGENT_FIRST_INSTALL_DESIGN.md",
        file=sys.stderr,
    )
    return 0
