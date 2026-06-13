# SPDX-License-Identifier: MIT
"""Tests for detached ``winpodx gui`` launch (#549)."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from winpodx.gui import spawn


class TestShouldDetachGui:
    def test_foreground_never_detaches(self):
        assert spawn.should_detach_gui(foreground=True) is False

    def test_interactive_tty_detaches(self):
        with patch("sys.stdout") as so, patch("sys.stdin") as si:
            so.isatty.return_value = True
            si.isatty.return_value = False
            assert spawn.should_detach_gui(foreground=False) is True

    def test_non_tty_stays_inline(self):
        # .desktop autostart / launcher subprocess: no terminal to free.
        with patch("sys.stdout") as so, patch("sys.stdin") as si:
            so.isatty.return_value = False
            si.isatty.return_value = False
            assert spawn.should_detach_gui(foreground=False) is False

    def test_isatty_raising_is_safe(self):
        with patch("sys.stdout") as so:
            so.isatty.side_effect = ValueError("detached")
            assert spawn.should_detach_gui(foreground=False) is False


class TestSpawnGuiDetached:
    def test_spawns_foreground_child_in_new_session(self):
        with (
            patch("winpodx.gui.spawn.shutil.which", return_value="/usr/bin/winpodx"),
            patch("winpodx.gui.spawn.subprocess.Popen") as popen,
        ):
            assert spawn.spawn_gui_detached() is True
        args, kw = popen.call_args
        assert args[0] == ["/usr/bin/winpodx", "gui", "--foreground"]
        assert kw["start_new_session"] is True

    def test_source_checkout_fallback(self):
        with (
            patch("winpodx.gui.spawn.shutil.which", return_value=None),
            patch("winpodx.gui.spawn.subprocess.Popen") as popen,
        ):
            assert spawn.spawn_gui_detached() is True
        cmd = popen.call_args[0][0]
        assert cmd[1:] == ["-m", "winpodx", "gui", "--foreground"]

    def test_popen_failure_returns_false(self):
        with (
            patch("winpodx.gui.spawn.shutil.which", return_value="/usr/bin/winpodx"),
            patch("winpodx.gui.spawn.subprocess.Popen", side_effect=OSError("boom")),
        ):
            assert spawn.spawn_gui_detached() is False


class TestGuiDispatch:
    def _dispatch(self, foreground):
        from winpodx.cli.main import _dispatch

        _dispatch(argparse.Namespace(command="gui", foreground=foreground))

    def test_interactive_detaches_without_running_loop(self, capsys):
        with (
            patch("winpodx.gui.main_window.run_gui") as run_gui,
            patch("winpodx.gui.spawn.should_detach_gui", return_value=True),
            patch("winpodx.gui.spawn.spawn_gui_detached", return_value=True) as sp,
        ):
            self._dispatch(foreground=False)
        sp.assert_called_once()
        run_gui.assert_not_called()
        assert "launched" in capsys.readouterr().out.lower()

    def test_foreground_runs_loop_inline(self):
        with (
            patch("winpodx.gui.main_window.run_gui") as run_gui,
            patch("winpodx.gui.spawn.should_detach_gui", return_value=False),
            patch("winpodx.gui.spawn.spawn_gui_detached") as sp,
        ):
            self._dispatch(foreground=True)
        run_gui.assert_called_once()
        sp.assert_not_called()

    def test_spawn_failure_falls_back_to_inline(self):
        # Detach wanted but the spawn failed -> still show a GUI in-process.
        with (
            patch("winpodx.gui.main_window.run_gui") as run_gui,
            patch("winpodx.gui.spawn.should_detach_gui", return_value=True),
            patch("winpodx.gui.spawn.spawn_gui_detached", return_value=False),
        ):
            self._dispatch(foreground=False)
        run_gui.assert_called_once()


def test_gui_subparser_accepts_foreground():
    from winpodx.cli.main import cli

    # --foreground must parse; we stub the heavy dispatch so nothing launches.
    with (
        patch("winpodx.cli.main._dispatch") as disp,
        patch("winpodx.cli.first_run.maybe_run_first_run_prompt"),
    ):
        try:
            cli(["gui", "--foreground"])
        except SystemExit:
            pytest.fail("`gui --foreground` should parse cleanly")
    assert disp.call_args[0][0].foreground is True
