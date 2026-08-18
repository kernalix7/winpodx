# SPDX-License-Identifier: MIT
"""Tests for gui.workers — the QObject workers behind Refresh Apps and the Info page.

Signals are connected directly (same thread, no event loop), so these run without a
QApplication. Every outward call in ``workers`` is imported INSIDE the function under
test, so each patch targets the defining module.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

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
