# SPDX-License-Identifier: MIT
"""Host monitor-layout detection for multi-monitor RDP geometry.

FreeRDP (xfreerdp, an X11 client) reads the host monitor layout from the X
server via XRandR. When two monitors run at *different* fractional scales, the
compositor's sub-pixel rounding can leave the logical rectangles non-tileable
(a 1 px gap or overlap at the boundary, mismatched heights, per-monitor scale
reported as 0). FreeRDP's ``/span`` / ``/multimon`` path validates that the
monitors tile into one contiguous region and refuses the connection at
``pre_connect`` otherwise — so the RemoteApp never opens.

This module reads the *same* source xfreerdp uses (``xrandr --listmonitors``)
and returns the overall bounding box, so :mod:`winpodx.core.rdp` can hand
FreeRDP an explicit single ``/size:WxH`` desktop instead. That skips the
per-monitor tiling check entirely while still covering every monitor's X
coordinates, so a RAIL window can live on either monitor (placement stays
consistent because it's the very coordinate space xfreerdp paints into).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from collections.abc import Mapping

log = logging.getLogger(__name__)

# A `xrandr --listmonitors` row, e.g.:
#   " 1: +HDMI-A-1 2773/698x1560/393+2561+0  HDMI-A-1"
# captures width / height (px) and the x / y offset (px); the /NNN parts are
# physical millimetres, which we don't need.
_MONITOR_RE = re.compile(r"(?P<w>\d+)/\d+x(?P<h>\d+)/\d+\+(?P<x>\d+)\+(?P<y>\d+)")


def _run_xrandr_listmonitors(environ: Mapping[str, str] | None = None) -> str | None:
    """Return ``xrandr --listmonitors`` stdout, or ``None``. Never raises.

    ``environ`` (#911): the child environment to read DISPLAY from and hand to
    xrandr -- defaults to the process environment. Callers that recovered an
    XWayland display without mutating ``os.environ`` pass their snapshot here so
    the layout probe sees the same display the RDP client will use.
    """
    env = os.environ if environ is None else environ
    if not env.get("DISPLAY") or shutil.which("xrandr") is None:
        return None
    try:
        result = subprocess.run(
            ["xrandr", "--listmonitors"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("xrandr --listmonitors failed to run: %s", e)
        return None
    if result.returncode != 0:
        log.debug("xrandr --listmonitors rc=%s", result.returncode)
        return None
    return result.stdout


def parse_monitor_extent(listmonitors_output: str) -> tuple[int, int, int] | None:
    """Parse ``xrandr --listmonitors`` text to ``(count, width, height)``.

    ``width`` / ``height`` are the bounding box of all monitors (``max(x+w)`` /
    ``max(y+h)``). Returns ``None`` when nothing parses. Pure (no I/O) so it's
    unit-testable against canned ``xrandr`` output.
    """
    width = height = count = 0
    for line in listmonitors_output.splitlines():
        m = _MONITOR_RE.search(line)
        if not m:
            continue
        w, h, x, y = (int(m.group("w")), int(m.group("h")), int(m.group("x")), int(m.group("y")))
        width = max(width, x + w)
        height = max(height, y + h)
        count += 1
    if count == 0 or width <= 0 or height <= 0:
        return None
    return count, width, height


def _kde_monitor_scales() -> list[float] | None:
    """Per-enabled-output scale via ``kscreen-doctor -j`` (KDE Plasma). None on
    failure / not-KDE."""
    if shutil.which("kscreen-doctor") is None:
        return None
    try:
        result = subprocess.run(
            ["kscreen-doctor", "-j"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("kscreen-doctor failed to run: %s", e)
        return None
    if result.returncode != 0:
        return None
    try:
        import json

        data = json.loads(result.stdout)
        scales = [
            float(o["scale"])
            for o in data.get("outputs", [])
            if o.get("enabled") and o.get("scale") is not None
        ]
    except (ValueError, KeyError, TypeError) as e:
        log.debug("kscreen-doctor output unparseable: %s", e)
        return None
    return scales or None


def _wlroots_monitor_scales() -> list[float] | None:
    """Per-output scale via ``swaymsg`` / ``hyprctl`` (wlroots). None on failure."""
    import json

    if shutil.which("swaymsg") is not None:
        try:
            r = subprocess.run(
                ["swaymsg", "-t", "get_outputs"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if r.returncode == 0:
                outs = json.loads(r.stdout)
                scales = [
                    float(o["scale"])
                    for o in outs
                    if o.get("active", True) and o.get("scale") is not None
                ]
                if scales:
                    return scales
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
            log.debug("swaymsg get_outputs failed: %s", e)

    if shutil.which("hyprctl") is not None:
        try:
            r = subprocess.run(
                ["hyprctl", "monitors", "-j"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if r.returncode == 0:
                mons = json.loads(r.stdout)
                scales = [float(m["scale"]) for m in mons if m.get("scale") is not None]
                if scales:
                    return scales
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as e:
            log.debug("hyprctl monitors failed: %s", e)

    return None


def detect_monitor_scales() -> list[float] | None:
    """Per-monitor display scale for every enabled output, or ``None``.

    Tries KDE (``kscreen-doctor``) then wlroots (``swaymsg`` / ``hyprctl``).
    These report the *compositor* scale (which XRandR logical geometry already
    folds in, so it can't be recovered from xrandr alone). Never raises.
    """
    return _kde_monitor_scales() or _wlroots_monitor_scales()


# Process-lifetime cache: probing the compositor (kscreen-doctor etc.) costs
# ~seconds, and this is consulted on every RAIL launch -- but monitor scales
# rarely change mid-session, and a change just needs a winpodx restart. Cleared
# by tests via _MIXED_SCALE_CACHE.clear().
_MIXED_SCALE_CACHE: dict[str, bool | None] = {}


def has_mixed_scale() -> bool | None:
    """``True`` when enabled monitors run at *different* scales, ``False`` when
    uniform, ``None`` when undetectable / single monitor. Cached per process.

    Mixed fractional scales are what makes FreeRDP RAIL + XWayland freeze a
    window dragged across monitors (and makes ``/span`` non-tileable at
    pre_connect), so :mod:`winpodx.core.rdp` pins the session to one monitor in
    that case. Never raises.
    """
    if "value" not in _MIXED_SCALE_CACHE:
        scales = detect_monitor_scales()
        if not scales or len(scales) < 2:
            _MIXED_SCALE_CACHE["value"] = None
        else:
            _MIXED_SCALE_CACHE["value"] = (max(scales) - min(scales)) > 0.01
    return _MIXED_SCALE_CACHE["value"]


def detect_x_screen_extent(*, environ: Mapping[str, str] | None = None) -> tuple[int, int] | None:
    """Return the multi-monitor bounding box ``(width, height)`` in pixels.

    Reads the live X screen via ``xrandr --listmonitors`` (the layout xfreerdp
    sees). ``environ`` (#911) selects the environment DISPLAY/XAUTHORITY come
    from; defaults to the process environment. Returns ``None`` when there's a
    single monitor (no spanning needed), when xrandr is unavailable / there's no
    X display, or when parsing fails — in every such case the caller should fall
    back to a single-monitor desktop. Never raises.
    """
    output = _run_xrandr_listmonitors(environ)
    if output is None:
        return None
    parsed = parse_monitor_extent(output)
    if parsed is None:
        return None
    count, width, height = parsed
    if count < 2:
        return None  # single monitor -- no span / explicit size needed
    return width, height
