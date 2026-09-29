# SPDX-License-Identifier: MIT
"""Tests for winpodx.core.rotation — moved from tests/test_provisioner.py
in Track A Sprint 1 Step 2."""

from __future__ import annotations

from unittest.mock import patch

import pytest


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
    from winpodx.core import rotation
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.rotation.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    monkeypatch.setattr(rotation, "_change_windows_password", lambda cfg, pw: True)
    monkeypatch.setattr(rotation, "_verify_windows_password", lambda cfg, pw: True)

    original_save = _rotation_cfg.save
    save_calls = 0

    def fail_first_save():
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise OSError("disk full")
        original_save()

    with patch.object(_rotation_cfg, "save", side_effect=fail_first_save):
        result = rotation._auto_rotate_password(_rotation_cfg)

    assert result.rdp.password == "old-password"
    assert not rotation._rotation_marker_path().exists()


def test_rotation_compose_failure_does_not_change_guest_or_config(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.rotation.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )

    changes: list[str] = []

    def fake_change(cfg, pw):
        changes.append(pw)
        return True

    monkeypatch.setattr(rotation, "_change_windows_password", fake_change)
    monkeypatch.setattr(rotation, "_verify_windows_password", lambda cfg, pw: True)
    monkeypatch.setattr(
        rotation,
        "generate_compose_to",
        lambda cfg, path: (_ for _ in ()).throw(OSError("compose full")),
    )

    with patch.object(_rotation_cfg, "save") as mock_save:
        result = rotation._auto_rotate_password(_rotation_cfg)

    assert result.rdp.password == "old-password"
    assert changes == []
    mock_save.assert_not_called()
    assert not rotation._rotation_marker_path().exists()


def test_rotation_rollback_failure_writes_marker(_rotation_cfg, monkeypatch):
    # Config save and Windows rollback both fail: must log error and write .rotation_pending marker.
    from winpodx.core import rotation
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.rotation.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )

    calls: list[str] = []

    def fake_change(cfg, pw):
        calls.append(pw)
        return len(calls) == 1

    monkeypatch.setattr(rotation, "_change_windows_password", fake_change)
    monkeypatch.setattr(rotation, "_verify_windows_password", lambda cfg, pw: True)

    with patch.object(_rotation_cfg, "save", side_effect=OSError("disk full")):
        with pytest.raises(rotation.RotationError, match="rollback is incomplete"):
            rotation._auto_rotate_password(_rotation_cfg)

    assert len(calls) == 2
    marker = rotation._rotation_marker_path()
    assert marker.exists()
    assert marker.stat().st_mode & 0o777 == 0o600


