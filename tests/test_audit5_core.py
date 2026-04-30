"""Tests added by the 5th-round core-team audit."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from winpodx.core.config import Config, PodConfig
from winpodx.core.pod import PodState, check_rdp_port, pod_status

# H3


def test_check_rdp_port_still_accepts_port_and_timeout():
    # Port 1 is privileged/reserved, so connect fails quickly.
    assert check_rdp_port("127.0.0.1", 1, timeout=0.1) is False


# H4


def test_podstate_has_paused():
    assert PodState.PAUSED.value == "paused"


def test_pod_status_reports_paused(monkeypatch):
    cfg = Config()

    fake_backend = MagicMock()
    fake_backend.is_running.return_value = True
    fake_backend.is_paused.return_value = True

    with patch("winpodx.core.pod.backend.get_backend", return_value=fake_backend):
        s = pod_status(cfg)

    assert s.state == PodState.PAUSED
    assert s.state.value == "paused"


def test_pod_status_falls_through_when_not_paused(monkeypatch):
    cfg = Config()

    fake_backend = MagicMock()
    fake_backend.is_running.return_value = True
    fake_backend.is_paused.return_value = False

    with (
        patch("winpodx.core.pod.backend.get_backend", return_value=fake_backend),
        patch("winpodx.core.pod.backend.check_rdp_port", return_value=True),
    ):
        s = pod_status(cfg)

    assert s.state == PodState.RUNNING


def test_podman_backend_is_paused_checks_state():
    from winpodx.backend.podman import PodmanBackend

    cfg = Config()
    backend = PodmanBackend(cfg)

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "paused\n"
    fake_result.stderr = ""

    with patch("winpodx.backend.podman.subprocess.run", return_value=fake_result):
        assert backend.is_paused() is True
        assert backend.is_running() is True


def test_docker_backend_is_paused_checks_state():
    from winpodx.backend.docker import DockerBackend

    cfg = Config()
    backend = DockerBackend(cfg)

    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "paused\n"
    fake_result.stderr = ""

    with patch("winpodx.backend.docker.subprocess.run", return_value=fake_result):
        assert backend.is_paused() is True
        assert backend.is_running() is True


def test_manual_backend_never_paused():
    from winpodx.backend.manual import ManualBackend

    cfg = Config()
    assert ManualBackend(cfg).is_paused() is False


# H5


def test_password_filter_clears_args():
    # record.args must be reset to () after sanitization.
    from winpodx.utils.logging import PasswordFilter

    pw_filter = PasswordFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token=%s user=%s",
        args=("sekret123", "alice"),
        exc_info=None,
    )

    pw_filter.filter(record)

    assert record.args == ()
    assert "sekret123" not in record.getMessage()
    assert "token=***" in record.getMessage()

    assert record.getMessage() == record.getMessage()


def test_password_filter_passes_through_clean_records():
    from winpodx.utils.logging import PasswordFilter

    pw_filter = PasswordFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="plain message %s",
        args=("arg",),
        exc_info=None,
    )
    assert pw_filter.filter(record) is True
    assert record.getMessage() == "plain message arg"


# H6


@pytest.mark.parametrize("backend_name", ["podman", "docker", "libvirt", "manual"])
def test_sec_tls_applied_for_all_backends(backend_name, monkeypatch):
    from winpodx.core.rdp import build_rdp_command

    monkeypatch.setattr(
        "winpodx.core.rdp.find_freerdp",
        lambda: ("/usr/bin/xfreerdp3", "xfreerdp"),
    )
    cfg = Config()
    cfg.rdp.user = "User"
    cfg.rdp.password = "p"
    cfg.pod.backend = backend_name

    cmd, _ = build_rdp_command(cfg)
    assert "/sec:tls" in cmd, f"/sec:tls missing for backend {backend_name!r}"


# H10


def test_terminate_tracked_sessions_signals_known_pids(tmp_path, monkeypatch):
    # The helper must SIGTERM only PIDs that is_freerdp_pid accepts.
    import os
    import subprocess

    from winpodx.core import process as proc_mod
    from winpodx.core import provisioner
    from winpodx.core.process import TrackedProcess

    child = subprocess.Popen(["sleep", "30"])  # noqa: S603,S607
    try:
        fake_sessions = [TrackedProcess(app_name="x", pid=child.pid)]

        call_count = {"n": 0}

        def fake_is_freerdp(pid):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return True
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False

        monkeypatch.setattr(proc_mod, "list_active_sessions", lambda: fake_sessions)
        monkeypatch.setattr(proc_mod, "is_freerdp_pid", fake_is_freerdp)

        signalled = provisioner.terminate_tracked_sessions(timeout=2.0)
        assert signalled == 1

        child.wait(timeout=5)
        assert child.returncode is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_terminate_tracked_sessions_skips_non_freerdp(tmp_path, monkeypatch):
    from winpodx.core import process as proc_mod
    from winpodx.core import provisioner
    from winpodx.core.process import TrackedProcess

    fake_sessions = [TrackedProcess(app_name="x", pid=1)]
    monkeypatch.setattr(proc_mod, "list_active_sessions", lambda: fake_sessions)
    monkeypatch.setattr(proc_mod, "is_freerdp_pid", lambda _pid: False)

    signalled = provisioner.terminate_tracked_sessions(timeout=0.1)
    assert signalled == 0


# M1


def test_list_available_apps_rejects_symlink_escape(tmp_path, monkeypatch):
    # A symlink in user_apps_dir pointing outside must be skipped.
    from winpodx.core import app as app_mod

    user_apps = tmp_path / "user_apps"
    user_apps.mkdir()

    legit = user_apps / "notepad"
    legit.mkdir()
    (legit / "app.toml").write_text('name = "notepad"\nexecutable = "notepad.exe"\n')

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.toml").write_text('name = "evil"\nexecutable = "evil.exe"\n')
    (user_apps / "evil").symlink_to(outside, target_is_directory=True)

    # v0.1.9 dropped bundled_apps_dir from list_available_apps; only
    # discovered + user dirs are sourced. Stub discovered_apps_dir to a
    # non-existent path so the symlink-escape test isolates user-dir behavior.
    empty = tmp_path / "discovered"
    empty.mkdir()
    monkeypatch.setattr(app_mod, "discovered_apps_dir", lambda: empty)
    monkeypatch.setattr(app_mod, "user_apps_dir", lambda: user_apps)

    names = [a.name for a in app_mod.list_available_apps()]
    assert "notepad" in names
    assert "evil" not in names


# M5


def test_check_freerdp_accepts_sdl(monkeypatch):
    from winpodx.utils import deps

    monkeypatch.setattr(
        "winpodx.core.rdp.find_freerdp",
        lambda: ("/usr/bin/sdl-freerdp3", "sdl"),
    )
    result = deps.check_freerdp()
    assert result.found is True
    assert result.path == "/usr/bin/sdl-freerdp3"
    assert result.name == "sdl"


def test_check_freerdp_reports_missing(monkeypatch):
    from winpodx.utils import deps

    monkeypatch.setattr("winpodx.core.rdp.find_freerdp", lambda: None)
    result = deps.check_freerdp()
    assert result.found is False
    assert "FreeRDP 3+" in result.note


# M8 / M9


def test_pod_config_image_and_disk_size_defaults():
    pod = PodConfig()
    assert pod.image == "ghcr.io/dockur/windows:latest"
    assert pod.disk_size == "64G"


def test_pod_config_image_and_disk_size_fallback_on_empty():
    pod = PodConfig(image="", disk_size="   ")
    assert pod.image == "ghcr.io/dockur/windows:latest"
    assert pod.disk_size == "64G"


def test_pod_config_image_and_disk_size_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg = Config()
    cfg.pod.image = "registry.example/win:stable"
    cfg.pod.disk_size = "128G"
    cfg.save()

    loaded = Config.load()
    assert loaded.pod.image == "registry.example/win:stable"
    assert loaded.pod.disk_size == "128G"


# L4


def test_docker_wait_for_ready_uses_short_sleep(monkeypatch):
    # wait_for_ready must poll frequently (<=1s) not every 5s.
    import winpodx.backend.docker as dm

    cfg = Config()
    backend = dm.DockerBackend(cfg)

    sleeps: list[float] = []

    clock = {"t": 0.0}

    def fake_monotonic() -> float:
        return clock["t"]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += max(seconds, 0.001)

    monkeypatch.setattr(dm.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(dm.time, "sleep", fake_sleep)
    monkeypatch.setattr(backend, "is_running", lambda: False)
    monkeypatch.setattr(backend, "is_paused", lambda: False)

    ok = backend.wait_for_ready(timeout=3)
    assert ok is False
    assert sleeps, "wait_for_ready must invoke sleep at least once"
    assert max(sleeps) <= 1.0
    assert len(sleeps) >= 2


# L5


def test_parse_scale_integer_percent():
    from winpodx.utils.compat import _parse_scale

    assert _parse_scale("140") == 140


def test_parse_scale_float_multiplier():
    from winpodx.utils.compat import _parse_scale

    assert _parse_scale("1.5") == 150


def test_parse_scale_clamps_high(caplog):
    from winpodx.utils.compat import _parse_scale

    with caplog.at_level(logging.WARNING, logger="winpodx.utils.compat"):
        assert _parse_scale("800") == 400
    assert any("clamping" in r.message for r in caplog.records)


def test_parse_scale_clamps_low(caplog):
    from winpodx.utils.compat import _parse_scale

    with caplog.at_level(logging.WARNING, logger="winpodx.utils.compat"):
        assert _parse_scale("50") == 100
    assert any("clamping" in r.message for r in caplog.records)


def test_parse_scale_non_numeric_falls_back(caplog):
    from winpodx.utils.compat import _parse_scale

    with caplog.at_level(logging.WARNING, logger="winpodx.utils.compat"):
        assert _parse_scale("abc") == 100
    assert any("not numeric" in r.message for r in caplog.records)
