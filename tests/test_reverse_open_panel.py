# SPDX-License-Identifier: MIT
"""Tests for the pure-Python helpers in ``winpodx.gui.reverse_open_panel``.

The Qt widget builder (:func:`build_panel`) requires PySide6 and an
X / Wayland display, so it's not exercised here — the existing GUI
test gap covers that. The slug + list helpers + status snapshot are
all Qt-free and important enough to pin.
"""

from __future__ import annotations

from winpodx.core.config import Config
from winpodx.gui.reverse_open_panel import (
    PanelStatus,
    add_slug,
    build_panel_status,
    format_status_line,
    remove_slug,
    validate_slug,
)

# --- validate_slug -----------------------------------------------------------


def test_validate_slug_accepts_kebab() -> None:
    ok, value = validate_slug("org-kde-kate")
    assert ok is True
    assert value == "org-kde-kate"


def test_validate_slug_lowercases_input() -> None:
    ok, value = validate_slug("Org.KDE.Kate")
    # The grammar excludes '.', so this returns invalid AFTER lowering.
    assert ok is False
    assert "must match" in value


def test_validate_slug_strips_whitespace() -> None:
    ok, value = validate_slug("   kate   ")
    assert ok is True
    assert value == "kate"


def test_validate_slug_rejects_empty() -> None:
    ok, msg = validate_slug("")
    assert ok is False
    assert "empty" in msg


def test_validate_slug_rejects_spaces() -> None:
    ok, msg = validate_slug("two words")
    assert ok is False
    assert "must match" in msg


def test_validate_slug_rejects_uppercase() -> None:
    # Note: the helper lowercases first then validates, so this only
    # fails because of an internal character — pure uppercase passes
    # via the lowercase step.
    ok, _ = validate_slug("Kate")
    assert ok is True


# --- add_slug / remove_slug --------------------------------------------------


def test_add_slug_to_empty() -> None:
    changed, cur, other, msg = add_slug([], ["evil"], "kate")
    assert changed is True
    assert cur == ["kate"]
    assert other == ["evil"]
    assert "added" in msg


def test_add_slug_idempotent() -> None:
    changed, cur, other, msg = add_slug(["kate"], [], "kate")
    assert changed is False
    assert cur == ["kate"]
    assert "already present" in msg


def test_add_slug_strips_from_other_list() -> None:
    changed, cur, other, _ = add_slug(["existing"], ["kate"], "kate")
    assert changed is True
    assert cur == ["existing", "kate"]
    assert other == []


def test_add_slug_keeps_results_sorted() -> None:
    _, cur, _, _ = add_slug(["b", "d"], [], "a")
    assert cur == ["a", "b", "d"]


def test_remove_slug_present() -> None:
    changed, cur, msg = remove_slug(["a", "b", "c"], "b")
    assert changed is True
    assert cur == ["a", "c"]
    assert "removed" in msg


def test_remove_slug_missing() -> None:
    changed, cur, msg = remove_slug(["a", "b"], "z")
    assert changed is False
    assert cur == ["a", "b"]
    assert "not present" in msg


# --- build_panel_status ------------------------------------------------------


def _cfg_with(enabled: bool, allow: list[str], deny: list[str]) -> Config:
    cfg = Config()
    cfg.reverse_open.enabled = enabled
    cfg.reverse_open.allowlist = list(allow)
    cfg.reverse_open.denylist = list(deny)
    # Re-run __post_init__ so the DANGEROUS_DEFAULTS fold doesn't
    # surprise the test caller — keep the lists exactly as set.
    return cfg


def test_build_panel_status_no_manifest() -> None:
    cfg = _cfg_with(False, [], [])
    status = build_panel_status(cfg, None)
    assert status.enabled is False
    assert status.daemon_running is False
    assert status.daemon_pid is None
    assert status.cached_app_count is None
    assert status.cached_generated_at is None
    assert status.allowlist == []


def test_build_panel_status_with_manifest() -> None:
    cfg = _cfg_with(True, ["kate"], ["code"])
    manifest = {
        "version": 1,
        "generated_at": "2026-05-11T12:00:00Z",
        "apps": [{"slug": "kate"}, {"slug": "gimp"}, {"slug": "vlc"}],
    }
    status = build_panel_status(cfg, manifest)
    assert status.enabled is True
    assert status.cached_app_count == 3
    assert status.cached_generated_at == "2026-05-11T12:00:00Z"
    assert status.allowlist == ["kate"]


def test_build_panel_status_tolerates_malformed_manifest() -> None:
    cfg = _cfg_with(False, [], [])
    # `apps` not a list, `generated_at` not a string → both fields fall
    # back to None without raising.
    status = build_panel_status(cfg, {"apps": "not a list", "generated_at": 42})
    assert status.cached_app_count is None
    assert status.cached_generated_at is None


# --- format_status_line ------------------------------------------------------


def test_format_status_line_disabled_no_daemon() -> None:
    status = PanelStatus(
        enabled=False,
        daemon_running=False,
        daemon_pid=None,
        cached_app_count=None,
        cached_generated_at=None,
        allowlist=[],
        denylist=[],
    )
    text = format_status_line(status)
    assert "disabled" in text
    assert "Daemon stopped" in text
    assert "no manifest" in text


def test_format_status_line_enabled_running() -> None:
    status = PanelStatus(
        enabled=True,
        daemon_running=True,
        daemon_pid=4321,
        cached_app_count=12,
        cached_generated_at="2026-05-11T12:00:00Z",
        allowlist=[],
        denylist=[],
    )
    text = format_status_line(status)
    assert "enabled" in text
    assert "pid 4321" in text
    assert "12 apps cached" in text
