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
    """Resolve a file under data/ across source, wheel, and user install layouts."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent / "data",
        Path(sys.prefix) / "share" / "winpodx" / "data",
        Path.home() / ".local" / "share" / "winpodx" / "data",
    ]
    for base in candidates:
        candidate = base.joinpath(*parts)
        if not candidate.exists():
            continue
        # Symlink escape guard: prevent leaking files outside the data dir via copy.
        try:
            resolved = candidate.resolve(strict=True)
            base_resolved = base.resolve(strict=True)
        except (OSError, RuntimeError):
            log.warning("Rejecting unresolvable data candidate: %s", candidate)
            continue
        if not resolved.is_relative_to(base_resolved):
            log.warning(
                "Rejecting symlink escape in data candidate: %s -> %s",
                candidate,
                resolved,
            )
            continue
        return candidate
    return None


def install_winpodx_icon() -> bool:
    """Install the main winpodx icon into the hicolor icon theme."""
    src = bundled_data_path("winpodx-icon.svg")
    if src is None:
        log.warning("Bundled icon not found in any known data location")
        return False

    # Defense-in-depth: refuse symlinks on the user-writable candidate path.
    if src.is_symlink():
        log.warning("Refusing to install icon from symlink: %s", src)
        return False

    dest_dir = icons_dir() / "scalable" / "apps"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "winpodx.svg"

    shutil.copy2(src, dest, follow_symlinks=False)
    log.info("Installed winpodx icon: %s", dest)
    return True


def _ensure_index_theme(icon_dir: Path) -> None:
    """Ensure index.theme exists so gtk cache and KDE Plasma can discover icons."""
    index = icon_dir / "index.theme"
    if index.exists():
        return

    system_index = Path("/usr/share/icons/hicolor/index.theme")
    if system_index.exists():
        icon_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(system_index, index)
        log.info("Copied system index.theme to %s", index)
        return

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


def refresh_icon_cache() -> None:
    """Refresh the system icon cache after installing one or more icons.

    Safe to call once after a batch of icon installs (e.g. after
    ``persist_discovered`` has written N app icons). Runs the gtk-update-icon-cache,
    xdg-icon-resource, and Plasma sycoca rebuild steps in sequence; each is
    bounded by a 30s timeout. Missing tools are skipped.

    For single-icon workflows, this is also safe to call per icon, but callers
    installing many icons at once should invoke this exactly once at the end
    of the batch to avoid redundant cache rebuilds.
    """
    _do_refresh_icon_cache()


def update_icon_cache() -> None:
    """Backward-compatible alias for :func:`refresh_icon_cache`."""
    _do_refresh_icon_cache()


def _do_refresh_icon_cache() -> None:
    icon_dir = Path.home() / ".local/share/icons/hicolor"
    _ensure_index_theme(icon_dir)
    try:
        result = subprocess.run(
            ["gtk-update-icon-cache", "-f", "-t", str(icon_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning("gtk-update-icon-cache failed: %s", result.stderr.strip())
    except FileNotFoundError:
        log.debug("gtk-update-icon-cache not found, skipping")
    except subprocess.TimeoutExpired:
        log.warning("gtk-update-icon-cache timed out after 30s (corrupt cache?)")

    try:
        result = subprocess.run(
            ["xdg-icon-resource", "forceupdate"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning("xdg-icon-resource failed: %s", result.stderr.strip())
    except FileNotFoundError:
        log.debug("xdg-icon-resource not found, skipping")
    except subprocess.TimeoutExpired:
        log.warning("xdg-icon-resource forceupdate timed out after 30s")

    # KDE Plasma sycoca rebuild; surface failures at debug/warning for diagnosis.
    for cmd in ("kbuildsycoca6", "kbuildsycoca5"):
        if shutil.which(cmd):
            try:
                result = subprocess.run(
                    [cmd, "--noincremental"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode != 0:
                    log.warning(
                        "%s exited %d: %s",
                        cmd,
                        result.returncode,
                        result.stderr.strip(),
                    )
            except FileNotFoundError:
                log.debug("%s not found after shutil.which - race or PATH change", cmd)
            except subprocess.TimeoutExpired:
                log.warning("%s timed out after 30s (sycoca rebuild stuck?)", cmd)
            break
