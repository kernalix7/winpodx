# SPDX-License-Identifier: MIT
"""Group every winpodx app under a single desktop-menu folder.

Mirrors how Wine creates its "Wine" submenu: a freedesktop ``.directory``
file names the folder, an ``applications-merged/*.menu`` fragment maps a
custom category into it, and each app ``.desktop`` carries that category.
The XDG menu spec auto-merges the fragment into the system
``applications.menu``, so KDE Plasma, XFCE, Cinnamon, MATE and LXQt all show
the folder.

GNOME's overview is a flat grid that ignores menu folders -- the entries
still appear, just not grouped (GNOME app-folders are a separate gsettings
mechanism we don't touch here).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Custom category (X- prefixed, per the spec) that every winpodx entry
# carries. The .menu fragment below maps exactly this category into the
# winpodx folder, so consolidating apps is just a matter of tagging them.
MENU_CATEGORY = "X-winpodx"

_DIRECTORY_FILENAME = "winpodx-windows.directory"
_MENU_FILENAME = "winpodx.menu"

_DIRECTORY_CONTENT = """\
[Desktop Entry]
Version=1.0
Type=Directory
Name=WinPodX (Windows Apps)
Comment=Windows applications via WinPodX
Icon=winpodx
"""

# <Name>Applications</Name> MUST match the root menu name for the fragment to
# merge into the right place; the nested <Menu> defines our submenu and pulls
# in every entry tagged with MENU_CATEGORY.
_MENU_CONTENT = """\
<!DOCTYPE Menu PUBLIC "-//freedesktop//DTD Menu 1.0//EN"
 "http://www.freedesktop.org/standards/menu-spec/menu-1.0.dtd">
<Menu>
  <Name>Applications</Name>
  <Menu>
    <Name>winpodx</Name>
    <Directory>winpodx-windows.directory</Directory>
    <Include>
      <Category>X-winpodx</Category>
    </Include>
  </Menu>
</Menu>
"""


def _directories_dir() -> Path:
    """``$XDG_DATA_HOME/desktop-directories`` -- where ``.directory`` files live."""
    base = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return Path(base) / "desktop-directories"


def _menu_dir() -> Path:
    """``$XDG_CONFIG_HOME/menus/applications-merged`` -- auto-merged menu fragments."""
    base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(base) / "menus" / "applications-merged"


def install_menu_folder() -> None:
    """Write the ``.directory`` + ``.menu`` fragment that defines the folder.

    Idempotent: the content is static, so re-writing on every app install is
    cheap and self-healing (a hand-deleted fragment comes back on the next
    ``app refresh``).
    """
    dir_dir = _directories_dir()
    dir_dir.mkdir(parents=True, exist_ok=True)
    (dir_dir / _DIRECTORY_FILENAME).write_text(_DIRECTORY_CONTENT, encoding="utf-8")

    menu_dir = _menu_dir()
    menu_dir.mkdir(parents=True, exist_ok=True)
    (menu_dir / _MENU_FILENAME).write_text(_MENU_CONTENT, encoding="utf-8")


def remove_menu_folder() -> None:
    """Delete the ``.directory`` + ``.menu`` fragment (best-effort).

    Called once the last winpodx app is removed so we don't leave an empty
    folder behind.
    """
    (_directories_dir() / _DIRECTORY_FILENAME).unlink(missing_ok=True)
    (_menu_dir() / _MENU_FILENAME).unlink(missing_ok=True)
