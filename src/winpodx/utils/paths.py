"""XDG-compliant path management for winpodx."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "winpodx"

# Directories shipped at the repo/bundle root (next to ``src/``) that the
# runtime needs to locate regardless of install mode.
_BUNDLE_MARKERS = ("scripts", "config", "data")


def bundle_dir() -> Path:
    """Return the directory containing ``scripts/``, ``config/`` and ``data/``.

    Several call sites previously hand-rolled ``__file__.parent.parent...``
    chains with inconsistent fallbacks, which broke for any install that
    doesn't preserve the repo layout (wheel, Nix, distro package). This is
    the single resolution point; search order:

      1. ``$WINPODX_BUNDLE_DIR`` -- set by packaging wrappers (Nix flake).
      2. Source checkout -- ``<repo>/`` derived from this file's location.
      3. ``sys.prefix/share/winpodx`` -- FHS-style install (wheel, distro).
      4. ``~/.local/bin/winpodx-app`` -- curl|bash installer drop location.

    Falls back to the source-checkout guess so callers get a stable path for
    error messages even when nothing exists.
    """
    env = os.environ.get("WINPODX_BUNDLE_DIR")
    src_guess = Path(__file__).resolve().parents[3]
    candidates = [
        Path(env) if env else None,
        src_guess,
        Path(sys.prefix) / "share" / APP_NAME,
        Path.home() / ".local" / "bin" / "winpodx-app",
    ]
    for c in candidates:
        if c is not None and all((c / m).is_dir() for m in _BUNDLE_MARKERS):
            return c
    return src_guess


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
