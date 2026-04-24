"""Tests for cli.migrate — post-upgrade wizard.

Covers version tuple parsing, installed-version I/O, fresh-install vs
pre-tracker-upgrade detection, release-note selection across a version
range, interactive prompt handling (mocked stdin), and the happy-path
flow through ``run_migrate`` with refresh skipped.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

from winpodx.cli.migrate import (
    _VERSION_NOTES,
    _detect_installed_version,
    _print_whats_new,
    _prompt_yes,
    _read_installed_version,
    _version_tuple,
    _write_installed_version,
    run_migrate,
)

# --- _version_tuple ---


def test_version_tuple_basic():
    assert _version_tuple("0.1.8") == (0, 1, 8)


def test_version_tuple_four_segments():
    assert _version_tuple("1.2.3.4") == (1, 2, 3, 4)


def test_version_tuple_prerelease_truncated():
    # Non-integer suffix ('rc1') stops parsing; ordering still works.
    assert _version_tuple("0.1.8rc1") == (0, 1)
    assert _version_tuple("0.1.8") > _version_tuple("0.1.8rc1")


def test_version_tuple_comparison():
    assert _version_tuple("0.1.7") < _version_tuple("0.1.8")
    assert _version_tuple("0.1.8") < _version_tuple("0.2.0")
    assert _version_tuple("1.0.0") > _version_tuple("0.9.99")


# --- Version marker file I/O ---


def test_write_and_read_installed_version(tmp_path, monkeypatch):
    # Redirect config_dir via a lambda so the helpers use our tmp path.
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    _write_installed_version("0.1.8")
    assert (tmp_path / "installed_version.txt").exists()
    assert _read_installed_version() == "0.1.8"


def test_read_installed_version_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    assert _read_installed_version() is None


def test_read_installed_version_empty_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("\n", encoding="utf-8")
    assert _read_installed_version() is None


def test_read_installed_version_strips_whitespace(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("  0.1.8  \n", encoding="utf-8")
    assert _read_installed_version() == "0.1.8"


# --- _detect_installed_version ---


def test_detect_returns_none_for_fresh_install(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    # No marker, no config file => treated as fresh install.
    with patch("winpodx.core.config.Config.path", return_value=tmp_path / "noconfig.toml"):
        assert _detect_installed_version() is None


def test_detect_returns_pretracker_when_config_exists(tmp_path, monkeypatch):
    """Config exists but no marker -> user was on 0.1.7 before this command existed."""
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    cfg_path = tmp_path / "winpodx.toml"
    cfg_path.write_text("[rdp]\n", encoding="utf-8")
    with patch("winpodx.core.config.Config.path", return_value=cfg_path):
        assert _detect_installed_version() == "0.1.7"


def test_detect_prefers_marker_over_baseline(tmp_path, monkeypatch):
    """Marker file wins even when config also exists."""
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("0.1.9\n", encoding="utf-8")
    cfg_path = tmp_path / "winpodx.toml"
    cfg_path.write_text("[rdp]\n", encoding="utf-8")
    with patch("winpodx.core.config.Config.path", return_value=cfg_path):
        assert _detect_installed_version() == "0.1.9"


# --- _print_whats_new ---


def test_whats_new_covers_range(capsys):
    _print_whats_new("0.1.7", "0.1.8")
    out = capsys.readouterr().out
    assert "0.1.8" in out
    # A known 0.1.8 bullet must appear.
    assert "winpodx app refresh" in out


def test_whats_new_empty_range(capsys):
    # No bullets when neither endpoint covers a release with recorded notes.
    _print_whats_new("0.2.0", "0.2.1")
    out = capsys.readouterr().out
    assert "no user-facing release notes" in out.lower()


def test_whats_new_notes_present_for_current_version():
    """The current-release key in _VERSION_NOTES must be the one documented."""
    assert "0.1.8" in _VERSION_NOTES
    assert len(_VERSION_NOTES["0.1.8"]) >= 3


# --- _prompt_yes ---


def test_prompt_yes_default_accept(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt_yes("x?", default=True) is True


def test_prompt_yes_default_decline(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert _prompt_yes("x?", default=False) is False


def test_prompt_yes_explicit_y(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "y")
    assert _prompt_yes("x?", default=False) is True


def test_prompt_yes_explicit_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "N")
    assert _prompt_yes("x?", default=True) is False


def test_prompt_yes_eof_returns_false(monkeypatch):
    def _raise_eof(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", _raise_eof)
    assert _prompt_yes("x?") is False


# --- run_migrate ---


def _args(no_refresh: bool = True, non_interactive: bool = True) -> argparse.Namespace:
    return argparse.Namespace(no_refresh=no_refresh, non_interactive=non_interactive)


def test_run_migrate_fresh_install_writes_marker(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    with patch("winpodx.core.config.Config.path", return_value=tmp_path / "noconfig.toml"):
        rc = run_migrate(_args())
    assert rc == 0
    assert (tmp_path / "installed_version.txt").exists()
    assert "fresh install" in capsys.readouterr().out.lower()


def test_run_migrate_already_current(tmp_path, monkeypatch, capsys):
    from winpodx import __version__ as current

    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text(current + "\n", encoding="utf-8")
    rc = run_migrate(_args())
    assert rc == 0
    assert "already current" in capsys.readouterr().out.lower()


def test_run_migrate_upgrade_skips_refresh_when_flagged(tmp_path, monkeypatch, capsys):
    """Use 0.1.0 as the 'installed' version so the test is robust whether
    or not the current package version has been bumped yet.
    """
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("0.1.0\n", encoding="utf-8")
    rc = run_migrate(_args(no_refresh=True, non_interactive=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "0.1.0" in out
    assert "Skipping app discovery" in out
    # Marker is bumped to current version on completion.
    from winpodx import __version__ as current

    assert (tmp_path / "installed_version.txt").read_text().strip() == current


def test_run_migrate_non_interactive_skips_prompt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("winpodx.cli.migrate.config_dir", lambda: tmp_path)
    (tmp_path / "installed_version.txt").write_text("0.1.0\n", encoding="utf-8")
    # no_refresh=False but non_interactive=True -> must not block on input.
    called = []
    monkeypatch.setattr("builtins.input", lambda _: called.append(True) or "y")
    rc = run_migrate(_args(no_refresh=False, non_interactive=True))
    assert rc == 0
    assert called == []  # input() must never have been called
    out = capsys.readouterr().out.lower()
    assert "--non-interactive" in out
