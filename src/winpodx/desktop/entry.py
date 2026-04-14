"""Freedesktop .desktop entry file generation and management."""

from __future__ import annotations

import shutil
from pathlib import Path

from winpodx.core.app import AppInfo
from winpodx.utils.paths import applications_dir, icons_dir

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
    desktop_path.write_text(content)
    desktop_path.chmod(0o644)
    return desktop_path


def remove_desktop_entry(app_name: str) -> None:
    """Remove the .desktop file for a Windows app."""
    desktop_path = applications_dir() / f"winpodx-{app_name}.desktop"
    desktop_path.unlink(missing_ok=True)

    # Remove icon
    for size_dir in icons_dir().glob("*x*/apps"):
        icon = size_dir / f"winpodx-{app_name}.svg"
        icon.unlink(missing_ok=True)
        icon = size_dir / f"winpodx-{app_name}.png"
        icon.unlink(missing_ok=True)


def _install_icon(app: AppInfo) -> str:
    """Install app icon into the hicolor icon theme. Returns the icon name."""
    icon_name = f"winpodx-{app.name}"

    if not app.icon_path:
        # No app-specific icon — fall back to main winpodx icon
        return "winpodx"

    src = Path(app.icon_path)
    if not src.exists():
        return "winpodx"

    dest_dir = icons_dir() / "scalable" / "apps"
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / f"{icon_name}{src.suffix}"
    shutil.copy2(src, dest)

    return icon_name
