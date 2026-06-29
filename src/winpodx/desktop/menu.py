# SPDX-License-Identifier: MIT
"""Group every winpodx app under a "winpodx" desktop-menu folder, mirroring the
Windows Start Menu folder hierarchy as nested sub-groups (#581 Goal 2).

Mirrors how Wine creates its "Wine" submenu: a freedesktop ``.directory`` file
names each folder, an ``applications-merged/*.menu`` fragment maps a custom
category into it, and each app ``.desktop`` carries that category. The XDG menu
spec auto-merges the fragment into the system ``applications.menu``, so KDE
Plasma, XFCE, Cinnamon, MATE and LXQt all show the nested folders.

GNOME's overview is a flat grid that ignores menu folders -- the entries still
appear, just not grouped (GNOME app-folders are a separate gsettings mechanism
we don't touch here).

Each app's Start Menu subfolder is recorded in its ``.desktop`` as
``X-Winpodx-Folder=Microsoft Office/Tools`` (display path). The category it
carries is the LEAF only -- ``X-winpodx`` for a top-level app, or
``X-winpodx-microsoft-office-tools`` for a foldered one -- so an app appears in
exactly one place. ``install_menu_folder()`` rebuilds the whole nested ``.menu``
tree + one ``.directory`` per folder from the installed ``winpodx-*.desktop``
set on every refresh (and prunes stale per-folder ``.directory`` files).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from winpodx.utils.paths import applications_dir

log = logging.getLogger(__name__)

# Custom root category (X- prefixed, per the spec) that every top-level winpodx
# entry carries. Foldered entries carry a deeper "X-winpodx-<slug-chain>" token.
MENU_CATEGORY = "X-winpodx"

_DIRECTORY_FILENAME = "winpodx-windows.directory"
_MENU_FILENAME = "winpodx.menu"
# Custom .desktop key carrying the app's (display) Start Menu folder path.
FOLDER_KEY = "X-Winpodx-Folder"

_ROOT_DIRECTORY_CONTENT = """\
[Desktop Entry]
Version=1.0
Type=Directory
Name=WinPodX (Windows Apps)
Comment=Windows applications via WinPodX
Icon=winpodx
"""

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(component: str) -> str:
    """Slugify one folder component to ``[a-z0-9-]`` (empty if nothing usable)."""
    return _SLUG_RE.sub("-", component.strip().lower()).strip("-")


def _slug_chain(folder: str) -> list[tuple[str, str]]:
    """Split a display folder path into ``(cumulative_slug, display_component)``.

    ``"Microsoft Office/Tools"`` ->
        [("microsoft-office", "Microsoft Office"),
         ("microsoft-office-tools", "Tools")]
    Components that slugify to empty terminate the chain (so a junk component
    can't produce an empty / colliding node).
    """
    chain: list[tuple[str, str]] = []
    parts: list[str] = []
    for raw in folder.replace("\\", "/").split("/"):
        comp = raw.strip()
        s = _slug(comp)
        if not s:
            break
        parts.append(s)
        chain.append(("-".join(parts), comp))
    return chain


def category_for_folder(folder: str) -> str:
    """The single (leaf) category an app in ``folder`` carries.

    ``""`` -> ``"X-winpodx"`` (top-level); else the deepest
    ``"X-winpodx-<cumulative-slug>"``. Falls back to the root category when the
    folder slugifies to nothing.
    """
    chain = _slug_chain(folder or "")
    if not chain:
        return MENU_CATEGORY
    return f"{MENU_CATEGORY}-{chain[-1][0]}"


def _directories_dir() -> Path:
    """``$XDG_DATA_HOME/desktop-directories`` -- where ``.directory`` files live."""
    base = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return Path(base) / "desktop-directories"


def _menu_dir() -> Path:
    """``$XDG_CONFIG_HOME/menus/applications-merged`` -- auto-merged menu fragments."""
    base = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(base) / "menus" / "applications-merged"


def _xml_escape(text: str) -> str:
    """Escape text for inclusion in the .menu XML (defensive: node names are
    slugs/categories, but never trust that)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _read_app_folders() -> list[str]:
    """Collect the X-Winpodx-Folder value of every installed winpodx app entry."""
    folders: list[str] = []
    apps_dir = applications_dir()
    if not apps_dir.exists():
        return folders
    prefix = f"{FOLDER_KEY}="
    for desktop in apps_dir.glob("winpodx-*.desktop"):
        try:
            for line in desktop.read_text(encoding="utf-8").splitlines():
                if line.startswith(prefix):
                    folders.append(line[len(prefix) :].strip())
                    break
        except OSError:
            continue
    # Sorted so a slug-collision merge (distinct display names → same slug) keeps
    # a deterministic label across rebuilds (glob order is arbitrary otherwise).
    return sorted(folders)


def _build_tree(folders: list[str]) -> dict:
    """Build a nested ``{slug: {"display": str, "children": {...}}}`` tree from
    the observed folder paths (deduped, order-independent)."""
    root: dict = {}
    for folder in folders:
        node = root
        for cum_slug, display in _slug_chain(folder):
            entry = node.setdefault(cum_slug, {"display": display, "children": {}})
            node = entry["children"]
    return root


def _render_menu_nodes(tree: dict, indent: str) -> str:
    """Render the nested <Menu> fragment for a subtree (recursive)."""
    out = ""
    for cum_slug in sorted(tree):
        node = tree[cum_slug]
        cat = f"{MENU_CATEGORY}-{cum_slug}"
        out += f"{indent}<Menu>\n"
        out += f"{indent}  <Name>{_xml_escape(cum_slug)}</Name>\n"
        out += f"{indent}  <Directory>winpodx-folder-{cum_slug}.directory</Directory>\n"
        out += f"{indent}  <Include><Category>{_xml_escape(cat)}</Category></Include>\n"
        out += _render_menu_nodes(node["children"], indent + "  ")
        out += f"{indent}</Menu>\n"
    return out


def _render_menu(tree: dict) -> str:
    body = _render_menu_nodes(tree, "    ")
    return (
        '<!DOCTYPE Menu PUBLIC "-//freedesktop//DTD Menu 1.0//EN"\n'
        ' "http://www.freedesktop.org/standards/menu-spec/menu-1.0.dtd">\n'
        "<Menu>\n"
        "  <Name>Applications</Name>\n"
        "  <Menu>\n"
        "    <Name>winpodx</Name>\n"
        f"    <Directory>{_DIRECTORY_FILENAME}</Directory>\n"
        "    <Include>\n"
        f"      <Category>{MENU_CATEGORY}</Category>\n"
        "    </Include>\n"
        f"{body}"
        "  </Menu>\n"
        "</Menu>\n"
    )


def _directory_content(display: str) -> str:
    # Desktop Entry format (not XML): the display name is literal; the sanitizer
    # upstream already stripped control chars / newlines.
    return f"[Desktop Entry]\nVersion=1.0\nType=Directory\nName={display}\nIcon=folder\n"


def _flatten(tree: dict) -> list[tuple[str, str]]:
    """Flatten the tree into ``(cumulative_slug, display)`` pairs."""
    out: list[tuple[str, str]] = []
    for cum_slug, node in tree.items():
        out.append((cum_slug, node["display"]))
        out.extend(_flatten(node["children"]))
    return out


def install_menu_folder() -> None:
    """Write the nested winpodx menu: root + one folder node per Start Menu
    subfolder observed across the installed ``winpodx-*.desktop`` set.

    Rebuilt from scratch each call (cheap, idempotent, self-healing): it picks
    up new folders, drops emptied ones, and prunes stale per-folder
    ``.directory`` files. A hand-deleted fragment comes back on the next
    ``app refresh``.
    """
    tree = _build_tree(_read_app_folders())

    dir_dir = _directories_dir()
    dir_dir.mkdir(parents=True, exist_ok=True)
    (dir_dir / _DIRECTORY_FILENAME).write_text(_ROOT_DIRECTORY_CONTENT, encoding="utf-8")

    # Per-folder .directory files for the current tree. The "folder-" namespace
    # keeps them out of the root's name space so a Start Menu folder named
    # exactly "Windows" (slug "windows") can't clobber winpodx-windows.directory.
    wanted = {_DIRECTORY_FILENAME}
    for cum_slug, display in _flatten(tree):
        fname = f"winpodx-folder-{cum_slug}.directory"
        wanted.add(fname)
        (dir_dir / fname).write_text(_directory_content(display), encoding="utf-8")

    # Prune stale per-folder .directory files from a previous layout.
    for stale in dir_dir.glob("winpodx-*.directory"):
        if stale.name not in wanted:
            stale.unlink(missing_ok=True)

    menu_dir = _menu_dir()
    menu_dir.mkdir(parents=True, exist_ok=True)
    (menu_dir / _MENU_FILENAME).write_text(_render_menu(tree), encoding="utf-8")


def remove_menu_folder() -> None:
    """Delete the winpodx ``.menu`` fragment and every winpodx ``.directory``
    (root + per-folder), best-effort. Called once the last winpodx app is gone.
    """
    dir_dir = _directories_dir()
    for d in dir_dir.glob("winpodx-*.directory"):
        d.unlink(missing_ok=True)
    (dir_dir / _DIRECTORY_FILENAME).unlink(missing_ok=True)
    (_menu_dir() / _MENU_FILENAME).unlink(missing_ok=True)
