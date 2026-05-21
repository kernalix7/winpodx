# SPDX-License-Identifier: MIT
"""Tests for :class:`winpodx.reverse_open.config.ReverseOpenConfig`.

Phase 1, PR 1: pure host-side schema. These tests pin the contract
described in ``docs/design/REVERSE_OPEN_DESIGN.md`` § "Component
contracts → config.py" — defaults stay opt-in, hand-edited TOML is
defended against, and the round-trip through ``Config.save`` /
``Config.load`` preserves user values.
"""

from __future__ import annotations

from winpodx.core.config import Config
from winpodx.reverse_open.config import ReverseOpenConfig

# ---- defaults --------------------------------------------------------


def test_defaults_enabled_and_lists_empty():
    """Fresh instance ships default-on: ``enabled=True``, no user lists."""
    cfg = ReverseOpenConfig()
    assert cfg.enabled is True
    assert cfg.allowlist == []
    assert cfg.last_synced_at == ""
    assert cfg.deny_dangerous is True


def test_defaults_fold_dangerous_into_denylist():
    """``deny_dangerous=True`` (default) seeds the denylist on init."""
    cfg = ReverseOpenConfig()
    # Every dangerous slug must appear in the effective denylist.
    for slug in ReverseOpenConfig.DANGEROUS_DEFAULTS:
        assert slug in cfg.denylist
    # No duplicates introduced.
    assert len(cfg.denylist) == len(set(cfg.denylist))


def test_dangerous_defaults_set_contents():
    """Pin the exact membership of the DANGEROUS_DEFAULTS set.

    Adding to this list is a deliberate security decision — this test
    ensures any future edit shows up as an explicit diff.
    """
    assert ReverseOpenConfig.DANGEROUS_DEFAULTS == frozenset(
        {
            "code",
            "vscodium",
            "atom",
            "gnome-terminal",
            "konsole",
            "xfce4-terminal",
            "alacritty",
            "kitty",
            "wezterm",
            "foot",
            "tilix",
        }
    )


# ---- deny_dangerous toggle -------------------------------------------


def test_deny_dangerous_false_does_not_fold():
    """Opting out of the default-deny disables the fold step."""
    cfg = ReverseOpenConfig(deny_dangerous=False)
    for slug in ReverseOpenConfig.DANGEROUS_DEFAULTS:
        assert slug not in cfg.denylist
    assert cfg.denylist == []


def test_deny_dangerous_false_preserves_user_denies():
    """User-supplied denylist survives the no-fold path intact."""
    cfg = ReverseOpenConfig(deny_dangerous=False, denylist=["my-app", "other-app"])
    assert cfg.denylist == ["my-app", "other-app"]


def test_deny_dangerous_true_unions_with_user_denies():
    """User denies + dangerous defaults coexist; no duplication."""
    cfg = ReverseOpenConfig(denylist=["my-app", "code"])
    # User entry preserved.
    assert "my-app" in cfg.denylist
    # Dangerous default folded in.
    assert "code" in cfg.denylist
    # No duplicate of "code" — fold is union, not append.
    assert cfg.denylist.count("code") == 1


# ---- slug regex defence ----------------------------------------------


def test_bad_slug_filtered_from_allowlist():
    """Slugs with whitespace / uppercase / punctuation are dropped.

    A hand-edited TOML can put anything here. The slug regex is the
    same one the listener uses to validate guest input, so an entry
    that wouldn't survive guest validation must not survive load
    either.
    """
    cfg = ReverseOpenConfig(allowlist=["good-slug", "foo bar baz", "Bad", "ok2"])
    assert cfg.allowlist == ["good-slug", "ok2"]


def test_bad_slug_filtered_from_denylist():
    cfg = ReverseOpenConfig(deny_dangerous=False, denylist=["good-slug", "BAD!", "ok"])
    assert cfg.denylist == ["good-slug", "ok"]


def test_non_string_slugs_dropped():
    """Non-string elements (int, None, dict) get binned silently."""
    cfg = ReverseOpenConfig(allowlist=["a", 42, None, "b", {"x": 1}])  # type: ignore[list-item]
    assert cfg.allowlist == ["a", "b"]


# ---- list-type coercion ----------------------------------------------


def test_non_list_allowlist_coerced_to_empty():
    cfg = ReverseOpenConfig(allowlist="not-a-list")  # type: ignore[arg-type]
    assert cfg.allowlist == []


def test_non_list_denylist_coerced_to_empty_then_dangerous_folded():
    """Coercion happens BEFORE the dangerous fold, so the user gets
    the safe defaults rather than a broken/empty denylist."""
    cfg = ReverseOpenConfig(denylist=42)  # type: ignore[arg-type]
    # Coerced to [] then folded.
    for slug in ReverseOpenConfig.DANGEROUS_DEFAULTS:
        assert slug in cfg.denylist


