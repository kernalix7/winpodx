# SPDX-License-Identifier: MIT
"""Tests for gui.workers — the QObject workers behind Refresh Apps and the Info page.

Signals are connected directly (same thread, no event loop), so these run without a
QApplication. Every outward call in ``workers`` is imported INSIDE the function under
test, so each patch targets the defining module.
"""

from __future__ import annotations

import os
import threading
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from winpodx.gui import workers  # noqa: E402


class _App:
    def __init__(self, slug: str, name: str = "") -> None:
        self.slug = slug
        self.name = name or slug


def _collect(sig) -> list:
    got: list = []
    sig.connect(lambda *a: got.append(a))
    return got


@pytest.fixture
def discovery_ok(monkeypatch):
    apps = [_App("word"), _App("excel")]
    monkeypatch.setattr("winpodx.core.config.Config.load", classmethod(lambda cls: object()))
    monkeypatch.setattr("winpodx.core.discovery.discover_apps", lambda cfg: apps)
    monkeypatch.setattr("winpodx.core.discovery.persist_discovered", lambda a: a)
    monkeypatch.setattr(workers, "sync_desktop_entries", lambda a: None)
    monkeypatch.setattr("winpodx.desktop.icons.refresh_icon_cache", lambda: None)
    return apps


# --- _looks_like_pod_down -------------------------------------------------


@pytest.mark.parametrize(
    "message",
    ["pod is not up", "no such CONTAINER", "connection refused", "winpodx-windows not running"],
)
def test_pod_down_tokens_are_recognised(message: str) -> None:
    assert workers._looks_like_pod_down(RuntimeError(message)) is True


def test_unrelated_error_is_not_pod_down() -> None:
    assert workers._looks_like_pod_down(ValueError("json decode failed at byte 3")) is False


# --- DiscoveryWorker ------------------------------------------------------


def test_discovery_success_emits_persisted_count(discovery_ok) -> None:
    w = workers.DiscoveryWorker()
    ok, done = _collect(w.succeeded), _collect(w.finished)

    w.run()

    assert ok == [(2,)]
    assert len(done) == 1


@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_discovery_worker_scopes_policy_on_actual_thread(
    monkeypatch, discovery_ok, protected, fails
) -> None:
    # Given a real discovery worker whose guest boundary reports policy or raises.
    from winpodx.core.transport import agent_required

    app = QApplication.instance() or QApplication([])
    observed: list[tuple[bool, int]] = []
    restored: list[bool] = []

    def discover(cfg):
        observed.append((agent_required(), threading.get_ident()))
        if fails:
            raise RuntimeError("discovery failed")
        return discovery_ok

    monkeypatch.setattr("winpodx.core.discovery.discover_apps", discover)
    worker = workers.DiscoveryWorker(require_agent=protected)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(
        lambda: restored.append(agent_required()), Qt.ConnectionType.DirectConnection
    )
    worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
    gui_thread = threading.get_ident()
    # When the worker executes independently of the GUI context.
    thread.start()
    try:
        assert thread.wait(3000)
    finally:
        thread.quit()
        thread.wait()
    # Then policy applies on the worker thread and is restored before completion.
    assert len(observed) == 1
    assert observed[0][0] is protected
    assert observed[0][1] != gui_thread
    assert restored == [False]
    assert agent_required() is False
    assert app is QApplication.instance()


def test_protected_discovery_worker_never_reaches_rdp_when_agent_is_missing(
    monkeypatch, tmp_path
) -> None:
    # Given real discovery and dispatch, with an offline agent and forbidden process boundaries.
    from winpodx.core import discovery
    from winpodx.core.config import Config
    from winpodx.core.transport import HealthStatus

    cfg = Config()
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))
    script = tmp_path / "discover.ps1"
    script.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(discovery, "_ps_script_path", lambda: script)
    monkeypatch.setattr(discovery, "shutil", SimpleNamespace(which=lambda runtime: runtime))
    monkeypatch.setattr(discovery, "_wait_for_transport_ready", Mock())
    monkeypatch.setattr(
        "winpodx.core.transport.agent.AgentTransport.health",
        lambda self: HealthStatus(available=False, detail="offline"),
    )
    rdp = Mock(side_effect=AssertionError("unsolicited RDP"))
    monkeypatch.setattr(import_module("winpodx.core.transport.dispatch"), "FreerdpTransport", rdp)
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", rdp)
    monkeypatch.setattr("winpodx.core.rdp.subprocess.Popen", rdp)
    worker = workers.DiscoveryWorker(require_agent=True)
    failed, finished = _collect(worker.failed), _collect(worker.finished)
    # When a queued automatic worker encounters the unavailable agent.
    worker.run()
    # Then it reports the retryable agent failure without constructing or launching RDP.
    assert failed[0][0] == "agent_unavailable"
    assert finished == [()]
    rdp.assert_not_called()


