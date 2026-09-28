# SPDX-License-Identifier: MIT
"""Additional branch coverage for the setup command."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from winpodx.cli import setup_cmd
from winpodx.core.config import Config
from winpodx.core.pod import PodState, PodStatus
from winpodx.utils.deps import DepCheck


@pytest.fixture(autouse=True)
def _pod_reports_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """Short-circuit the half-uninstalled guard's live pod probe."""
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )


def _args(**overrides: str | bool | None) -> argparse.Namespace:
    values: dict[str, str | bool | None] = {
        "backend": None,
        "customize": False,
        "update_image": False,
        "migrate_storage": False,
        "migrate_storage_target": None,
        "yes": False,
        "non_interactive": False,
        "win_version": None,
        "freerdp_source": None,
        "multimon": None,
        "storage_path": None,
        "win_iso": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _deps(*, daemon_reachable: bool | None = True) -> dict[str, DepCheck]:
    return {
        "freerdp": DepCheck("freerdp", True, note="FreeRDP 3"),
        "podman": DepCheck("podman", True, note="Podman", daemon_reachable=daemon_reachable),
        "docker": DepCheck("docker", False, note="not installed"),
        "kvm": DepCheck("kvm", True, note="available"),
    }


def test_compose_timeout_parses_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(setup_cmd.COMPOSE_TIMEOUT_ENV_VAR, "0")
    assert setup_cmd._compose_timeout_secs() is None

    monkeypatch.setenv(setup_cmd.COMPOSE_TIMEOUT_ENV_VAR, "invalid")
    assert setup_cmd._compose_timeout_secs() == setup_cmd.COMPOSE_TIMEOUT_DEFAULT_SECS

    monkeypatch.setenv(setup_cmd.COMPOSE_TIMEOUT_ENV_VAR, "-4")
    assert setup_cmd._compose_timeout_secs() == setup_cmd.COMPOSE_TIMEOUT_DEFAULT_SECS


def test_oem_token_staging_handles_existing_dir_and_oserror(tmp_path: Path, capsys) -> None:
    stage = MagicMock()
    with (
        patch("winpodx.cli.setup_cmd.ensure_agent_token") as ensure,
        patch("winpodx.cli.setup_cmd._find_oem_dir", return_value=str(tmp_path)),
        patch("winpodx.cli.setup_cmd.stage_token_to_oem", stage),
    ):
        setup_cmd._ensure_oem_token_staged()

    ensure.assert_called_once_with()
    stage.assert_called_once_with(tmp_path)

    with (
        patch("winpodx.cli.setup_cmd.ensure_agent_token"),
        patch("winpodx.cli.setup_cmd._find_oem_dir", side_effect=OSError("denied")),
    ):
        setup_cmd._ensure_oem_token_staged()
    assert "could not stage agent token" in capsys.readouterr().out


def test_prompt_edition_locale_tuning_applies_answers(capsys) -> None:
    cfg = Config()
    answers = iter(["11", "Korean", "ko-KR", "ko-KR", "performance"])

    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=lambda *args, **kwargs: next(answers)),
        patch("winpodx.core.config.known_win_version_codes", return_value=("11", "10")),
        patch(
            "winpodx.utils.locale.detect_install_locale",
            return_value=("English", "en-US", "en-US"),
        ),
    ):
        setup_cmd._prompt_edition_locale_tuning(cfg)

    assert (cfg.pod.win_version, cfg.pod.language, cfg.pod.region) == ("11", "Korean", "ko-KR")
    assert (cfg.pod.keyboard, cfg.pod.tuning_profile) == ("ko-KR", "performance")
    assert "Edition / locale / tuning" in capsys.readouterr().out


def test_prompt_edition_locale_tuning_rejects_unknown_profile(capsys) -> None:
    cfg = Config()
    original = cfg.pod.tuning_profile
    with (
        patch(
            "winpodx.cli.setup_cmd._ask", side_effect=["11", "English", "en-US", "en-US", "warp"]
        ),
        patch("winpodx.core.config.known_win_version_codes", return_value=("11",)),
        patch(
            "winpodx.utils.locale.detect_install_locale",
            return_value=("English", "en-US", "en-US"),
        ),
    ):
        setup_cmd._prompt_edition_locale_tuning(cfg)

    assert cfg.pod.tuning_profile == original
    assert "unknown profile" in capsys.readouterr().out


