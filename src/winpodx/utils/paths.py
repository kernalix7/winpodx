"""XDG-compliant path management for winpodx."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "winpodx"


def config_dir() -> Path:
    """~/.config/winpodx/"""
    base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(base) / APP_NAME


def data_dir() -> Path:
    """~/.local/share/winpodx/"""
    base = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def runtime_dir() -> Path:
    """Runtime dir for PID files and sockets."""
    base = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(base) / APP_NAME


def applications_dir() -> Path:
    """~/.local/share/applications/ for .desktop files."""
    base = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return Path(base) / "applications"


def icons_dir() -> Path:
    """~/.local/share/icons/hicolor/ for app icons."""
    base = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return Path(base) / "icons" / "hicolor"
