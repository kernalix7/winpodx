"""Tests for auto-provisioning engine."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from winpodx.core.provisioner import ProvisionError


def test_provision_error():
    err = ProvisionError("test error")
    assert str(err) == "test error"
    assert isinstance(err, Exception)


def test_ensure_config_creates_default(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from winpodx.core.provisioner import _ensure_config

    cfg = _ensure_config()

    assert cfg.rdp.user == "User"
    assert cfg.rdp.ip == "127.0.0.1"
    assert (tmp_path / "winpodx" / "winpodx.toml").exists()


# C3: password rotation rollback failure handling


@pytest.fixture()
def _rotation_cfg(tmp_path, monkeypatch):
    """Config set up to trigger _auto_rotate_password work."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from winpodx.core.config import Config

    cfg = Config()
    cfg.rdp.user = "User"
    cfg.rdp.password = "old-password"
    cfg.rdp.password_max_age = 1  # day
    cfg.rdp.password_updated = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    cfg.pod.backend = "podman"
    cfg.save()
    return cfg


def test_rotation_rollback_success_reverts_password(_rotation_cfg, monkeypatch):
    # When config.save fails but Windows rollback succeeds, config keeps the old password.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    monkeypatch.setattr(provisioner, "_change_windows_password", lambda cfg, pw: True)

    with patch.object(_rotation_cfg, "save", side_effect=OSError("disk full")):
        result = provisioner._auto_rotate_password(_rotation_cfg)

    assert result.rdp.password == "old-password"
    assert not provisioner._rotation_marker_path().exists()


def test_rotation_rollback_failure_writes_marker(_rotation_cfg, monkeypatch):
    # Config save and Windows rollback both fail: must log error and write .rotation_pending marker.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )

    calls: list[str] = []

    def fake_change(cfg, pw):
        calls.append(pw)
        return len(calls) == 1

    monkeypatch.setattr(provisioner, "_change_windows_password", fake_change)

    with patch.object(_rotation_cfg, "save", side_effect=OSError("disk full")):
        provisioner._auto_rotate_password(_rotation_cfg)

    assert len(calls) == 2
    marker = provisioner._rotation_marker_path()
    assert marker.exists()
    assert marker.stat().st_mode & 0o777 == 0o600