def test_storage_iso_prompt_enter_keeps_defaults(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    cfg = Config()
    with (
        patch("builtins.input", return_value=""),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
    ):
        storage, iso = setup_cmd._prompt_storage_and_iso(
            cfg, _args(customize=True), config_existed=False
        )

    assert storage is None
    assert iso is None
    output = capsys.readouterr().out
    assert "Storage:" in output
    assert "ISO:" in output
    assert "download" in output
    assert "--migrate-storage" in output


def test_storage_iso_prompt_routes_custom_path_and_readable_iso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config()
    target = tmp_path / "custom-storage"
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"iso fixture")
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=[str(target), str(iso), "y"]),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
    ):
        storage, selected_iso = setup_cmd._prompt_storage_and_iso(
            cfg, _args(customize=True), config_existed=False
        )

    assert storage == target
    assert selected_iso == str(iso)
    assert not target.exists()
    output = capsys.readouterr().out
    assert str(target) in output
    assert str(iso) in output


@pytest.mark.parametrize("selected", ["relative/path", "/etc/winpodx", "/"])
def test_storage_iso_prompt_rejects_invalid_storage_before_creating_pod(
    selected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = Config()
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=[selected, "", "y"]),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd._prompt_storage_and_iso(cfg, _args(customize=True), config_existed=False)

    assert exc.value.code == 1
    assert cfg.pod.storage_path == ""


@pytest.mark.parametrize("iso_name", ["missing.iso", "directory"])
def test_storage_iso_prompt_rejects_missing_or_nonfile_iso(tmp_path: Path, iso_name: str) -> None:
    if iso_name == "directory":
        (tmp_path / iso_name).mkdir()
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=["", str(tmp_path / iso_name), "y"]),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd._prompt_storage_and_iso(Config(), _args(customize=True), config_existed=False)

    assert exc.value.code == 1


def test_storage_iso_prompt_rejects_unreadable_iso(tmp_path: Path) -> None:
    iso = tmp_path / "unreadable.iso"
    iso.write_bytes(b"iso fixture")
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=["", str(iso), "y"]),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
        patch.object(Path, "open", side_effect=PermissionError("denied")),
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd._prompt_storage_and_iso(Config(), _args(customize=True), config_existed=False)

    assert exc.value.code == 1


def test_storage_iso_prompt_existing_config_requires_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = Config()
    cfg.pod.storage_path = str(tmp_path / "existing")
    chosen = tmp_path / "elsewhere"
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=[str(chosen), "", "y"]),
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd._prompt_storage_and_iso(cfg, _args(customize=True), config_existed=True)

    assert exc.value.code == 1
    assert cfg.pod.storage_path == str(tmp_path / "existing")
    assert not chosen.exists()
    output = capsys.readouterr().out
    assert str(chosen) in output
    assert "ISO:" in output
    assert "--migrate-storage" in output


def test_storage_iso_prompt_existing_volume_rejects_new_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    chosen = tmp_path / "elsewhere"
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=[str(chosen), "", "y"]),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value="winpodx-data"),
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd._prompt_storage_and_iso(Config(), _args(customize=True), config_existed=False)

    assert exc.value.code == 1
    assert "--migrate-storage" in capsys.readouterr().out
    assert not chosen.exists()


def test_storage_iso_prompt_existing_volume_rejects_iso(tmp_path: Path, capsys) -> None:
    iso = tmp_path / "replacement.iso"
    iso.write_bytes(b"iso fixture")
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=["", str(iso), "y"]),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value="winpodx-data"),
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd._prompt_storage_and_iso(Config(), _args(customize=True), config_existed=False)

    assert exc.value.code == 1
    assert "--migrate-storage" in capsys.readouterr().out


def test_storage_iso_prompt_rejects_nonempty_storage_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "windows.img").write_bytes(b"guest data")
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=[str(target), "", "y"]),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd._prompt_storage_and_iso(Config(), _args(customize=True), config_existed=False)

    assert exc.value.code == 1
    assert (target / "windows.img").read_bytes() == b"guest data"


