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
        cfg.pod.backend = "libvirt"

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

    def test_does_not_run_for_libvirt_backend(self, tmp_path, monkeypatch):
        """Libvirt / manual backends don't have a 'container' to detect;
        the heal path must short-circuit before probing pod_status so the
        function is a no-op for non-container deployments."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))
        cfg = _make_existing_config(tmp_path)
        cfg.pod.backend = "libvirt"
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
