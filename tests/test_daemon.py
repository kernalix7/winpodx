"""Tests for daemon module (lock files, suspend, time sync)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from winpodx.core.config import Config
from winpodx.core.daemon import (
    cleanup_lock_files,
    is_pod_paused,
    resume_pod,
    suspend_pod,
    sync_windows_time,
)


def test_cleanup_lock_files(tmp_path):
    """Lock files should be removed, normal files preserved."""
    # Create a lock file
    lock = tmp_path / "~$test.docx"
    lock.write_text("x")

    # Create a normal file
    normal = tmp_path / "test.docx"
    normal.write_text("real content")

    removed = cleanup_lock_files([tmp_path])

    assert len(removed) == 1
    assert removed[0] == lock
    assert not lock.exists()
    assert normal.exists()


def test_cleanup_ignores_large_files(tmp_path):
    """Files matching lock pattern but >1KB should not be removed."""
    lock = tmp_path / "~$big.docx"
    lock.write_text("x" * 2000)  # > 1KB

    removed = cleanup_lock_files([tmp_path])
    assert len(removed) == 0
    assert lock.exists()


def test_cleanup_empty_dir(tmp_path):
    """Should handle empty directories gracefully."""
    removed = cleanup_lock_files([tmp_path])
    assert removed == []


def test_cleanup_nonexistent_dir():
    """Should handle nonexistent directories gracefully."""
    removed = cleanup_lock_files([Path("/nonexistent/path")])
    assert removed == []


def _mock_run_ok(stdout: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def test_suspend_pod_uses_configured_container_name():
    """suspend_pod must invoke podman/docker with cfg.pod.container_name."""
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.container_name = "alt-winpod"

    with patch("winpodx.core.daemon.subprocess.run", return_value=_mock_run_ok()) as mr:
        assert suspend_pod(cfg) is True

    cmd = mr.call_args.args[0]
    assert cmd == ["podman", "pause", "alt-winpod"]


def test_resume_pod_uses_configured_container_name():
    cfg = Config()
    cfg.pod.backend = "docker"
    cfg.pod.container_name = "alt-winpod"

    with patch("winpodx.core.daemon.subprocess.run", return_value=_mock_run_ok()) as mr:
        assert resume_pod(cfg) is True

    cmd = mr.call_args.args[0]
    assert cmd == ["docker", "unpause", "alt-winpod"]


def test_is_pod_paused_uses_configured_container_name():
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.container_name = "alt-winpod"

    with patch("winpodx.core.daemon.subprocess.run", return_value=_mock_run_ok("paused\n")) as mr:
        assert is_pod_paused(cfg) is True

    cmd = mr.call_args.args[0]
    assert "alt-winpod" in cmd
    assert "winpodx-windows" not in cmd


def test_sync_windows_time_uses_configured_container_name():
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.container_name = "alt-winpod"

    with patch("winpodx.core.daemon.subprocess.run", return_value=_mock_run_ok()) as mr:
        assert sync_windows_time(cfg) is True

    cmd = mr.call_args.args[0]
    assert cmd[0] == "podman"
    assert cmd[1] == "exec"
    assert cmd[2] == "alt-winpod"


def test_cleanup_ignores_symlinks(tmp_path):
    """Symlinks matching lock pattern should NOT be followed or deleted."""
    target = tmp_path / "important.txt"
    target.write_text("important data")

    symlink = tmp_path / "~$evil.docx"
    symlink.symlink_to(target)

    removed = cleanup_lock_files([tmp_path])
    assert len(removed) == 0
    assert target.exists()
    assert symlink.is_symlink()
