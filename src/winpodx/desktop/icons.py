"""Icon installation and cache management."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from winpodx.utils.paths import icons_dir

log = logging.getLogger(__name__)


def install_winpodx_icon() -> bool:
    """Install the main winpodx icon into the hicolor icon theme.

    Copies data/winpodx-icon.svg → ~/.local/share/icons/hicolor/scalable/apps/winpodx.svg
    so that Icon=winpodx in .desktop files resolves correctly.

    Returns True if the icon was installed.
    """
    src = Path(__file__).parent.parent.parent.parent / "data" / "winpodx-icon.svg"
    if not src.exists():
        log.warning("Bundled icon not found: %s", src)
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
        "Type=Scalable\n"
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
