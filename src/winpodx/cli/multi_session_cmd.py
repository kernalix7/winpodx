"""CLI handlers for multi-session RDP (rdprrap) inside the Windows guest."""

from __future__ import annotations

import argparse
import subprocess
import sys

RDPRRAP_INSTALLER = r"C:\winpodx\rdprrap\rdprrap-installer.exe"


def handle_multi_session(args: argparse.Namespace) -> None:
    """Route `winpodx multi-session {status|enable|disable}` subcommands."""
    cmd = args.multi_session_command
    if cmd == "status":
        sys.exit(_run_installer(["status"]))
    elif cmd == "enable":
        sys.exit(_run_installer(["install", "--skip-restart"]))
    elif cmd == "disable":
        sys.exit(_run_installer(["uninstall"]))
    else:
        print("Usage: winpodx multi-session {status|enable|disable}")
        sys.exit(1)


def _run_installer(installer_args: list[str]) -> int:
    from winpodx.core.config import Config

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(
            f"multi-session requires a container backend; current backend is '{cfg.pod.backend}'.",
            file=sys.stderr,
        )
        return 2

    runtime = "podman" if cfg.pod.backend == "podman" else "docker"
    container = cfg.pod.container_name

    cmd = [runtime, "exec", container, RDPRRAP_INSTALLER, *installer_args]
    try:
        result = subprocess.run(cmd, text=True)
    except FileNotFoundError:
        print(f"'{runtime}' not found. Is {cfg.pod.backend} installed?", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130

    if result.returncode != 0:
        hint = (
            "Is the pod running and rdprrap deployed? "
            "Start the pod with 'winpodx pod start' and wait for first boot to finish."
        )
        print(f"rdprrap-installer failed (exit {result.returncode}). {hint}", file=sys.stderr)
    return result.returncode
