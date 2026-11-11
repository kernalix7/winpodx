# SPDX-License-Identifier: MIT
"""Reuse configured local X11 sessions without changing the caller's environment.

A Wayland GUI may predate an XWayland bridge's environment registration. Read
only DISPLAY/XAUTHORITY from the same user's session manager; never discover a
server by guessing a display number or evaluate shell environment files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass

_LOCAL_DISPLAY = re.compile(r"(?:unix/?)?:([0-9]{1,6})(?:\.[0-9]+)?\Z")
_DISPLAY_KEYS = frozenset({"DISPLAY", "XAUTHORITY"})

# Xlib reads XAUTHORITY from the process environment. Run this in a child rather
# than temporarily changing os.environ in a multithreaded GUI/tray process.
_XLIB_PROBE = """\
import ctypes
import sys
try:
    x11 = ctypes.CDLL("libX11.so.6")
except OSError:
    sys.exit(3)
x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
display = x11.XOpenDisplay(None)
if not display:
    sys.exit(2)
x11.XCloseDisplay(display)
"""


@dataclass(frozen=True)
class X11Environment:
    """A child-environment snapshot and an optional display-health result.

    ``usable`` is None until probed, or when Xlib is unavailable (for example,
    a host using only Flatpak FreeRDP). Never report that as verified access.
    ``problem`` is a diagnostic code: missing, unreachable, authentication,
    unverified, or the empty string for successful / unrequested probes.
    """

    env: dict[str, str]
    recovered: bool = False
    usable: bool | None = None
    problem: str = ""


def _user_session_display(env: dict[str, str]) -> dict[str, str]:
    """Read the user manager's D-Bus string array, not shell-quoted output."""
    busctl = shutil.which("busctl", path=env.get("PATH"))
    if not busctl:
        return {}
    try:
        result = subprocess.run(
            [
                busctl,
                "--user",
                "--json=short",
                "get-property",
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager",
                "Environment",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        if result.returncode:
            return {}
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    if not isinstance(payload, dict) or payload.get("type") != "as":
        return {}
    values = payload.get("data")
    if not isinstance(values, list):
        return {}
    display_env = {}
    for value in values:
        if not isinstance(value, str):
            continue
        key, separator, content = value.partition("=")
        if separator and key in _DISPLAY_KEYS and content and "\0" not in content:
            display_env[key] = content
    return display_env


def _probe_display(env: dict[str, str]) -> tuple[bool | None, str]:
    # Do not turn doctor into a remote-network probe for explicit SSH / TCP
    # displays. Explicit caller settings remain usable by the actual client.
    if _LOCAL_DISPLAY.fullmatch(env["DISPLAY"]) is None:
        return None, "unverified"
    try:
        result = subprocess.run(
            [sys.executable, "-c", _XLIB_PROBE],
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "unreachable"
    if result.returncode == 0:
        return True, ""
    if result.returncode == 3:
        return None, "unverified"
    error = result.stderr.lower()
    if any(
        token in error
        for token in ("authorization", "authentication", "magic-cookie", "protocol specified")
    ):
        return False, "authentication"
    return False, "unreachable"


def resolve_x11_environment(
    *, environ: Mapping[str, str] | None = None, probe: bool = False
) -> X11Environment:
    """Resolve an X11 child environment, preserving nonempty explicit values.

    Recovery is limited to local displays published by a Wayland user's session
    manager. Headless jobs do not acquire an unrelated desktop's display. No
    cache: configuring a bridge after a failed launch must work without a GUI
    restart. A probe is opt-in so fast dependency checks do not open X clients.
    """
    env = dict(os.environ if environ is None else environ)
    recovered = False
    if not env.get("DISPLAY"):
        wayland = env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE") == "wayland"
        if wayland:
            candidate = _user_session_display(env)
            display = candidate.get("DISPLAY", "")
            if _LOCAL_DISPLAY.fullmatch(display) is not None:
                env["DISPLAY"] = display
                if not env.get("XAUTHORITY") and candidate.get("XAUTHORITY"):
                    env["XAUTHORITY"] = candidate["XAUTHORITY"]
                recovered = True
        if not env.get("DISPLAY"):
            return X11Environment(env, usable=False, problem="missing")
    if probe:
        usable, problem = _probe_display(env)
        return X11Environment(env, recovered=recovered, usable=usable, problem=problem)
    return X11Environment(env, recovered=recovered)