def test_discovery_falls_back_to_app_count_when_persisted_has_no_len(
    monkeypatch, discovery_ok
) -> None:
    monkeypatch.setattr("winpodx.core.discovery.persist_discovered", lambda a: None)
    w = workers.DiscoveryWorker()
    ok = _collect(w.succeeded)

    w.run()

    assert ok == [(2,)]


def test_discovery_error_uses_kind_attribute_when_present(monkeypatch, discovery_ok) -> None:
    exc = RuntimeError("guest said no")
    exc.kind = "agent_unreachable"
    monkeypatch.setattr(
        "winpodx.core.discovery.discover_apps", lambda cfg: (_ for _ in ()).throw(exc)
    )
    w = workers.DiscoveryWorker()
    bad, done = _collect(w.failed), _collect(w.finished)

    w.run()

    assert bad == [("agent_unreachable", "guest said no")]
    assert len(done) == 1


def test_discovery_error_without_kind_is_classified_as_pod_down(monkeypatch, discovery_ok) -> None:
    monkeypatch.setattr(
        "winpodx.core.discovery.discover_apps",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("container is not running")),
    )
    w = workers.DiscoveryWorker()
    bad = _collect(w.failed)

    w.run()

    assert bad[0][0] == "pod_not_running"


def test_discovery_error_without_kind_defaults_to_unexpected(monkeypatch, discovery_ok) -> None:
    monkeypatch.setattr(
        "winpodx.core.discovery.discover_apps",
        lambda cfg: (_ for _ in ()).throw(ValueError("bad json")),
    )
    w = workers.DiscoveryWorker()
    bad = _collect(w.failed)

    w.run()

    assert bad[0][0] == "unexpected"


def test_persist_failure_is_reported_and_stops_the_run(monkeypatch, discovery_ok) -> None:
    monkeypatch.setattr(
        "winpodx.core.discovery.persist_discovered",
        lambda a: (_ for _ in ()).throw(OSError("disk full")),
    )
    called: list = []
    monkeypatch.setattr(workers, "sync_desktop_entries", lambda a: called.append(a))
    w = workers.DiscoveryWorker()
    bad, ok = _collect(w.failed), _collect(w.succeeded)

    w.run()

    assert bad == [("unexpected", "disk full")]
    assert ok == []
    assert called == []


def test_entry_sync_failure_does_not_fail_the_refresh(monkeypatch, discovery_ok) -> None:
    monkeypatch.setattr(
        workers, "sync_desktop_entries", lambda a: (_ for _ in ()).throw(OSError("no perms"))
    )
    w = workers.DiscoveryWorker()
    ok, bad = _collect(w.succeeded), _collect(w.failed)

    w.run()

    assert ok == [(2,)]
    assert bad == []


def test_icon_cache_failure_does_not_fail_the_refresh(monkeypatch, discovery_ok) -> None:
    monkeypatch.setattr(
        "winpodx.desktop.icons.refresh_icon_cache", lambda: (_ for _ in ()).throw(OSError("no gtk"))
    )
    w = workers.DiscoveryWorker()
    ok, bad = _collect(w.succeeded), _collect(w.failed)

    w.run()

    assert ok == [(2,)]
    assert bad == []


# --- InfoWorker -----------------------------------------------------------


