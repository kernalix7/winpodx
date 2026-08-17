# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from winpodx.cli import main


def _patch_config(monkeypatch: pytest.MonkeyPatch, cfg: object) -> None:
    from winpodx.core.config import Config

    monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))


def _namespace(**values: object) -> argparse.Namespace:
    return argparse.Namespace(**values)


class TestCliEntryPoint:
    def _isolate_entry(self, monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock, Mock]:
        from winpodx.cli import first_run
        from winpodx.core import i18n
        from winpodx.desktop import tray_spawn
        from winpodx.utils import logging as logging_utils

        monkeypatch.setattr(logging_utils, "setup_logging", Mock())
        monkeypatch.setattr(i18n, "init_from_config", Mock())
        prompt = Mock()
        tray = Mock()
        dispatch = Mock()
        monkeypatch.setattr(first_run, "maybe_run_first_run_prompt", prompt)
        monkeypatch.setattr(tray_spawn, "maybe_spawn_tray", tray)
        monkeypatch.setattr(main, "_maybe_resume_pending", Mock())
        monkeypatch.setattr(main, "_dispatch", dispatch)
        return prompt, tray, dispatch

    def test_no_arguments_prints_help_without_hooks(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        prompt, tray, dispatch = self._isolate_entry(monkeypatch)

        main.cli([])

        assert "Windows app integration for Linux desktop" in capsys.readouterr().out
        prompt.assert_not_called()
        tray.assert_not_called()
        dispatch.assert_not_called()

    def test_version_exits_zero_and_prints_detected_source(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.utils import install_source

        self._isolate_entry(monkeypatch)
        monkeypatch.setattr(install_source, "detect", lambda: SimpleNamespace(label="test package"))

        with pytest.raises(SystemExit) as exc:
            main.cli(["--version"])

        assert exc.value.code == 0
        assert capsys.readouterr().out.strip() == f"winpodx {main.__version__} (test package)"

    def test_command_runs_prompt_tray_then_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        prompt, tray, dispatch = self._isolate_entry(monkeypatch)
        calls: list[str] = []
        prompt.side_effect = lambda command: calls.append(f"prompt:{command}")
        tray.side_effect = lambda: calls.append("tray")
        dispatch.side_effect = lambda args: calls.append(f"dispatch:{args.command}")

        main.cli(["cleanup"])

        assert calls == ["prompt:cleanup", "tray", "dispatch:cleanup"]

    @pytest.mark.parametrize("command", ["setup", "gui", "tray", "launch"])
    def test_commands_with_own_lifecycle_skip_tray(
        self, command: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt, tray, dispatch = self._isolate_entry(monkeypatch)

        main.cli([command])

        prompt.assert_called_once_with(command)
        tray.assert_not_called()
        dispatch.assert_called_once()

    def test_prompt_and_tray_failures_do_not_block_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt, tray, dispatch = self._isolate_entry(monkeypatch)
        prompt.side_effect = RuntimeError("prompt failed")
        tray.side_effect = RuntimeError("tray failed")

        main.cli(["cleanup"])

        dispatch.assert_called_once()
        assert dispatch.call_args.args[0].command == "cleanup"


class TestPendingResume:
    @pytest.mark.parametrize(
        "argv",
        [[], ["--version"], ["--help"], ["uninstall"], ["config"], ["info"], ["gui"], ["tray"]],
    )
    def test_introspection_and_recovery_paths_skip_pending_probe(
        self, argv: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from winpodx.utils import pending

        probe = Mock(return_value=True)
        monkeypatch.setattr(pending, "has_pending", probe)

        main._maybe_resume_pending(argv)

        probe.assert_not_called()

    def test_pending_setup_is_resumed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from winpodx.utils import pending

        resume = Mock()
        monkeypatch.setattr(pending, "has_pending", lambda: True)
        monkeypatch.setattr(pending, "resume", resume)

        main._maybe_resume_pending(["cleanup"])

        resume.assert_called_once_with()

    def test_no_pending_setup_does_not_resume(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from winpodx.utils import pending

        resume = Mock()
        monkeypatch.setattr(pending, "has_pending", lambda: False)
        monkeypatch.setattr(pending, "resume", resume)

        main._maybe_resume_pending(["cleanup"])

        resume.assert_not_called()

    def test_pending_probe_failure_is_best_effort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from winpodx.utils import pending

        probe = Mock(side_effect=RuntimeError("broken marker"))
        monkeypatch.setattr(pending, "has_pending", probe)

        main._maybe_resume_pending(["cleanup"])

        probe.assert_called_once()


class TestDispatchDetails:
    def test_setup_host_forwards_flags_and_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from winpodx.setup_wizard import __main__ as setup_host

        handler = Mock(return_value=7)
        monkeypatch.setattr(setup_host, "main", handler)

        with pytest.raises(SystemExit) as exc:
            main._dispatch(_namespace(command="setup-host", status=True, apply=True))

        assert exc.value.code == 7
        handler.assert_called_once_with(["--status", "--apply"])

    def test_migrate_propagates_handler_exit_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from winpodx.cli import migrate

        args = _namespace(command="migrate")
        handler = Mock(return_value=3)
        monkeypatch.setattr(migrate, "run_migrate", handler)

        with pytest.raises(SystemExit) as exc:
            main._dispatch(args)

        assert exc.value.code == 3
        handler.assert_called_once_with(args)

    def test_uninstall_dispatches_exact_namespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from winpodx.cli import uninstall

        args = _namespace(command="uninstall", purge=True, yes=True)
        handler = Mock()
        monkeypatch.setattr(uninstall, "handle_uninstall", handler)

        main._dispatch(args)

        handler.assert_called_once_with(args)


class TestLanguage:
    def test_show_auto_language_reports_resolution(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import i18n

        cfg = SimpleNamespace(ui=SimpleNamespace(language="auto"))
        _patch_config(monkeypatch, cfg)
        monkeypatch.setattr(i18n, "resolve_language", lambda code: "ko")

        main._cmd_language(None)

        output = capsys.readouterr().out
        assert "UI language: auto (resolved: ko)" in output
        assert "Available: auto, en, ko, zh, ja, de, fr, it" in output

    def test_set_language_saves_and_activates_it(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import i18n

        cfg = SimpleNamespace(ui=SimpleNamespace(language="auto"), save=Mock())
        _patch_config(monkeypatch, cfg)
        set_language = Mock()
        monkeypatch.setattr(i18n, "set_language", set_language)
        monkeypatch.setattr(i18n, "current_language", lambda: "fr")

        main._cmd_language("fr")

        assert cfg.ui.language == "fr"
        cfg.save.assert_called_once_with()
        set_language.assert_called_once_with("fr")
        assert "UI language set to fr (resolved: fr)." in capsys.readouterr().out


class TestAutostart:
    @pytest.mark.parametrize("action,enabled", [("on", True), ("off", False)])
    def test_toggle_updates_autostart(
        self,
        action: str,
        enabled: bool,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from winpodx.desktop import autostart

        setter = Mock()
        monkeypatch.setattr(autostart, "set_autostart", setter)

        main._cmd_autostart(action)

        setter.assert_called_once_with(enabled)
        assert f"Autostart {action.upper()}" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "enabled,tray_enabled,expected",
        [
            (True, True, ["auto-start: ON", "entry: present"]),
            (False, False, ["OFF", "absent", "enable with"]),
        ],
    )
    def test_status_reports_both_config_and_entry(
        self,
        enabled: bool,
        tray_enabled: bool,
        expected: list[str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from winpodx.desktop import autostart

        monkeypatch.setattr(autostart, "is_autostart_enabled", lambda: enabled)
        monkeypatch.setattr(autostart, "is_tray_autostart_enabled", lambda: tray_enabled)

        main._cmd_autostart("status")

        output = capsys.readouterr().out
        assert all(fragment in output for fragment in expected)


class TestInfo:
    def test_info_renders_complete_snapshot_and_budget_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from winpodx.core import info as info_module
        from winpodx.utils import specs

        cfg = SimpleNamespace(pod=SimpleNamespace(cpu_cores=4, ram_gb=8, tuning_profile="auto"))
        _patch_config(monkeypatch, cfg)
        snapshot = {
            "system": {
                "winpodx": "1.2.3",
                "install_source": "pip",
                "oem_bundle": "6.0",
                "rdprrap": "0.3",
                "distro": "Test Linux",
                "kernel": "6.0",
            },
            "display": {
                "session_type": "wayland",
                "desktop_environment": "KDE",
                "wayland_freerdp": "native",
                "raw_scale": "1.25",
                "rdp_scale": 125,
            },
            "dependencies": {
                "xfreerdp": {"found": "true", "path": "/bin/xfreerdp"},
                "podman": {"found": "false", "path": ""},
            },
            "pod": {
                "state": "running",
                "uptime": "today",
                "rdp_port": 3389,
                "rdp_reachable": True,
                "vnc_port": 8006,
                "vnc_reachable": False,
                "active_sessions": 2,
            },
            "config": {
                "path": "/tmp/test.toml",
                "backend": "podman",
                "ip": "127.0.0.1",
                "port": 3389,
                "user": "tester",
                "scale": 125,
                "idle_timeout": 60,
                "max_sessions": 10,
                "ram_gb": 8,
                "budget_warning": "host memory is tight",
            },
        }
        monkeypatch.setattr(info_module, "gather_info", lambda loaded: snapshot)
        monkeypatch.setattr(specs, "detect_tuning_capability", lambda **kwargs: "cap")
        monkeypatch.setattr(specs, "recommend_tuning_profile", lambda cap, user_pref: "safe")
        monkeypatch.setattr(specs, "format_tuning_summary", lambda cap, profile: "Tuning safe")

        main._cmd_info()

        captured = capsys.readouterr()
        assert "WinPodX:        1.2.3" in captured.out
        assert "xfreerdp        [OK] (/bin/xfreerdp)" in captured.out
        assert "podman          [MISSING]" in captured.out
        assert "RDP 3389         reachable" in captured.out
        assert "VNC 8006         unreachable" in captured.out
        assert "Tuning safe" in captured.out
        assert "WARNING: host memory is tight" in captured.err


class TestCheck:
    def test_human_report_returns_failure_status(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import checks

        _patch_config(monkeypatch, object())
        probes = [
            checks.Probe("rdp", "ok", "ready", 2),
            checks.Probe("agent", "fail", "offline", 3),
        ]
        monkeypatch.setattr(checks, "run_all", lambda cfg: probes)
        monkeypatch.setattr(checks, "overall", lambda values: "fail")

        result = main._cmd_check(_namespace(json=False))

        output = capsys.readouterr().out
        assert result == 1
        assert "[OK  ] rdp" in output
        assert "[FAIL] agent" in output
        assert "Overall: FAIL" in output

    def test_json_report_returns_success_and_has_probe_fields(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import checks

        _patch_config(monkeypatch, object())
        probes = [checks.Probe("rdp", "warn", "slow", 9)]
        monkeypatch.setattr(checks, "run_all", lambda cfg: probes)
        monkeypatch.setattr(checks, "overall", lambda values: "warn")

        result = main._cmd_check(_namespace(json=True))

        payload = json.loads(capsys.readouterr().out)
        assert result == 0
        assert payload == {
            "overall": "warn",
            "probes": [{"name": "rdp", "status": "warn", "detail": "slow", "duration_ms": 9}],
        }


class TestCleanupAndTimeSync:
    @pytest.mark.parametrize("removed", [[], ["~$one.docx", "~$two.xlsx"]])
    def test_cleanup_reports_removed_files(
        self,
        removed: list[str],
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from winpodx.core import daemon

        monkeypatch.setattr(daemon, "cleanup_lock_files", lambda: removed)

        main._cmd_cleanup()

        output = capsys.readouterr().out
        if removed:
            assert "Removed: ~$one.docx" in output
            assert "2 lock files cleaned up." in output
        else:
            assert output.strip() == "No lock files found."

    @pytest.mark.parametrize(
        "success,message",
        [(True, "Windows time synchronized."), (False, "Time sync failed. Is the pod running?")],
    )
    def test_timesync_reports_result(
        self,
        success: bool,
        message: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from winpodx.core import daemon

        cfg = object()
        _patch_config(monkeypatch, cfg)
        sync = Mock(return_value=success)
        monkeypatch.setattr(daemon, "sync_windows_time", sync)

        main._cmd_timesync()

        sync.assert_called_once_with(cfg)
        assert capsys.readouterr().out.strip() == message


class TestPower:
    @pytest.mark.parametrize(
        "suspend,resume,result,message",
        [
            (True, False, True, "Pod suspended (paused). CPU freed, memory retained."),
            (True, False, False, "Failed to suspend pod."),
            (False, True, True, "Pod resumed."),
            (False, True, False, "Failed to resume pod."),
        ],
    )
    def test_power_action_reports_backend_result(
        self,
        suspend: bool,
        resume: bool,
        result: bool,
        message: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from winpodx.core import daemon

        cfg = object()
        _patch_config(monkeypatch, cfg)
        suspend_handler = Mock(return_value=result)
        resume_handler = Mock(return_value=result)
        monkeypatch.setattr(daemon, "suspend_pod", suspend_handler)
        monkeypatch.setattr(daemon, "resume_pod", resume_handler)

        main._cmd_power(_namespace(suspend=suspend, resume=resume))

        selected = suspend_handler if suspend else resume_handler
        selected.assert_called_once_with(cfg)
        assert capsys.readouterr().out.strip() == message

    @pytest.mark.parametrize("paused,state", [(True, "suspended"), (False, "active")])
    def test_power_without_action_reports_state(
        self,
        paused: bool,
        state: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from winpodx.core import daemon

        cfg = object()
        _patch_config(monkeypatch, cfg)
        probe = Mock(return_value=paused)
        monkeypatch.setattr(daemon, "is_pod_paused", probe)

        main._cmd_power(_namespace(suspend=False, resume=False))

        probe.assert_called_once_with(cfg)
        assert capsys.readouterr().out.strip() == f"Pod power state: {state}"


class TestProvision:
    def _args(self, **overrides: object) -> argparse.Namespace:
        values: dict[str, object] = {
            "verbose": False,
            "no_reverse_open": False,
            "no_discovery": False,
            "wait_timeout": 42,
            "require_agent": True,
            "retries": 3,
        }
        values.update(overrides)
        return _namespace(**values)

    def test_manual_backend_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="manual")))

        result = main._cmd_provision(self._args())

        assert result == 2
        assert "not supported for backend 'manual'" in capsys.readouterr().out

    def test_success_forwards_flags_waiter_and_formats_results(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from winpodx.cli import pod
        from winpodx.core import provisioner

        cfg = SimpleNamespace(pod=SimpleNamespace(backend="podman"))
        _patch_config(monkeypatch, cfg)
        wait_ready = Mock()
        monkeypatch.setattr(pod, "_wait_ready", wait_ready)
        captured: dict[str, object] = {}

        def finish(loaded: object, **kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            kwargs["on_progress"]("agent", "ready")  # type: ignore[operator]
            assert kwargs["wait_fn"](loaded, 42) is True  # type: ignore[operator]
            return {
                "wait_ready": "ok",
                "apply_fixes": {"rdp": "ok", "network": "ok"},
                "discovery": "7 apps",
            }

        monkeypatch.setattr(provisioner, "finish_provisioning", finish)

        result = main._cmd_provision(self._args(verbose=True, no_reverse_open=True))

        assert result == 0
        assert captured["wait_timeout"] == 42
        assert captured["require_agent"] is True
        assert captured["with_reverse_open"] is False
        assert captured["with_discovery"] is True
        assert captured["retries"] == 3
        wait_ready.assert_called_once_with(42, show_logs=True, verbose=True)
        output = capsys.readouterr()
        assert "[agent] ready" in output.err
        assert "Provisioning complete." in output.out
        assert "apply_fixes: 2/2 fixes OK" in output.out
        assert "discovery: 7 apps" in output.out

    def test_mixed_fix_results_render_details(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import provisioner

        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="docker")))
        monkeypatch.setattr(
            provisioner,
            "finish_provisioning",
            lambda cfg, **kwargs: {
                "wait_ready": "ok",
                "apply_fixes": {"rdp": "ok", "network": "failed"},
            },
        )

        result = main._cmd_provision(self._args(no_discovery=True))

        assert result == 0
        assert "apply_fixes: rdp: ok, network: failed" in capsys.readouterr().out

    def test_agent_unavailable_returns_deferred_exit(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import provisioner

        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="podman")))
        monkeypatch.setattr(
            provisioner,
            "finish_provisioning",
            Mock(side_effect=provisioner.ProvisionAgentUnavailable("agent offline")),
        )

        result = main._cmd_provision(self._args())

        assert result == 5
        assert "provision deferred: agent offline" in capsys.readouterr().err

    def test_discovery_failure_returns_deferred_exit_without_success_banner(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import provisioner

        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="podman")))
        monkeypatch.setattr(
            provisioner,
            "finish_provisioning",
            lambda cfg, **kwargs: {"wait_ready": "ok", "discovery": "failed: guest channel closed"},
        )

        result = main._cmd_provision(self._args())

        output = capsys.readouterr()
        assert result == 5
        assert "provision deferred" in output.err
        assert "discovery: failed: guest channel closed" in output.err
        assert "Provisioning complete." not in output.out

    def test_wait_timeout_returns_retryable_exit(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import provisioner

        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="podman")))
        monkeypatch.setattr(
            provisioner,
            "finish_provisioning",
            lambda cfg, **kwargs: {"wait_ready": "timeout"},
        )

        result = main._cmd_provision(self._args())

        assert result == 4
        assert "did not become responsive in time" in capsys.readouterr().err

    def test_waiter_maps_nonzero_system_exit_to_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from winpodx.cli import pod
        from winpodx.core import provisioner

        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="podman")))
        monkeypatch.setattr(pod, "_wait_ready", Mock(side_effect=SystemExit(3)))

        def finish(cfg: object, **kwargs: object) -> dict[str, str]:
            assert kwargs["wait_fn"](cfg, 8) is False  # type: ignore[operator]
            return {"wait_ready": "timeout"}

        monkeypatch.setattr(provisioner, "finish_provisioning", finish)

        assert main._cmd_provision(self._args()) == 4


class TestDebloat:
    def _args(self, **overrides: object) -> argparse.Namespace:
        values: dict[str, object] = {
            "list": False,
            "menu": False,
            "preset": None,
            "items": None,
            "undo": False,
        }
        values.update(overrides)
        return _namespace(**values)

    def test_catalog_listing_avoids_guest_and_config(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import debloat

        catalog = object()
        monkeypatch.setattr(debloat, "load_catalog", lambda: catalog)
        monkeypatch.setattr(debloat, "format_catalog_listing", lambda value: "catalog text")

        main._cmd_debloat(self._args(list=True))

        assert capsys.readouterr().out.strip() == "catalog text"

    def test_catalog_error_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import debloat

        monkeypatch.setattr(
            debloat,
            "load_catalog",
            Mock(side_effect=debloat.DebloatCatalogError("bad catalog")),
        )

        main._cmd_debloat(self._args())

        assert capsys.readouterr().out.strip() == "Debloat catalog error: bad catalog"

    def test_selection_error_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import debloat

        monkeypatch.setattr(debloat, "load_catalog", lambda: object())
        monkeypatch.setattr(
            debloat,
            "resolve_selection",
            Mock(side_effect=debloat.DebloatCatalogError("unknown item")),
        )

        main._cmd_debloat(self._args(items=" valid, ,unknown "))

        assert capsys.readouterr().out.strip() == "Debloat selection error: unknown item"

    def test_menu_quit_stops_before_config_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from winpodx.cli import debloat_menu
        from winpodx.core import debloat

        catalog = object()
        monkeypatch.setattr(debloat, "load_catalog", lambda: catalog)
        run_menu = Mock(return_value=None)
        monkeypatch.setattr(debloat_menu, "run_menu", run_menu)

        main._cmd_debloat(self._args(menu=True, preset="full"))

        run_menu.assert_called_once_with(catalog, initial_preset="full")

    def test_manual_backend_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import debloat

        monkeypatch.setattr(debloat, "load_catalog", lambda: object())
        monkeypatch.setattr(debloat, "resolve_selection", lambda *args, **kwargs: ["telemetry"])
        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="manual")))

        main._cmd_debloat(self._args())

        assert "only supported for Podman/Docker" in capsys.readouterr().out

    def test_payload_error_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import debloat

        monkeypatch.setattr(debloat, "load_catalog", lambda: object())
        monkeypatch.setattr(debloat, "resolve_selection", lambda *args, **kwargs: ["telemetry"])
        monkeypatch.setattr(
            debloat,
            "build_undo_script",
            Mock(side_effect=debloat.DebloatCatalogError("no undo")),
        )
        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="podman")))

        main._cmd_debloat(self._args(undo=True))

        assert capsys.readouterr().out.strip() == "Debloat payload build error: no undo"

    def test_apply_success_sends_exact_payload_and_prints_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from winpodx.core import debloat, windows_exec

        cfg = SimpleNamespace(pod=SimpleNamespace(backend="docker"))
        catalog = object()
        _patch_config(monkeypatch, cfg)
        monkeypatch.setattr(debloat, "load_catalog", lambda: catalog)
        monkeypatch.setattr(
            debloat,
            "resolve_selection",
            lambda value, preset, items: ["telemetry", "ads"],
        )
        monkeypatch.setattr(debloat, "build_run_script", lambda value, selected: "payload")
        runner = Mock(return_value=windows_exec.WindowsExecResult(0, "done\n", ""))
        monkeypatch.setattr(windows_exec, "run_via_transport", runner)

        main._cmd_debloat(self._args(items="telemetry, ads"))

        runner.assert_called_once_with(
            cfg,
            "payload",
            description="debloat-apply (telemetry,ads)",
            timeout=300,
        )
        output = capsys.readouterr().out
        assert "Running debloat apply (2 item(s)" in output
        assert "done" in output
        assert "Debloat apply complete." in output

    def test_nonzero_result_reports_stderr(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import debloat, windows_exec

        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="podman")))
        monkeypatch.setattr(debloat, "load_catalog", lambda: object())
        monkeypatch.setattr(debloat, "resolve_selection", lambda *args, **kwargs: ["ads"])
        monkeypatch.setattr(debloat, "build_run_script", lambda *args: "payload")
        monkeypatch.setattr(
            windows_exec,
            "run_via_transport",
            lambda *args, **kwargs: windows_exec.WindowsExecResult(9, "", "denied"),
        )

        main._cmd_debloat(self._args())

        assert "Debloat apply failed (rc=9): denied" in capsys.readouterr().out

    def test_channel_failure_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from winpodx.core import debloat, windows_exec

        _patch_config(monkeypatch, SimpleNamespace(pod=SimpleNamespace(backend="podman")))
        monkeypatch.setattr(debloat, "load_catalog", lambda: object())
        monkeypatch.setattr(debloat, "resolve_selection", lambda *args, **kwargs: ["ads"])
        monkeypatch.setattr(debloat, "build_run_script", lambda *args: "payload")
        monkeypatch.setattr(
            windows_exec,
            "run_via_transport",
            Mock(side_effect=windows_exec.WindowsExecError("transport down")),
        )

        main._cmd_debloat(self._args())

        assert capsys.readouterr().out.strip().endswith("Debloat channel failure: transport down")


def test_version_format_falls_back_when_detection_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpodx.utils import install_source

    monkeypatch.setattr(install_source, "detect", Mock(side_effect=RuntimeError("broken")))

    assert main._format_version_string() == f"winpodx {main.__version__}"
