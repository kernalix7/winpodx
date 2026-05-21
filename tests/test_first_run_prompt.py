# SPDX-License-Identifier: MIT
"""Tests for the CLI first-run setup prompt (#255 PR 1)."""

from __future__ import annotations

from winpodx.cli.first_run import (
    _SKIP_COMMANDS,
    maybe_run_first_run_prompt,
    should_prompt,
)
from winpodx.core.config import Config


def _captured_print():
    captured: list[str] = []

    def _emit(line: str) -> None:
        captured.append(line)

    return _emit, captured


def _scripted_input(answers: list[str]):
    it = iter(answers)

    def _read(_prompt: str) -> str:
        return next(it)

    return _read


class TestShouldPrompt:
    def test_none_command_skips(self):
        assert should_prompt(None) is False

    def test_skip_list_commands_skip(self):
        for cmd in _SKIP_COMMANDS:
            assert should_prompt(cmd) is False, f"{cmd} should be in skip list"

    def test_other_commands_eligible(self, monkeypatch):
        # Force TTY=True so the stdin check passes.
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        for cmd in ("app", "pod", "check", "timesync", "debloat"):
            assert should_prompt(cmd) is True

    def test_non_tty_stdin_skips(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        assert should_prompt("app") is False


class TestMaybeRunFirstRunPrompt:
    def test_skipped_command_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        out, _ = _captured_print()
        ran = maybe_run_first_run_prompt(
            "info",
            input_fn=_scripted_input([]),
            print_fn=out,
        )
        assert ran is False

    def test_already_initialized_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        cfg = Config()
        cfg.pod.initialized = True
        cfg.save()

        out, _ = _captured_print()
        ran = maybe_run_first_run_prompt(
            "app",
            input_fn=_scripted_input([]),
            print_fn=out,
        )
        assert ran is False

    def test_no_answer_skips(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        Config().save()  # initialized = False

        out, lines = _captured_print()
        ran = maybe_run_first_run_prompt(
            "app",
            input_fn=_scripted_input(["n"]),
            print_fn=out,
        )
        assert ran is False
        joined = "\n".join(lines)
        assert "Run setup now" in joined

    def test_eof_skips(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        Config().save()

        def _eof(_prompt):
            raise EOFError

        out, _ = _captured_print()
        ran = maybe_run_first_run_prompt("app", input_fn=_eof, print_fn=out)
        assert ran is False

    def test_yes_invokes_setup_auto(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        Config().save()

        called_with: dict = {}

        def _fake_handle_setup(args):
            called_with["non_interactive"] = args.non_interactive
            called_with["customize"] = args.customize

        monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", _fake_handle_setup)

        out, _ = _captured_print()
        ran = maybe_run_first_run_prompt(
            "app",
            input_fn=_scripted_input(["y"]),
            print_fn=out,
        )
        assert ran is True
        assert called_with["non_interactive"] is True
        assert called_with["customize"] is False

    def test_custom_invokes_setup_customize(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        Config().save()

        called_with: dict = {}

        def _fake_handle_setup(args):
            called_with["non_interactive"] = args.non_interactive
            called_with["customize"] = args.customize

        monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", _fake_handle_setup)

        out, _ = _captured_print()
        ran = maybe_run_first_run_prompt(
            "app",
            input_fn=_scripted_input(["c"]),
            print_fn=out,
        )
        assert ran is True
        assert called_with["non_interactive"] is False
        assert called_with["customize"] is True

    def test_empty_answer_defaults_to_yes(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        Config().save()

        called: list = []
        monkeypatch.setattr(
            "winpodx.cli.setup_cmd.handle_setup",
            lambda args: called.append(("non_interactive", args.non_interactive)),
        )

        out, _ = _captured_print()
        ran = maybe_run_first_run_prompt(
            "app",
            input_fn=_scripted_input([""]),
            print_fn=out,
        )
        assert ran is True
        assert called[0] == ("non_interactive", True)

    def test_unrecognised_answer_skips(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        Config().save()

        out, lines = _captured_print()
        ran = maybe_run_first_run_prompt(
            "app",
            input_fn=_scripted_input(["bogus"]),
            print_fn=out,
        )
        assert ran is False
        joined = "\n".join(lines)
        assert "Unrecognised choice" in joined


class TestInstallSourceDetection:
    """Smoke test the install_source module from a known path."""

    def test_unknown_when_no_binary(self):
        from winpodx.utils.install_source import detect

        result = detect(binary_path="/nonexistent/path/winpodx")
        assert result.kind == "unknown"
        assert "not detected" in result.label
