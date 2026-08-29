# SPDX-License-Identifier: MIT
"""Tests for daemon module (lock files, suspend, time sync)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from winpodx.core import daemon
from winpodx.core.config import Config
from winpodx.core.daemon import (
    cleanup_lock_files,
    is_pod_paused,
    resume_pod,
    suspend_pod,
    sync_windows_time,
)


def test_rail_window_classes_none_on_nonzero_exit(monkeypatch):
    """Security/robustness: a non-zero wmctrl exit (WM transitioning) must return
    None, not an empty set -- an empty set would arm every live session for
    reaping and kill them all on two transient scan failures."""
    from winpodx.core.daemon import _rail_window_classes

    class _R:
        returncode = 1
        stdout = ""  # wmctrl printed nothing because it failed

    monkeypatch.setattr("winpodx.core.daemon.subprocess.run", lambda *a, **k: _R())
    assert _rail_window_classes("wmctrl") is None


def test_rail_window_classes_parses_on_success(monkeypatch):
    from winpodx.core.daemon import _rail_window_classes

    class _R:
        returncode = 0
        stdout = (
            "0x01 0 RAIL.winword host Doc\n"
            "0x02 0 RAIL.excel host Sheet\n"
            "0x03 0 Navigator.firefox host Web\n"
        )

    monkeypatch.setattr("winpodx.core.daemon.subprocess.run", lambda *a, **k: _R())
    assert _rail_window_classes("wmctrl") == {"winword", "excel"}


def test_cleanup_lock_files(tmp_path):
    # Lock files should be removed, normal files preserved.
    lock = tmp_path / "~$test.docx"
    lock.write_text("x")

    normal = tmp_path / "test.docx"
    normal.write_text("real content")

    removed = cleanup_lock_files([tmp_path])

    assert len(removed) == 1
    assert removed[0] == lock
    assert not lock.exists()
    assert normal.exists()


def test_cleanup_ignores_large_files(tmp_path):
    # Files matching lock pattern but >1KB should not be removed.
    lock = tmp_path / "~$big.docx"
    lock.write_text("x" * 2000)

    removed = cleanup_lock_files([tmp_path])
    assert len(removed) == 0
    assert lock.exists()


def test_cleanup_empty_dir(tmp_path):
    removed = cleanup_lock_files([tmp_path])
    assert removed == []


def test_cleanup_nonexistent_dir():
    removed = cleanup_lock_files([Path("/nonexistent/path")])
    assert removed == []


def _mock_run_ok(stdout: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def test_suspend_pod_uses_configured_container_name():
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.container_name = "alt-winpod"

    with patch("winpodx.core.daemon.subprocess.run", return_value=_mock_run_ok()) as mr:
        assert suspend_pod(cfg) is True

    cmd = mr.call_args.args[0]
    assert cmd == ["podman", "pause", "alt-winpod"]


def test_idle_action_default_pauses(monkeypatch):
    # Default idle_action="pause": freeze the pod (keep RAM), never stop it.
    cfg = Config()
    assert cfg.pod.idle_action == "pause"
    monkeypatch.setattr(daemon, "is_pod_paused", lambda _cfg: False)
    monkeypatch.setattr(daemon, "suspend_pod", lambda _cfg: True)
    monkeypatch.setattr(daemon, "cleanup_lock_files", lambda: None)
    suspended = {"n": 0}
    monkeypatch.setattr(daemon, "suspend_pod", lambda _cfg: suspended.__setitem__("n", 1))
    daemon._apply_idle_action(cfg)
    assert suspended["n"] == 1


def test_idle_action_stop_stops_running_pod(monkeypatch):
    # idle_action="stop" (#622) frees RAM by stopping a *running* pod.
    from winpodx.core.pod import PodState

    cfg = Config()
    cfg.pod.idle_action = "stop"
    cfg.pod.__post_init__()
    stopped = {"n": 0}
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: type("S", (), {"state": PodState.RUNNING})(),
    )
    monkeypatch.setattr("winpodx.core.pod.stop_pod", lambda _cfg: stopped.__setitem__("n", 1))
    monkeypatch.setattr(daemon, "cleanup_lock_files", lambda: None)
    monkeypatch.setattr(daemon, "suspend_pod", lambda _cfg: pytest.fail("must not pause"))
    daemon._apply_idle_action(cfg)
    assert stopped["n"] == 1


def test_idle_action_stop_skips_already_stopped(monkeypatch):
    # Don't re-stop a pod that isn't running (avoids churn every idle tick).
    from winpodx.core.pod import PodState

    cfg = Config()
    cfg.pod.idle_action = "stop"
    cfg.pod.__post_init__()
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status",
        lambda _cfg: type("S", (), {"state": PodState.STOPPED})(),
    )
    monkeypatch.setattr(
        "winpodx.core.pod.stop_pod", lambda _cfg: pytest.fail("must not stop a stopped pod")
    )
    monkeypatch.setattr(daemon, "suspend_pod", lambda _cfg: pytest.fail("must not pause"))
    daemon._apply_idle_action(cfg)  # no raise = pass


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


def test_sync_windows_time_uses_windows_exec_channel(monkeypatch):
    """v0.1.9.5: sync_windows_time migrated from podman exec to FreeRDP RemoteApp."""
    from winpodx.core.windows_exec import WindowsExecResult

    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.container_name = "alt-winpod"
    cfg.rdp.password = "secret"

    captured: dict[str, str] = {}

    def fake(cfg_inner, payload, *, timeout=60, description="windows-exec"):
        captured["payload"] = payload
        captured["description"] = description
        return WindowsExecResult(rc=0, stdout="time synced", stderr="")

    # Force the FreeRDP fallback path so the run_in_windows stub is reachable.
    # On a dev box where a real winpodx agent is listening on 127.0.0.1:8765,
    # transport.dispatch picks AgentTransport and bypasses run_in_windows;
    # making dispatch raise routes the call through the FreeRDP branch
    # exactly as run_via_transport's contract documents.
    def _no_agent(_cfg, **_kw):
        raise RuntimeError("agent transport disabled for tests")

    monkeypatch.setattr("winpodx.core.transport.dispatch", _no_agent)
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", fake)
    assert sync_windows_time(cfg) is True
    assert "w32tm" in captured["payload"]
    assert captured["description"] == "sync-time"


def test_cleanup_ignores_symlinks(tmp_path):
    target = tmp_path / "important.txt"
    target.write_text("important data")

    symlink = tmp_path / "~$evil.docx"
    symlink.symlink_to(target)

    removed = cleanup_lock_files([tmp_path])
    assert len(removed) == 0
    assert target.exists()
    assert symlink.is_symlink()


# -- periodic checksum-gated icon refresh ---------------------------------


def test_icon_refresh_once_skips_when_paused(monkeypatch):
    from winpodx.core import daemon

    monkeypatch.setattr(daemon, "is_pod_paused", lambda cfg: True)
    hit = {"n": 0}
    monkeypatch.setattr(
        "winpodx.core.discovery.discover_apps", lambda cfg: hit.__setitem__("n", 1) or []
    )
    daemon._icon_refresh_once(Config())
    assert hit["n"] == 0  # never discovered while paused


def test_icon_refresh_once_skips_when_not_running(monkeypatch):
    from winpodx.core import daemon
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(daemon, "is_pod_paused", lambda cfg: False)
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status", lambda cfg: PodStatus(state=PodState.STOPPED)
    )
    hit = {"n": 0}
    monkeypatch.setattr(
        "winpodx.core.discovery.discover_apps", lambda cfg: hit.__setitem__("n", 1) or []
    )
    daemon._icon_refresh_once(Config())
    assert hit["n"] == 0  # pod not running -> skip


def test_icon_refresh_once_no_sync_when_nothing_changed(monkeypatch):
    from winpodx.core import daemon
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(daemon, "is_pod_paused", lambda cfg: False)
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status", lambda cfg: PodStatus(state=PodState.RUNNING)
    )
    seq = []
    monkeypatch.setattr(
        "winpodx.core.discovery.discover_apps", lambda cfg: seq.append("disc") or ["app"]
    )
    monkeypatch.setattr(
        "winpodx.core.discovery.persist_discovered", lambda apps: seq.append("persist") or []
    )
    monkeypatch.setattr(
        "winpodx.desktop.entry.install_desktop_entry",
        lambda info: seq.append("install"),  # must NOT be called
    )
    daemon._icon_refresh_once(Config())
    assert seq == ["disc", "persist"]  # checksum gate left nothing changed -> no resync


def test_icon_refresh_once_syncs_when_changed(monkeypatch):
    from winpodx.core import daemon
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(daemon, "is_pod_paused", lambda cfg: False)
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status", lambda cfg: PodStatus(state=PodState.RUNNING)
    )
    monkeypatch.setattr("winpodx.core.discovery.discover_apps", lambda cfg: ["app"])
    monkeypatch.setattr("winpodx.core.discovery.persist_discovered", lambda apps: ["/p/app.toml"])
    monkeypatch.setattr(
        "winpodx.core.app.list_available_apps",
        lambda: [
            type("A", (), {"name": "word", "hidden": False})(),
            type("A", (), {"name": "hiddenapp", "hidden": True})(),
        ],
    )
    installed = []
    monkeypatch.setattr(
        "winpodx.desktop.entry.install_desktop_entry", lambda info: installed.append(info.name)
    )
    monkeypatch.setattr(
        "winpodx.desktop.icons.refresh_icon_cache", lambda: installed.append("cache")
    )
    daemon._icon_refresh_once(Config())
    assert installed == ["word", "cache"]  # visible app installed, hidden skipped, cache refreshed


# --- Session window-reaper (#680) --------------------------------------------


def test_rail_window_classes_parses_res_class(monkeypatch):
    out = (
        "0x01 0 RAIL.excel host Book1\n"
        "0x02 0 RAIL.winword host Doc\n"
        "0x03 0 firefox.Firefox host web\n"
    )
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda *a, **k: type("R", (), {"stdout": out, "returncode": 0})(),
    )
    assert daemon._rail_window_classes("wmctrl") == {"excel", "winword"}


def test_rail_window_classes_none_on_scan_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("wmctrl gone")

    monkeypatch.setattr(daemon.subprocess, "run", _boom)
    assert daemon._rail_window_classes("wmctrl") is None


def _reaper_env(monkeypatch, scans):
    """Wire a fast session window-reaper over a scripted sequence of window-class
    scans. Returns (stop_event, killed_list)."""
    import threading

    from winpodx.core.process import TrackedProcess

    monkeypatch.setattr(daemon, "_WINDOW_REAP_POLL_SECS", 0.001)
    monkeypatch.setattr(daemon, "_WINDOW_REAP_DEBOUNCE_SECS", 0.0)
    monkeypatch.setattr(daemon.shutil, "which", lambda _n: "/usr/bin/wmctrl")
    monkeypatch.setattr(daemon, "list_active_sessions", lambda: [TrackedProcess("excel", 111)])
    it = iter(scans)
    monkeypatch.setattr(daemon, "_rail_window_classes", lambda _w: next(it, set()))
    stop = threading.Event()
    killed: list[str] = []

    def _kill(name, expected_pid=None):
        killed.append(name)
        stop.set()  # one reap is enough for the test

    monkeypatch.setattr(daemon, "kill_session", _kill)
    return stop, killed


def test_session_window_reaper_reaps_after_window_closes(monkeypatch):
    # Window present on the first scan (arms), gone after -> reaped via kill_session.
    stop, killed = _reaper_env(monkeypatch, [{"winpodx-excel"}, set(), set()])
    daemon.run_session_window_reaper(Config(), stop)
    assert killed == ["excel"]


def test_session_window_reaper_never_reaps_unseen_window(monkeypatch):
    # A session whose RAIL window never appears must never be reaped.
    import threading

    from winpodx.core.process import TrackedProcess

    monkeypatch.setattr(daemon, "_WINDOW_REAP_POLL_SECS", 0.001)
    monkeypatch.setattr(daemon, "_WINDOW_REAP_DEBOUNCE_SECS", 0.0)
    monkeypatch.setattr(daemon.shutil, "which", lambda _n: "/usr/bin/wmctrl")
    monkeypatch.setattr(daemon, "list_active_sessions", lambda: [TrackedProcess("excel", 111)])

    stop = threading.Event()
    calls = {"n": 0}

    def _scan(_w):
        calls["n"] += 1
        if calls["n"] >= 4:
            stop.set()
        return set()  # excel window never maps

    monkeypatch.setattr(daemon, "_rail_window_classes", _scan)
    killed: list[str] = []
    monkeypatch.setattr(daemon, "kill_session", lambda name, expected_pid=None: killed.append(name))

    daemon.run_session_window_reaper(Config(), stop)
    assert killed == []


def test_session_window_reaper_maps_bare_win32_name_to_rail_class(monkeypatch):
    # Given bare Win32 and already-resolved UWP/custom identities in .cproc.
    from winpodx.core import app as app_mod
    from winpodx.core.app import AppInfo
    from winpodx.core.process import TrackedProcess

    monkeypatch.setattr(daemon, "_WINDOW_REAP_POLL_SECS", 0.001)
    monkeypatch.setattr(daemon, "_WINDOW_REAP_DEBOUNCE_SECS", 0.0)
    monkeypatch.setattr(daemon.shutil, "which", lambda _name: "/usr/bin/wmctrl")
    sessions = [
        TrackedProcess("excel", 111),
        TrackedProcess("winpodx-uwp-calc", 222),
        TrackedProcess("foo", 333),
    ]
    monkeypatch.setattr(daemon, "list_active_sessions", lambda: sessions)
    monkeypatch.setattr(
        app_mod,
        "list_available_apps",
        lambda: [
            AppInfo(
                name="custom",
                full_name="Custom",
                executable="C:\\Apps\\foo.exe",
                wm_class_hint="custom-class",
            )
        ],
    )
    scans = iter(
        [
            {"winpodx-excel", "winpodx-uwp-calc", "custom-class"},
            set(),
            set(),
        ]
    )
    monkeypatch.setattr(daemon, "_rail_window_classes", lambda _wmctrl: next(scans))
    stop = _ScriptedStopEvent(iterations=2)
    killed: list[str] = []

    def fake_kill(name, expected_pid=None):
        killed.append(name)

    monkeypatch.setattr(daemon, "kill_session", fake_kill)

    # When the long-lived reaper scans the RAIL windows.
    daemon.run_session_window_reaper(Config(), stop)

    # Then bare Win32 resolves to the namespaced RAIL class, without changing
    # identities that were already resolved for UWP or custom classes.
    assert set(killed) == {"foo", "excel", "winpodx-uwp-calc"}


def test_session_window_reaper_resets_state_for_replacement_pid(monkeypatch):
    import threading

    from winpodx.core import rdp as rdp_mod
    from winpodx.core.process import TrackedProcess

    monkeypatch.setattr(daemon, "_WINDOW_REAP_POLL_SECS", 0.001)
    monkeypatch.setattr(daemon, "_WINDOW_REAP_DEBOUNCE_SECS", 0.0)
    monkeypatch.setattr(daemon.shutil, "which", lambda _name: "/usr/bin/wmctrl")
    scan_count = 0
    stop = threading.Event()

    def sessions():
        pid = 111 if scan_count == 0 else 222
        return [TrackedProcess("excel", pid)]

    def scan(_wmctrl):
        nonlocal scan_count
        scan_count += 1
        if scan_count == 2:
            stop.set()
            return {"new-class"}
        return {"old-class"}

    resolved = iter((("old-class",), ("new-class",)))
    monkeypatch.setattr(daemon, "list_active_sessions", sessions)
    monkeypatch.setattr(daemon, "_rail_window_classes", scan)
    monkeypatch.setattr(rdp_mod, "resolve_session_wm_classes", lambda _name: next(resolved))
    killed: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        daemon,
        "kill_session",
        lambda name, expected_pid=None: killed.append((name, expected_pid)),
    )

    daemon.run_session_window_reaper(Config(), stop)

    assert killed == []


def test_session_window_reaper_noop_without_wmctrl(monkeypatch):
    import threading

    monkeypatch.setattr(daemon.shutil, "which", lambda _n: None)
    called: list = []
    monkeypatch.setattr(daemon, "list_active_sessions", lambda: called.append("x") or [])
    daemon.run_session_window_reaper(Config(), threading.Event())
    assert called == []  # returned before the loop


def test_container_commands_reject_manual_backend(monkeypatch):
    cfg = Config()
    cfg.pod.backend = "manual"
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("manual backend must not invoke a container runtime"),
    )

    assert suspend_pod(cfg) is False
    assert resume_pod(cfg) is False
    assert is_pod_paused(cfg) is False


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError(), daemon.subprocess.TimeoutExpired("podman", 1)],
)
def test_suspend_pod_degrades_when_runtime_cannot_execute(monkeypatch, error):
    cfg = Config()
    monkeypatch.setattr(daemon.subprocess, "run", MagicMock(side_effect=error))

    assert suspend_pod(cfg) is False


def test_container_commands_report_nonzero_results(monkeypatch):
    cfg = Config()
    failed = MagicMock(returncode=125, stdout="", stderr="runtime failure")
    monkeypatch.setattr(daemon.subprocess, "run", MagicMock(return_value=failed))

    assert suspend_pod(cfg) is False
    assert resume_pod(cfg) is False
    assert is_pod_paused(cfg) is False


def test_ensure_pod_awake_resumes_and_confirms_transition(monkeypatch):
    cfg = Config()
    paused_states = iter([True, False])
    resumed: list[str] = []
    monkeypatch.setattr(daemon, "is_pod_paused", lambda _cfg: next(paused_states))
    monkeypatch.setattr(
        daemon,
        "resume_pod",
        lambda inner: resumed.append(inner.pod.container_name) or True,
    )
    monkeypatch.setattr(daemon.time, "sleep", lambda _seconds: None)

    daemon.ensure_pod_awake(cfg)

    assert resumed == [cfg.pod.container_name]


def test_ensure_pod_awake_fails_when_resume_does_not_unpause(monkeypatch):
    from winpodx.core.provisioner import ProvisionError

    cfg = Config()
    monkeypatch.setattr(daemon, "is_pod_paused", lambda _cfg: True)
    monkeypatch.setattr(daemon, "resume_pod", lambda _cfg: False)
    monkeypatch.setattr(daemon.time, "sleep", lambda _seconds: None)

    with pytest.raises(ProvisionError, match=cfg.pod.container_name):
        daemon.ensure_pod_awake(cfg)


def test_sync_windows_time_reports_channel_and_command_failures(monkeypatch):
    from winpodx.core.windows_exec import WindowsExecError, WindowsExecResult

    cfg = Config()
    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_via_transport",
        MagicMock(side_effect=WindowsExecError("channel down")),
    )
    assert sync_windows_time(cfg) is False

    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_via_transport",
        MagicMock(return_value=WindowsExecResult(rc=7, stdout="", stderr="denied")),
    )
    assert sync_windows_time(cfg) is False


class _ScriptedStopEvent:
    def __init__(self, iterations: int) -> None:
        self.iterations = iterations
        self.checks = 0
        self.waits: list[float] = []

    def is_set(self) -> bool:
        self.checks += 1
        return self.checks > self.iterations

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        return False


def test_idle_monitor_suspends_after_timeout_without_waiting(monkeypatch):
    cfg = Config()
    cfg.pod.idle_timeout = 5
    cfg.pod.disk_autogrow = False
    stop = _ScriptedStopEvent(iterations=2)
    actions: list[str] = []
    monkeypatch.setattr(daemon, "list_active_sessions", lambda: [])
    monkeypatch.setattr(daemon.time, "monotonic", MagicMock(side_effect=[10.0, 16.0]))
    monkeypatch.setattr(daemon, "_apply_idle_action", lambda _cfg: actions.append("suspend"))

    daemon.run_idle_monitor(cfg, stop)

    assert actions == ["suspend"]
    assert stop.waits == [30, 30]


def test_idle_monitor_active_session_resets_idle_timer(monkeypatch):
    cfg = Config()
    cfg.pod.idle_timeout = 1
    cfg.pod.disk_autogrow = False
    stop = _ScriptedStopEvent(iterations=3)
    sessions = iter([[], [MagicMock()], []])
    actions: list[str] = []
    monkeypatch.setattr(daemon, "list_active_sessions", lambda: next(sessions))
    monkeypatch.setattr(daemon.time, "monotonic", MagicMock(side_effect=[10.0, 100.0]))
    monkeypatch.setattr(daemon, "_apply_idle_action", lambda _cfg: actions.append("suspend"))

    daemon.run_idle_monitor(cfg, stop)

    assert actions == []


def test_idle_monitor_disabled_returns_without_polling(monkeypatch):
    cfg = Config()
    cfg.pod.idle_timeout = 0
    monkeypatch.setattr(
        daemon,
        "list_active_sessions",
        lambda: pytest.fail("disabled monitor must not inspect sessions"),
    )

    daemon.run_idle_monitor(cfg)


def test_icon_refresh_monitor_runs_due_pass_without_waiting(monkeypatch):
    stop = _ScriptedStopEvent(iterations=1)
    refreshed: list[str] = []
    monkeypatch.setattr(daemon.time, "monotonic", MagicMock(side_effect=[0.0, 300.0, 300.0]))
    monkeypatch.setattr(
        daemon,
        "_icon_refresh_once",
        lambda cfg: refreshed.append(cfg.pod.container_name),
    )

    cfg = Config()
    daemon.run_icon_refresh_monitor(cfg, stop)

    assert refreshed == [cfg.pod.container_name]
    assert stop.waits == [60]
