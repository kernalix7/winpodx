# SPDX-License-Identifier: MIT
"""pkexec wrapper for the host-side fixes the wizard owns.

We assemble a single shell script that performs every selected fix in
one go, then hand it to ``pkexec`` as a single elevation. That keeps
the polkit prompt count to one per session even when multiple items
need attention.

The script is conservative:

- Re-checks state before mutating (so re-running the wizard against
  an already-fixed host is a no-op).
- Logs each action with a clear ``[wizard]`` prefix.
- Exits non-zero on the first unexpected failure -- partial fixes are
  fine but a hard failure stops the script so the user sees it.
"""

from __future__ import annotations

import os
import pwd
import shlex
import shutil
import subprocess
from collections.abc import Iterable

from winpodx.setup_wizard.host_state import HostState


class PkexecUnavailable(RuntimeError):
    """``pkexec`` binary not on PATH (probably no polkit installed)."""


class PkexecAuthDenied(RuntimeError):
    """User dismissed the polkit prompt (or auth failed)."""


class PkexecScriptFailed(RuntimeError):
    """The elevated script ran but returned non-zero."""


def _current_username() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _build_apply_script(items: Iterable[str], username: str) -> str:
    """Compose the bash payload that pkexec will run as root.

    Each item is rendered as a conditional block: re-check state, mutate
    if still needed, log either way.
    """
    user = shlex.quote(username)
    lines: list[str] = [
        "#!/usr/bin/env bash",
        "set -uo pipefail",
        "echo '[wizard] Running pkexec-elevated host setup'",
    ]

    selected = set(items)

    if "kvm-group-membership" in selected:
        lines += [
            "",
            "# kvm group membership -- only adds if user is missing.",
            f"if ! id -nG {user} | tr ' ' '\\n' | grep -qx kvm; then",
            f"    usermod -aG kvm {user}",
            f"    echo '[wizard] Added {username} to kvm group (log out + back in for it to take effect).'",
            "else",
            f"    echo '[wizard] {username} already in kvm group; skipping.'",
            "fi",
        ]

    if "subuid-entry" in selected:
        lines += [
            "",
            "# /etc/subuid -- rootless podman uid mapping.",
            f"if ! grep -q \"^{username}:\" /etc/subuid 2>/dev/null; then",
            f"    echo '{username}:100000:65536' >> /etc/subuid",
            f"    echo '[wizard] Added subuid entry for {username}.'",
            "else",
            f"    echo '[wizard] subuid entry for {username} already present; skipping.'",
            "fi",
        ]

    if "subgid-entry" in selected:
        lines += [
            "",
            "# /etc/subgid -- rootless podman gid mapping.",
            f"if ! grep -q \"^{username}:\" /etc/subgid 2>/dev/null; then",
            f"    echo '{username}:100000:65536' >> /etc/subgid",
            f"    echo '[wizard] Added subgid entry for {username}.'",
            "else",
            f"    echo '[wizard] subgid entry for {username} already present; skipping.'",
            "fi",
        ]

    if "kvm-module-persistence" in selected:
        lines += [
            "",
            "# Ensure kvm_intel / kvm_amd loads on every boot.",
            "if [ ! -e /etc/modules-load.d/kvm-winpodx.conf ]; then",
            "    {",
            "        # Pick the matching module for the host CPU vendor.",
            "        if grep -q GenuineIntel /proc/cpuinfo 2>/dev/null; then",
            "            echo kvm_intel",
            "        elif grep -q AuthenticAMD /proc/cpuinfo 2>/dev/null; then",
            "            echo kvm_amd",
            "        fi",
            "    } > /etc/modules-load.d/kvm-winpodx.conf",
            "    echo '[wizard] Wrote /etc/modules-load.d/kvm-winpodx.conf so kvm module persists across reboot.'",
            "else",
            "    echo '[wizard] /etc/modules-load.d/kvm-winpodx.conf already present; skipping.'",
            "fi",
        ]

    lines += [
        "",
        "echo '[wizard] Done.'",
    ]
    return "\n".join(lines) + "\n"


def apply_via_pkexec(state: HostState) -> None:
    """Run pkexec against the fixable items currently missing.

    Raises:
        PkexecUnavailable: pkexec binary not on PATH.
        PkexecAuthDenied:  polkit prompt dismissed or auth failed.
        PkexecScriptFailed: elevated script returned non-zero.

    Returns silently on success. ``HostState`` should be re-detected
    afterwards to verify the fixes landed.
    """
    items = state.missing_fixable
    if not items:
        return

    if not shutil.which("pkexec"):
        raise PkexecUnavailable("pkexec not found on PATH (install polkit / polkit-pkexec).")

    script = _build_apply_script(items, _current_username())

    # `pkexec bash -c <script>` runs the script as root in a single
    # auth session. The script is small enough to pass via argv.
    try:
        completed = subprocess.run(
            ["pkexec", "bash", "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PkexecUnavailable(str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise PkexecScriptFailed("pkexec script timed out after 120s") from exc

    # pkexec exit code 126 = auth denied; 127 = command not authorised /
    # not found. Anything else non-zero = script failed.
    if completed.returncode == 126:
        raise PkexecAuthDenied("Polkit prompt dismissed or authentication failed.")
    if completed.returncode == 127:
        raise PkexecUnavailable("pkexec refused to run the requested command.")
    if completed.returncode != 0:
        raise PkexecScriptFailed(
            f"Elevated script returned {completed.returncode}:\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
