"""Tests for winpodx.core.storage_migration — named-volume → bind-mount move."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from winpodx.core import storage_migration as sm


class TestNamedVolumeExists:
    def test_returns_false_for_unsupported_backend(self):
        assert sm.named_volume_exists("libvirt") is False

    def test_returns_false_when_backend_binary_missing(self):
        with patch.object(sm.shutil, "which", return_value=None):
            assert sm.named_volume_exists("podman") is False

    def test_true_when_volume_exists_returncode_zero(self):
        with (
            patch.object(sm.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(sm.subprocess, "run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert sm.named_volume_exists("podman") is True

    def test_false_when_volume_exists_returncode_nonzero(self):
        with (
            patch.object(sm.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(sm.subprocess, "run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            assert sm.named_volume_exists("podman") is False


class TestGetVolumeMountpoint:
    def test_parses_inspect_json(self):
        payload = json.dumps(
            [{"Mountpoint": "/var/lib/containers/storage/volumes/winpodx-data/_data"}]
        )
        with (
            patch.object(sm.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(sm.subprocess, "run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout=payload, stderr="")
            mp = sm.get_volume_mountpoint("podman")
        assert mp == Path("/var/lib/containers/storage/volumes/winpodx-data/_data")

    def test_returns_none_on_inspect_error(self):
        with (
            patch.object(sm.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(sm.subprocess, "run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="no such")
            assert sm.get_volume_mountpoint("podman") is None

    def test_returns_none_on_malformed_json(self):
        with (
            patch.object(sm.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(sm.subprocess, "run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="not-json", stderr="")
            assert sm.get_volume_mountpoint("podman") is None


class TestPlanMigration:
    def _cfg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.save()
        return cfg

    def test_returns_error_string_on_unsupported_backend(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        cfg.pod.backend = "libvirt"
        result = sm.plan_migration(cfg, target=tmp_path / "target")
        assert isinstance(result, str)
        assert "named volumes" in result

    def test_returns_error_string_when_no_named_volume(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        with patch.object(sm, "named_volume_exists", return_value=False):
            result = sm.plan_migration(cfg, target=tmp_path / "target")
        assert isinstance(result, str)
        assert "no" in result.lower() and "winpodx-data" in result

    def test_returns_error_string_when_target_not_empty(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        target = tmp_path / "target"
        target.mkdir()
        (target / "junk").write_text("hi")

        src = tmp_path / "src"
        src.mkdir()
        with (
            patch.object(sm, "named_volume_exists", return_value=True),
            patch.object(sm, "get_volume_mountpoint", return_value=src),
        ):
            result = sm.plan_migration(cfg, target=target)
        assert isinstance(result, str)
        assert "not empty" in result

    def test_returns_plan_on_happy_path(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        target = tmp_path / "target"
        src = tmp_path / "src"
        src.mkdir()
        (src / "win.qcow2").write_bytes(b"x" * 1024)

        with (
            patch.object(sm, "named_volume_exists", return_value=True),
            patch.object(sm, "get_volume_mountpoint", return_value=src),
            patch.object(sm, "detect_path_fs", return_value="btrfs"),
        ):
            plan = sm.plan_migration(cfg, target=target)

        assert isinstance(plan, sm.MigrationPlan)
        assert plan.source_mountpoint == src
        assert plan.target_path == target
        assert plan.target_fs == "btrfs"
        assert plan.chattr_will_run is True
        assert plan.source_size_bytes >= 1024


class TestExecuteMigration:
    def _cfg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.save()
        return cfg

    def test_failed_when_pod_stop_fails(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        plan = sm.MigrationPlan(
            backend="podman",
            source_volume="winpodx-data",
            source_mountpoint=tmp_path / "src",
            source_size_bytes=0,
            target_path=tmp_path / "target",
            target_fs="ext4",
            chattr_will_run=False,
            free_bytes_target=0,
        )
        with patch.object(sm, "_stop_pod", return_value=(False, "compose down failed")):
            result = sm.execute_migration(cfg, plan, start_pod=False)
        assert result.status == "failed"
        assert "stop pod" in result.detail

    def test_happy_path_persists_storage_path_and_removes_volume(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        src = tmp_path / "src"
        src.mkdir()
        target = tmp_path / "target"

        plan = sm.MigrationPlan(
            backend="podman",
            source_volume="winpodx-data",
            source_mountpoint=src,
            source_size_bytes=1024,
            target_path=target,
            target_fs="ext4",
            chattr_will_run=False,
            free_bytes_target=10 << 30,
        )

        # Patch every external touch point: stop pod, copy, generate
        # compose, podman volume rm. We don't need real shell calls.
        with (
            patch.object(sm, "_stop_pod", return_value=(True, "stopped")),
            patch.object(sm, "_rsync_copy", return_value=(True, "ok")),
            patch("winpodx.core.compose.generate_compose"),
            patch.object(sm.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(sm.subprocess, "run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = sm.execute_migration(cfg, plan, start_pod=False)

        assert result.status == "ok"
        # cfg.pod.storage_path was persisted to disk.
        from winpodx.core.config import Config

        loaded = Config.load()
        assert loaded.pod.storage_path == str(target)

    def test_failed_copy_cleans_up_target_dir(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        src = tmp_path / "src"
        src.mkdir()
        target = tmp_path / "target"

        plan = sm.MigrationPlan(
            backend="podman",
            source_volume="winpodx-data",
            source_mountpoint=src,
            source_size_bytes=1024,
            target_path=target,
            target_fs="ext4",
            chattr_will_run=False,
            free_bytes_target=10 << 30,
        )

        with (
            patch.object(sm, "_stop_pod", return_value=(True, "stopped")),
            patch.object(sm, "_rsync_copy", return_value=(False, "disk full")),
        ):
            result = sm.execute_migration(cfg, plan, start_pod=False)

        assert result.status == "failed"
        assert "disk full" in result.detail
        # target dir should be gone (or empty) after cleanup
        assert not target.exists() or not any(target.iterdir())


class TestComposeRendersBothModes:
    """End-to-end check that the compose template handles both storage modes."""

    def _cfg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.rdp.user = "User"
        cfg.rdp.password = "Test12345!"
        cfg.save()
        return cfg

    def test_named_volume_when_storage_path_empty(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        cfg.pod.storage_path = ""

        from winpodx.core.compose import _build_compose_content

        content = _build_compose_content(cfg)
        assert "volumes:\n  winpodx-data:" in content
        assert "- winpodx-data:/storage:Z" in content

    def test_bind_mount_when_storage_path_set(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        bind = tmp_path / "bind"
        cfg.pod.storage_path = str(bind)

        from winpodx.core.compose import _build_compose_content

        content = _build_compose_content(cfg)
        # Top-level named-volume declaration must be gone
        assert "  winpodx-data:" not in content
        # Bind mount line must be present
        assert f"- {bind}:/storage:Z" in content

    def test_unsafe_storage_path_falls_back_to_named_volume(self, tmp_path, monkeypatch):
        """Defence: a path with newline / quote can't be safely interpolated."""
        cfg = self._cfg(tmp_path, monkeypatch)
        cfg.pod.storage_path = "/tmp/evil\nfoo"

        from winpodx.core.compose import _build_compose_content

        content = _build_compose_content(cfg)
        # Falls back to named volume, no bind mount line
        assert "- winpodx-data:/storage:Z" in content
        assert "evil" not in content


