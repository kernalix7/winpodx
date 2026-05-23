# SPDX-License-Identifier: MIT
"""CLI entrypoint for the host-side setup wizard.

Usage:
    python -m winpodx.setup_wizard           # detect + prompt
    python -m winpodx.setup_wizard --status  # detect only, print report
    python -m winpodx.setup_wizard --apply   # detect + apply without prompt

The wizard is intended primarily for AppImage users on immutable
distros (Fedora Silverblue / Kinoite / Aeon, Steam Deck) who can't
``sudo dnf install``. On systems where ``install.sh`` ran (curl
one-liner / package manager install), the wizard's actions are
already in place and it'll report nothing to do.
"""

from __future__ import annotations

import argparse
import sys

from winpodx.setup_wizard.host_state import detect_host_state
from winpodx.setup_wizard.pkexec import (
    PkexecAuthDenied,
    PkexecScriptFailed,
    PkexecUnavailable,
    apply_via_pkexec,
)


def _print_status(state) -> None:  # type: ignore[no-untyped-def]
    print("=== winpodx host setup ===\n")

    def line(label: str, ok: bool, note: str = "") -> None:
        mark = "[OK]" if ok else "[--]"
        print(f"  {mark}  {label}" + (f"  ({note})" if note else ""))

    line("/dev/kvm present", state.dev_kvm_present, "host kernel exposes KVM")
    line("/dev/kvm readable by you", state.dev_kvm_readable, "rootless QEMU can open it")
    line("kvm group exists", state.kvm_group_exists)
    line("you are in kvm group", state.in_kvm_group, "log out + back in after fix")
    line("subuid entry for you", state.subuid_configured, "rootless podman uid mapping")
    line("subgid entry for you", state.subgid_configured, "rootless podman gid mapping")
    line("kvm module loads at boot", state.kvm_module_persistent, "persistence across reboots")

    print()
    if state.missing_fixable:
        print("Fixable via pkexec (one polkit prompt for all):")
        for item in state.missing_fixable:
            print(f"  - {item}")
    elif state.is_complete:
        print("Host is fully set up.")
    else:
        print("Some required state is missing but the wizard cannot fix it:")
        if not state.dev_kvm_present:
            print("  - /dev/kvm missing -- enable VT-x / AMD-V in BIOS, then")
            print("    `sudo modprobe kvm_intel` (or kvm_amd).")


def _confirm(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m winpodx.setup_wizard",
        description=(
            "Detect + fix host-side setup that winpodx needs but an "
            "AppImage cannot do from user space."
        ),
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print the host state report and exit. No mutation.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply via pkexec without prompting for confirmation.",
    )
    args = parser.parse_args(argv)

    state = detect_host_state()
    _print_status(state)

    if args.status:
        return 0 if state.is_complete else 1

    if not state.missing_fixable:
        return 0

    if not args.apply:
        if not _confirm("\nApply the above via pkexec?"):
            print("Skipped. Run again with --apply to skip the prompt.")
            return 1

    try:
        apply_via_pkexec(state)
    except PkexecUnavailable as e:
        print(f"\nError: {e}", file=sys.stderr)
        print(
            "Install polkit / polkit-pkexec and re-run, or perform the steps manually:\n"
            "  sudo usermod -aG kvm $USER\n"
            f"  echo '{state and ''}'...   # see /etc/subuid documentation",
            file=sys.stderr,
        )
        return 2
    except PkexecAuthDenied as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 3
    except PkexecScriptFailed as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 4

    print("\nRe-checking host state ...")
    new_state = detect_host_state()
    _print_status(new_state)
    if not new_state.in_kvm_group and state.in_kvm_group is False:
        print(
            "\nNote: kvm group membership requires you to log out + back in before it takes effect."
        )
    return 0 if new_state.is_complete else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