def test_check_rotation_pending_warns(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core import provisioner

    marker = provisioner._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    with caplog.at_level(logging.ERROR, logger="winpodx.core.provisioner"):
        provisioner._check_rotation_pending()

    assert any("Pending password rotation" in r.message for r in caplog.records)


def test_rotation_marker_cleared_on_success(_rotation_cfg, monkeypatch):
    # A successful rotation must clear any previously-written marker.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    marker = provisioner._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    monkeypatch.setattr(provisioner, "_change_windows_password", lambda cfg, pw: True)

    provisioner._auto_rotate_password(_rotation_cfg)

    assert not marker.exists()


# OEM push over podman exec


@pytest.fixture()
def _oem_push_cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.container_name = "winpodx-test"
    cfg.pod.last_oem_push = ""
    cfg.save()
    return cfg


def _stub_shipped(monkeypatch, provisioner, tmp_path, bat_body: bytes, ps_body: bytes):
    fake_bat = tmp_path / "install.bat"
    fake_bat.write_bytes(bat_body)
    fake_ps = tmp_path / "oem_updater.ps1"
    fake_ps.write_bytes(ps_body)
    monkeypatch.setattr(
        provisioner,
        "_find_share_file",
        lambda relpath: {
            "config/oem/install.bat": fake_bat,
            "scripts/windows/oem_updater.ps1": fake_ps,
        }.get(relpath),
    )
    return fake_bat, fake_ps


def test_push_oem_skips_when_hash_matches(_oem_push_cfg, monkeypatch, tmp_path):
    from winpodx.core import provisioner

    fake_bat, fake_ps = _stub_shipped(
        monkeypatch, provisioner, tmp_path, b"set WINPODX_OEM_VERSION=7\n", b"# stub\n"
    )
    digest = provisioner._oem_content_hash(fake_bat, fake_ps, None)
    _oem_push_cfg.pod.last_oem_push = digest

    def _boom(*_a, **_kw):
        raise AssertionError("podman exec must not run when hashes match")

    monkeypatch.setattr(provisioner.subprocess, "run", _boom)

    result = provisioner._push_oem_update_if_stale(_oem_push_cfg)
    assert result.pod.last_oem_push == digest


def test_push_oem_bumps_last_push_on_success(_oem_push_cfg, monkeypatch, tmp_path):
    from winpodx.core import provisioner

    fake_bat, fake_ps = _stub_shipped(
        monkeypatch, provisioner, tmp_path, b"set WINPODX_OEM_VERSION=7\n", b"# stub\n"
    )
    expected = provisioner._oem_content_hash(fake_bat, fake_ps, None)

    calls: list[list[str]] = []

    class _R:
        returncode = 0
        stderr = ""
        stdout = ""

    def _fake_run(cmd, **_kw):
        calls.append(cmd)
        return _R()

    monkeypatch.setattr(provisioner.subprocess, "run", _fake_run)

    result = provisioner._push_oem_update_if_stale(_oem_push_cfg)

    assert result.pod.last_oem_push == expected
    assert len(calls) == 3
    assert any("oem_updater.ps1" in arg for arg in calls[0])
    assert any("install_shipped.bat" in arg for arg in calls[1])
    assert calls[-1][-1].endswith("oem_updater.ps1")
    assert calls[-1][-2] == "-File"


def test_push_oem_keeps_hash_unchanged_on_failure(_oem_push_cfg, monkeypatch, tmp_path):
    from winpodx.core import provisioner

    _stub_shipped(monkeypatch, provisioner, tmp_path, b"set WINPODX_OEM_VERSION=7\n", b"# stub\n")

    class _R:
        returncode = 1
        stderr = "access denied"
        stdout = ""

    monkeypatch.setattr(provisioner.subprocess, "run", lambda *_a, **_kw: _R())

    result = provisioner._push_oem_update_if_stale(_oem_push_cfg)
    assert result.pod.last_oem_push == ""


def test_push_oem_noop_for_unsupported_backend(_oem_push_cfg, monkeypatch):
    from winpodx.core import provisioner

    _oem_push_cfg.pod.backend = "libvirt"

    def _boom(*_a, **_kw):
        raise AssertionError("podman exec must not run for libvirt backend")

    monkeypatch.setattr(provisioner.subprocess, "run", _boom)

    result = provisioner._push_oem_update_if_stale(_oem_push_cfg)
    assert result.pod.last_oem_push == ""


def test_push_oem_skips_when_shipped_files_missing(_oem_push_cfg, monkeypatch):
    from winpodx.core import provisioner

    monkeypatch.setattr(provisioner, "_find_share_file", lambda _relpath: None)

    def _boom(*_a, **_kw):
        raise AssertionError("subprocess.run must not fire when files aren't found")

    monkeypatch.setattr(provisioner.subprocess, "run", _boom)

    result = provisioner._push_oem_update_if_stale(_oem_push_cfg)
    assert result.pod.last_oem_push == ""


def test_push_oem_async_spawns_thread_and_persists(_oem_push_cfg, monkeypatch, tmp_path):
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    fake_bat, fake_ps = _stub_shipped(
        monkeypatch, provisioner, tmp_path, b"async body\n", b"# async stub\n"
    )
    expected = provisioner._oem_content_hash(fake_bat, fake_ps, None)

    class _R:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(provisioner.subprocess, "run", lambda *_a, **_kw: _R())

    spawned: list = []
    real_thread = provisioner.threading.Thread

    def _capture(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        spawned.append(t)
        return t

    monkeypatch.setattr(provisioner.threading, "Thread", _capture)

    provisioner._push_oem_update_if_stale_async(_oem_push_cfg)
    assert len(spawned) == 1
    spawned[0].join(timeout=5)
    assert not spawned[0].is_alive()

    reloaded = Config.load()
    assert reloaded.pod.last_oem_push == expected


def test_push_oem_async_single_flight(_oem_push_cfg, monkeypatch):
    # A second async call while a push is in flight must silently skip.
    from winpodx.core import provisioner

    assert provisioner._OEM_PUSH_LOCK.acquire(blocking=False)
    try:

        def _boom(*_a, **_kw):
            raise AssertionError("second push must not spawn a thread")

        monkeypatch.setattr(provisioner.threading, "Thread", _boom)
        provisioner._push_oem_update_if_stale_async(_oem_push_cfg)
    finally:
        provisioner._OEM_PUSH_LOCK.release()


def test_push_oem_async_flock_blocks_second_process(_oem_push_cfg, monkeypatch):
    # Another winpodx process holding the flock: async must release its in-process lock and skip.
    import fcntl

    from winpodx.core import provisioner

    lock_path = provisioner._oem_push_flock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    other_fd = provisioner.os.open(
        str(lock_path), provisioner.os.O_CREAT | provisioner.os.O_RDWR, 0o600
    )
    fcntl.flock(other_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:

        def _boom(*_a, **_kw):
            raise AssertionError("flocked push must not spawn a thread")

        monkeypatch.setattr(provisioner.threading, "Thread", _boom)
        provisioner._push_oem_update_if_stale_async(_oem_push_cfg)

        assert provisioner._OEM_PUSH_LOCK.acquire(blocking=False)
        provisioner._OEM_PUSH_LOCK.release()
    finally:
        fcntl.flock(other_fd, fcntl.LOCK_UN)
        provisioner.os.close(other_fd)
