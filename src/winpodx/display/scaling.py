"""DPI and display scaling detection.

Auto-detects the display scale factor from the current desktop environment
and maps it to an RDP-compatible scale value (100, 140, or 180).
"""

from __future__ import annotations

import logging
import os
import subprocess

from winpodx.display.detector import desktop_environment

log = logging.getLogger(__name__)


def detect_scale_factor() -> int:
    """Detect the current display scale factor.

    Returns an RDP-compatible scale value: 100, 140, or 180.
    Checks DE-specific settings, then environment variables, then xrdb.
    """
    de = desktop_environment()
    factor = 1.0

    if de == "gnome":
        factor = _gnome_scale()
    elif de == "kde":
        factor = _kde_scale()
    elif de in ("sway", "hyprland"):
        factor = _wayland_compositor_scale()
    elif de == "cinnamon":
        factor = _cinnamon_scale()
    else:
        factor = _env_scale() or _xrdb_scale()

    log.debug("Detected scale factor: %.2f (DE: %s)", factor, de)

    # Map to nearest RDP scale
    if factor >= 1.7:
        return 180
    elif factor >= 1.3:
        return 140
    return 100


def detect_raw_scale() -> float:
    """Detect the raw scale factor as a float (e.g. 1.0, 1.25, 2.0)."""
    de = desktop_environment()

    if de == "gnome":
        return _gnome_scale()
    elif de == "kde":
        return _kde_scale()
    elif de in ("sway", "hyprland"):
        return _wayland_compositor_scale()
    elif de == "cinnamon":
        return _cinnamon_scale()
    return _env_scale() or _xrdb_scale()


def _gnome_scale() -> float:
    # GNOME has two scale mechanisms: integer UI scale + text scaling factor
    ui_scale = 1
    text_scale = 1.0

    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "scaling-factor"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        val = result.stdout.strip().removeprefix("uint32 ")
        if val and val != "0":
            ui_scale = int(val)
    except (ValueError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "text-scaling-factor"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        text_scale = float(result.stdout.strip())
    except (ValueError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    combined = max(ui_scale, 1) * text_scale
    return combined if combined > 0 else 1.0


def _kde_scale() -> float:
    # Try kreadconfig6 first (Plasma 6), then kreadconfig5
    for cmd in ("kreadconfig6", "kreadconfig5"):
        try:
            result = subprocess.run(
                [cmd, "--group", "KScreen", "--key", "ScaleFactor"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            val = result.stdout.strip()
            if val:
                return float(val)
        except (ValueError, FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Fallback: QT_SCALE_FACTOR env var
    qt_scale = os.environ.get("QT_SCALE_FACTOR", "")
    if qt_scale:
        try:
            return float(qt_scale)
        except ValueError:
            pass

    return 1.0


def _wayland_compositor_scale() -> float:
    """Detect scale from Wayland compositors (sway, hyprland)."""
    import json

    # Sway: swaymsg -t get_outputs
    try:
        result = subprocess.run(
            ["swaymsg", "-t", "get_outputs"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            outputs = json.loads(result.stdout)
            for output in outputs:
                if output.get("focused") or output.get("active"):
                    return float(output.get("scale", 1.0))
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        pass

    # Hyprland: hyprctl monitors -j
    try:
        result = subprocess.run(
            ["hyprctl", "monitors", "-j"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            monitors = json.loads(result.stdout)
            for mon in monitors:
                if mon.get("focused"):
                    return float(mon.get("scale", 1.0))
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        pass

    return 1.0


def _cinnamon_scale() -> float:
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.cinnamon.desktop.interface", "scaling-factor"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        val = result.stdout.strip().removeprefix("uint32 ")
        if val and val != "0":
            return float(val)
    except (ValueError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return 1.0


def _env_scale() -> float:
    """Check environment variables for scale hints."""
    for var in ("GDK_SCALE", "QT_SCALE_FACTOR", "ELM_SCALE"):
        val = os.environ.get(var, "")
        if val:
            try:
                f = float(val)
                if f > 0:
                    return f
            except ValueError:
                continue
    return 1.0


def _xrdb_scale() -> float:
    try:
        result = subprocess.run(
            ["xrdb", "-query"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            if "Xft.dpi" in line:
                dpi = float(line.split(":")[-1].strip())
                return dpi / 96.0
    except (ValueError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return 1.0