# --- Additional hardening tests (Security review follow-up) ---


class TestComposeRejectsColonInPath:
    """Compose `_render_storage_blocks` must drop to named volume when
    `storage_path` contains `:` — otherwise `/tmp/x:/etc/shadow` would
    bind /etc/shadow as the storage target.
    """

    def _cfg(self, tmp_path, monkeypatch, storage_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.rdp.user = "User"
        cfg.rdp.password = "Test12345!"
        # Bypass __post_init__'s allowlist so we can inject a
        # malicious value that mimics a hand-edited TOML — that's the
        # exact bypass scenario this test guards against.
        cfg.pod.storage_path = storage_path
        cfg.save()
        return cfg

    def test_path_with_colon_falls_back_to_named_volume(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch, storage_path="/tmp/x:/etc/shadow")
        from winpodx.core.compose import _build_compose_content

        content = _build_compose_content(cfg)
        # Named-volume mode → top-level `winpodx-data:` declaration present
        assert "  winpodx-data:" in content
        # The malicious target must not appear anywhere
        assert "/etc/shadow" not in content


class TestExecuteMigrationDefersVolumeRm:
    """`execute_migration` must not remove the named volume until the
    pod has successfully started on the new bind mount. If pod start
    fails, the legacy volume stays in place so the user can roll back.
    """

    def _cfg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.save()
        return cfg

    def test_pod_start_failure_keeps_named_volume(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        src = tmp_path / "src"
        src.mkdir()
        target = tmp_path / "target"

        plan = sm.MigrationPlan(
            backend="podman",
            source_volume="winpodx-data",
            source_mountpoint=src,
            source_size_bytes=1024,
            target_path=target,
            target_fs="ext4",
            chattr_will_run=False,
            free_bytes_target=10 << 30,
        )

        volume_rm_called = []

        def track_run(cmd, **_kwargs):
            volume_rm_called.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch.object(sm, "_stop_pod", return_value=(True, "stopped")),
            patch.object(sm, "_rsync_copy", return_value=(True, "ok")),
            patch("winpodx.core.compose.generate_compose"),
            patch.object(sm.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(sm.subprocess, "run", side_effect=track_run),
            patch(
                "winpodx.core.provisioner.ensure_ready",
                side_effect=RuntimeError("pod start failed"),
            ),
        ):
            result = sm.execute_migration(cfg, plan, start_pod=True)

        assert result.status == "failed"
        assert "pod start failed" in result.detail
        # The retry path message should be present
        assert (
            "winpodx setup --migrate-storage" in result.detail
            or "rollback" in result.detail.lower()
            or "roll back" in result.detail.lower()
        )
        # CRITICAL: `volume rm` must NOT have been invoked while pod start failed
        assert not any("rm" in cmd for cmd in volume_rm_called), (
            f"volume rm called despite pod start failure: {volume_rm_called!r}"
        )

    def test_pod_start_success_removes_named_volume(self, tmp_path, monkeypatch):
        cfg = self._cfg(tmp_path, monkeypatch)
        src = tmp_path / "src"
        src.mkdir()
        target = tmp_path / "target"

        plan = sm.MigrationPlan(
            backend="podman",
            source_volume="winpodx-data",
            source_mountpoint=src,
            source_size_bytes=1024,
            target_path=target,
            target_fs="ext4",
            chattr_will_run=False,
            free_bytes_target=10 << 30,
        )

        volume_rm_called = []

        def track_run(cmd, **_kwargs):
            volume_rm_called.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch.object(sm, "_stop_pod", return_value=(True, "stopped")),
            patch.object(sm, "_rsync_copy", return_value=(True, "ok")),
            patch("winpodx.core.compose.generate_compose"),
            patch.object(sm.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(sm.subprocess, "run", side_effect=track_run),
            patch("winpodx.core.provisioner.ensure_ready"),
        ):
            result = sm.execute_migration(cfg, plan, start_pod=True)

        assert result.status == "ok"
        # `volume rm` must have run after successful pod start
        assert any(cmd[:3] == ["podman", "volume", "rm"] for cmd in volume_rm_called), (
            f"volume rm not called after pod start success: {volume_rm_called!r}"
        )
