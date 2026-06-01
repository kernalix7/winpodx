# SPDX-License-Identifier: MIT
"""Freedesktop .desktop entry file generation and management."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from winpodx.core.app import AppInfo
from winpodx.desktop.menu import MENU_CATEGORY, install_menu_folder, remove_menu_folder
from winpodx.utils.paths import applications_dir, icons_dir

log = logging.getLogger(__name__)

DESKTOP_TEMPLATE = """\
[Desktop Entry]
Version=1.0
Type=Application
Name={full_name}
Comment={comment}
Exec=winpodx app run {name} %F
Icon={icon_name}
Categories={categories}
MimeType={mime_types}
Keywords=windows;winpodx;rdp;{name};
Terminal=false
StartupNotify=true
StartupWMClass={wm_class}
"""

# Default Comment when discovery couldn't pull a real description from
# the app's metadata. Better than nothing — keeps the .desktop spec's
# Comment field non-empty for menu tooltips and file managers.
_DEFAULT_COMMENT = "Windows application via winpodx"


def install_desktop_entry(app: AppInfo) -> Path:
    """Create and install a .desktop file for a Windows app."""
    dest_dir = applications_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)

    icon_name = _install_icon(app)

    # wm_class must match /wm-class:{stem} in rdp.py
    from pathlib import PureWindowsPath

    wm_class = PureWindowsPath(app.executable).stem.lower()

    # Prefer the app's real description (from exe metadata / .lnk Comment /
    # UWP <Description>); fall back to the generic stamp when blank. Strip
    # newlines and tabs because the .desktop spec keys are line-terminated
    # and a newline mid-Comment would corrupt later keys.
    comment = (app.description or "").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    comment = comment.strip() or _DEFAULT_COMMENT

    # Consolidate every Windows app under one menu folder (Wine-style) instead
    # of scattering them across native categories (Office, Graphics, ...). The
    # entry carries only the custom MENU_CATEGORY, which the winpodx .menu
    # fragment maps into the "winpodx" submenu. App-type discoverability is
    # preserved via Keywords (windows;winpodx;<name>) for menu search.
    categories = f"{MENU_CATEGORY};"

    content = DESKTOP_TEMPLATE.format(
        full_name=app.full_name,
        name=app.name,
        comment=comment,
        icon_name=icon_name,
        categories=categories,
        mime_types=";".join(app.mime_types) + ";" if app.mime_types else "",
        wm_class=wm_class,
    )

    desktop_path = dest_dir / f"winpodx-{app.name}.desktop"
    # Explicit UTF-8: .desktop spec requires UTF-8; system locale may be C/POSIX.
    desktop_path.write_text(content, encoding="utf-8")
    desktop_path.chmod(0o644)

    # Ensure the folder definition exists so the category resolves to a named
    # submenu rather than "Lost & Found". Idempotent + best-effort: a failure
    # here must not block the (already written) entry.
    try:
        install_menu_folder()
    except OSError as e:
        log.warning("Could not write winpodx menu folder definition: %s", e)

    return desktop_path


def remove_desktop_entry(app_name: str) -> None:
    """Remove the .desktop file, icons, and MIME associations for a Windows app."""
    # MIME cleanup must precede file deletion; unregister only reads app.name.
    try:
        from winpodx.core.app import AppInfo
        from winpodx.desktop.mime import unregister_mime_types

        unregister_mime_types(AppInfo(name=app_name, full_name=app_name, executable=""))
    except Exception as e:  # pragma: no cover - defensive, never blocks removal
        log.warning("MIME unregister failed for %s: %s", app_name, e)

    apps_dir = applications_dir()
    desktop_path = apps_dir / f"winpodx-{app_name}.desktop"
    desktop_path.unlink(missing_ok=True)

    # Clean both scalable/apps (SVG) and sized dirs (PNG fallbacks from old installs).
    hicolor = icons_dir()
    scalable_apps = hicolor / "scalable" / "apps"
    for ext in (".svg", ".png"):
        (scalable_apps / f"winpodx-{app_name}{ext}").unlink(missing_ok=True)

    for size_dir in hicolor.glob("*x*/apps"):
        (size_dir / f"winpodx-{app_name}.svg").unlink(missing_ok=True)
        (size_dir / f"winpodx-{app_name}.png").unlink(missing_ok=True)

    # Tear down the shared menu folder once the last Windows app is gone, so we
    # don't leave an empty "winpodx" submenu behind. The GUI launcher's entry is
    # winpodx.desktop (no "winpodx-" prefix), so it never counts here.
    if apps_dir.exists() and not any(apps_dir.glob("winpodx-*.desktop")):
        try:
            remove_menu_folder()
        except OSError as e:  # pragma: no cover - defensive, never blocks removal
            log.warning("Could not remove winpodx menu folder definition: %s", e)


def _install_icon(app: AppInfo) -> str:
    """Install app icon into the hicolor icon theme. Returns the icon name.

    SVG icons go to scalable/apps/, PNG icons to 32x32/apps/ per hicolor spec.
    Other formats fall back to the default winpodx icon.
    """
    icon_name = f"winpodx-{app.name}"

    if not app.icon_path:
        return "winpodx"

    src = Path(app.icon_path)
    # Refuse symlinks: prevents a stray/malicious link from leaking targets via copy.
    if src.is_symlink() or not src.exists():
        return "winpodx"

    suffix = src.suffix.lower()
    if suffix == ".svg":
        dest_dir = icons_dir() / "scalable" / "apps"
        dest = dest_dir / f"{icon_name}.svg"
    elif suffix == ".png":
        # Discovered apps often only have PNG from extracted Windows resources.
        dest_dir = icons_dir() / "32x32" / "apps"
        dest = dest_dir / f"{icon_name}.png"
    else:
        log.warning(
            "Icon %s for app %s is not SVG or PNG (%s); "
            "hicolor accepts only those. Falling back to default winpodx icon.",
            src,
            app.name,
            src.suffix,
        )
        return "winpodx"

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest, follow_symlinks=False)

    return icon_name
