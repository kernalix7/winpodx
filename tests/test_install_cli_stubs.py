"""Tests for `winpodx pod install-status` and `winpodx pod install-resume` CLI stubs.

Phase 1 surface — argument parsing and stub behaviour only.
Phase 3 will fill in the implementation.

See docs/design/AGENT_FIRST_INSTALL_DESIGN.md §"CLI surface".
"""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_pod_args(argv: list[str]):
    """Parse `winpodx pod <argv>` and return the Namespace."""
    from winpodx.cli.main import cli

    captured = {}

    def _fake_dispatch(args):
        captured["args"] = args

    with patch("winpodx.cli.main._dispatch", side_effect=_fake_dispatch):
        try:
            cli(["pod"] + argv)
        except SystemExit:
            pass
    return captured.get("args")


# ---------------------------------------------------------------------------
# install-status argument parsing
# ---------------------------------------------------------------------------


class TestInstallStatusArgs:
    def test_json_flag_accepted(self):
        args = _parse_pod_args(["install-status", "--json"])
        assert args is not None
        assert args.json is True

    def test_no_color_flag_accepted(self):
        args = _parse_pod_args(["install-status", "--no-color"])
        assert args is not None
        assert args.no_color is True

    def test_logs_flag_accepted(self):
        args = _parse_pod_args(["install-status", "--logs"])
        assert args is not None
        assert args.logs is True

    def test_non_interactive_flag_accepted(self):
        args = _parse_pod_args(["install-status", "--non-interactive"])
        assert args is not None
        assert args.non_interactive is True

    def test_combined_flags_accepted(self):
        args = _parse_pod_args(["install-status", "--json", "--no-color"])
        assert args is not None
        assert args.json is True
        assert args.no_color is True

    def test_no_flags_accepted(self):
        args = _parse_pod_args(["install-status"])
        assert args is not None
        assert args.json is False
        assert args.no_color is False
        assert args.logs is False
        assert args.non_interactive is False


# ---------------------------------------------------------------------------
# install-status handle() behaviour
# ---------------------------------------------------------------------------


class TestInstallStatusHandle:
    def test_returns_zero(self, capsys):
        from winpodx.cli.pod_install_status import handle

        args = _parse_pod_args(["install-status"])
        rc = handle(args)
        assert rc == 0

    def test_non_interactive_implies_json(self, capsys):
        from winpodx.cli.pod_install_status import handle

        args = _parse_pod_args(["install-status", "--non-interactive"])
        handle(args)
        assert args.json is True

    def test_stub_message_on_stderr(self, capsys):
        from winpodx.cli.pod_install_status import handle

        args = _parse_pod_args(["install-status"])
        handle(args)
        captured = capsys.readouterr()
        assert "install-status" in captured.err
        assert "Phase 3" in captured.err

    def test_stub_message_references_design_doc(self, capsys):
        from winpodx.cli.pod_install_status import handle

        args = _parse_pod_args(["install-status"])
        handle(args)
        captured = capsys.readouterr()
        assert "AGENT_FIRST_INSTALL_DESIGN.md" in captured.err


# ---------------------------------------------------------------------------
# install-resume argument parsing
# ---------------------------------------------------------------------------


class TestInstallResumeArgs:
    def test_non_interactive_flag_accepted(self):
        args = _parse_pod_args(["install-resume", "--non-interactive"])
        assert args is not None
        assert args.non_interactive is True

    def test_yes_long_flag_accepted(self):
        args = _parse_pod_args(["install-resume", "--yes"])
        assert args is not None
        assert args.yes is True

    def test_yes_short_flag_accepted(self):
        args = _parse_pod_args(["install-resume", "-y"])
        assert args is not None
        assert args.yes is True

    def test_force_flag_accepted(self):
        args = _parse_pod_args(["install-resume", "--force"])
        assert args is not None
        assert args.force is True

    def test_all_flags_combined(self):
        args = _parse_pod_args(["install-resume", "--non-interactive", "--force"])
        assert args is not None
        assert args.non_interactive is True
        assert args.force is True

    def test_no_flags_accepted(self):
        args = _parse_pod_args(["install-resume"])
        assert args is not None
        assert args.non_interactive is False
        assert args.yes is False
        assert args.force is False


# ---------------------------------------------------------------------------
# install-resume handle() behaviour
# ---------------------------------------------------------------------------


class TestInstallResumeHandle:
    def test_returns_zero(self, capsys):
        from winpodx.cli.pod_install_resume import handle

        args = _parse_pod_args(["install-resume"])
        rc = handle(args)
        assert rc == 0

    def test_stub_message_on_stderr(self, capsys):
        from winpodx.cli.pod_install_resume import handle

        args = _parse_pod_args(["install-resume"])
        handle(args)
        captured = capsys.readouterr()
        assert "install-resume" in captured.err
        assert "Phase 3" in captured.err

    def test_stub_message_references_design_doc(self, capsys):
        from winpodx.cli.pod_install_resume import handle

        args = _parse_pod_args(["install-resume"])
        handle(args)
        captured = capsys.readouterr()
        assert "AGENT_FIRST_INSTALL_DESIGN.md" in captured.err


# ---------------------------------------------------------------------------
# `winpodx pod --help` lists both new subcommands
# ---------------------------------------------------------------------------


class TestPodHelpText:
    def test_install_status_in_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "winpodx", "pod", "--help"],
            capture_output=True,
            text=True,
        )
        assert "install-status" in result.stdout

    def test_install_resume_in_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "winpodx", "pod", "--help"],
            capture_output=True,
            text=True,
        )
        assert "install-resume" in result.stdout

    def test_install_status_help_text(self):
        result = subprocess.run(
            [sys.executable, "-m", "winpodx", "pod", "--help"],
            capture_output=True,
            text=True,
        )
        assert "Show install step progress and last log lines" in result.stdout

    def test_install_resume_help_text(self):
        result = subprocess.run(
            [sys.executable, "-m", "winpodx", "pod", "--help"],
            capture_output=True,
            text=True,
        )
        assert "Retry a failed or incomplete guest install" in result.stdout
