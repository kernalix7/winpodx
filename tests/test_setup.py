# SPDX-License-Identifier: MIT
"""Tests for setup_cmd's half-uninstalled state guard.

Covers the case where ``winpodx setup --non-interactive`` is invoked
(by ``install.sh``) with an existing ``winpodx.toml`` but the podman/
docker container has been removed (e.g. by a non-purge ``uninstall.sh``
or external cleanup). Without this guard the next install.sh step
(``pod wait-ready``) would fail with ``Error: no such container ...``
and the user would have to do a ``--purge`` reinstall to recover.
"""

from __future__ import annotations

import argparse
import subprocess
from unittest.mock import MagicMock, patch

import pytest


def _make_existing_config(tmp_path):
    """Persist a podman config so handle_setup hits the existing-config branch."""
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.container_name = "winpodx-windows"
    cfg.rdp.user = "User"
    cfg.rdp.password = "Pa55w0rd!Test"  # noqa: S105 — fixture only
    cfg.save()
    return cfg


def _setup_args(non_interactive: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        backend=None,
        non_interactive=non_interactive,
        update_image=False,
        migrate_storage=False,
    )


def _mock_freerdp_ok():
    dep = MagicMock()
    dep.found = True
    dep.note = ""
    return {"freerdp": dep}


class TestContainerExistsOnBackend:
    def test_returns_true_when_container_in_ps(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = _make_existing_config(tmp_path)

        from winpodx.cli.setup_cmd import _container_exists_on_backend

        completed = subprocess.CompletedProcess(
            args=["podman", "ps", "-a", "--format", "{{.Names}}"],
            returncode=0,
            stdout="winpodx-windows\nother-container\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=completed):
            assert _container_exists_on_backend(cfg) is True

    def test_returns_false_when_container_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = _make_existing_config(tmp_path)

        from winpodx.cli.setup_cmd import _container_exists_on_backend

        completed = subprocess.CompletedProcess(
            args=["podman", "ps", "-a", "--format", "{{.Names}}"],
            returncode=0,
            stdout="other-container\n",
            stderr="",
        )
        with patch("subprocess.run", return_value=completed):
            assert _container_exists_on_backend(cfg) is False

    def test_returns_false_on_subprocess_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = _make_existing_config(tmp_path)

        from winpodx.cli.setup_cmd import _container_exists_on_backend

        with patch("subprocess.run", side_effect=OSError("podman not found")):
            assert _container_exists_on_backend(cfg) is False

    def test_returns_false_on_timeout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = _make_existing_config(tmp_path)

        from winpodx.cli.setup_cmd import _container_exists_on_backend

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="podman", timeout=10),
        ):
            assert _container_exists_on_backend(cfg) is False

    def test_returns_false_when_backend_not_container(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = _make_existing_config(tmp_path)
        cfg.pod.backend = "manual"

        from winpodx.cli.setup_cmd import _container_exists_on_backend

        # No subprocess.run call at all for non-container backends.
        with patch("subprocess.run", side_effect=AssertionError("must not be called")):
            assert _container_exists_on_backend(cfg) is False


class TestHalfUninstalledGuard:
    def test_skips_setup_when_container_exists(self, tmp_path, monkeypatch):
        """Happy path: config + container both present → setup still skipped,
        ensure_ready NOT called (preserves pre-fix behaviour)."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        _make_existing_config(tmp_path)

        ensure_ready_mock = MagicMock()
        with (
            patch("winpodx.cli.setup_cmd.check_all", return_value=_mock_freerdp_ok()),
            patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
            patch("winpodx.cli.setup_cmd._ensure_oem_token_staged"),
            patch(
                "winpodx.cli.setup_cmd._container_exists_on_backend",
                return_value=True,
            ),
            patch("winpodx.core.provisioner.ensure_ready", ensure_ready_mock),
        ):
            from winpodx.cli.setup_cmd import handle_setup

            handle_setup(_setup_args(non_interactive=True))

        assert ensure_ready_mock.call_count == 0, (
            "ensure_ready must not run when container is healthy"
        )

    def test_marks_initialized_on_existing_config_skip(self, tmp_path, monkeypatch):
        """#341: non-interactive setup over an existing config whose
        `initialized` flag is still False must flip it True (and persist),
        so the first-run prompt stops firing on every invocation."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        cfg = _make_existing_config(tmp_path)
        assert cfg.pod.initialized is False  # precondition: the buggy state

        with (
            patch("winpodx.cli.setup_cmd.check_all", return_value=_mock_freerdp_ok()),
            patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
            patch("winpodx.cli.setup_cmd._ensure_oem_token_staged"),
            patch(
                "winpodx.cli.setup_cmd._container_exists_on_backend",
                return_value=True,
            ),
        ):
            from winpodx.cli.setup_cmd import handle_setup

            handle_setup(_setup_args(non_interactive=True))

        from winpodx.core.config import Config

        assert Config.load().pod.initialized is True, (
            "existing-config skip path must mark the install initialized (#341)"
        )

    def test_freerdp_source_persisted_on_existing_config(self, tmp_path, monkeypatch):
        """`winpodx setup --freerdp-source flatpak` on an existing config must
        persist the preference even on the skip path."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        _make_existing_config(tmp_path)

        args = _setup_args(non_interactive=True)
        args.freerdp_source = "flatpak"

        with (
            patch("winpodx.cli.setup_cmd.check_all", return_value=_mock_freerdp_ok()),
            patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
            patch("winpodx.cli.setup_cmd._ensure_oem_token_staged"),
            patch(
                "winpodx.cli.setup_cmd._container_exists_on_backend",
                return_value=True,
            ),
        ):
            from winpodx.cli.setup_cmd import handle_setup

            handle_setup(args)

        from winpodx.core.config import Config

        assert Config.load().rdp.freerdp_source == "flatpak"

    def test_calls_ensure_ready_when_container_missing(self, tmp_path, monkeypatch):
        """Half-uninstalled: config present, container gone → ensure_ready
        is called to recreate the container from compose.yaml."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        _make_existing_config(tmp_path)

        from winpodx.core.pod import PodState, PodStatus

        ensure_ready_mock = MagicMock()
        with (
            patch("winpodx.cli.setup_cmd.check_all", return_value=_mock_freerdp_ok()),
            patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
            patch("winpodx.cli.setup_cmd._ensure_oem_token_staged"),
            patch(
                "winpodx.core.pod.pod_status",
                return_value=PodStatus(state=PodState.STOPPED),
            ),
            patch(
                "winpodx.cli.setup_cmd._container_exists_on_backend",
                return_value=False,
            ),
            patch("winpodx.core.provisioner.ensure_ready", ensure_ready_mock),
        ):
            from winpodx.cli.setup_cmd import handle_setup

            handle_setup(_setup_args(non_interactive=True))

        assert ensure_ready_mock.call_count == 1, "ensure_ready must run when container is missing"

    def test_does_not_crash_when_ensure_ready_raises(self, tmp_path, monkeypatch, capsys):
        """Recovery is best-effort: if ensure_ready can't bring the pod up,
        print a warning + recovery hint and return cleanly so install.sh's
        downstream steps still get a chance to run."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        _make_existing_config(tmp_path)

        from winpodx.core.pod import PodState, PodStatus

        with (
            patch("winpodx.cli.setup_cmd.check_all", return_value=_mock_freerdp_ok()),
            patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
            patch("winpodx.cli.setup_cmd._ensure_oem_token_staged"),
            patch(
                "winpodx.core.pod.pod_status",
                return_value=PodStatus(state=PodState.STOPPED),
            ),
            patch(
                "winpodx.cli.setup_cmd._container_exists_on_backend",
                return_value=False,
            ),
            patch(
                "winpodx.core.provisioner.ensure_ready",
                side_effect=RuntimeError("podman socket down"),
            ),
        ):
            from winpodx.cli.setup_cmd import handle_setup

            # Must not raise.
            handle_setup(_setup_args(non_interactive=True))

        captured = capsys.readouterr().out
        assert "WARNING: could not start pod" in captured
        assert "podman socket down" in captured
        assert "uninstall.sh --purge" in captured

    def test_does_not_run_for_manual_backend(self, tmp_path, monkeypatch):
        """The manual backend doesn't have a 'container' to detect; the heal
        path must short-circuit before probing pod_status so the function is a
        no-op for non-container deployments. (libvirt was dropped in 0.6.0.)"""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        cfg = _make_existing_config(tmp_path)
        cfg.pod.backend = "manual"
        cfg.save()

        ensure_ready_mock = MagicMock()
        container_check_mock = MagicMock(return_value=False)
        with (
            patch("winpodx.cli.setup_cmd.check_all", return_value=_mock_freerdp_ok()),
            patch("winpodx.cli.setup_cmd.import_winapps_config", return_value=None),
            patch("winpodx.cli.setup_cmd._ensure_oem_token_staged"),
            patch(
                "winpodx.cli.setup_cmd._container_exists_on_backend",
                container_check_mock,
            ),
            patch("winpodx.core.provisioner.ensure_ready", ensure_ready_mock),
        ):
            from winpodx.cli.setup_cmd import handle_setup

            handle_setup(_setup_args(non_interactive=True))

        assert container_check_mock.call_count == 0
        assert ensure_ready_mock.call_count == 0


class TestHealHelperDirect:
    """Direct unit tests for _heal_missing_container_if_needed."""

    def test_pod_status_failure_treated_as_missing(self, tmp_path, monkeypatch):
        """If pod_status raises (e.g. podman not on PATH at probe time)
        AND _container_exists_on_backend says False, we still attempt the
        heal — better to try and fail loud than silently skip."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = _make_existing_config(tmp_path)

        ensure_ready_mock = MagicMock()
        with (
            patch(
                "winpodx.core.pod.pod_status",
                side_effect=RuntimeError("backend init failed"),
            ),
            patch(
                "winpodx.cli.setup_cmd._container_exists_on_backend",
                return_value=False,
            ),
            patch("winpodx.core.provisioner.ensure_ready", ensure_ready_mock),
        ):
            from winpodx.cli.setup_cmd import _heal_missing_container_if_needed

            _heal_missing_container_if_needed(cfg)

        assert ensure_ready_mock.call_count == 1

    @pytest.mark.parametrize("state_name", ["RUNNING", "PAUSED", "STARTING"])
    def test_skips_when_pod_already_alive(self, tmp_path, monkeypatch, state_name):
        """If pod_status reports the pod is alive in any form, the heal
        path must not try to recreate it — that would needlessly cycle a
        working container."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = _make_existing_config(tmp_path)

        from winpodx.core.pod import PodState, PodStatus

        ensure_ready_mock = MagicMock()
        with (
            patch(
                "winpodx.core.pod.pod_status",
                return_value=PodStatus(state=getattr(PodState, state_name)),
            ),
            patch("winpodx.core.provisioner.ensure_ready", ensure_ready_mock),
        ):
            from winpodx.cli.setup_cmd import _heal_missing_container_if_needed

            _heal_missing_container_if_needed(cfg)

        assert ensure_ready_mock.call_count == 0


class TestResolveCredentials:
    """`_resolve_credentials` decides whether to prompt, generate, or preserve.

    Regression coverage for #216: an interactive `winpodx setup` rerun on an
    existing install used to reprompt for the password and silently
    overwrite cfg.rdp.password, which desynced from the Windows guest
    account (dockur honors USERNAME/PASSWORD only on first boot) and locked
    the user out at next launch.
    """

    def test_non_interactive_generates_fresh_credentials(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.cli.setup_cmd import _resolve_credentials
        from winpodx.core.config import Config

        cfg = Config()
        cfg.rdp.user = ""
        cfg.rdp.password = ""

        _resolve_credentials(cfg, non_interactive=True, config_existed=False)

        assert cfg.rdp.user == "WPX-User"
        assert cfg.rdp.password  # randomly generated
        assert cfg.rdp.ip == "127.0.0.1"
        assert cfg.rdp.password_updated

    def test_preserves_password_when_config_existed(self, tmp_path, monkeypatch):
        """Interactive rerun on an existing install must not reprompt for
        the password — that would desync the host config from the Windows
        guest account. #216."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.cli.setup_cmd import _resolve_credentials
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.rdp.user = "User"
        cfg.rdp.password = "OldPa55w0rd!"  # noqa: S105 — fixture only
        cfg.rdp.password_updated = "2026-01-01T00:00:00+00:00"
        cfg.rdp.ip = "127.0.0.1"

        getpass_mock = MagicMock(return_value="NewPa55w0rd!")
        ask_mock = MagicMock(return_value="bogus")
        with (
            patch("winpodx.cli.setup_cmd._ask", ask_mock),
            patch("getpass.getpass", getpass_mock),
        ):
            _resolve_credentials(cfg, non_interactive=False, config_existed=True)

        assert cfg.rdp.user == "User"
        assert cfg.rdp.password == "OldPa55w0rd!"
        assert cfg.rdp.password_updated == "2026-01-01T00:00:00+00:00"
        assert cfg.rdp.ip == "127.0.0.1"
        getpass_mock.assert_not_called()
        ask_mock.assert_not_called()

    def test_interactive_fresh_install_prompts_for_credentials(self, tmp_path, monkeypatch):
        """No prior config — the wizard must ask for user / password / ip."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.cli.setup_cmd import _resolve_credentials
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"

        ask_returns = iter(["alice", "127.0.0.1"])
        ask_mock = MagicMock(side_effect=lambda *a, **kw: next(ask_returns))
        getpass_mock = MagicMock(return_value="ChosenPassw0rd!")

        with (
            patch("winpodx.cli.setup_cmd._ask", ask_mock),
            patch("getpass.getpass", getpass_mock),
        ):
            _resolve_credentials(cfg, non_interactive=False, config_existed=False)

        assert cfg.rdp.user == "alice"
        assert cfg.rdp.password == "ChosenPassw0rd!"
        assert cfg.rdp.ip == "127.0.0.1"
        getpass_mock.assert_called_once()

    def test_interactive_existing_config_but_blank_password_still_prompts(
        self, tmp_path, monkeypatch
    ):
        """An existing config with an empty password (mid-init / corruption)
        should not silently keep the blank — fall back to the prompt."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.cli.setup_cmd import _resolve_credentials
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.rdp.user = "User"
        cfg.rdp.password = ""

        ask_returns = iter(["User", "127.0.0.1"])
        ask_mock = MagicMock(side_effect=lambda *a, **kw: next(ask_returns))
        getpass_mock = MagicMock(return_value="FillIn!")

        with (
            patch("winpodx.cli.setup_cmd._ask", ask_mock),
            patch("getpass.getpass", getpass_mock),
        ):
            _resolve_credentials(cfg, non_interactive=False, config_existed=True)

        assert cfg.rdp.password == "FillIn!"
        getpass_mock.assert_called_once()


class TestDecideStorageModeExplicitTarget:
    """`--storage-path` / install.sh `--storage-dir` (#646)."""

    def test_explicit_target_fresh_sets_storage_path(self, tmp_path):
        from pathlib import Path

        from winpodx.cli.setup_cmd import _decide_storage_mode
        from winpodx.core.config import Config

        target = tmp_path / "roomy" / "winpodx"
        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.pod.storage_path = ""
        with (
            patch("winpodx.core.storage_migration.resolve_named_volume", return_value=None),
            patch("winpodx.utils.btrfs.detect_path_fs", return_value="ext4"),
            patch("winpodx.utils.btrfs.host_storage_is_ssd", return_value=False),
        ):
            _decide_storage_mode(cfg, non_interactive=True, explicit_target=Path(target))
        assert cfg.pod.storage_path == str(target)
        assert target.is_dir()

    def test_explicit_target_ignored_when_already_configured(self, tmp_path):
        from pathlib import Path

        from winpodx.cli.setup_cmd import _decide_storage_mode
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.pod.storage_path = "/existing/storage"
        _decide_storage_mode(cfg, non_interactive=True, explicit_target=Path(tmp_path / "other"))
        # An already-configured install isn't relocated here (that's --migrate-storage).
        assert cfg.pod.storage_path == "/existing/storage"


class TestStageWinIso:
    """`_stage_win_iso` — #647: stage <storage>/custom.iso before compose-up."""

    def _iso(self, tmp_path):
        from pathlib import Path

        p = Path(tmp_path) / "win.iso"
        p.write_bytes(b"0" * 4096)
        return p

    def test_none_path_is_noop(self, tmp_path):
        from winpodx.cli.setup_cmd import _stage_win_iso
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.storage_path = str(tmp_path / "storage")
        _stage_win_iso(cfg, None)
        assert not (tmp_path / "storage" / "custom.iso").exists()

    def test_stages_to_custom_iso(self, tmp_path):
        from pathlib import Path

        from winpodx.cli.setup_cmd import _stage_win_iso
        from winpodx.core.config import Config

        iso = self._iso(tmp_path)
        cfg = Config()
        cfg.pod.storage_path = str(tmp_path / "storage")
        _stage_win_iso(cfg, str(iso))
        dst = Path(tmp_path) / "storage" / "custom.iso"
        assert dst.is_file()
        assert dst.read_bytes() == iso.read_bytes()

    def test_no_storage_path_skips(self, tmp_path):
        from winpodx.cli.setup_cmd import _stage_win_iso
        from winpodx.core.config import Config

        iso = self._iso(tmp_path)
        cfg = Config()
        cfg.pod.storage_path = ""  # legacy named-volume → no host dir
        _stage_win_iso(cfg, str(iso))  # must not raise
        # nothing to assert beyond "no crash"; named-volume has no host dir

    def test_missing_iso_skips(self, tmp_path):
        from winpodx.cli.setup_cmd import _stage_win_iso
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.storage_path = str(tmp_path / "storage")
        _stage_win_iso(cfg, str(tmp_path / "nope.iso"))  # must not raise
        assert not (tmp_path / "storage" / "custom.iso").exists()

    def test_same_file_is_noop(self, tmp_path):
        from pathlib import Path

        from winpodx.cli.setup_cmd import _stage_win_iso
        from winpodx.core.config import Config

        storage = Path(tmp_path) / "storage"
        storage.mkdir()
        dst = storage / "custom.iso"
        dst.write_bytes(b"0" * 4096)
        cfg = Config()
        cfg.pod.storage_path = str(storage)
        _stage_win_iso(cfg, str(dst))  # src == dst → no error, no truncation
        assert dst.read_bytes() == b"0" * 4096
