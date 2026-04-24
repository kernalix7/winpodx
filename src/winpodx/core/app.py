"""Windows application discovery, registration, and launching."""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
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
    # Provenance: "bundled" (shipped in data/apps), "discovered" (auto
    # from guest enumerator), "user" (manually placed in
    # ~/.local/share/winpodx/apps). Surfaces a Detected/Bundled badge
    # in the GUI and lets tooling decide what it can safely overwrite
    # on a rediscovery pass.
    source: str = "bundled"
    # Optional extras populated by discovery; blank for bundled entries.
    args: str = ""
    wm_class_hint: str = ""
    launch_uri: str = ""  # UWP AUMID (shell:AppsFolder\<AUMID>)


def bundled_apps_dir() -> Path:
    """Path to bundled app definitions shipped with winpodx.

    Tries locations in order and returns the first that exists:
      1. Source / editable install: ``<repo>/data/apps`` (4 levels above)
      2. pip wheel install: ``<sys.prefix>/share/winpodx/data/apps``
      3. User install: ``~/.local/share/winpodx/data/apps``

    Mirrors ``winpodx.desktop.icons.bundled_data_path`` (inlined to avoid
    a core -> desktop dependency). If none exist, returns the source-layout
    path so callers see a non-existent Path rather than None.
    """
    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent / "data" / "apps",
        Path(sys.prefix) / "share" / "winpodx" / "data" / "apps",
        Path.home() / ".local" / "share" / "winpodx" / "data" / "apps",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def user_apps_dir() -> Path:
    """Path to user-installed app definitions."""
    return data_dir() / "apps"


def discovered_apps_dir() -> Path:
    """Path to auto-discovered app definitions (written by discovery)."""
    return data_dir() / "discovered"


_VALID_APP_SOURCES = frozenset({"bundled", "discovered", "user"})


def load_app(app_dir: Path, default_source: str = "bundled") -> AppInfo | None:
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
    """List all available app definitions (bundled + discovered + user).

    Entries found under the discovered dir are loaded with
    ``source="discovered"``; the user dir defaults to ``"user"``.
    If two dirs define the same ``name`` the later one wins in the
    search order ``bundled -> discovered -> user`` so a user-authored
    override beats both. Skips any entry that resolves outside its
    containing dir so a malicious symlink cannot cause us to load
    attacker-controlled TOML.
    """
    sources: list[tuple[Path, str]] = [
        (bundled_apps_dir(), "bundled"),
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