def test_storage_iso_prompt_preserves_explicit_flags_on_enter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "flag-storage"
    iso = tmp_path / "flag.iso"
    iso.write_bytes(b"iso fixture")
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=["", "", "y"]),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
    ):
        storage, selected_iso = setup_cmd._prompt_storage_and_iso(
            Config(),
            _args(customize=True, storage_path=str(target), win_iso=str(iso)),
            config_existed=False,
        )

    assert storage == target
    assert selected_iso == str(iso)


def test_storage_iso_prompt_declined_review_makes_no_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "new-storage"
    with (
        patch("winpodx.cli.setup_cmd._ask", side_effect=[str(target), "", "n"]),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd._prompt_storage_and_iso(Config(), _args(customize=True), config_existed=False)

    assert exc.value.code == 1
    assert not target.exists()


def test_migrate_storage_reports_plan_and_success(tmp_path: Path, capsys) -> None:
    from winpodx.core.storage_migration import MigrationPlan, MigrationResult

    cfg = Config()
    plan = MigrationPlan(
        backend="podman",
        source_volume="winpodx-data",
        source_mountpoint=tmp_path / "source",
        source_size_bytes=3 << 30,
        target_path=tmp_path / "target",
        target_fs="btrfs",
        chattr_will_run=True,
        free_bytes_target=20 << 30,
    )
    execute = MagicMock(return_value=MigrationResult("ok", "moved safely"))
    with (
        patch("winpodx.cli.setup_cmd.Config.load", return_value=cfg),
        patch("winpodx.core.storage_migration.plan_migration", return_value=plan),
        patch("winpodx.core.storage_migration.execute_migration", execute),
    ):
        result = setup_cmd._handle_migrate_storage(
            _args(migrate_storage_target=str(tmp_path / "target"), yes=True)
        )

    assert result == 0
    execute.assert_called_once_with(cfg, plan, start_pod=True)
    output = capsys.readouterr().out
    assert "chattr" in output
    assert "20 GiB" in output
    assert "OK: moved safely" in output


def test_migrate_storage_handles_plan_error_abort_and_failure(tmp_path: Path, capsys) -> None:
    from winpodx.core.storage_migration import MigrationPlan, MigrationResult

    cfg = Config()
    with (
        patch("winpodx.cli.setup_cmd.Config.load", return_value=cfg),
        patch("winpodx.core.storage_migration.plan_migration", return_value="no volume"),
    ):
        assert setup_cmd._handle_migrate_storage(_args()) == 2

    plan = MigrationPlan("podman", "vol", tmp_path, 0, tmp_path / "dst", "ext4", False, -1)
    with (
        patch("winpodx.cli.setup_cmd.Config.load", return_value=cfg),
        patch("winpodx.core.storage_migration.plan_migration", return_value=plan),
        patch("winpodx.cli.setup_cmd._ask", return_value="n"),
        patch("winpodx.core.storage_migration.execute_migration") as execute,
    ):
        assert setup_cmd._handle_migrate_storage(_args()) == 0
        execute.assert_not_called()

    with (
        patch("winpodx.cli.setup_cmd.Config.load", return_value=cfg),
        patch("winpodx.core.storage_migration.plan_migration", return_value=plan),
        patch(
            "winpodx.core.storage_migration.execute_migration",
            return_value=MigrationResult("failed", "rsync failed"),
        ),
    ):
        assert setup_cmd._handle_migrate_storage(_args(yes=True)) == 3
    output = capsys.readouterr().out
    assert "--migrate-storage: no volume" in output
    assert "Aborted." in output
    assert "FAIL: rsync failed" in output


def test_storage_mode_btrfs_path_keeps_ssd_auto(tmp_path: Path, capsys) -> None:
    cfg = Config()
    cfg.pod.backend = "podman"
    target = tmp_path / "storage"
    with (
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
        patch("winpodx.utils.btrfs.detect_path_fs", return_value="btrfs"),
        patch("winpodx.utils.btrfs.disable_cow_on_path", return_value=("disabled", "")),
    ):
        setup_cmd._decide_storage_mode(cfg, non_interactive=True, explicit_target=target)

    assert cfg.pod.storage_path == str(target)
    assert cfg.pod.ssd is None
    output = capsys.readouterr().out
    assert "applied chattr +C" in output
    assert "emulate SSD" not in output


def test_storage_mode_preserves_explicit_hdd(tmp_path: Path) -> None:
    cfg = Config()
    cfg.pod.ssd = False
    target = tmp_path / "storage"
    with (
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
        patch("winpodx.utils.btrfs.detect_path_fs", return_value="ext4"),
    ):
        setup_cmd._decide_storage_mode(cfg, non_interactive=True, explicit_target=target)

    assert cfg.pod.storage_path == str(target)
    assert cfg.pod.ssd is False


def test_storage_mode_named_volume_warns_on_btrfs(tmp_path: Path, capsys) -> None:
    cfg = Config()
    cfg.pod.backend = "podman"
    with (
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value="legacy"),
        patch("winpodx.core.storage_migration.get_volume_mountpoint", return_value=tmp_path),
        patch("winpodx.utils.btrfs.detect_path_fs", return_value="btrfs"),
    ):
        setup_cmd._decide_storage_mode(cfg, non_interactive=True)
    assert "existing 'legacy' volume is on btrfs" in capsys.readouterr().out


