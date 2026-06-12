# SPDX-License-Identifier: MIT
"""Tests for the quick app launcher (PR #561, reworked to source from core).

The launcher used to re-parse ``.desktop`` files and grab raw keyboard input
via evdev/pynput. It now builds its list from ``core.app.list_available_apps``
and is opened via ``winpodx launch`` (DE shortcut), so these tests pin that the
reimplementation/hotkey machinery is gone and discovery is core-backed.
"""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_discover_apps_sources_from_core(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from winpodx.core.app import data_dir
    from winpodx.gui import launcher

    for full, name in [("Word", "word"), ("Excel", "excel")]:
        d = data_dir() / "discovered" / name
        d.mkdir(parents=True)
        (d / "app.toml").write_text(
            f'name = "{name}"\nfull_name = "{full}"\n'
            f'executable = "C:\\\\x.exe"\ncategories = ["Office"]\n',
            encoding="utf-8",
        )
    # a hidden app must be skipped
    h = data_dir() / "discovered" / "noise1"
    h.mkdir(parents=True)
    (h / "app.toml").write_text(
        'name = "noise1"\nfull_name = "Noise"\nexecutable = "C:\\\\n.exe"\nhidden = true\n',
        encoding="utf-8",
    )

    entries = launcher.discover_apps()
    by_slug = {e.slug: e for e in entries}
    assert set(by_slug) == {"word", "excel"}  # hidden skipped, core-sourced
    assert by_slug["word"].name == "Word"
    # launches via the canonical winpodx path, not a bespoke Exec line
    assert by_slug["word"].exec_ == "winpodx app run word"


def test_no_global_hotkey_or_desktop_reparse(monkeypatch, tmp_path):
    """The evdev/pynput global-hotkey grab and .desktop re-parser are gone;
    activation is delegated to the DE via ``winpodx launch``."""
    from winpodx.gui import launcher

    for gone in (
        "_start_hotkey_listener",
        "_start_evdev_listener",
        "_start_pynput_listener",
        "parse_desktop_file",
        "HotkeySignals",
    ):
        assert not hasattr(launcher, gone), gone
    assert hasattr(launcher, "show_launcher")


def test_launch_command_dispatches_to_show_launcher(monkeypatch):
    """`winpodx launch` routes to the launcher entry point."""
    import argparse

    import winpodx.gui.launcher as launcher
    from winpodx.cli.main import _dispatch

    called = []
    monkeypatch.setattr(launcher, "show_launcher", lambda: called.append(True))
    _dispatch(argparse.Namespace(command="launch"))
    assert called == [True]
