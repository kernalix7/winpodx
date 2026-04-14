r"""RDPWrap INI update — manual trigger from GUI only.

Runs C:\OEM\update_rdpwrap.ps1 inside the Windows container
via a RemoteApp PowerShell session. No network downloads.
"""

from __future__ import annotations

import logging
import subprocess

from winpodx.core.config import Config

log = logging.getLogger(__name__)


def update_rdpwrap_ini(cfg: Config) -> bool:
    """Apply bundled rdpwrap.ini inside the Windows container.

    Launches PowerShell via RDP RemoteApp to run the local update script.
    Returns True on success.
    """
    from winpodx.core.rdp import build_rdp_command, find_freerdp

    if not find_freerdp():
        log.error("FreeRDP not found")
        return False

    # Build command to run the update script via RemoteApp
    cmd, _ = build_rdp_command(
        cfg,
        app_executable=(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"),
    )

    # Replace the /shell argument to pass the script as arguments
    for i, arg in enumerate(cmd):
        if arg.startswith("/shell:"):
            cmd[i] = (
                r"/shell:C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
                r" -ExecutionPolicy Bypass -File C:\OEM\update_rdpwrap.ps1"
            )
            break

    log.info("Running RDPWrap INI update via RDP")
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as e:
        log.error("RDPWrap update failed: %s", e)
        return False