def test_stage_iso_falls_back_when_reflink_copy_fails(tmp_path: Path) -> None:
    source = tmp_path / "windows.iso"
    source.write_bytes(b"local iso")
    cfg = Config()
    cfg.pod.storage_path = str(tmp_path / "storage")

    fake_subprocess = SimpleNamespace(
        run=MagicMock(side_effect=FileNotFoundError),
        CalledProcessError=subprocess.CalledProcessError,
    )
    with patch.dict(sys.modules, {"subprocess": fake_subprocess}):
        setup_cmd._stage_win_iso(cfg, str(source))

    assert (tmp_path / "storage" / "custom.iso").read_bytes() == b"local iso"


def test_recreate_container_podman_plugin_warns_then_starts(tmp_path: Path, capsys) -> None:
    cfg = Config()
    cfg.pod.backend = "podman"
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 1, "", "socket warning"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
    )
    run = MagicMock(side_effect=lambda *args, **kwargs: next(results))
    fake_subprocess = SimpleNamespace(
        run=run,
        CalledProcessError=subprocess.CalledProcessError,
    )
    with (
        patch("winpodx.cli.setup_cmd.find_podman_compose", return_value=None),
        patch("winpodx.backend._hostenv.host_env", return_value={"PATH": "/host"}),
        patch.dict(sys.modules, {"subprocess": fake_subprocess}),
    ):
        setup_cmd._recreate_container(cfg)

    assert run.call_args_list[1].args[0] == ["podman", "compose", "down"]
    assert run.call_args_list[2].args[0] == ["podman", "compose", "up", "-d"]
    output = capsys.readouterr().out
    assert "compose down returned 1" in output
    assert "Container started." in output


def test_recreate_container_docker_failure_raises(tmp_path: Path, capsys) -> None:
    cfg = Config()
    cfg.pod.backend = "docker"
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 4, "", "pull denied"),
        ]
    )
    fake_subprocess = SimpleNamespace(
        run=MagicMock(side_effect=lambda *args, **kwargs: next(results)),
        CalledProcessError=subprocess.CalledProcessError,
    )
    with (
        patch("winpodx.cli.setup_cmd.shutil.which", return_value="/usr/bin/docker-compose"),
        patch("winpodx.backend._hostenv.host_env", return_value=None),
        patch.dict(sys.modules, {"subprocess": fake_subprocess}),
        pytest.raises(RuntimeError, match="Container start failed: pull denied"),
    ):
        setup_cmd._recreate_container(cfg)
    assert "Failed to start container: pull denied" in capsys.readouterr().out


def test_full_provision_forwards_options_and_reports_warnings(capsys) -> None:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.reverse_open.enabled = True
    finish = MagicMock(return_value={"wait_ready": "timeout", "discovery": "0 apps"})
    with patch("winpodx.core.provisioner.finish_provisioning", finish):
        setup_cmd._run_full_provision(cfg)

    kwargs = finish.call_args.kwargs
    assert kwargs["wait_timeout"] == 3600
    assert kwargs["with_reverse_open"] is True
    assert kwargs["retries"] == 5
    kwargs["on_progress"]("agent", "ready")
    output = capsys.readouterr().out
    assert "wait-ready did not complete" in output
    assert "app discovery did not find any applications" in output