def test_check_rotation_pending_warns(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core import rotation

    marker = rotation._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    with caplog.at_level(logging.ERROR, logger="winpodx.core.rotation"):
        rotation._check_rotation_pending()

    assert any("Pending password rotation" in r.message for r in caplog.records)


def test_auto_rotation_blocks_existing_pending_marker(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.pod import PodState, PodStatus

    marker = rotation._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("pending\n")

    monkeypatch.setattr(
        "winpodx.core.rotation.pod_status",
        lambda cfg: PodStatus(state=PodState.RUNNING),
    )
    change_password = patch.object(rotation, "_change_windows_password")

    with change_password as change:
        with pytest.raises(rotation.RotationError, match="unresolved"):
            rotation._auto_rotate_password(_rotation_cfg)

    change.assert_not_called()
    assert marker.exists()


def test_auto_rotation_raises_when_pending_recovery_is_unresolved(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation

    marker = rotation._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    original = "old-password\nnew-password\n"
    marker.write_text(original, encoding="utf-8")
    monkeypatch.setattr(rotation, "_recover_pending_rotation", lambda _cfg: None)

    with pytest.raises(rotation.RotationError, match="unresolved"):
        rotation._auto_rotate_password(_rotation_cfg)

    assert marker.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("configured_password", "max_age"),
    [("old-password", 0), ("", 1), ("stale-password", 0)],
)
def test_pending_rotation_recovers_before_auto_rotation_gates(
    _rotation_cfg, monkeypatch, configured_password, max_age
):
    from winpodx.core import rotation
    from winpodx.core.config import Config

    _rotation_cfg.rdp.password = configured_password
    _rotation_cfg.rdp.password_max_age = max_age
    _rotation_cfg.save()
    marker = rotation._rotation_marker_path()
    marker.write_text("old-password\nnew-password\n", encoding="utf-8")
    monkeypatch.setattr(
        rotation, "_verify_windows_password", lambda _cfg, password: password == "new-password"
    )

    result = rotation.maybe_rotate(_rotation_cfg)

    assert result.rdp.password == "new-password"
    assert Config.load().rdp.password == "new-password"
    assert not marker.exists()


def test_pending_rotation_restores_verified_old_credential_with_disabled_auto_rotation(
    _rotation_cfg, monkeypatch
):
    from winpodx.core import rotation
    from winpodx.core.config import Config

    _rotation_cfg.rdp.password = "stale-password"
    _rotation_cfg.rdp.password_max_age = 0
    _rotation_cfg.save()
    marker = rotation._rotation_marker_path()
    marker.write_text("old-password\nnew-password\n", encoding="utf-8")
    monkeypatch.setattr(
        rotation, "_verify_windows_password", lambda _cfg, password: password == "old-password"
    )

    result = rotation.maybe_rotate(_rotation_cfg)

    assert result.rdp.password == "old-password"
    assert Config.load().rdp.password == "old-password"
    assert not marker.exists()


def test_pending_rotation_recovers_old_when_candidate_verification_raises(
    _rotation_cfg, monkeypatch
):
    from winpodx.core import rotation
    from winpodx.core.config import Config

    # Given an interrupted rotation whose saved credential is stale.
    _rotation_cfg.rdp.password = "stale-password"
    _rotation_cfg.rdp.password_max_age = 0
    _rotation_cfg.save()
    marker = rotation._rotation_marker_path()
    marker.write_text("old-password\nnew-password\n", encoding="utf-8")
    attempts: list[str] = []

    def verify(_cfg: Config, password: str) -> bool:
        attempts.append(password)
        if password == "new-password":
            raise rotation.RotationError("Password verification outcome is unknown")
        return password == "old-password"

    monkeypatch.setattr(rotation, "_verify_windows_password", verify)

    # When readiness recovers the pending rotation.
    result = rotation.maybe_rotate(_rotation_cfg)

    # Then only the verified old credential is persisted everywhere.
    assert attempts == ["new-password", "old-password"]
    assert result.rdp.password == "old-password"
    assert Config.load().rdp.password == "old-password"
    assert "old-password" in (Config.path().parent / "compose.yaml").read_text()
    assert not marker.exists()


@pytest.mark.parametrize(
    ("configured_password", "max_age"),
    [("old-password", 0), ("", 1), ("stale-password", 0)],
)
def test_pending_rotation_fails_closed_before_auto_rotation_gates(
    _rotation_cfg, monkeypatch, configured_password, max_age
):
    from winpodx.core import rotation
    from winpodx.core.config import Config

    _rotation_cfg.rdp.password = configured_password
    _rotation_cfg.rdp.password_max_age = max_age
    _rotation_cfg.save()
    marker = rotation._rotation_marker_path()
    original = "old-password\nnew-password\n"
    marker.write_text(original, encoding="utf-8")
    monkeypatch.setattr(rotation, "_verify_windows_password", lambda _cfg, _password: False)

    with pytest.raises(rotation.RotationError, match="unresolved"):
        rotation.maybe_rotate(_rotation_cfg)

    assert Config.load().rdp.password == configured_password
    assert marker.read_text(encoding="utf-8") == original


def test_rotation_transaction_marks_before_guest_change_and_commits(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.config import Config

    marker = rotation._rotation_marker_path()
    guest_calls: list[tuple[str, str]] = []

    def change_password(cfg, password):
        assert marker.exists()
        guest_calls.append((cfg.rdp.password, password))
        return True

    monkeypatch.setattr(rotation, "_change_windows_password", change_password)
    monkeypatch.setattr(rotation, "_verify_windows_password", lambda cfg, pw: True)

    assert rotation.rotate_password(_rotation_cfg, "new-password") is True
    assert guest_calls == [("old-password", "new-password")]
    assert Config.load().rdp.password == "new-password"
    assert "new-password" in (Config.path().parent / "compose.yaml").read_text()
    assert not marker.exists()


def test_rotation_interrupted_guest_change_keeps_pending_marker(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.config import Config

    def interrupt(_cfg, _password):
        raise KeyboardInterrupt

    monkeypatch.setattr(rotation, "_change_windows_password", interrupt)

    with pytest.raises(KeyboardInterrupt):
        rotation.rotate_password(_rotation_cfg, "new-password")

    assert Config.load().rdp.password == "old-password"
    assert rotation._rotation_marker_path().exists()


def test_rotation_save_failure_rolls_guest_back_with_new_credential(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.config import Config

    guest_calls: list[tuple[str, str]] = []

    def change_password(cfg, password):
        guest_calls.append((cfg.rdp.password, password))
        return True

    original_save = _rotation_cfg.save
    save_calls = 0

    def fail_first_save():
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise OSError("disk full")
        original_save()

    monkeypatch.setattr(rotation, "_change_windows_password", change_password)
    monkeypatch.setattr(rotation, "_verify_windows_password", lambda cfg, pw: True)
    monkeypatch.setattr(_rotation_cfg, "save", fail_first_save)

    assert rotation.rotate_password(_rotation_cfg, "new-password") is False
    assert guest_calls == [
        ("old-password", "new-password"),
        ("new-password", "old-password"),
    ]
    assert Config.load().rdp.password == "old-password"
    assert "old-password" in (Config.path().parent / "compose.yaml").read_text()
    assert not rotation._rotation_marker_path().exists()


def test_rotation_marker_failure_prevents_guest_change(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation

    guest_change = patch.object(rotation, "_change_windows_password")
    monkeypatch.setattr(rotation, "_mark_rotation_pending", lambda _old, _new: False)

    with guest_change as change:
        assert rotation.rotate_password(_rotation_cfg, "new-password") is False

    change.assert_not_called()


def test_failed_manual_recovery_preserves_preexisting_marker(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation

    marker = rotation._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    original = "recovery-old\nrecovery-candidate\n"
    marker.write_text(original, encoding="utf-8")
    marker.chmod(0o600)

    guest_change = patch.object(rotation, "_change_windows_password")
    prepare = patch.object(rotation, "_prepare_rotation_compose")

    with guest_change as change, prepare as compose:
        with pytest.raises(rotation.RotationError, match="pending") as excinfo:
            rotation.rotate_password(_rotation_cfg, "new-attempt")

    change.assert_not_called()
    compose.assert_not_called()
    assert marker.read_text(encoding="utf-8") == original
    assert "rotate-password" not in str(excinfo.value)
    assert "app run desktop" in str(excinfo.value)


# --- Public API smoke tests ---


def test_maybe_rotate_returns_cfg_when_no_password(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core import rotation
    from winpodx.core.config import Config

    cfg = Config()
    cfg.rdp.password = ""

    result = rotation.maybe_rotate(cfg)

    assert result is cfg


def test_check_pending_no_marker_quiet(tmp_path, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core import rotation

    with caplog.at_level(logging.ERROR, logger="winpodx.core.rotation"):
        rotation.check_pending()

    assert not any("Pending password rotation" in r.message for r in caplog.records)


def test_rotation_requires_candidate_authentication(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.config import Config

    monkeypatch.setattr(rotation, "_change_windows_password", lambda _cfg, _password: True)
    monkeypatch.setattr(
        rotation,
        "_verify_windows_password",
        lambda _cfg, password: password == "old-password",
    )

    assert rotation.rotate_password(_rotation_cfg, "new-password") is False
    assert Config.load().rdp.password == "old-password"
    assert not rotation._rotation_marker_path().exists()


def test_restart_recovers_verified_candidate_before_new_rotation(_rotation_cfg, monkeypatch):
    from winpodx.core import rotation
    from winpodx.core.config import Config
    from winpodx.core.pod import PodState, PodStatus

    _rotation_cfg.save()
    marker = rotation._rotation_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("old-password\nnew-password\n", encoding="utf-8")
    marker.chmod(0o600)

    monkeypatch.setattr(
        "winpodx.core.rotation.pod_status",
        lambda _cfg: PodStatus(state=PodState.RUNNING),
    )
    monkeypatch.setattr(
        rotation,
        "_verify_windows_password",
        lambda _cfg, password: password == "new-password",
    )
    new_rotation = patch.object(rotation, "rotate_password")

    with new_rotation as rotate:
        result = rotation.maybe_rotate(_rotation_cfg)

    rotate.assert_not_called()
    assert result.rdp.password == "new-password"
    assert Config.load().rdp.password == "new-password"
    assert not marker.exists()
