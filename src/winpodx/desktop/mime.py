"""MIME type registration for Windows applications."""

from __future__ import annotations

import logging
import subprocess

from winpodx.core.app import AppInfo
from winpodx.utils.paths import applications_dir, config_dir

log = logging.getLogger(__name__)


def register_mime_types(app: AppInfo) -> None:
    """Register MIME type associations for a Windows app."""
    desktop_file = applications_dir() / f"winpodx-{app.name}.desktop"
    if not desktop_file.exists():
        return

    for mime in app.mime_types:
        try:
            result = subprocess.run(
                ["xdg-mime", "default", desktop_file.name, mime],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                log.warning(
                    "xdg-mime register %s failed: %s",
                    mime,
                    result.stderr.strip(),
                )
        except FileNotFoundError:
            log.debug("xdg-mime not found, skipping MIME registration")
            break  # No point retrying if binary is missing


def unregister_mime_types(app: AppInfo) -> None:
    """Remove MIME type associations for a Windows app.

    Note: xdg-mime doesn't have a direct 'unset' command.
    We reset to the system default by removing the association.
    """
    # xdg-mime stores defaults in $XDG_CONFIG_HOME/mimeapps.list
    mimeapps = config_dir().parent / "mimeapps.list"
    if not mimeapps.exists():
        return

    desktop_name = f"winpodx-{app.name}.desktop"
    lines = mimeapps.read_text(encoding="utf-8").splitlines()
    filtered = [line for line in lines if desktop_name not in line]
    mimeapps.write_text("\n".join(filtered) + "\n", encoding="utf-8")
