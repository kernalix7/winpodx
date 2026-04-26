r"""Windows Update toggle via FreeRDP RemoteApp PowerShell.

The actual toggle logic lives in ``config/oem/toggle_updates.ps1`` which
dockur stages into ``C:\OEM\`` during unattended install. v0.1.9.5
migrated this off the broken ``podman exec ... powershell.exe`` path
(which never reached the Windows VM) onto ``windows_exec.run_in_windows``.
"""

from __future__ import annotations

import logging

from winpodx.core.config import Config

log = logging.getLogger(__name__)


def _exec_toggle(cfg: Config, action: str) -> tuple[bool, str]:
    """Run ``toggle_updates.ps1 -Action <action>`` inside the Windows VM."""
    if cfg.pod.backend not in ("podman", "docker"):
        return False, "Only supported for podman/docker backends"

    if action not in ("enable", "disable", "status"):
        return False, f"unknown action {action!r}"

    payload = (
        f"& 'C:\\OEM\\toggle_updates.ps1' -Action '{action}'\n"
        "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }\n"
    )
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(cfg, payload, description=f"updates-{action}", timeout=45)
    except WindowsExecError as e:
        return False, str(e)

    output = (result.stdout or "").strip()
    if result.rc == 0:
        return True, output
    return False, (result.stderr.strip() or output) or f"rc={result.rc}"


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
    """Check if Windows Update is enabled or disabled."""
    ok, msg = _exec_toggle(cfg, "status")
    if ok and msg in ("enabled", "disabled"):
        return msg
    return None
