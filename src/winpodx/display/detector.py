"""Display server detection (X11 vs Wayland)."""

from __future__ import annotations

import os
import shutil


def session_type() -> str:
    """Detect display server session type: 'x11', 'wayland', or 'unknown'."""
    xdg = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if xdg in ("x11", "wayland"):
        return xdg

    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"

    return "unknown"


def desktop_environment() -> str:
    """Detect desktop environment from XDG_CURRENT_DESKTOP leading segment."""
    xdg_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()

    de_map = {
        "gnome": "gnome",
        "kde": "kde",
        "xfce": "xfce",
        "sway": "sway",
        "hyprland": "hyprland",
        "lxqt": "lxqt",
        "mate": "mate",
        "cinnamon": "cinnamon",
        "budgie": "budgie",
        "deepin": "deepin",
    }

    # Priority match: leading segment before first ':' per freedesktop spec.
    leading = xdg_desktop.split(":", 1)[0].strip()
    if leading in de_map:
        return de_map[leading]

    # Fallback: substring scan for values like "X-Cinnamon".
    for key, name in de_map.items():
        if key in xdg_desktop:
            return name

    session = os.environ.get("DESKTOP_SESSION", "").lower()
    if session:
        return session

    return "unknown"


def has_wayland_freerdp() -> bool:
    """Check if a Wayland-native FreeRDP binary is available."""
    return shutil.which("wlfreerdp3") is not None or shutil.which("wlfreerdp") is not None


def display_info() -> dict[str, str]:
    """Gather comprehensive display information for diagnostics."""
    return {
        "session_type": session_type(),
        "desktop_environment": desktop_environment(),
        "wayland_display": os.environ.get("WAYLAND_DISPLAY", ""),
        "x11_display": os.environ.get("DISPLAY", ""),
        "wayland_freerdp": "yes" if has_wayland_freerdp() else "no",
    }
