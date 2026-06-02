# SPDX-License-Identifier: MIT
"""``winpodx uninstall`` -- thin wrapper that exec's ``uninstall.sh``.

The canonical uninstall implementation lives in the top-level ``uninstall.sh``
bash script. The Python CLI's job is to (1) find that script regardless of
how winpodx was installed (curl / pip / deb / rpm / aur) and (2) hand off
via ``os.execvp`` so the bash process owns the terminal for the rest of
the run -- crucially this means uninstall keeps working even when the
Python install is half-broken.

See https://github.com/kernalix7/winpodx/issues/255 for the consolidation
rationale.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# Locations checked in priority order. The first existing path wins.
#
# Order rationale:
#   1. /usr/share/winpodx       -- deb / rpm / aur / system pip (--prefix=/usr)
#   2. /usr/local/share/winpodx -- pip install --prefix=/usr/local
#   3. ~/.local/bin/winpodx-app -- curl install bundle dir (script's own home)
#   4. ~/.local/share/winpodx   -- pip install --user shared-data
#   5. sys.prefix/share/winpodx -- venv pip install
#   6. dev-checkout repo root   -- running ``python -m winpodx uninstall`` from src
def _candidate_paths() -> list[Path]:
    paths = [
        Path("/usr/share/winpodx/uninstall.sh"),
        Path("/usr/local/share/winpodx/uninstall.sh"),
        Path.home() / ".local" / "bin" / "winpodx-app" / "uninstall.sh",
        Path.home() / ".local" / "share" / "winpodx" / "uninstall.sh",
        Path(sys.prefix) / "share" / "winpodx" / "uninstall.sh",
    ]
    try:
        paths.append(Path(__file__).resolve().parents[3] / "uninstall.sh")
    except IndexError:
        pass
    return paths


def handle_uninstall(args: argparse.Namespace) -> None:
    """Locate uninstall.sh and ``execvp`` it with the right flags."""
    for path in _candidate_paths():
        if path.is_file():
            argv = ["bash", str(path)]
            if getattr(args, "purge", False):
                argv.append("--purge")
            if getattr(args, "yes", False):
                argv.append("--yes")
            os.execvp("bash", argv)
            # Real execvp never returns; only reached when mocked in tests.
            return

    sys.exit(
        "uninstall.sh not found. Looked in:\n  - "
        + "\n  - ".join(str(p) for p in _candidate_paths())
        + "\n\nReinstall WinPodX, or fetch the script directly:\n"
        "  curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh "
        "| bash -s -- --yes"
    )
