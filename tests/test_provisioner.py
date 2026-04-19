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


# --- C3: password rotation rollback failure handling -----------------------


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
    # When config.save fails but Windows rollback succeeds, the returned
    # config should hold the ORIGINAL password (not the new one).
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    # Windows password change: accept both the new and rollback calls.
    monkeypatch.setattr(provisioner, "_change_windows_password", lambda cfg, pw: True)

    with patch.object(_rotation_cfg, "save", side_effect=OSError("disk full")):
        result = provisioner._auto_rotate_password(_rotation_cfg)

    # In-memory config must have been reverted — returning the new password
    # here would have the CLI connect with a password Windows rejects.
    assert result.rdp.password == "old-password"
    # No pending marker when rollback succeeded.
    assert not provisioner._rotation_marker_path().exists()


def test_rotation_rollback_failure_writes_marker(_rotation_cfg, monkeypatch):
    # Config save fails AND Windows rollback fails: must log an error
    # and write the .rotation_pending marker for follow-up.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )

    calls: list[str] = []

    def fake_change(cfg, pw):
        calls.append(pw)
        # First call = change to new password → succeeds.
        # Second call = rollback to old password → fails (container down).
        return len(calls) == 1

    monkeypatch.setattr(provisioner, "_change_windows_password", fake_change)

    with patch.object(_rotation_cfg, "save", side_effect=OSError("disk full")):
        provisioner._auto_rotate_password(_rotation_cfg)

    # Both the forward change and the rollback attempt must have run.
    assert len(calls) == 2
    # Marker must exist so next ensure_ready warns the user.
    marker = provisioner._rotation_marker_path()
    assert marker.exists()
    assert marker.stat().st_mode & 0o777 == 0o600


def test_check_rotation_pending_warns(tmp_path, monkeypatch, caplog):
    # ensure_ready should log an error when the marker exists.
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
    # A successful rotation must clear a previously-written marker so
    # the user isn't nagged forever after a manual recovery.
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    # Pre-seed the marker as if an earlier rotation half-failed.
    marker = provisioner._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    monkeypatch.setattr(
        "winpodx.core.provisioner.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    monkeypatch.setattr(provisioner, "_change_windows_password", lambda cfg, pw: True)
    # _generate_compose writes to XDG_CONFIG_HOME, which tmp_path provides.

    provisioner._auto_rotate_password(_rotation_cfg)

    assert not marker.exists()


# --- OEM version push over podman exec -------------------------------------


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
    """Point _find_share_file at in-tmp stand-ins for the shipped files.

    Returns the (install.bat, oem_updater.ps1) paths so tests can compute
    the expected content hash without duplicating the hasher.
    """
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
    digest = provisioner._oem_content_hash(fake_bat, fake_ps)
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
    expected = provisioner._oem_content_hash(fake_bat, fake_ps)

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
    # Two pushes (ps1 first, then bat), then the updater invocation.
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
    # Exercises the async wrapper end-to-end: thread runs to completion,
    # last_oem_push gets reload-merged into the on-disk config.
    from winpodx.core import provisioner
    from winpodx.core.config import Config

    fake_bat, fake_ps = _stub_shipped(
        monkeypatch, provisioner, tmp_path, b"async body\n", b"# async stub\n"
    )
    expected = provisioner._oem_content_hash(fake_bat, fake_ps)

    class _R:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(provisioner.subprocess, "run", lambda *_a, **_kw: _R())

    # Grab the thread handle so the test can deterministically wait on it.
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
    # A second async call while a push is in flight must silently skip,
    # not queue up a duplicate podman exec chain.
    from winpodx.core import provisioner

    # Hold the lock as if another push were already running.
    assert provisioner._OEM_PUSH_LOCK.acquire(blocking=False)
    try:

        def _boom(*_a, **_kw):
            raise AssertionError("second push must not spawn a thread")

        monkeypatch.setattr(provisioner.threading, "Thread", _boom)
        provisioner._push_oem_update_if_stale_async(_oem_push_cfg)
    finally:
        provisioner._OEM_PUSH_LOCK.release()
