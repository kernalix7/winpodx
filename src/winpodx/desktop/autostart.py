# SPDX-License-Identifier: MIT
"""Manage `~/.config/autostart/winpodx-tray.desktop` for boot-time tray launch.

Using the XDG autostart spec (rather than systemd user units or a
cfg.toml field with code that wires itself into the session at runtime)
keeps the implementation portable across KDE / GNOME / XFCE / Cinnamon
without per-DE branching, and lets the user uninstall the autostart by
hand by just deleting the .desktop file.

File existence is the source of truth -- ``is_tray_autostart_enabled``
just checks ``Path.exists()``. ``X-GNOME-Autostart-enabled=true`` is
written defensively so GNOME's session manager honours it even after a
DE upgrade that resets per-user defaults.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

AUTOSTART_FILE_NAME = "winpodx-tray.desktop"


def _autostart_dir() -> Path:
    """Return ``$XDG_CONFIG_HOME/autostart`` (default ``~/.config/autostart``)."""
    import os

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".config"
    return base / "autostart"


def autostart_file_path() -> Path:
    """Absolute path to the autostart .desktop entry winpodx manages."""
    return _autostart_dir() / AUTOSTART_FILE_NAME


def is_tray_autostart_enabled() -> bool:
    """Return True when the autostart .desktop entry exists."""
    return autostart_file_path().is_file()


def _resolve_tray_command() -> str:
    """Pick the command line to put in the .desktop ``Exec=`` field.

    Prefers the wrapper script (``~/.local/bin/winpodx`` etc.) because
    it sets PYTHONPATH for source / curl-install layouts. Falls back to
    ``python3 -m winpodx tray`` when no wrapper is on PATH.
    """
    cmd = shutil.which("winpodx")
    if cmd:
        return f"{cmd} tray"
    # Source / dev fallback. The current interpreter is the best guess.
    return f"{sys.executable} -m winpodx tray"


def enable_tray_autostart() -> Path:
    """Create the .desktop entry. Returns the path written."""
    import os

    target = autostart_file_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    exec_line = _resolve_tray_command()
    contents = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=winpodx tray\n"
        "Comment=System tray icon + idle-stall auto-recovery for winpodx\n"
        f"Exec={exec_line}\n"
        "Icon=winpodx\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "X-GNOME-Autostart-enabled=true\n"
        "NoDisplay=true\n"
        "StartupNotify=false\n"
    ).encode("utf-8")
    # ``os.open`` with an explicit mode skips the umask-dependent
    # window where ``write_text`` then ``chmod`` would briefly leave
    # the file at 0664 on permissive umasks. Truncate-and-rewrite is
    # idempotent across repeat enables.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.write(fd, contents)
    finally:
        os.close(fd)
    log.info("Wrote tray autostart entry: %s", target)
    return target


def disable_tray_autostart() -> bool:
    """Remove the .desktop entry. Returns True when something was removed."""
    target = autostart_file_path()
    if not target.exists():
        return False
    try:
        target.unlink()
    except OSError as e:
        log.warning("Could not remove autostart entry %s: %s", target, e)
        return False
    log.info("Removed tray autostart entry: %s", target)
    return True


def set_tray_autostart(enabled: bool) -> None:
    """Idempotent toggle. Safe to call with the current state."""
    if enabled:
        enable_tray_autostart()
    else:
        disable_tray_autostart()
