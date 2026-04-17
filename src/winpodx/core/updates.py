r"""Windows Update toggle — enable/disable/status via container exec.

Runs C:\OEM\toggle_updates.ps1 inside the Windows container.
No RDP session required — uses podman/docker exec directly.
"""

from __future__ import annotations

import logging
import subprocess

from winpodx.core.config import Config

log = logging.getLogger(__name__)

_SCRIPT = r"C:\OEM\toggle_updates.ps1"
_PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def _exec_toggle(cfg: Config, action: str) -> tuple[bool, str]:
    """Run toggle_updates.ps1 with the given action.

    Returns (success, output_text).
    """
    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return False, "Only supported for podman/docker backends"

    runtime = "podman" if backend == "podman" else "docker"
    cmd = [
        runtime,
        "exec",
        cfg.pod.container_name,
        _PS,
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        _SCRIPT,
        "-Action",
        action,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return False, f"{runtime} not found"
    except subprocess.TimeoutExpired:
        return False, "Command timed out"

    output = result.stdout.strip()
    if result.returncode == 0:
        return True, output
    return False, result.stderr.strip() or output


def disable_updates(cfg: Config) -> bool:
    """Disable Windows Update services and block update domains."""
    ok, msg = _exec_toggle(cfg, "disable")
    if ok:
        log.info("Windows Update disabled")
    else:
        log.error("Failed to disable updates: %s", msg)
    return ok


def enable_updates(cfg: Config) -> bool:
    """Enable Windows Update services and unblock update domains."""
    ok, msg = _exec_toggle(cfg, "enable")
    if ok:
        log.info("Windows Update enabled")
    else:
        log.error("Failed to enable updates: %s", msg)
    return ok


def get_update_status(cfg: Config) -> str | None:
    """Check if Windows Update is enabled or disabled.

    Returns 'enabled', 'disabled', or None on error.
    """
    ok, msg = _exec_toggle(cfg, "status")
    if ok and msg in ("enabled", "disabled"):
        return msg
    return None
