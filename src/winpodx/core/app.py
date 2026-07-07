# SPDX-License-Identifier: MIT
"""Windows application discovery, registration, and launching."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # Python 3.9, 3.10

from winpodx.utils.paths import data_dir

log = logging.getLogger(__name__)

# Only allow safe characters in app names (alphanumeric, dash, underscore)
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass
class AppInfo:
    name: str
    full_name: str
    executable: str
    icon_path: str = ""
    categories: list[str] = field(default_factory=list)
    mime_types: list[str] = field(default_factory=list)
    installed: bool = False
    # Provenance: "discovered" (auto from guest enumerator) or "user"
    # (manually placed in ~/.local/share/winpodx/apps). Surfaces a
    # Detected badge in the GUI. v0.1.9 dropped the "bundled" source
    # entirely — discovery is the only path that populates the menu.
    source: str = "user"
    # Optional extras populated by discovery; blank for user entries.
    args: str = ""
    wm_class_hint: str = ""
    launch_uri: str = ""  # UWP AUMID (shell:AppsFolder\<AUMID>)
    # One-line description from the guest's app metadata (Win32 exe
    # ProductName / .lnk Comment / UWP AppxManifest <Description>).
    # Lands in the .desktop Comment= field so the user's app menu shows
    # the actual app description instead of the generic "Windows
    # application via winpodx" stamp.
    description: str = ""
    # Hybrid filter state (set by discovery, overridable by the user via
    # the GUI's "Hide / Show" action). The GUI grid filters where
    # `hidden=True`; toggling adds an explicit override that survives
    # the next discovery sweep so a user's preference is sticky.
    hidden: bool = False
    # True when this entry is in the curated essentials list (File
    # Explorer, Calculator, Settings, …). Even when the guest scan
    # missed it, persist_discovered() synthesizes a stub so essentials
    # always appear. Users can still hide them.
    essential: bool = False
    # SHA256 of the guest exe (discovery). The periodic icon-refresh compares
    # this to re-extract only apps whose exe changed (an app update). "" for
    # UWP / user entries.
    exe_hash: str = ""
    # #581 Goal 2: the app's Windows Start Menu subfolder (relative, "/"-joined,
    # e.g. "Microsoft Office/Tools"). "" = top-level. Mirrored into a nested
    # winpodx submenu by the desktop-entry generator.
    start_menu_folder: str = ""
    # #421/#694: URL schemes the Windows app registered as a handler for
    # (mailto, https, slack, ...). Emitted as x-scheme-handler/<scheme> in the
    # .desktop MimeType so clicking a host URL opens it in this Windows app.
    url_schemes: list[str] = field(default_factory=list)


def user_apps_dir() -> Path:
    """Path to user-installed app definitions."""
    return data_dir() / "apps"


def discovered_apps_dir() -> Path:
    """Path to auto-discovered app definitions (written by discovery)."""
    return data_dir() / "discovered"


def _suppressed_file() -> Path:
    """Tombstone list of discovered slugs the user deleted (#514).

    Deleting a discovered profile only removes its directory — the next
    discovery sweep would re-create it. Recording the slug here lets
    ``persist_discovered`` skip it so a deleted auto-discovered app (the
    "garbage" entries) stays gone across rescans.
    """
    return data_dir() / "discovered_suppressed.txt"


def suppressed_app_slugs() -> set[str]:
    """Slugs the user has deleted from discovery (skipped on rediscovery)."""
    try:
        text = _suppressed_file().read_text(encoding="utf-8")
    except OSError:
        return set()
    return {
        ln.strip() for ln in text.splitlines() if ln.strip() and _SAFE_NAME_RE.match(ln.strip())
    }


def suppress_app_slug(slug: str) -> None:
    """Add *slug* to the discovery suppress list (idempotent)."""
    if not _SAFE_NAME_RE.match(slug):
        return
    current = suppressed_app_slugs()
    if slug in current:
        return
    current.add(slug)
    path = _suppressed_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(sorted(current)) + "\n", encoding="utf-8")
    except OSError as e:  # noqa: BLE001
        log.warning("could not record suppressed app %r: %s", slug, e)


def unsuppress_app_slug(slug: str) -> None:
    """Remove *slug* from the suppress list so discovery can re-add it."""
    current = suppressed_app_slugs()
    if slug not in current:
        return
    current.discard(slug)
    path = _suppressed_file()
    try:
        if current:
            path.write_text("\n".join(sorted(current)) + "\n", encoding="utf-8")
        else:
            path.unlink(missing_ok=True)
    except OSError:  # noqa: BLE001
        pass


def clear_suppressed_slugs() -> int:
    """Drop every deletion tombstone so the next discovery re-adds them (#530).

    Powers the GUI's "Re-detect all" — undeletes every app the user previously
    removed in one shot. Returns the number of tombstones cleared. The apps
    themselves reappear on the next discovery sweep (their ``discovered/<slug>``
    dirs were removed on delete), so callers should trigger a refresh after.
    """
    current = suppressed_app_slugs()
    if not current:
        return 0
    try:
        _suppressed_file().unlink(missing_ok=True)
    except OSError as e:  # noqa: BLE001
        log.warning("could not clear suppressed-app list: %s", e)
        return 0
    return len(current)


_VALID_APP_SOURCES = frozenset({"discovered", "user"})


def load_app(app_dir: Path, default_source: str = "user") -> AppInfo | None:
    """Load an app definition from a directory containing app.toml.

    ``default_source`` is used when the TOML does not declare a
    ``source`` field; ``list_available_apps`` passes the provenance of
    the containing directory so discovered entries are flagged even if
    the author of the TOML forgot to set the field.
    """
    toml_path = app_dir / "app.toml"
    if not toml_path.exists():
        return None

    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None

    # Validate app name to prevent path traversal and command injection
    name = data.get("name", "")
    if not name or len(name) > 255 or not _SAFE_NAME_RE.match(name):
        return None

    executable = data.get("executable", "")
    if not executable:
        return None

    icon = ""
    for ext in ("svg", "png"):
        candidate = app_dir / f"icon.{ext}"
        if candidate.exists():
            icon = str(candidate)
            break

    source = data.get("source", default_source)
    if source not in _VALID_APP_SOURCES:
        source = default_source

    return AppInfo(
        name=name,
        full_name=data.get("full_name", name),
        executable=executable,
        icon_path=icon,
        categories=data.get("categories", []) or [],
        mime_types=data.get("mime_types", []) or [],
        source=source,
        args=data.get("args", "") or "",
        wm_class_hint=data.get("wm_class_hint", "") or "",
        launch_uri=data.get("launch_uri", "") or "",
        description=data.get("description", "") or "",
        hidden=bool(data.get("hidden", False)),
        essential=bool(data.get("essential", False)),
        exe_hash=str(data.get("exe_hash", "") or ""),
        start_menu_folder=str(data.get("start_menu_folder", "") or ""),
        url_schemes=data.get("url_schemes", []) or [],
    )


def _is_within(candidate: Path, root: Path) -> bool:
    """Return True if ``candidate`` resolves inside ``root`` (symlink-safe).

    Mirrors the guard in ``desktop/icons.bundled_data_path`` so that a
    user-writable apps directory (``~/.local/share/winpodx/data/apps``)
    cannot smuggle a symlink like ``apps/evil -> /etc`` into our loader
    and cause ``load_app`` to read ``/etc/app.toml``.
    """
    try:
        candidate_resolved = candidate.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    return candidate_resolved.is_relative_to(root_resolved)


def list_available_apps() -> list[AppInfo]:
    """List all available app definitions (discovered + user).

    v0.1.9 dropped the bundled-profile set; the menu is now populated
    entirely by `winpodx app refresh` (auto-fired on first pod boot)
    and any manually-authored entries the user drops into
    ``~/.local/share/winpodx/apps``. Entries found under the discovered
    dir are loaded with ``source="discovered"``; the user dir defaults
    to ``"user"``. If two dirs define the same ``name`` the user dir
    wins. Skips any entry that resolves outside its containing dir so a
    malicious symlink cannot cause us to load attacker-controlled TOML.
    """
    sources: list[tuple[Path, str]] = [
        (discovered_apps_dir(), "discovered"),
        (user_apps_dir(), "user"),
    ]
    by_name: dict[str, AppInfo] = {}
    order: list[str] = []
    for source_dir, provenance in sources:
        if not source_dir.exists():
            continue
        for app_dir in sorted(source_dir.iterdir()):
            if not app_dir.is_dir():
                continue
            if not _is_within(app_dir, source_dir):
                log.warning(
                    "Rejecting app entry that escapes its source dir: %s",
                    app_dir,
                )
                continue
            app = load_app(app_dir, default_source=provenance)
            if app is None:
                continue
            prev = by_name.get(app.name)
            if provenance == "user" and not app.icon_path and prev is not None and prev.icon_path:
                # A user override only carries metadata -- the GUI editor can't
                # set an icon -- so an override created by editing name/MIME has
                # no icon file and would otherwise fall back to the generic
                # letter glyph (#530). Inherit the discovered profile's icon.
                # This also keeps it FRESH: a later discovery re-extract updates
                # discovered/<name>/icon.*, which flows through here (vs freezing
                # a copy in the override that a guest-side app update can't
                # refresh).
                app = replace(app, icon_path=prev.icon_path)
            if app.name not in by_name:
                order.append(app.name)
            by_name[app.name] = app
    return [by_name[n] for n in order]


def find_app(name: str) -> AppInfo | None:
    """Find an app by short name."""
    for app in list_available_apps():
        if app.name == name:
            return app
    return None


def _find_app_dir(name: str) -> Path | None:
    """Return the directory holding ``<name>/app.toml`` (user dir wins over
    discovered), or None. Name is validated to block path traversal."""
    if not name or len(name) > 255 or not _SAFE_NAME_RE.match(name):
        return None
    for base in (user_apps_dir(), discovered_apps_dir()):
        cand = base / name
        if _is_within(cand, base) and (cand / "app.toml").is_file():
            return cand
    return None


def set_app_hidden(name: str, hidden: bool) -> AppInfo | None:
    """Set an app's ``hidden`` flag in its app.toml and sync its Linux menu entry.

    Hidden apps are dropped from the app menu (``.desktop`` removed); shown apps
    get it (re)installed. The flag is persisted to app.toml so the choice
    survives the next discovery sweep (discovery honours an explicit override).
    Returns the updated :class:`AppInfo`, or ``None`` if the app isn't found.
    """
    from winpodx.utils.toml_writer import dumps as toml_dumps

    app_dir = _find_app_dir(name)
    if app_dir is None:
        return None
    toml_path = app_dir / "app.toml"
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        return None
    data["hidden"] = bool(hidden)
    toml_path.write_text(toml_dumps(data), encoding="utf-8")

    app = load_app(app_dir)
    if app is None:
        return None

    # Sync the Linux app-menu entry: drop it when hidden, (re)install when shown.
    try:
        from winpodx.desktop.entry import install_desktop_entry, remove_desktop_entry
        from winpodx.desktop.icons import update_icon_cache

        if hidden:
            remove_desktop_entry(name)
        else:
            install_desktop_entry(app)
        update_icon_cache()
    except Exception as e:  # noqa: BLE001 — menu sync is best-effort
        log.warning("desktop entry sync for %s failed: %s", name, e)
    return app


def discovered_profile_exists(name: str) -> bool:
    """True if an auto-discovered profile exists for ``name`` (#530).

    Used by the GUI to decide whether a user override is *revertible*: a
    "Reset to detected" action only makes sense when an auto-discovered
    ``discovered/<name>`` profile is there to fall back to.
    """
    if not name or len(name) > 255 or not _SAFE_NAME_RE.match(name):
        return False
    d = discovered_apps_dir() / name
    return _is_within(d, discovered_apps_dir()) and (d / "app.toml").is_file()


def reset_app_profile(name: str) -> AppInfo | None:
    """Discard a user override and restore the auto-discovered profile (#530).

    Editing a discovered app in the GUI writes a user override under
    ``apps/<name>`` that shadows the auto-discovered ``discovered/<name>``
    profile (``list_available_apps`` lets the user dir win). When the user
    later wants the original back -- the detected metadata *and* its
    freshly-extracted icon -- reverting deletes ONLY that override, leaving
    the discovered profile untouched, then re-syncs the Linux menu entry so
    the discovered icon + fields return.

    Returns the restored (discovered) :class:`AppInfo`, or ``None`` when there
    is no override to remove or no discovered profile to fall back to -- in
    which case the caller should treat the request as a plain delete instead.
    """
    import shutil

    if not name or len(name) > 255 or not _SAFE_NAME_RE.match(name):
        return None

    user_dir = user_apps_dir() / name
    discovered_dir = discovered_apps_dir() / name
    # A revert only makes sense when a user override shadows a discovered twin.
    if not (_is_within(user_dir, user_apps_dir()) and user_dir.is_dir()):
        return None
    if not (
        _is_within(discovered_dir, discovered_apps_dir())
        and (discovered_dir / "app.toml").is_file()
    ):
        return None

    shutil.rmtree(user_dir, ignore_errors=True)

    app = load_app(discovered_dir, default_source="discovered")
    if app is None:
        return None

    # Re-sync the Linux menu entry so the discovered icon + metadata return.
    try:
        from winpodx.desktop.entry import install_desktop_entry, remove_desktop_entry
        from winpodx.desktop.icons import update_icon_cache

        if app.hidden:
            remove_desktop_entry(name)
        else:
            install_desktop_entry(app)
        update_icon_cache()
    except Exception as e:  # noqa: BLE001 — menu sync is best-effort
        log.warning("desktop entry sync for %s failed: %s", name, e)
    return app
