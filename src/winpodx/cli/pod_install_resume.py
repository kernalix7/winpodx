"""Stub for `winpodx pod install-resume`. Phase 1 ships the CLI
surface; Phase 3 fills in the implementation. See
docs/design/AGENT_FIRST_INSTALL_DESIGN.md §"Host side: winpodx pod install-resume"."""

from __future__ import annotations

import argparse
import sys


def add_subcommand(pod_subparsers: argparse._SubParsersAction) -> None:
    """Register the install-resume subcommand on the pod parser."""
    p = pod_subparsers.add_parser(
        "install-resume",
        help="Retry a failed or incomplete guest install",
    )
    p.add_argument(
        "--non-interactive",
        action="store_true",
        help="No prompts; suppresses confirmation",
    )
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Suppress confirmation prompts (alias for --non-interactive)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-run even on already-complete install "
            "(override once-per-session-id auto-trigger guard)"
        ),
    )


def handle(args: argparse.Namespace) -> int:
    """Phase 3 implementation will POST /exec to agent and stream progress."""
    print(
        "install-resume: Phase 3 implementation pending. "
        "Design: docs/design/AGENT_FIRST_INSTALL_DESIGN.md",
        file=sys.stderr,
    )
    return 0
