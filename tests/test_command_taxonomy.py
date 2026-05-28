# SPDX-License-Identifier: MIT
"""Tests for the 0.6.0 item G command taxonomy reorganisation.

Covers:
- Every old ``pod <x>`` alias still parses AND dispatches (emits deprecation
  line, calls the shared handler).
- Every new canonical command (``guest``, ``install``) parses AND dispatches.
- ``pod start/stop/status/restart/recreate/wait-ready`` unchanged (no
  deprecation).
- ``doctor --json`` emits valid JSON with the expected keys.
- ``doctor --quick`` skips the slow probe (_check_container_health), runs
  cheap probes.
- ``winpodx --help`` lists ``pod``, ``guest``, ``install``, ``doctor``,
  ``provision``.
- ``winpodx info`` and ``winpodx check`` emit the deprecation notice.
- Grep assertions over install.sh / uninstall.sh (no old command literals).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parent.parent


def _parse(argv: list[str]) -> argparse.Namespace | None:
    """Parse ``winpodx <argv>`` and return the Namespace without dispatching."""
    from winpodx.cli.main import cli

    captured: dict = {}

    def _fake_dispatch(args: argparse.Namespace) -> None:
        captured["args"] = args

    with patch("winpodx.cli.main._dispatch", side_effect=_fake_dispatch):
        try:
            cli(argv)
        except SystemExit:
            pass
    return captured.get("args")


def _run_cli(argv: list[str]) -> tuple[str, str, int]:
    """Run ``winpodx`` as a subprocess and return (stdout, stderr, returncode)."""
    result = subprocess.run(
        [sys.executable, "-m", "winpodx"] + argv,
        capture_output=True,
        text=True,
    )
    return result.stdout, result.stderr, result.returncode


# ---------------------------------------------------------------------------
# Part 1: doctor --json
# ---------------------------------------------------------------------------


class TestDoctorJson:
    def test_json_flag_produces_valid_json(self, monkeypatch):
        """``doctor --json`` must emit a JSON array parseable by json.loads."""
        from winpodx.cli.doctor import Finding, handle_doctor

        ok = Finding("ok", "stub-title", detail="d", suggestion="s")
        for name in (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        ):
            monkeypatch.setattr(f"winpodx.cli.doctor.{name}", lambda: ok)
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])
        monkeypatch.setattr("winpodx.cli.doctor._check_container_health", lambda: [ok])

        buf = StringIO()
        with patch("sys.stdout", buf):
            handle_doctor(argparse.Namespace(json=True, quick=False))

        payload = json.loads(buf.getvalue())
        assert isinstance(payload, list)
        assert len(payload) > 0
        first = payload[0]
        assert set(first.keys()) >= {"severity", "title", "detail", "suggestion"}

    def test_json_exits_one_on_fail(self, monkeypatch):
        """``doctor --json`` with a FAIL finding must still exit 1."""
        from winpodx.cli.doctor import Finding, handle_doctor

        fail = Finding("fail", "broken", detail="detail", suggestion="fix it")
        ok = Finding("ok", "fine")
        for name in (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        ):
            monkeypatch.setattr(f"winpodx.cli.doctor.{name}", lambda: fail)
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])
        monkeypatch.setattr("winpodx.cli.doctor._check_container_health", lambda: [ok])

        buf = StringIO()
        with patch("sys.stdout", buf):
            with pytest.raises(SystemExit) as exc:
                handle_doctor(argparse.Namespace(json=True, quick=False))
        assert exc.value.code == 1
        payload = json.loads(buf.getvalue())
        assert any(f["severity"] == "fail" for f in payload)

    def test_json_keys_present_for_every_finding(self, monkeypatch):
        """Every element in the JSON array must have all four required keys."""
        from winpodx.cli.doctor import Finding, handle_doctor

        ok = Finding("ok", "t", detail="", suggestion="")
        for name in (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        ):
            monkeypatch.setattr(f"winpodx.cli.doctor.{name}", lambda: ok)
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])
        monkeypatch.setattr("winpodx.cli.doctor._check_container_health", lambda: [ok])

        buf = StringIO()
        with patch("sys.stdout", buf):
            handle_doctor(argparse.Namespace(json=True, quick=False))

        for item in json.loads(buf.getvalue()):
            for key in ("severity", "title", "detail", "suggestion"):
                assert key in item


# ---------------------------------------------------------------------------
# Part 2: doctor --quick
# ---------------------------------------------------------------------------


class TestDoctorQuick:
    def test_quick_skips_container_health(self, monkeypatch):
        """``doctor --quick`` must NOT call ``_check_container_health``."""
        from winpodx.cli.doctor import Finding, handle_doctor

        ok = Finding("ok", "stub")
        for name in (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        ):
            monkeypatch.setattr(f"winpodx.cli.doctor.{name}", lambda: ok)
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])

        called = []
        monkeypatch.setattr(
            "winpodx.cli.doctor._check_container_health",
            lambda: called.append(True) or [],  # type: ignore[return-value]
        )

        buf = StringIO()
        with patch("sys.stdout", buf):
            handle_doctor(argparse.Namespace(json=False, quick=True))

        assert called == [], "_check_container_health must not be called with --quick"

    def test_quick_still_runs_cheap_checks(self, monkeypatch):
        """``doctor --quick`` must still run the cheap local probes."""
        from winpodx.cli.doctor import Finding, handle_doctor

        ok = Finding("ok", "stub")
        called: list[str] = []

        def _make_probe(name: str):
            def _probe():
                called.append(name)
                return ok

            return _probe

        cheap_names = (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        )
        for n in cheap_names:
            monkeypatch.setattr(f"winpodx.cli.doctor.{n}", _make_probe(n))
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])
        monkeypatch.setattr("winpodx.cli.doctor._check_container_health", lambda: [])

        buf = StringIO()
        with patch("sys.stdout", buf):
            handle_doctor(argparse.Namespace(json=False, quick=True))

        for n in cheap_names:
            assert n in called, f"{n} should have been called in --quick mode"

    def test_quick_not_quick_includes_container_health(self, monkeypatch):
        """Without ``--quick``, ``_check_container_health`` IS called."""
        from winpodx.cli.doctor import Finding, handle_doctor

        ok = Finding("ok", "stub")
        for name in (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        ):
            monkeypatch.setattr(f"winpodx.cli.doctor.{name}", lambda: ok)
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])

        called = []
        monkeypatch.setattr(
            "winpodx.cli.doctor._check_container_health",
            lambda: called.append(True) or [],  # type: ignore[return-value]
        )

        buf = StringIO()
        with patch("sys.stdout", buf):
            handle_doctor(argparse.Namespace(json=False, quick=False))

        assert called, "_check_container_health should be called without --quick"


# ---------------------------------------------------------------------------
# Part 3: deprecation of info / check
# ---------------------------------------------------------------------------


class TestInfoDeprecation:
    def test_info_prints_deprecation_to_stderr(self, capsys, monkeypatch):
        """``winpodx info`` must print a deprecation notice to stderr."""
        # Stub _cmd_info so we don't need a real config
        monkeypatch.setattr("winpodx.cli.main._cmd_info", lambda: None)
        monkeypatch.setattr("winpodx.cli.main._maybe_resume_pending", lambda _: None)
        monkeypatch.setattr(
            "winpodx.cli.main.maybe_run_first_run_prompt",
            lambda _: None,
            raising=False,
        )

        args = argparse.Namespace(command="info")
        from winpodx.cli.main import _dispatch

        _dispatch(args)
        err = capsys.readouterr().err
        assert "[deprecated]" in err
        assert "winpodx info" in err
        assert "0.7.0" in err
        assert "winpodx doctor" in err

    def test_check_prints_deprecation_to_stderr(self, capsys, monkeypatch):
        """``winpodx check`` must print a deprecation notice to stderr."""
        monkeypatch.setattr("winpodx.cli.main._cmd_check", lambda _args: 0, raising=False)
        monkeypatch.setattr("winpodx.cli.main._maybe_resume_pending", lambda _: None)

        args = argparse.Namespace(command="check", json=False)
        from winpodx.cli.main import _dispatch

        with pytest.raises(SystemExit):
            _dispatch(args)
        err = capsys.readouterr().err
        assert "[deprecated]" in err
        assert "winpodx check" in err
        assert "winpodx doctor" in err


# ---------------------------------------------------------------------------
# Part 4: pod lifecycle commands — no deprecation
# ---------------------------------------------------------------------------


class TestPodLifecycleNoDeprecation:
    """start/stop/status/restart/recreate/wait-ready must NOT emit deprecation."""

    @pytest.mark.parametrize(
        "argv,extra_attrs",
        [
            (["pod", "start"], {"wait": False, "timeout": 300}),
            (["pod", "stop"], {}),
            (["pod", "status"], {}),
            (["pod", "restart"], {}),
        ],
    )
    def test_lifecycle_command_parses(self, argv, extra_attrs):
        ns = _parse(argv)
        assert ns is not None
        assert ns.pod_command == argv[1]

    def test_pod_start_no_deprecation(self, monkeypatch, capsys):
        """``pod start`` must not emit any deprecation warning."""
        from winpodx.cli import pod as _pod

        monkeypatch.setattr(
            _pod,
            "_start",
            lambda wait, timeout, tuning_override=None: None,
        )

        ns = argparse.Namespace(pod_command="start", wait=False, timeout=300, tuning=None)
        _pod.handle_pod(ns)
        err = capsys.readouterr().err
        assert "[deprecated]" not in err

    @pytest.mark.parametrize("subcmd", ["stop", "status", "restart"])
    def test_no_deprecation_for_basic_lifecycle(self, subcmd, monkeypatch, capsys):
        from winpodx.cli import pod as _pod

        monkeypatch.setattr(_pod, "_stop", lambda: None)
        monkeypatch.setattr(_pod, "_status", lambda: None)
        monkeypatch.setattr(_pod, "_restart", lambda: None)

        ns = argparse.Namespace(pod_command=subcmd)
        _pod.handle_pod(ns)
        err = capsys.readouterr().err
        assert "[deprecated]" not in err


# ---------------------------------------------------------------------------
# Part 5: pod alias commands — deprecation emitted, shared handler called
# ---------------------------------------------------------------------------


class TestPodDeprecatedAliases:
    """Each moved pod subcommand must emit a deprecation line AND call the
    shared handler function from pod.py."""

    def _check_alias(
        self,
        monkeypatch,
        capsys,
        pod_subcmd: str,
        new_full_cmd: str,
        handler_name: str,
        ns_extra: dict,
        handler_kwargs: dict | None = None,
    ) -> None:
        from winpodx.cli import pod as _pod

        called = []

        def _spy(*args, **kwargs):
            called.append((args, kwargs))

        monkeypatch.setattr(_pod, handler_name, _spy)

        ns = argparse.Namespace(pod_command=pod_subcmd, **ns_extra)
        _pod.handle_pod(ns)

        err = capsys.readouterr().err
        assert "[deprecated]" in err, f"No deprecation line for pod {pod_subcmd}"
        assert f"pod {pod_subcmd}" in err
        assert new_full_cmd in err
        assert called, f"{handler_name} was not called for pod {pod_subcmd}"

    def test_apply_fixes_deprecated(self, monkeypatch, capsys):
        self._check_alias(
            monkeypatch,
            capsys,
            "apply-fixes",
            "guest apply-fixes",
            "_apply_fixes",
            {},
        )

    def test_sync_guest_deprecated(self, monkeypatch, capsys):
        self._check_alias(
            monkeypatch,
            capsys,
            "sync-guest",
            "guest sync",
            "_sync_guest",
            {"force": False},
        )

    def test_sync_password_deprecated(self, monkeypatch, capsys):
        self._check_alias(
            monkeypatch,
            capsys,
            "sync-password",
            "guest sync-password",
            "_sync_password",
            {"non_interactive": False},
        )

    def test_multi_session_deprecated(self, monkeypatch, capsys):
        self._check_alias(
            monkeypatch,
            capsys,
            "multi-session",
            "guest multi-session",
            "_multi_session",
            {"action": "status"},
        )

    def test_recover_oem_deprecated(self, monkeypatch, capsys):
        self._check_alias(
            monkeypatch,
            capsys,
            "recover-oem",
            "guest recover-oem",
            "_recover_oem",
            {},
        )

    def test_grow_disk_deprecated(self, monkeypatch, capsys):
        self._check_alias(
            monkeypatch,
            capsys,
            "grow-disk",
            "install grow-disk",
            "_grow_disk",
            {"size": None, "increment": None, "extend_only": False, "yes": False},
        )

    def test_disk_usage_deprecated(self, monkeypatch, capsys):
        self._check_alias(
            monkeypatch,
            capsys,
            "disk-usage",
            "install disk-usage",
            "_disk_usage",
            {},
        )

    def test_install_status_deprecated(self, monkeypatch, capsys):
        """pod install-status should emit deprecation and call handle_install_status."""
        from winpodx.cli import pod as _pod

        called = []

        def _fake_install_status(args):
            called.append(args)
            return 0

        monkeypatch.setattr("winpodx.cli.pod_install_status.handle", _fake_install_status)

        ns = argparse.Namespace(
            pod_command="install-status",
            json=False,
            no_color=False,
            logs=False,
            non_interactive=False,
        )
        with pytest.raises(SystemExit) as exc:
            _pod.handle_pod(ns)
        assert exc.value.code == 0

        err = capsys.readouterr().err
        assert "[deprecated]" in err
        assert "pod install-status" in err
        assert "install status" in err
        assert called

    def test_install_resume_deprecated(self, monkeypatch, capsys):
        """pod install-resume should emit deprecation and call handle_install_resume."""
        from winpodx.cli import pod as _pod

        called = []

        def _fake_install_resume(args):
            called.append(args)
            return 0

        monkeypatch.setattr("winpodx.cli.pod_install_resume.handle", _fake_install_resume)

        ns = argparse.Namespace(
            pod_command="install-resume",
            non_interactive=False,
            yes=False,
            force=False,
        )
        with pytest.raises(SystemExit) as exc:
            _pod.handle_pod(ns)
        assert exc.value.code == 0

        err = capsys.readouterr().err
        assert "[deprecated]" in err
        assert "pod install-resume" in err
        assert "install resume" in err
        assert called


# ---------------------------------------------------------------------------
# Part 6: new canonical guest commands dispatch to shared handlers
# ---------------------------------------------------------------------------


class TestGuestDispatch:
    def test_guest_apply_fixes_calls_handler(self, monkeypatch):
        from winpodx.cli import guest as _guest
        from winpodx.cli import pod as _pod

        called = []
        monkeypatch.setattr(_pod, "_apply_fixes", lambda: called.append(True))

        ns = argparse.Namespace(guest_command="apply-fixes")
        _guest.handle_guest(ns)
        assert called

    def test_guest_sync_calls_handler(self, monkeypatch):
        from winpodx.cli import guest as _guest
        from winpodx.cli import pod as _pod

        called = []
        monkeypatch.setattr(_pod, "_sync_guest", lambda force: called.append(force))

        ns = argparse.Namespace(guest_command="sync", force=True)
        _guest.handle_guest(ns)
        assert called == [True]

    def test_guest_sync_password_calls_handler(self, monkeypatch):
        from winpodx.cli import guest as _guest
        from winpodx.cli import pod as _pod

        called = []
        monkeypatch.setattr(
            _pod, "_sync_password", lambda non_interactive: called.append(non_interactive)
        )

        ns = argparse.Namespace(guest_command="sync-password", non_interactive=False)
        _guest.handle_guest(ns)
        assert called == [False]

    def test_guest_multi_session_calls_handler(self, monkeypatch):
        from winpodx.cli import guest as _guest
        from winpodx.cli import pod as _pod

        called = []
        monkeypatch.setattr(_pod, "_multi_session", lambda action: called.append(action))

        ns = argparse.Namespace(guest_command="multi-session", action="status")
        _guest.handle_guest(ns)
        assert called == ["status"]

    def test_guest_recover_oem_calls_handler(self, monkeypatch):
        from winpodx.cli import guest as _guest
        from winpodx.cli import pod as _pod

        called = []
        monkeypatch.setattr(_pod, "_recover_oem", lambda: called.append(True))

        ns = argparse.Namespace(guest_command="recover-oem")
        _guest.handle_guest(ns)
        assert called

    def test_guest_unknown_command_exits_one(self, capsys):
        from winpodx.cli import guest as _guest

        ns = argparse.Namespace(guest_command="nonexistent")
        with pytest.raises(SystemExit) as exc:
            _guest.handle_guest(ns)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Part 7: new canonical install commands dispatch to shared handlers
# ---------------------------------------------------------------------------


class TestInstallDispatch:
    def test_install_grow_disk_calls_handler(self, monkeypatch):
        from winpodx.cli import install_cmd as _install
        from winpodx.cli import pod as _pod

        called = []
        monkeypatch.setattr(
            _pod,
            "_grow_disk",
            lambda target_size, increment, extend_only, assume_yes: called.append(
                (target_size, increment, extend_only, assume_yes)
            ),
        )

        ns = argparse.Namespace(
            install_command="grow-disk",
            size=None,
            increment=None,
            extend_only=False,
            yes=False,
        )
        _install.handle_install_group(ns)
        assert called == [(None, None, False, False)]

    def test_install_disk_usage_calls_handler(self, monkeypatch):
        from winpodx.cli import install_cmd as _install
        from winpodx.cli import pod as _pod

        called = []
        monkeypatch.setattr(_pod, "_disk_usage", lambda: called.append(True))

        ns = argparse.Namespace(install_command="disk-usage")
        _install.handle_install_group(ns)
        assert called

    def test_install_status_calls_handle(self, monkeypatch):
        from winpodx.cli import install_cmd as _install

        called = []

        def _fake(args):
            called.append(args)
            return 0

        monkeypatch.setattr("winpodx.cli.pod_install_status.handle", _fake)

        ns = argparse.Namespace(
            install_command="status",
            json=False,
            no_color=False,
            logs=False,
            non_interactive=False,
        )
        with pytest.raises(SystemExit) as exc:
            _install.handle_install_group(ns)
        assert exc.value.code == 0
        assert called

    def test_install_resume_calls_handle(self, monkeypatch):
        from winpodx.cli import install_cmd as _install

        called = []

        def _fake(args):
            called.append(args)
            return 0

        monkeypatch.setattr("winpodx.cli.pod_install_resume.handle", _fake)

        ns = argparse.Namespace(
            install_command="resume",
            non_interactive=False,
            yes=False,
            force=False,
        )
        with pytest.raises(SystemExit) as exc:
            _install.handle_install_group(ns)
        assert exc.value.code == 0
        assert called

    def test_install_unknown_command_exits_one(self, capsys):
        from winpodx.cli import install_cmd as _install

        ns = argparse.Namespace(install_command="bogus")
        with pytest.raises(SystemExit) as exc:
            _install.handle_install_group(ns)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Part 8: argparse parsing — new commands appear in the tree
# ---------------------------------------------------------------------------


class TestArgparseParsing:
    """The CLI parser must accept the new command shapes without error."""

    def test_guest_apply_fixes_parses(self):
        ns = _parse(["guest", "apply-fixes"])
        assert ns is not None
        assert ns.command == "guest"
        assert ns.guest_command == "apply-fixes"

    def test_guest_sync_parses(self):
        ns = _parse(["guest", "sync"])
        assert ns is not None
        assert ns.guest_command == "sync"

    def test_guest_sync_force_flag(self):
        ns = _parse(["guest", "sync", "--force"])
        assert ns is not None
        assert ns.force is True

    def test_guest_sync_password_parses(self):
        ns = _parse(["guest", "sync-password"])
        assert ns is not None
        assert ns.guest_command == "sync-password"

    def test_guest_multi_session_parses(self):
        ns = _parse(["guest", "multi-session", "on"])
        assert ns is not None
        assert ns.guest_command == "multi-session"
        assert ns.action == "on"

    def test_guest_recover_oem_parses(self):
        ns = _parse(["guest", "recover-oem"])
        assert ns is not None
        assert ns.guest_command == "recover-oem"

    def test_install_status_parses(self):
        ns = _parse(["install", "status"])
        assert ns is not None
        assert ns.command == "install"
        assert ns.install_command == "status"

    def test_install_resume_parses(self):
        ns = _parse(["install", "resume"])
        assert ns is not None
        assert ns.install_command == "resume"

    def test_install_grow_disk_parses(self):
        ns = _parse(["install", "grow-disk"])
        assert ns is not None
        assert ns.install_command == "grow-disk"

    def test_install_grow_disk_size_arg(self):
        ns = _parse(["install", "grow-disk", "128G"])
        assert ns is not None
        assert ns.size == "128G"

    def test_install_disk_usage_parses(self):
        ns = _parse(["install", "disk-usage"])
        assert ns is not None
        assert ns.install_command == "disk-usage"

    def test_doctor_json_flag_parses(self):
        ns = _parse(["doctor", "--json"])
        assert ns is not None
        assert ns.json is True

    def test_doctor_quick_flag_parses(self):
        ns = _parse(["doctor", "--quick"])
        assert ns is not None
        assert ns.quick is True

    def test_doctor_both_flags(self):
        ns = _parse(["doctor", "--json", "--quick"])
        assert ns is not None
        assert ns.json is True
        assert ns.quick is True

    # -- old pod aliases still parse --

    def test_pod_apply_fixes_still_parses(self):
        ns = _parse(["pod", "apply-fixes"])
        assert ns is not None
        assert ns.pod_command == "apply-fixes"

    def test_pod_sync_guest_still_parses(self):
        ns = _parse(["pod", "sync-guest"])
        assert ns is not None
        assert ns.pod_command == "sync-guest"

    def test_pod_sync_password_still_parses(self):
        ns = _parse(["pod", "sync-password"])
        assert ns is not None
        assert ns.pod_command == "sync-password"

    def test_pod_multi_session_still_parses(self):
        ns = _parse(["pod", "multi-session", "off"])
        assert ns is not None
        assert ns.pod_command == "multi-session"

    def test_pod_recover_oem_still_parses(self):
        ns = _parse(["pod", "recover-oem"])
        assert ns is not None
        assert ns.pod_command == "recover-oem"

    def test_pod_install_status_still_parses(self):
        ns = _parse(["pod", "install-status"])
        assert ns is not None
        assert ns.pod_command == "install-status"

    def test_pod_install_resume_still_parses(self):
        ns = _parse(["pod", "install-resume"])
        assert ns is not None
        assert ns.pod_command == "install-resume"

    def test_pod_grow_disk_still_parses(self):
        ns = _parse(["pod", "grow-disk"])
        assert ns is not None
        assert ns.pod_command == "grow-disk"

    def test_pod_disk_usage_still_parses(self):
        ns = _parse(["pod", "disk-usage"])
        assert ns is not None
        assert ns.pod_command == "disk-usage"

    # -- info and check still parse (just deprecated) --

    def test_info_still_parses(self):
        ns = _parse(["info"])
        assert ns is not None
        assert ns.command == "info"

    def test_check_still_parses(self):
        ns = _parse(["check"])
        assert ns is not None
        assert ns.command == "check"


# ---------------------------------------------------------------------------
# Part 9: ``winpodx --help`` lists expected top-level commands
# ---------------------------------------------------------------------------


class TestTopLevelHelp:
    def test_help_lists_pod(self):
        stdout, _, _ = _run_cli(["--help"])
        assert "pod" in stdout

    def test_help_lists_guest(self):
        stdout, _, _ = _run_cli(["--help"])
        assert "guest" in stdout

    def test_help_lists_install(self):
        stdout, _, _ = _run_cli(["--help"])
        assert "install" in stdout

    def test_help_lists_doctor(self):
        stdout, _, _ = _run_cli(["--help"])
        assert "doctor" in stdout

    def test_help_lists_provision(self):
        stdout, _, _ = _run_cli(["--help"])
        assert "provision" in stdout


# ---------------------------------------------------------------------------
# Part 10: grep install.sh / uninstall.sh for banned literals
# ---------------------------------------------------------------------------


class TestShellScriptClean:
    """Assert no live invocations of deprecated/moved commands remain in the
    shell scripts.  Comment lines are excluded from the check."""

    def _live_lines(self, path: Path) -> list[str]:
        """Return non-comment, non-blank lines from a shell script."""
        if not path.exists():
            return []
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                lines.append(line)
        return lines

    @pytest.mark.parametrize(
        "banned",
        [
            "winpodx pod sync-guest",
            "winpodx pod apply-fixes",
            "winpodx pod sync-password",
            "winpodx pod multi-session",
            "winpodx pod recover-oem",
            "winpodx pod install-status",
            "winpodx pod install-resume",
            "winpodx pod grow-disk",
            "winpodx pod disk-usage",
            "winpodx info",
            "winpodx check",
        ],
    )
    def test_install_sh_no_banned(self, banned):
        path = _REPO / "install.sh"
        live = self._live_lines(path)
        hits = [line for line in live if banned in line]
        assert hits == [], f"install.sh contains banned literal '{banned}': {hits}"

    @pytest.mark.parametrize(
        "banned",
        [
            "winpodx pod sync-guest",
            "winpodx pod apply-fixes",
            "winpodx info",
            "winpodx check",
        ],
    )
    def test_uninstall_sh_no_banned(self, banned):
        path = _REPO / "uninstall.sh"
        live = self._live_lines(path)
        hits = [line for line in live if banned in line]
        assert hits == [], f"uninstall.sh contains banned literal '{banned}': {hits}"
