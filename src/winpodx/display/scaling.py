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
    factor = detect_raw_scale()
    log.debug("Detected scale factor: %.2f", factor)

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
    """Detect scale from Wayland compositors (sway, hyprland).

    Mixed-DPI setups (e.g. 2x laptop panel + 1x external HDMI) broke when we
    only read the focused monitor: if winpodx was launched from the 1x screen,
    RDP came back at 100% and apps looked tiny on the 2x panel the user then
    moved them to. Return the MAX scale across all outputs so the RDP session
    is sized for the densest display that might host it. Qt's
    ``devicePixelRatio`` is preferred when a QGuiApplication is live, because
    it already reflects per-screen scale chosen by the compositor.
    """
    import json

    # Prefer Qt when available — it aggregates per-screen scale from the
    # compositor without re-implementing swaymsg/hyprctl parsing.
    qt_scale = _qt_max_device_pixel_ratio()
    if qt_scale is not None:
        return qt_scale

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
            scales = [
                float(o.get("scale", 1.0))
                for o in outputs
                if o.get("active", True) and o.get("scale") is not None
            ]
            if scales:
                return max(scales)
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
            scales = [float(m.get("scale", 1.0)) for m in monitors if m.get("scale") is not None]
            if scales:
                return max(scales)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        pass

    return 1.0


def _qt_max_device_pixel_ratio() -> float | None:
    """Return max devicePixelRatio across screens, or None if Qt is unusable.

    Only works when a QGuiApplication is already instantiated in the process
    (i.e. we're being called from the GUI, not a one-shot CLI invocation).
    Creating a QGuiApplication solely to probe DPR would spawn a Qt event
    loop for no reason, so we bail out cleanly when one isn't live.
    """
    try:
        from PySide6.QtGui import QGuiApplication
    except ImportError:
        return None

    try:
        app = QGuiApplication.instance()
        if app is None:
            return None
        screens = QGuiApplication.screens()
        if not screens:
            return None
        return max(float(s.devicePixelRatio()) for s in screens)
    except Exception:  # pragma: no cover — defensive: Qt state can be odd
        return None


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
                if dpi > 0:
                    return dpi / 96.0
    except (ValueError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return 1.0