# ---- ISO-8601 round-trip ---------------------------------------------


def test_valid_iso8601_preserved():
    cfg = ReverseOpenConfig(last_synced_at="2026-05-07T09:41:00+00:00")
    assert cfg.last_synced_at == "2026-05-07T09:41:00+00:00"


def test_z_suffix_preserved():
    """``Z`` is normalised only for *parsing*; the original is kept."""
    cfg = ReverseOpenConfig(last_synced_at="2026-05-07T09:41:00Z")
    assert cfg.last_synced_at == "2026-05-07T09:41:00Z"


def test_bad_iso8601_cleared():
    cfg = ReverseOpenConfig(last_synced_at="not-a-timestamp")
    assert cfg.last_synced_at == ""


def test_non_string_iso8601_cleared():
    cfg = ReverseOpenConfig(last_synced_at=12345)  # type: ignore[arg-type]
    assert cfg.last_synced_at == ""


def test_empty_iso8601_stays_empty():
    cfg = ReverseOpenConfig(last_synced_at="")
    assert cfg.last_synced_at == ""


# ---- post_init idempotence -------------------------------------------


def test_post_init_idempotent():
    """Re-running ``__post_init__`` (as Config.load does) doesn't
    double-fold the dangerous defaults."""
    cfg = ReverseOpenConfig()
    before = list(cfg.denylist)
    cfg.__post_init__()
    cfg.__post_init__()
    assert cfg.denylist == before


# ---- Config.save / Config.load round-trip ----------------------------


def test_config_roundtrip_preserves_reverse_open_values(tmp_path, monkeypatch):
    """A user's choices survive disk round-trip through Config.save."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg = Config()
    cfg.reverse_open.enabled = True
    cfg.reverse_open.allowlist = ["org-kde-kate", "org-gimp-gimp"]
    cfg.reverse_open.last_synced_at = "2026-05-07T09:41:00+00:00"
    cfg.reverse_open.deny_dangerous = False
    # Strip the dangerous defaults that were folded in by the
    # default-True deny_dangerous path, then re-set the user's denies.
    cfg.reverse_open.denylist = ["custom-bad-app"]
    cfg.save()

    loaded = Config.load()
    assert loaded.reverse_open.enabled is True
    assert loaded.reverse_open.allowlist == ["org-kde-kate", "org-gimp-gimp"]
    assert loaded.reverse_open.last_synced_at == "2026-05-07T09:41:00+00:00"
    # deny_dangerous=False so the dangerous set is NOT re-folded on load.
    assert loaded.reverse_open.deny_dangerous is False
    assert loaded.reverse_open.denylist == ["custom-bad-app"]


def test_config_roundtrip_default_denylist_stable(tmp_path, monkeypatch):
    """A default config saved + loaded yields the same denylist
    (no double-fold via the round-trip)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg = Config()
    cfg.save()

    loaded = Config.load()
    assert sorted(loaded.reverse_open.denylist) == sorted(cfg.reverse_open.denylist)
    # And every dangerous default is in there exactly once.
    for slug in ReverseOpenConfig.DANGEROUS_DEFAULTS:
        assert loaded.reverse_open.denylist.count(slug) == 1


def test_config_load_revalidates_reverse_open(tmp_path, monkeypatch):
    """A hand-edited TOML with malformed slugs is sanitised on load."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = Config.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[reverse_open]\n"
        "enabled = true\n"
        'allowlist = ["good-slug", "Bad Slug", "AlsoBad!"]\n'
        'denylist = ["mine"]\n'
        'last_synced_at = "garbage"\n'
        "deny_dangerous = false\n",
        encoding="utf-8",
    )

    loaded = Config.load()
    assert loaded.reverse_open.enabled is True
    # Bad slugs filtered out; good one preserved.
    assert loaded.reverse_open.allowlist == ["good-slug"]
    # User-supplied denylist preserved (no fold because deny_dangerous=False).
    assert loaded.reverse_open.denylist == ["mine"]
    # Garbage timestamp cleared.
    assert loaded.reverse_open.last_synced_at == ""


def test_config_load_missing_section_yields_defaults(tmp_path, monkeypatch):
    """Old configs (pre-reverse_open) load with default values."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = Config.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[rdp]\nuser = "old-user"\n[pod]\nbackend = "podman"\n',
        encoding="utf-8",
    )

    loaded = Config.load()
    assert loaded.rdp.user == "old-user"
    # reverse_open defaults applied even though the section wasn't on disk.
    assert loaded.reverse_open.enabled is True
    assert loaded.reverse_open.allowlist == []
    assert loaded.reverse_open.deny_dangerous is True
    # Dangerous defaults still folded (because deny_dangerous=True default).
    for slug in ReverseOpenConfig.DANGEROUS_DEFAULTS:
        assert slug in loaded.reverse_open.denylist