def test_info_worker_attaches_health_probes(monkeypatch) -> None:
    probe = SimpleNamespace(name="rdp", status="ok", detail="reachable", duration_ms=12)
    monkeypatch.setattr("winpodx.core.info.gather_info", lambda cfg: {"version": "0.10.4"})
    monkeypatch.setattr("winpodx.core.checks.run_all", lambda cfg: [probe])
    monkeypatch.setattr("winpodx.core.checks.overall", lambda probes: "ok")
    w = workers.InfoWorker(cfg=object())
    got = _collect(w.done)

    w.run()

    snapshot = got[0][0]
    assert snapshot["version"] == "0.10.4"
    assert snapshot["health"] == [
        {"name": "rdp", "status": "ok", "detail": "reachable", "duration_ms": 12}
    ]
    assert snapshot["health_overall"] == "ok"


def test_health_probe_failure_degrades_instead_of_blocking_info(monkeypatch) -> None:
    monkeypatch.setattr("winpodx.core.info.gather_info", lambda cfg: {"version": "0.10.4"})
    monkeypatch.setattr(
        "winpodx.core.checks.run_all", lambda cfg: (_ for _ in ()).throw(RuntimeError("probe boom"))
    )
    w = workers.InfoWorker(cfg=object())
    got, bad = _collect(w.done), _collect(w.failed)

    w.run()

    assert got[0][0]["health"] == []
    assert got[0][0]["health_overall"] == "fail"
    assert bad == []


def test_info_worker_reports_gather_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "winpodx.core.info.gather_info", lambda cfg: (_ for _ in ()).throw(OSError("no config"))
    )
    w = workers.InfoWorker(cfg=object())
    got, bad = _collect(w.done), _collect(w.failed)

    w.run()

    assert bad == [("no config",)]
    assert got == []


# --- sync_desktop_entries -------------------------------------------------


@pytest.fixture
def entry_sync(monkeypatch, tmp_path):
    installed: list = []
    removed: list = []
    monkeypatch.setattr("winpodx.utils.paths.applications_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "winpodx.desktop.entry.install_desktop_entry", lambda i: installed.append(i)
    )
    monkeypatch.setattr("winpodx.desktop.entry.install_desktop_shortcut", lambda: None)
    monkeypatch.setattr("winpodx.desktop.entry.remove_desktop_entry", lambda s: removed.append(s))
    return SimpleNamespace(dir=tmp_path, installed=installed, removed=removed)


def test_sync_installs_entries_for_known_discovered_apps(monkeypatch, entry_sync) -> None:
    known = _App("word")
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [known])

    workers.sync_desktop_entries([_App("word"), _App("ghost")])

    assert entry_sync.installed == [known]


def test_sync_removes_stale_entries_not_in_available(monkeypatch, entry_sync) -> None:
    (entry_sync.dir / "winpodx-oldapp.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [])

    workers.sync_desktop_entries([])

    assert entry_sync.removed == ["oldapp"]


def test_sync_never_removes_the_gui_launcher_or_shortcut_entries(monkeypatch, entry_sync) -> None:
    from winpodx.desktop.entry import DESKTOP_SHORTCUT_STEM

    shortcut_slug = DESKTOP_SHORTCUT_STEM[len("winpodx-") :]
    for stem in ("winpodx-gui", "winpodx-launcher", DESKTOP_SHORTCUT_STEM):
        (entry_sync.dir / f"{stem}.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [])

    workers.sync_desktop_entries([])

    assert entry_sync.removed == []
    assert shortcut_slug not in entry_sync.removed


def test_sync_keeps_entries_that_are_still_available(monkeypatch, entry_sync) -> None:
    (entry_sync.dir / "winpodx-word.desktop").write_text("[Desktop Entry]\n", encoding="utf-8")
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [_App("word")])

    workers.sync_desktop_entries([])

    assert entry_sync.removed == []


def test_sync_survives_a_failing_install(monkeypatch, entry_sync) -> None:
    monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [_App("word")])
    monkeypatch.setattr(
        "winpodx.desktop.entry.install_desktop_entry",
        lambda i: (_ for _ in ()).throw(OSError("read-only fs")),
    )

    workers.sync_desktop_entries([_App("word")])

    assert entry_sync.removed == []
