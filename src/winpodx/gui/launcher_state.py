# SPDX-License-Identifier: MIT
"""Persistent launcher pin and recent-app state for the WinPodX GUI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from winpodx.utils.paths import APP_NAME, data_dir

_STATE_FILENAME = "launcher_state.json"
_RECENT_LIMIT = 8


def _state_dir() -> Path:
    """Return the XDG state directory, falling back to the data dir."""
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / APP_NAME
    home = Path.home()
    if str(home):
        return home / ".local" / "state" / APP_NAME
    return data_dir()


def _state_path() -> Path:
    return _state_dir() / _STATE_FILENAME


def _normalize(raw: Any) -> dict:
    state = raw if isinstance(raw, dict) else {}
    pinned = _clean_names(state.get("pinned", []))
    recent = _clean_names(state.get("recent", []))[:_RECENT_LIMIT]
    return {"pinned": pinned, "recent": recent}


def _clean_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def load() -> dict:
    """Load launcher state, creating the JSON file if it is missing."""
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        state = {"pinned": [], "recent": []}
        save(state)
        return state
    try:
        state = _normalize(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        state = {"pinned": [], "recent": []}
    save(state)
    return state


def save(state: dict) -> None:
    """Persist launcher state."""
    normalized = _normalize(state)
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def pin(name: str) -> None:
    """Pin an app by its stable app name."""
    app_name = name.strip()
    if not app_name:
        return
    state = load()
    pinned = state["pinned"]
    if app_name not in pinned:
        pinned.append(app_name)
        save(state)


def unpin(name: str) -> None:
    """Remove an app from the pinned row."""
    app_name = name.strip()
    if not app_name:
        return
    state = load()
    state["pinned"] = [item for item in state["pinned"] if item != app_name]
    save(state)


def is_pinned(name: str) -> bool:
    """Return whether an app is pinned."""
    return name.strip() in load()["pinned"]


def record_recent(name: str) -> None:
    """Record a successful launch, capped at 8 most-recent unique apps."""
    app_name = name.strip()
    if not app_name:
        return
    state = load()
    state["recent"] = [app_name] + [item for item in state["recent"] if item != app_name]
    state["recent"] = state["recent"][:_RECENT_LIMIT]
    save(state)


def get_pinned() -> list[str]:
    """Return pinned app names in user-defined order."""
    return list(load()["pinned"])


def get_recent() -> list[str]:
    """Return recent app names, most recent first."""
    return list(load()["recent"])