def test_full_provision_wait_fn_streams_live_lines_as_wait_ready_progress(capsys) -> None:
    # Given: a captured finish_provisioning call and a fake _wait_ready that
    # emits a live line through the on_log callback it is handed.
    cfg = Config()
    cfg.pod.backend = "podman"
    finish = MagicMock(return_value={})
    progress: list[tuple[str, str]] = []

    def fake_wait_ready(timeout: int, show_logs: bool, verbose: bool = False, on_log=None) -> None:
        assert on_log is not None
        on_log("      OK Container running")

    with (
        patch("winpodx.core.provisioner.finish_provisioning", finish),
        patch("winpodx.cli.pod._wait_ready", fake_wait_ready),
    ):
        setup_cmd._run_full_provision(cfg, on_progress=lambda s, d: progress.append((s, d)))

        # When: the injected wait_fn runs the wait-ready phase.
        wait_fn = finish.call_args.kwargs["wait_fn"]
        assert wait_fn(cfg, 3600) is True

    # Then: the live line was forwarded to the outer progress collector under
    # the wait_ready stage.
    assert ("wait_ready", "      OK Container running") in progress


def test_rotate_password_success_writes_config_and_compose(tmp_path: Path, capsys) -> None:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.rdp.password = "old-password"
    cfg.save()
    generated = MagicMock(side_effect=lambda current, path: path.write_text("compose-new\n"))
    with (
        patch("winpodx.cli.setup_cmd.Config.load", return_value=cfg),
        patch("winpodx.core.pod.pod_status", return_value=PodStatus(PodState.RUNNING)),
        patch("winpodx.core.provisioner._change_windows_password", return_value=True) as change,
        patch("winpodx.cli.setup_cmd._generate_password", return_value="new-password"),
        patch("winpodx.cli.setup_cmd._generate_compose_to", generated),
    ):
        setup_cmd.handle_rotate_password(_args())

    change.assert_called_once_with(cfg, "new-password")
    assert (Config.path().parent / "compose.yaml").read_text() == "compose-new\n"
    assert Config.load().rdp.password == "new-password"
    assert "Password rotated successfully." in capsys.readouterr().out


def test_rotate_password_rejects_backend_stopped_and_guest_failure(capsys) -> None:
    cfg = Config()
    cfg.pod.backend = "manual"
    with patch("winpodx.cli.setup_cmd.Config.load", return_value=cfg), pytest.raises(SystemExit):
        setup_cmd.handle_rotate_password(_args())

    cfg.pod.backend = "podman"
    with (
        patch("winpodx.cli.setup_cmd.Config.load", return_value=cfg),
        patch("winpodx.core.pod.pod_status", return_value=PodStatus(PodState.STOPPED)),
        pytest.raises(SystemExit),
    ):
        setup_cmd.handle_rotate_password(_args())

    with (
        patch("winpodx.cli.setup_cmd.Config.load", return_value=cfg),
        patch("winpodx.core.pod.pod_status", return_value=PodStatus(PodState.RUNNING)),
        patch("winpodx.core.provisioner._change_windows_password", return_value=False),
        patch("winpodx.cli.setup_cmd._generate_password", return_value="unused-password"),
        pytest.raises(SystemExit),
    ):
        setup_cmd.handle_rotate_password(_args())
    output = capsys.readouterr().out
    assert "only supported for podman/docker" in output
    assert "Container is not running" in output
    assert "Failed to change Windows password" in output


