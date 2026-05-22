# SPDX-License-Identifier: MIT
"""Tests for the ``winpodx uninstall`` Python wrapper (#255 consolidation).

The wrapper's only job is to locate ``uninstall.sh`` and ``exec`` it.
Tests verify (1) the candidate paths cover every install topology and
(2) ``os.execvp`` is invoked with the right argv when a script is found.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from winpodx.cli.uninstall import _candidate_paths, handle_uninstall


class TestCandidatePaths:
    def test_includes_system_share(self):
        paths = _candidate_paths()
        assert Path("/usr/share/winpodx/uninstall.sh") in paths
        assert Path("/usr/local/share/winpodx/uninstall.sh") in paths

    def test_includes_curl_bundle(self):
        paths = _candidate_paths()
        assert Path.home() / ".local" / "bin" / "winpodx-app" / "uninstall.sh" in paths

    def test_includes_venv_prefix(self):
        paths = _candidate_paths()
        assert Path(sys.prefix) / "share" / "winpodx" / "uninstall.sh" in paths

    def test_includes_dev_repo_root(self):
        paths = _candidate_paths()
        # Should include something ending in repo-root / uninstall.sh
        assert any(p.name == "uninstall.sh" for p in paths)


class TestHandleUninstall:
    def test_exec_invoked_when_script_found(self, tmp_path):
        fake_script = tmp_path / "uninstall.sh"
        fake_script.write_text("#!/usr/bin/env bash\nexit 0\n")
        fake_script.chmod(0o755)

        with (
            patch(
                "winpodx.cli.uninstall._candidate_paths",
                return_value=[fake_script],
            ),
            patch("os.execvp") as mock_exec,
        ):
            handle_uninstall(argparse.Namespace(purge=False, yes=False))
            mock_exec.assert_called_once_with("bash", ["bash", str(fake_script)])

    def test_purge_flag_forwarded(self, tmp_path):
        fake_script = tmp_path / "uninstall.sh"
        fake_script.write_text("#!/usr/bin/env bash\nexit 0\n")
        fake_script.chmod(0o755)

        with (
            patch(
                "winpodx.cli.uninstall._candidate_paths",
                return_value=[fake_script],
            ),
            patch("os.execvp") as mock_exec,
        ):
            handle_uninstall(argparse.Namespace(purge=True, yes=False))
            args = mock_exec.call_args.args[1]
            assert "--purge" in args

    def test_yes_flag_forwarded(self, tmp_path):
        fake_script = tmp_path / "uninstall.sh"
        fake_script.write_text("#!/usr/bin/env bash\nexit 0\n")
        fake_script.chmod(0o755)

        with (
            patch(
                "winpodx.cli.uninstall._candidate_paths",
                return_value=[fake_script],
            ),
            patch("os.execvp") as mock_exec,
        ):
            handle_uninstall(argparse.Namespace(purge=False, yes=True))
            args = mock_exec.call_args.args[1]
            assert "--yes" in args

    def test_both_flags_forwarded(self, tmp_path):
        fake_script = tmp_path / "uninstall.sh"
        fake_script.write_text("#!/usr/bin/env bash\nexit 0\n")
        fake_script.chmod(0o755)

        with (
            patch(
                "winpodx.cli.uninstall._candidate_paths",
                return_value=[fake_script],
            ),
            patch("os.execvp") as mock_exec,
        ):
            handle_uninstall(argparse.Namespace(purge=True, yes=True))
            args = mock_exec.call_args.args[1]
            assert "--purge" in args
            assert "--yes" in args

    def test_first_existing_path_wins(self, tmp_path):
        first = tmp_path / "first.sh"
        second = tmp_path / "second.sh"
        second.write_text("#!/usr/bin/env bash\nexit 0\n")
        second.chmod(0o755)
        # first does NOT exist; should fall through to second

        with (
            patch(
                "winpodx.cli.uninstall._candidate_paths",
                return_value=[first, second],
            ),
            patch("os.execvp") as mock_exec,
        ):
            handle_uninstall(argparse.Namespace(purge=False, yes=False))
            args = mock_exec.call_args.args[1]
            assert str(second) in args
            assert str(first) not in args

    def test_systemexit_when_no_script_found(self):
        with (
            patch(
                "winpodx.cli.uninstall._candidate_paths",
                return_value=[Path("/nonexistent/path/uninstall.sh")],
            ),
            pytest.raises(SystemExit) as exc,
        ):
            handle_uninstall(argparse.Namespace(purge=False, yes=False))
        # Error message should mention the search paths so the user can
        # diagnose. And the curl one-liner so they can recover.
        assert "uninstall.sh not found" in str(exc.value)
        assert "curl -fsSL" in str(exc.value)
