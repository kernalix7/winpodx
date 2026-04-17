"""Freedesktop .desktop entry file generation and management."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from winpodx.core.app import AppInfo
from winpodx.utils.paths import applications_dir, icons_dir

log = logging.getLogger(__name__)

DESKTOP_TEMPLATE = """\
[Desktop Entry]
Version=1.0
Type=Application
Name={full_name}
Comment=Windows application via winpodx
Exec=winpodx app run {name} %F
Icon={icon_name}
Categories={categories}
MimeType={mime_types}
Keywords=windows;winpodx;rdp;{name};
Terminal=false
StartupNotify=true
StartupWMClass={wm_class}
"""


def install_desktop_entry(app: AppInfo) -> Path:
    """Create and install a .desktop file for a Windows app."""
    dest_dir = applications_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)

    icon_name = _install_icon(app)

    # wm_class must match /wm-class:{stem} in rdp.py
    from pathlib import PureWindowsPath

    wm_class = PureWindowsPath(app.executable).stem.lower()

    content = DESKTOP_TEMPLATE.format(
        full_name=app.full_name,
        name=app.name,
        icon_name=icon_name,
        categories=";".join(app.categories) + ";" if app.categories else "",
        mime_types=";".join(app.mime_types) + ";" if app.mime_types else "",
        wm_class=wm_class,
    )

    desktop_path = dest_dir / f"winpodx-{app.name}.desktop"
    # Explicit UTF-8: .desktop spec requires UTF-8 and system locale may be
    # C/POSIX (common in containers/minimal installs), which would otherwise
    # raise UnicodeEncodeError on non-ASCII full_name (e.g. Korean/Japanese).
    desktop_path.write_text(content, encoding="utf-8")
    desktop_path.chmod(0o644)
    return desktop_path


def remove_desktop_entry(app_name: str) -> None:
    """Remove the .desktop file, icons, and MIME associations for a Windows app.

    Also clears per-app MIME handler entries from ``~/.config/mimeapps.list``.
    Without this step, reinstalling the app under a different name would leave
    stale ``winpodx-<old>.desktop`` entries behind and cause double-registered
    handlers — or worse, xdg-open resolving to a .desktop file that no longer
    exists.
    """
    # MIME cleanup first: we need the desktop filename to still be meaningful
    # even though we're about to delete the file itself. ``unregister_mime_types``
    # only reads the app name, so ordering is purely defensive.
    try:
        from winpodx.core.app import AppInfo
        from winpodx.desktop.mime import unregister_mime_types

        # Minimal stub — unregister_mime_types only inspects app.name.
        unregister_mime_types(AppInfo(name=app_name, full_name=app_name, executable=""))
    except Exception as e:  # pragma: no cover — defensive, never blocks removal
        log.warning("MIME unregister failed for %s: %s", app_name, e)

    desktop_path = applications_dir() / f"winpodx-{app_name}.desktop"
    desktop_path.unlink(missing_ok=True)

    # Remove icon. `_install_icon` places SVGs in ``scalable/apps/`` which the
    # old ``glob("*x*/apps")`` pattern skipped (it only matched sized dirs like
    # ``48x48/apps``) — so ``winpodx app remove`` left the icon behind. We now
    # clean both the scalable directory and any sized dirs that might hold PNG
    # fallbacks from older installs.
    hicolor = icons_dir()
    scalable_apps = hicolor / "scalable" / "apps"
    for ext in (".svg", ".png"):
        (scalable_apps / f"winpodx-{app_name}{ext}").unlink(missing_ok=True)

    for size_dir in hicolor.glob("*x*/apps"):
        (size_dir / f"winpodx-{app_name}.svg").unlink(missing_ok=True)
        (size_dir / f"winpodx-{app_name}.png").unlink(missing_ok=True)


def _install_icon(app: AppInfo) -> str:
    """Install app icon into the hicolor icon theme. Returns the icon name."""
    icon_name = f"winpodx-{app.name}"

    if not app.icon_path:
        # No app-specific icon — fall back to main winpodx icon
        return "winpodx"

    src = Path(app.icon_path)
    # Refuse symlinks outright: a malicious/stray symlink in an app
    # definition would otherwise cause shutil.copy2 to read whatever the
    # target points at (outside the app dir) and copy it into the shared
    # hicolor tree as that app's icon.
    if src.is_symlink() or not src.exists():
        return "winpodx"

    # hicolor spec: ``scalable/apps/`` is reserved for SVG. gtk-update-icon-cache
    # silently drops non-SVG entries there, making the app icon appear missing.
    # For anything that isn't SVG (.ico, .png, .bmp…) we fall back to the shared
    # winpodx icon rather than install something the cache will discard.
    if src.suffix.lower() != ".svg":
        log.warning(
            "Icon %s for app %s is not SVG (%s); scalable/apps/ requires SVG. "
            "Falling back to default winpodx icon.",
            src,
            app.name,
            src.suffix,
        )
        return "winpodx"

    dest_dir = icons_dir() / "scalable" / "apps"
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / f"{icon_name}.svg"
    shutil.copy2(src, dest, follow_symlinks=False)

    return icon_name
