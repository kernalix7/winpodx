"""Icon installation and cache management."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from winpodx.utils.paths import icons_dir

log = logging.getLogger(__name__)


def bundled_data_path(*parts: str) -> Path | None:
    """Resolve a file under the project's ``data/`` directory.

    Tries locations in order:
      1. Source / editable install: ``<repo>/data/...`` (4 levels above this file)
      2. pip wheel install: ``<sys.prefix>/share/winpodx/data/...``
         (per ``[tool.hatch.build.targets.wheel.shared-data]`` in pyproject)
      3. User install: ``~/.local/share/winpodx/data/...``

    Returns the first existing path, or ``None`` if not found. This keeps
    icon/data lookup working whether winpodx is run from a checkout, from
    ``pip install -e .``, or from a wheel landing in site-packages where
    the old ``Path(__file__).parent.parent.parent.parent`` escape hatches
    to ``site-packages`` and misses the data dir entirely.
    """
    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent / "data",
        Path(sys.prefix) / "share" / "winpodx" / "data",
        Path.home() / ".local" / "share" / "winpodx" / "data",
    ]
    for base in candidates:
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    return None


def install_winpodx_icon() -> bool:
    """Install the main winpodx icon into the hicolor icon theme.

    Copies data/winpodx-icon.svg → ~/.local/share/icons/hicolor/scalable/apps/winpodx.svg
    so that Icon=winpodx in .desktop files resolves correctly.

    Returns True if the icon was installed.
    """
    src = bundled_data_path("winpodx-icon.svg")
    if src is None:
        log.warning("Bundled icon not found in any known data location")
        return False

    dest_dir = icons_dir() / "scalable" / "apps"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "winpodx.svg"

    shutil.copy2(src, dest)
    log.info("Installed winpodx icon: %s", dest)
    return True


def _ensure_index_theme(icon_dir: Path) -> None:
    """Ensure index.theme exists in the user's hicolor directory.

    Without index.theme, gtk-update-icon-cache builds a broken cache and
    KDE Plasma cannot discover icons in the scalable/apps subdirectory.
    """
    index = icon_dir / "index.theme"
    if index.exists():
        return

    # Try to copy from system hicolor first
    system_index = Path("/usr/share/icons/hicolor/index.theme")
    if system_index.exists():
        icon_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(system_index, index)
        log.info("Copied system index.theme to %s", index)
        return

    # Fallback: write a minimal index.theme with scalable/apps
    icon_dir.mkdir(parents=True, exist_ok=True)
    index.write_text(
        "[Icon Theme]\n"
        "Name=Hicolor\n"
        "Comment=Fallback icon theme\n"
        "Hidden=true\n"
        "Directories=scalable/apps\n"
        "\n"
        "[scalable/apps]\n"
        "Size=64\n"
        "MinSize=1\n"
        "MaxSize=512\n"
        "Context=Applications\n"
        "Type=Scalable\n",
        encoding="utf-8",
    )
    log.info("Created minimal index.theme at %s", index)


def update_icon_cache() -> None:
    """Refresh the system icon cache after installing icons."""
    icon_dir = Path.home() / ".local/share/icons/hicolor"
    _ensure_index_theme(icon_dir)
    try:
        result = subprocess.run(
            ["gtk-update-icon-cache", "-f", "-t", str(icon_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log.warning("gtk-update-icon-cache failed: %s", result.stderr.strip())
    except FileNotFoundError:
        log.debug("gtk-update-icon-cache not found, skipping")

    try:
        result = subprocess.run(
            ["xdg-icon-resource", "forceupdate"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log.warning("xdg-icon-resource failed: %s", result.stderr.strip())
    except FileNotFoundError:
        log.debug("xdg-icon-resource not found, skipping")

    # KDE Plasma sycoca cache rebuild (picks up new icons and .desktop files)
    for cmd in ("kbuildsycoca6", "kbuildsycoca5"):
        if shutil.which(cmd):
            try:
                subprocess.run(
                    [cmd, "--noincremental"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            break