def test_rotate_password_rolls_back_when_compose_generation_fails(capsys) -> None:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.rdp.password = "old-password"
    cfg.rdp.password_updated = "old-time"
    with (
        patch("winpodx.cli.setup_cmd.Config.load", return_value=cfg),
        patch("winpodx.core.pod.pod_status", return_value=PodStatus(PodState.RUNNING)),
        patch("winpodx.core.provisioner._change_windows_password", return_value=True),
        patch("winpodx.cli.setup_cmd._generate_password", return_value="new-password"),
        patch("winpodx.cli.setup_cmd._generate_compose_to", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        setup_cmd.handle_rotate_password(_args())
    assert cfg.rdp.password == "old-password"
    assert cfg.rdp.password_updated == "old-time"
    assert "config and compose were not modified" in capsys.readouterr().out


def test_register_desktop_entries_installs_apps_and_updates_cache() -> None:
    apps = [SimpleNamespace(name="Word"), SimpleNamespace(name="Excel")]
    install_entry = MagicMock()
    with (
        patch("winpodx.core.app.list_available_apps", return_value=apps),
        patch("winpodx.desktop.entry.install_desktop_entry", install_entry),
        patch("winpodx.desktop.entry.install_desktop_shortcut") as shortcut,
        patch("winpodx.desktop.icons.install_gui_launcher_desktop") as launcher,
        patch("winpodx.desktop.icons.install_winpodx_icon") as icon,
        patch("winpodx.desktop.icons.update_icon_cache") as cache,
    ):
        setup_cmd._register_all_desktop_entries()

    assert install_entry.call_args_list == [call(apps[0]), call(apps[1])]
    icon.assert_called_once_with()
    launcher.assert_called_once_with()
    shortcut.assert_called_once_with()
    cache.assert_called_once_with()


def test_handle_setup_customize_podman_applies_wizard_and_writes_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    monkeypatch.setenv("WINPODX_NO_PROVISION", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    storage_target = tmp_path / "chosen-storage"
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"iso fixture")
    answers = iter(
        [
            "podman",
            "WizardUser",
            "127.0.0.2",
            "bad-cpu",
            "bad-ram",
            "Asia/Seoul",
            str(storage_target),
            str(iso),
            "y",
        ]
    )
    tier = SimpleNamespace(cpu_cores=6, ram_gb=8, label="high")
    host = SimpleNamespace(cpu_threads=16, ram_gb=32)
    with (
        patch("winpodx.cli.setup_cmd.check_all", return_value=_deps()),
        patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
        patch("winpodx.cli.setup_cmd._ask", side_effect=lambda *args, **kwargs: next(answers)),
        patch("getpass.getpass", return_value="WizardPassword!"),
        patch("winpodx.utils.specs.detect_host_specs", return_value=host),
        patch("winpodx.utils.specs.recommend_tier", return_value=tier),
        patch("winpodx.utils.locale.detect_timezone", return_value="UTC"),
        patch("winpodx.cli.setup_cmd._prompt_edition_locale_tuning") as locale_prompt,
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
        patch("winpodx.cli.setup_cmd._decide_storage_mode") as storage_decision,
        patch("winpodx.cli.setup_cmd._stage_win_iso") as stage_iso,
        patch("winpodx.cli.setup_cmd._generate_compose") as compose,
        patch("winpodx.cli.setup_cmd._recreate_container") as recreate,
        patch("winpodx.display.scaling.detect_scale_factor", return_value=125),
        patch("winpodx.display.scaling.detect_raw_scale", return_value=1.5),
        patch("winpodx.utils.specs.detect_tuning_capability", return_value=SimpleNamespace()),
        patch("winpodx.utils.specs.recommend_tuning_profile", return_value="safe"),
        patch("winpodx.utils.specs.format_tuning_summary", return_value="safe tuning"),
        patch("winpodx.cli.setup_cmd._ensure_oem_token_staged"),
        patch("winpodx.cli.setup_cmd._register_all_desktop_entries"),
    ):
        setup_cmd.handle_setup(_args(customize=True))

    saved = Config.load()
    assert saved.pod.backend == "podman"
    assert (saved.pod.cpu_cores, saved.pod.ram_gb) == (6, 8)
    assert saved.pod.timezone == "Asia/Seoul"
    assert (saved.rdp.user, saved.rdp.ip) == ("WizardUser", "127.0.0.2")
    assert (saved.rdp.scale, saved.rdp.dpi) == (125, 150)
    locale_prompt.assert_called_once()
    assert storage_decision.call_args.kwargs["explicit_target"] == storage_target
    assert stage_iso.call_args.args[1] == str(iso)
    compose.assert_called_once()
    recreate.assert_called_once()
    output = capsys.readouterr().out
    assert "Invalid number, using default: 6" in output
    assert "Invalid number, using default: 8" in output
    assert "Setup Complete" in output
    assert "Run `winpodx provision` to finish" in output
    assert str(storage_target) in output
    assert str(iso) in output


def test_handle_setup_rejects_invalid_wizard_storage_before_container_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    with (
        patch("winpodx.cli.setup_cmd.check_all", return_value=_deps()),
        patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
        patch("winpodx.cli.setup_cmd._resolve_credentials"),
        patch(
            "winpodx.utils.specs.detect_host_specs",
            return_value=SimpleNamespace(cpu_threads=8, ram_gb=16),
        ),
        patch(
            "winpodx.utils.specs.recommend_tier",
            return_value=SimpleNamespace(cpu_cores=4, ram_gb=6, label="mid"),
        ),
        patch("winpodx.utils.locale.detect_timezone", return_value="UTC"),
        patch("winpodx.cli.setup_cmd._prompt_edition_locale_tuning"),
        patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
        patch("winpodx.cli.setup_cmd._ask", side_effect=["", "", "", "relative/path", ""]),
        patch("winpodx.cli.setup_cmd._decide_storage_mode") as storage_decision,
        patch("winpodx.cli.setup_cmd._generate_compose") as compose,
        patch("winpodx.cli.setup_cmd._recreate_container") as recreate,
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd.handle_setup(_args(backend="podman", customize=True))

    assert exc.value.code == 1
    storage_decision.assert_not_called()
    compose.assert_not_called()
    recreate.assert_not_called()


def test_handle_setup_dependency_and_backend_failures(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    missing = _deps()
    missing["freerdp"] = DepCheck("freerdp", False, note="missing")
    from winpodx.setup_wizard.host_state import PreflightIssue, PreflightReport

    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
    with (
        patch("winpodx.cli.setup_cmd.check_all", return_value=missing),
        patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
        patch("winpodx.cli.setup_cmd._resolve_credentials"),
        patch("winpodx.utils.specs.detect_host_specs", return_value=SimpleNamespace()),
        patch(
            "winpodx.utils.specs.recommend_tier",
            return_value=SimpleNamespace(cpu_cores=4, ram_gb=6),
        ),
        patch(
            "winpodx.setup_wizard.host_state.inspect_preflight",
            return_value=PreflightReport(
                (
                    PreflightIssue("freerdp", "Install FreeRDP 3+", False),
                    PreflightIssue("cpu_virtualization", "Enable CPU virtualization", False),
                )
            ),
        ),
        pytest.raises(SystemExit) as exc,
    ):
        setup_cmd.handle_setup(_args(backend="podman"))
    assert exc.value.code == 1
    report = capsys.readouterr().err
    assert report.count("Host preflight failed:") == 1
    assert "FreeRDP 3+" in report
    assert "CPU virtualization" in report
    assert "Traceback" not in report

    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
    with (
        patch("winpodx.cli.setup_cmd.check_all", return_value=_deps()),
        patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
        patch("winpodx.cli.setup_cmd._ask", return_value="invalid"),
        pytest.raises(SystemExit),
    ):
        setup_cmd.handle_setup(_args(customize=True))
    output = capsys.readouterr().out
    assert "freerdp" in output
    assert "Invalid choice: invalid" in output


def test_handle_setup_rejects_unreachable_selected_daemon(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
    with (
        patch("winpodx.cli.setup_cmd.check_all", return_value=_deps(daemon_reachable=False)),
        patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
        patch("winpodx.backend.select.choose_backend", return_value="podman"),
        patch("winpodx.cli.setup_cmd._generate_password", return_value="generated-password"),
        patch("winpodx.utils.specs.detect_host_specs", return_value=SimpleNamespace()),
        patch(
            "winpodx.utils.specs.recommend_tier",
            return_value=SimpleNamespace(cpu_cores=4, ram_gb=6),
        ),
        patch("winpodx.cli.setup_cmd._decide_storage_mode"),
        patch("winpodx.cli.setup_cmd._stage_win_iso"),
        patch("winpodx.setup_wizard.host_state.require_preflight"),
        pytest.raises(SystemExit),
    ):
        setup_cmd.handle_setup(_args())
    assert "Cannot use the podman backend: Podman" in capsys.readouterr().out


@pytest.mark.parametrize("backend", ["podman", "docker"])
def test_update_image_pin_uses_exact_x86_ghcr_repository(backend: str) -> None:
    cfg = Config()
    cfg.pod.backend = backend
    cfg.pod.image = "docker.io/dockurr/windows@sha256:old"
    ghcr_pin = f"ghcr.io/dockur/windows@sha256:{'a' * 64}"
    run = MagicMock(
        side_effect=[
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(
                    [
                        f"docker.io/dockurr/windows@sha256:{'b' * 64}",
                        ghcr_pin,
                    ]
                ),
                stderr="",
            ),
        ]
    )
    fake_subprocess = SimpleNamespace(
        run=run,
        CalledProcessError=subprocess.CalledProcessError,
    )

    with (
        patch("winpodx.core.config.Config.load", return_value=cfg),
        patch("shutil.which", return_value=f"/usr/bin/{backend}"),
        patch("platform.machine", return_value="x86_64"),
        patch.dict(sys.modules, {"subprocess": fake_subprocess}),
        patch.object(cfg, "save") as save,
        patch("winpodx.core.compose.generate_compose") as generate_compose,
    ):
        result = setup_cmd._update_image_pin()

    assert result == 0
    assert run.call_args_list[0] == call(
        [backend, "pull", "ghcr.io/dockur/windows:latest"], check=True
    )
    assert run.call_args_list[1].args[0][3] == "ghcr.io/dockur/windows:latest"
    assert cfg.pod.image == ghcr_pin
    save.assert_called_once_with()
    generate_compose.assert_called_once_with(cfg)


def test_update_image_pin_uses_exact_arm_ghcr_repository() -> None:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.image = "docker.io/dockurr/windows-arm@sha256:old"
    ghcr_pin = f"ghcr.io/dockur/windows-arm@sha256:{'c' * 64}"
    run = MagicMock(
        side_effect=[
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0, stdout=json.dumps([ghcr_pin]), stderr=""),
        ]
    )
    fake_subprocess = SimpleNamespace(
        run=run,
        CalledProcessError=subprocess.CalledProcessError,
    )

    with (
        patch("winpodx.core.config.Config.load", return_value=cfg),
        patch("shutil.which", return_value="/usr/bin/podman"),
        patch("platform.machine", return_value="aarch64"),
        patch.dict(sys.modules, {"subprocess": fake_subprocess}),
        patch.object(cfg, "save") as save,
        patch("winpodx.core.compose.generate_compose") as generate_compose,
    ):
        result = setup_cmd._update_image_pin()

    assert result == 0
    assert run.call_args_list[0] == call(
        ["podman", "pull", "ghcr.io/dockur/windows-arm:latest"], check=True
    )
    assert run.call_args_list[1].args[0][3] == "ghcr.io/dockur/windows-arm:latest"
    assert cfg.pod.image == ghcr_pin
    save.assert_called_once_with()
    generate_compose.assert_called_once_with(cfg)


def test_update_image_pin_rejects_digest_from_another_registry() -> None:
    cfg = Config()
    cfg.pod.backend = "podman"
    original_image = "registry.example/windows@sha256:keep"
    cfg.pod.image = original_image
    run = MagicMock(
        side_effect=[
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps([f"docker.io/dockurr/windows@sha256:{'d' * 64}"]),
                stderr="",
            ),
        ]
    )
    fake_subprocess = SimpleNamespace(
        run=run,
        CalledProcessError=subprocess.CalledProcessError,
    )

    with (
        patch("winpodx.core.config.Config.load", return_value=cfg),
        patch("shutil.which", return_value="/usr/bin/podman"),
        patch("platform.machine", return_value="x86_64"),
        patch.dict(sys.modules, {"subprocess": fake_subprocess}),
        patch.object(cfg, "save") as save,
        patch("winpodx.core.compose.generate_compose") as generate_compose,
    ):
        result = setup_cmd._update_image_pin()

    assert result == 3
    assert cfg.pod.image == original_image
    save.assert_not_called()
    generate_compose.assert_not_called()
