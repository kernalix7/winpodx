# SPDX-License-Identifier: MIT
"""Freedesktop .desktop entry file generation and management."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from winpodx.core.app import AppInfo
from winpodx.desktop.menu import (
    FOLDER_KEY,
    category_for_folder,
    install_menu_folder,
    remove_menu_folder,
)
from winpodx.utils.paths import applications_dir, icons_dir

log = logging.getLogger(__name__)

DESKTOP_TEMPLATE = """\
[Desktop Entry]
Version=1.0
Type=Application
Name={full_name}
Comment={comment}
Exec={winpodx_exe} app run {name} %F
Icon={icon_name}
Categories={categories}
MimeType={mime_types}
Keywords=windows;winpodx;rdp;{name};
Terminal=false
StartupNotify=true
StartupWMClass={wm_class}
{folder_line}"""

# Default Comment when discovery couldn't pull a real description from
# the app's metadata. Better than nothing — keeps the .desktop spec's
# Comment field non-empty for menu tooltips and file managers.
_DEFAULT_COMMENT = "Windows application via WinPodX"


def update_desktop_database() -> None:
    """Rebuild the applications ``mimeinfo.cache`` so ``MimeType=`` lines take
    effect — without it, an app's declared file associations never surface in
    the file manager's "Open with" menu (#545). Best-effort: no-op when
    ``update-desktop-database`` isn't installed; never raises.
    """
    import subprocess

    tool = shutil.which("update-desktop-database")
    if not tool:
        return
    try:
        subprocess.run(
            [tool, str(applications_dir())],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        log.debug("update-desktop-database failed", exc_info=True)


def _winpodx_exe() -> str:
    """Return the absolute path to the winpodx executable.

    Desktop entries must use an absolute path so they work when launched by
    desktop environments that run apps as systemd transient units with a
    stripped PATH (e.g. Deepin's dde-application-manager).  Falls back to the
    bare name when shutil.which() can't resolve it (e.g. during tests).
    """
    return shutil.which("winpodx") or "winpodx"


def install_desktop_entry(app: AppInfo) -> Path:
    """Create and install a .desktop file for a Windows app."""
    dest_dir = applications_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)

    icon_name = _install_icon(app)

    # StartupWMClass must be byte-identical to the /wm-class token FreeRDP is
    # given (rdp.py), or the WM can't match the RemoteApp window to this entry
    # -- shared resolver handles UWP (AUMID slug) + wm_class_hint, not just the
    # exe stem (a UWP exe stem like "microsoft" never matched, so Calculator &
    # other UWP apps showed up unmatched in the taskbar).
    from winpodx.core.rdp import resolve_wm_class

    wm_class = resolve_wm_class(
        app.executable,
        getattr(app, "wm_class_hint", None) or None,
        getattr(app, "launch_uri", None) or None,
    )

    # Prefer the app's real description (from exe metadata / .lnk Comment /
    # UWP <Description>); fall back to the generic stamp when blank. Strip
    # newlines and tabs because the .desktop spec keys are line-terminated
    # and a newline mid-Comment would corrupt later keys.
    comment = (app.description or "").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    comment = comment.strip() or _DEFAULT_COMMENT

    # Same line-termination hazard for the Name= key: full_name comes from guest
    # discovery JSON (a compromised/hostile guest could embed a newline to inject
    # arbitrary .desktop keys like Exec= into the launcher spec). Strip control
    # whitespace exactly as Comment= does, and fall back to the slug if blank.
    full_name = (app.full_name or app.name).replace("\n", " ").replace("\r", " ").replace("\t", " ")
    full_name = full_name.strip() or app.name

    # Consolidate every Windows app under the "winpodx" menu folder (Wine-style)
    # instead of scattering them across native categories. #581 Goal 2: the entry
    # carries the LEAF category for its Start Menu subfolder (just X-winpodx for a
    # top-level app, or X-winpodx-<slug-chain> for a foldered one), so it lands in
    # exactly one nested sub-group that the winpodx .menu fragment defines.
    # App-type discoverability stays via Keywords for menu search.
    folder = (getattr(app, "start_menu_folder", "") or "").strip()
    categories = f"{category_for_folder(folder)};"
    # Record the display folder path so menu.py can rebuild the nested tree +
    # name each .directory. Sanitised upstream; strip stray newlines defensively.
    folder_line = ""
    if folder:
        safe_folder = folder.replace("\n", " ").replace("\r", " ").strip()
        folder_line = f"{FOLDER_KEY}={safe_folder}\n"

    content = DESKTOP_TEMPLATE.format(
        winpodx_exe=_winpodx_exe(),
        full_name=full_name,
        name=app.name,
        comment=comment,
        icon_name=icon_name,
        categories=categories,
        mime_types=";".join(app.mime_types) + ";" if app.mime_types else "",
        wm_class=wm_class,
        folder_line=folder_line,
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

    # Register file associations so the app shows up in "Open with" (#545).
    # Only when this app declares MIME types -- most apps don't, so the cache
    # rebuild stays bounded to the handful that need it.
    if app.mime_types:
        update_desktop_database()

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

    # Keep the nested menu tree consistent. The GUI launcher's entry is
    # winpodx.desktop (no "winpodx-" prefix), so it never counts here.
    #   - last app gone -> tear the whole folder down (no empty "winpodx").
    #   - apps remain   -> rebuild so an emptied subfolder's .directory is
    #     pruned and the .menu fragment drops the now-unused node (#581 Goal 2).
    try:
        if apps_dir.exists() and any(apps_dir.glob("winpodx-*.desktop")):
            install_menu_folder()
        else:
            remove_menu_folder()
    except OSError as e:  # pragma: no cover - defensive, never blocks removal
        log.warning("Could not update winpodx menu folder definition: %s", e)


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
