"""Windows application discovery, registration, and launching."""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from winpodx.utils.paths import data_dir

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


def bundled_apps_dir() -> Path:
    """Path to bundled app definitions shipped with winpodx.

    Tries locations in order and returns the first that exists:
      1. Source / editable install: ``<repo>/data/apps`` (4 levels above)
      2. pip wheel install: ``<sys.prefix>/share/winpodx/data/apps``
      3. User install: ``~/.local/share/winpodx/data/apps``

    Mirrors ``winpodx.desktop.icons.bundled_data_path`` — kept inline here
    to avoid a core -> desktop dependency. If none exist, returns the
    source-layout path so callers see a non-existent Path rather than
    None (matches prior behaviour).
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


def load_app(app_dir: Path) -> AppInfo | None:
    """Load an app definition from a directory containing app.toml."""
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

    return AppInfo(
        name=name,
        full_name=data.get("full_name", name),
        executable=executable,
        icon_path=icon,
        categories=data.get("categories", []),
        mime_types=data.get("mime_types", []),
    )


def list_available_apps() -> list[AppInfo]:
    """List all available app definitions (bundled + user)."""
    apps: list[AppInfo] = []
    for source in (bundled_apps_dir(), user_apps_dir()):
        if not source.exists():
            continue
        for app_dir in sorted(source.iterdir()):
            if app_dir.is_dir():
                app = load_app(app_dir)
                if app:
                    apps.append(app)
    return apps


def find_app(name: str) -> AppInfo | None:
    """Find an app by short name."""
    for app in list_available_apps():
        if app.name == name:
            return app
    return None
