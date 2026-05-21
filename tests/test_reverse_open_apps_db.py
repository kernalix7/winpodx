# SPDX-License-Identifier: MIT
"""Tests for ``winpodx.reverse_open.apps_db``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from winpodx.reverse_open.apps_db import AppEntry, AppsDatabase, substitute_path


def _manifest(*apps: dict) -> dict:
    return {
        "version": 1,
        "generated_at": "2026-05-11T00:00:00Z",
        "host": {"xdg_current_desktop": "KDE"},
        "apps": list(apps),
    }


def _kate_dict() -> dict:
    return {
        "slug": "kate",
        "name": "Kate",
        "comment": "Advanced text editor",
        "exec_argv": ["/usr/bin/kate", "%F"],
        "icon_name": "kate",
        "mime_types": ["text/plain", "text/xml"],
        "desktop_file": "/usr/share/applications/org.kde.kate.desktop",
        "is_default_for": ["text/plain"],
    }


# --- load --------------------------------------------------------------------


def test_load_returns_empty_when_missing(tmp_path: Path) -> None:
    db = AppsDatabase.load(tmp_path / "nope.json")
    assert len(db) == 0
    assert db.slugs() == []


def test_load_returns_empty_when_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "apps.json"
    p.write_text("not json", encoding="utf-8")
    db = AppsDatabase.load(p)
    assert len(db) == 0


def test_load_returns_empty_when_top_level_not_object(tmp_path: Path) -> None:
    p = tmp_path / "apps.json"
    p.write_text("[]", encoding="utf-8")
    db = AppsDatabase.load(p)
    assert len(db) == 0


def test_load_rejects_unknown_version(tmp_path: Path) -> None:
    p = tmp_path / "apps.json"
    p.write_text(json.dumps({"version": 99, "apps": [_kate_dict()]}), encoding="utf-8")
    db = AppsDatabase.load(p)
    assert len(db) == 0


def test_load_happy_path(tmp_path: Path) -> None:
    p = tmp_path / "apps.json"
    p.write_text(json.dumps(_manifest(_kate_dict())), encoding="utf-8")
    db = AppsDatabase.load(p)
    assert len(db) == 1
    assert "kate" in db
    entry = db.get("kate")
    assert isinstance(entry, AppEntry)
    assert entry.slug == "kate"
    assert entry.name == "Kate"
    assert entry.exec_argv == ["/usr/bin/kate", "%F"]
    assert entry.is_default_for == ["text/plain"]


def test_load_skips_malformed_entries(tmp_path: Path) -> None:
    bad_slug = dict(_kate_dict(), slug="BAD UPPERCASE!")
    no_argv = dict(_kate_dict(), slug="empty-argv", exec_argv=[])
    p = tmp_path / "apps.json"
    p.write_text(
        json.dumps(_manifest(_kate_dict(), bad_slug, no_argv)),
        encoding="utf-8",
    )
    db = AppsDatabase.load(p)
    assert db.slugs() == ["kate"]


def test_load_preserves_generated_at(tmp_path: Path) -> None:
    p = tmp_path / "apps.json"
    p.write_text(json.dumps(_manifest(_kate_dict())), encoding="utf-8")
    db = AppsDatabase.load(p)
    assert db.generated_at == "2026-05-11T00:00:00Z"


# --- substitute_path ---------------------------------------------------------


def test_substitute_path_replaces_first_placeholder() -> None:
    assert substitute_path(["kate", "%F", "--readonly"], "/home/user/file.xml") == [
        "kate",
        "/home/user/file.xml",
        "--readonly",
    ]


def test_substitute_path_drops_subsequent_placeholders() -> None:
    # Two %f's: only the first survives, the second is dropped (we
    # never want to pass the path twice).
    assert substitute_path(["kate", "%f", "%U", "tail"], "/p") == [
        "kate",
        "/p",
        "tail",
    ]


def test_substitute_path_appends_when_no_placeholder() -> None:
    assert substitute_path(["xdg-open"], "/p") == ["xdg-open", "/p"]


def test_substitute_path_does_not_mutate_input() -> None:
    argv = ["kate", "%f"]
    substitute_path(argv, "/p")
    assert argv == ["kate", "%f"]


@pytest.mark.parametrize("token", ["%f", "%u", "%F", "%U"])
def test_substitute_path_all_four_placeholders(token: str) -> None:
    assert substitute_path(["app", token], "/x") == ["app", "/x"]
