# SPDX-License-Identifier: MIT
"""Icon installation and cache management."""

from __future__ import annotations

import configparser
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from winpodx.utils.paths import bundle_dir, icons_dir

log = logging.getLogger(__name__)


def _install_user_file(src: Path, dest: Path) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "wb") as target, src.open("rb") as source:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        tmp.chmod(0o644)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def bundled_data_path(*parts: str) -> Path | None:
    """Resolve a file under the bundled ``data/`` tree."""
    for base in (bundle_dir() / "data",):
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

    _install_user_file(src, dest)
    log.info("Installed winpodx icon: %s", dest)
    return True


def install_gui_launcher_desktop() -> bool:
    """Install the winpodx GUI launcher ``.desktop`` into ~/.local/share/applications.

    install.sh handles this for curl installs; deb / rpm / aur ship it under
    /usr/share/applications. ``winpodx setup`` invoked manually (pip, dev
    checkout, or a curl install where the launcher copy got lost) needs to
    register it explicitly so the GUI shows up in the app menu without
    re-running install.sh.

    Skips when /usr/share/applications/winpodx.desktop already exists -- that
    means a package install owns it, and dropping a user-level copy would
    shadow the package version with a stale Exec= path on later upgrades.
    """
    if Path("/usr/share/applications/winpodx.desktop").is_file():
        log.debug("System winpodx.desktop already present; skipping user copy")
        return False

    src = bundled_data_path("winpodx.desktop")
    if src is None:
        log.warning("Bundled winpodx.desktop not found")
        return False

    if src.is_symlink():
        log.warning("Refusing to install .desktop from symlink: %s", src)
        return False

    dest_dir = Path.home() / ".local" / "share" / "applications"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "winpodx.desktop"

    _install_user_file(src, dest)
    log.info("Installed winpodx GUI launcher: %s", dest)
    return True


def _ensure_index_theme(icon_dir: Path) -> None:
    """Merge installed app directories into hicolor metadata without replacing user values."""
    index = icon_dir / "index.theme"
    try:
        existing = index.exists()
        data_roots = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
        sources = (
            [index]
            if existing
            else [
                Path(root) / "icons/hicolor/index.theme"
                for root in data_roots
                if root and Path(root).is_absolute()
            ]
        )
        theme = configparser.ConfigParser(interpolation=None, delimiters=("=",))
        theme.optionxform = str
        for source in sources:
            if not source.exists():
                continue
            seed = configparser.ConfigParser(interpolation=None, delimiters=("=",))
            seed.optionxform = str
            try:
                seed.read_string(source.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, configparser.Error) as exc:
                if existing:
                    raise
                log.debug("Skipping icon theme seed %s: %s", source, type(exc).__name__)
                continue
            if existing or seed.has_section("Icon Theme"):
                theme = seed
                break

        before = {section: dict(theme[section]) for section in theme}
        if not theme.has_section("Icon Theme"):
            theme.add_section("Icon Theme")
        header = theme["Icon Theme"]
        for key, value in (
            ("Name", "Hicolor"),
            ("Comment", "Fallback icon theme"),
            ("Hidden", "true"),
        ):
            header.setdefault(key, value)

        installed = {"scalable/apps": 64}
        for apps in sorted(icon_dir.glob("*x*/apps")):
            width, _, height = apps.parent.name.partition("x")
            if apps.is_dir() and width.isdecimal() and width == height and int(width) > 0:
                installed[apps.relative_to(icon_dir).as_posix()] = int(width)
        declared = header.get("Directories", "")
        directories = [part.strip() for part in declared.split(",")]
        for directory, default_size in installed.items():
            if directory not in directories:
                declared += ("," if declared and not declared.endswith(",") else "") + directory
                directories.append(directory)
            if not theme.has_section(directory):
                theme.add_section(directory)
            section = theme[directory]
            scalable = directory == "scalable/apps"
            section.setdefault("Type", "Scalable" if scalable else "Fixed")
            section.setdefault("Context", "Applications")
            sizes = {"Size": default_size}
            if scalable:
                sizes.update(MinSize=1, MaxSize=512)
            for key, default in sizes.items():
                try:
                    value = section.getint(key, fallback=0)
                except ValueError:
                    value = 0
                if value <= 0:
                    section[key] = str(default)
            if scalable:
                size = section.getint("Size")
                if section.getint("MinSize") > size:
                    section["MinSize"] = str(size)
                if section.getint("MaxSize") < size:
                    section["MaxSize"] = str(size)
        header["Directories"] = declared
        if existing and before == {section: dict(theme[section]) for section in theme}:
            return

        icon_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=icon_dir, prefix=".index.theme.", suffix=".tmp")
        tmp = Path(tmp_path)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as target:
                theme.write(target, space_around_delimiters=False)
                target.flush()
                os.fchmod(target.fileno(), 0o644)
                os.fsync(target.fileno())
            os.replace(tmp, index)
        finally:
            tmp.unlink(missing_ok=True)
        log.info("Updated icon theme index: %s", index)
    except (OSError, UnicodeError, configparser.Error) as exc:
        log.warning("Could not repair icon theme index %s: %s", index, type(exc).__name__)


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
    icon_dir = icons_dir()
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
