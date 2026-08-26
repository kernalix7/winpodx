# SPDX-License-Identifier: MIT
"""Tests for the GUI Dashboard home, the pod-status polling mixin, and the
``main_window`` shell.

These three modules are plain mixins (``DashboardMixin`` / ``PodStatusMixin``)
plus a thin ``QMainWindow`` shell, so every test here mixes ONE class into a
bare host object and feeds it only the attributes the mixin actually reads.
Nothing constructs a real ``WinpodxWindow`` -- that pulls in 13 mixins and a
live pod probe.

Everything that would touch the host is stubbed: ``pod_resource_snapshot``,
``pod_status``, ``Config.load``, ``ensure_ready``, ``launch_app``, the guest
agent, and the tray spawn. Worker threads are replaced by an inline-running
``_SyncThread`` so the assertions are deterministic and the file stays well
under a second.

Covers:
  - Dashboard: page scaffolding, gauge/bar value formatting, the "n/a"
    fallbacks and the last-known-value caching for RAM + disk, the pod-state
    to gauge/recovery-line mapping, workspace tile ordering + wrapping, and
    the responsive reflow.
  - PodStatusMixin: the launch path (debounce, lock, FreeRDP exit codes), pod
    start/stop, the 15 s polling timer, the transport dots, and every
    pod-state transition the chip renders.
  - main_window: signal wiring, log-bar ticker, worker-thread join, screen
    fitting, scroll-area minimums, and the ``run_gui()`` entry point.
"""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QBoxLayout,
    QWidget,
)

import winpodx.gui._main_window_dashboard as dash_mod  # noqa: E402
from winpodx.core.app import AppInfo  # noqa: E402
from winpodx.core.config import Config  # noqa: E402
from winpodx.core.i18n import tr  # noqa: E402
from winpodx.core.stats import ResourceSnapshot  # noqa: E402
from winpodx.gui import launcher_state  # noqa: E402
from winpodx.gui._main_window_dashboard import DashboardMixin  # noqa: E402
from winpodx.gui.theme import C  # noqa: E402

# ----- shared helpers ----------------------------------------------------


def _ensure_qapp() -> QApplication:
    """Return a QApplication, creating one if needed."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeSignal:
    """Minimal stand-in for a Qt Signal that records emits + connects."""

    def __init__(self) -> None:
        self.emissions: list[tuple] = []
        self.connections: list[Any] = []

    def emit(self, *args: Any) -> None:
        self.emissions.append(args)

    def connect(self, slot: Any) -> None:
        self.connections.append(slot)


class _SyncThread:
    """``threading.Thread`` stand-in that runs the target inline on start()."""

    def __init__(self, target=None, daemon: bool = False, args=(), kwargs=None) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


def _sync_threads(monkeypatch: pytest.MonkeyPatch, module) -> list[_SyncThread]:
    """Swap the module's ``threading`` reference for an inline-running fake."""
    created: list[_SyncThread] = []

    def _factory(*args, **kwargs) -> _SyncThread:
        t = _SyncThread(*args, **kwargs)
        created.append(t)
        return t

    monkeypatch.setattr(module, "threading", SimpleNamespace(Thread=_factory, Lock=threading.Lock))
    return created


def _make_cfg() -> Config:
    return Config()


def _snapshot(**overrides: Any) -> ResourceSnapshot:
    base: dict[str, Any] = {
        "pod_state": "running",
        "cpu_cores": 4,
        "cpu_pct": None,
        "ram_gb": 8,
        "ram_used_gb": None,
        "ram_pct": None,
        "disk_total_gb": None,
        "disk_used_gb": None,
        "disk_pct": None,
    }
    base.update(overrides)
    return ResourceSnapshot(**base)


def _app(name: str) -> AppInfo:
    return AppInfo(name=name, full_name=name.title(), executable=f"C:\\{name}.exe")


# ----- Dashboard harness -------------------------------------------------


class DashHarness(DashboardMixin, QWidget):
    """Bare host exposing only what DashboardMixin reads."""

    def __init__(self, cfg: Config, apps: list[AppInfo] | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.apps = list(apps or [])
        self.dashboard_updated = FakeSignal()
        self.launched: list[AppInfo] = []
        self.menued: list[AppInfo] = []
        # Only ``.width()`` is read off ``pages``.
        self.pages = QWidget(self)
        self.pages.resize(1100, 720)

    def _launch_app(self, app: AppInfo) -> None:
        self.launched.append(app)

    def _show_app_menu(self, app: AppInfo, pos: Any) -> None:
        self.menued.append(app)


def _build_dash(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cfg: Config | None = None,
    apps: list[AppInfo] | None = None,
    pinned: tuple[str, ...] = (),
    recent: tuple[str, ...] = (),
    snapshot: ResourceSnapshot | None = None,
) -> DashHarness:
    """Build the whole Dashboard page against a bare harness, hermetically."""
    _ensure_qapp()
    _sync_threads(monkeypatch, dash_mod)
    monkeypatch.setattr(launcher_state, "get_pinned", lambda: list(pinned))
    monkeypatch.setattr(launcher_state, "get_recent", lambda: list(recent))
    monkeypatch.setattr(
        dash_mod,
        "pod_resource_snapshot",
        lambda _cfg, pod_state=None, with_disk=True: snapshot or _snapshot(),
    )
    host = DashHarness(cfg or _make_cfg(), apps)
    page = host._build_dashboard_page()
    page.setParent(host)  # tie lifetimes; never a stray top-level window
    host._dashboard_timer.stop()
    return host


# ----- Dashboard: page scaffolding ---------------------------------------


def test_dashboard_page_builds_every_card(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)

    assert host._gauge_pod is not None
    assert host._gauge_ram is not None
    assert host._gauge_cpu is not None
    assert host._bar_disk is not None
    assert host._recovery_label.text()
    assert host._reverse_open_check is not None
    assert host._workspace_holder.count() == 1  # the empty-state panel


def test_dashboard_timer_is_armed_at_the_live_refresh_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    _sync_threads(monkeypatch, dash_mod)
    monkeypatch.setattr(launcher_state, "get_pinned", list)
    monkeypatch.setattr(launcher_state, "get_recent", list)
    monkeypatch.setattr(
        dash_mod, "pod_resource_snapshot", lambda _cfg, pod_state=None, with_disk=True: _snapshot()
    )
    host = DashHarness(_make_cfg())
    page = host._build_dashboard_page()
    page.setParent(host)

    assert host._dashboard_timer.isActive()
    assert host._dashboard_timer.interval() == dash_mod._REFRESH_MS
    host._dashboard_timer.stop()


def test_reverse_open_checkbox_mirrors_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.reverse_open.enabled = True
    _sync_threads(monkeypatch, dash_mod)
    monkeypatch.setattr(launcher_state, "get_pinned", list)
    monkeypatch.setattr(launcher_state, "get_recent", list)
    host = DashHarness(cfg)

    card = host._build_reverse_open_card()
    card.setParent(host)

    assert host._reverse_open_check.isChecked() is True


def test_reverse_open_checkbox_defaults_off_without_the_config_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    _sync_threads(monkeypatch, dash_mod)
    host = DashHarness(_make_cfg())
    # A config tree without the reverse_open block must not crash the card.
    host.cfg = SimpleNamespace()

    card = host._build_reverse_open_card()
    card.setParent(host)

    assert host._reverse_open_check.isChecked() is False


# ----- Dashboard: live refresh -------------------------------------------


def test_refresh_dashboard_passes_the_known_pod_state_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)
    calls: list[tuple] = []
    monkeypatch.setattr(
        dash_mod,
        "pod_resource_snapshot",
        lambda cfg, pod_state=None, with_disk=True: (
            calls.append((pod_state, with_disk)) or _snapshot()
        ),
    )
    host._pod_state = "paused"
    host._dashboard_tick = 0
    host._dashboard_refreshing = False

    host._refresh_dashboard()

    assert calls == [("paused", True)]


def test_refresh_dashboard_probes_disk_on_every_other_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)
    seen: list[bool] = []
    monkeypatch.setattr(
        dash_mod,
        "pod_resource_snapshot",
        lambda cfg, pod_state=None, with_disk=True: seen.append(with_disk) or _snapshot(),
    )
    host._dashboard_tick = 0
    for _ in range(4):
        host._dashboard_refreshing = False
        host._refresh_dashboard()

    assert seen == [True, False, True, False]


def test_refresh_dashboard_emits_the_snapshot_on_the_host_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)
    snap = _snapshot(cpu_pct=12.0)
    monkeypatch.setattr(
        dash_mod, "pod_resource_snapshot", lambda cfg, pod_state=None, with_disk=True: snap
    )
    host.dashboard_updated.emissions.clear()
    host._dashboard_refreshing = False

    host._refresh_dashboard()

    assert host.dashboard_updated.emissions == [(snap,)]


def test_refresh_dashboard_skips_a_stacked_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(
        dash_mod,
        "pod_resource_snapshot",
        lambda cfg, pod_state=None, with_disk=True: calls.append(1) or _snapshot(),
    )
    host._dashboard_refreshing = True

    host._refresh_dashboard()

    assert calls == []


def test_refresh_dashboard_swallows_a_failed_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)

    def _boom(_cfg, pod_state=None, with_disk=True):
        raise RuntimeError("podman is down")

    monkeypatch.setattr(dash_mod, "pod_resource_snapshot", _boom)
    host.dashboard_updated.emissions.clear()
    host._dashboard_refreshing = False

    host._refresh_dashboard()

    assert host.dashboard_updated.emissions == []
    # The guard must be released or the dashboard would never refresh again.
    assert host._dashboard_refreshing is False


# ----- Dashboard: snapshot painting --------------------------------------


def test_apply_snapshot_formats_cpu_ram_and_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(
        _snapshot(
            cpu_pct=37.4,
            ram_pct=61.8,
            disk_pct=45.0,
            disk_used_gb=28.6,
            disk_total_gb=64.0,
        )
    )

    assert host._gauge_cpu._center_text == "37%"
    assert host._gauge_ram._center_text == "62%"
    assert host._bar_disk._detail == "29 / 64 GB"
    assert host._bar_disk._pct == pytest.approx(45.0)


def test_disk_bar_accessibility_uses_the_configured_autogrow_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _make_cfg()
    cfg.pod.disk_autogrow_threshold_pct = 85
    host = _build_dash(monkeypatch, cfg=cfg)

    host._apply_snapshot(_snapshot(disk_pct=84.0, disk_used_gb=54.0, disk_total_gb=64.0))
    assert host._bar_disk.accessibleName() == tr("Disk C:")
    assert "54 / 64 GB" in host._bar_disk.accessibleDescription()
    assert tr("WARNING") not in host._bar_disk.accessibleDescription()

    host._apply_snapshot(_snapshot(disk_pct=85.0, disk_used_gb=54.4, disk_total_gb=64.0))
    assert "54 / 64 GB" in host._bar_disk.accessibleDescription()
    assert tr("WARNING") in host._bar_disk.accessibleDescription()


def test_apply_snapshot_falls_back_to_na_with_no_reading_yet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot())

    assert host._gauge_cpu._center_text == tr("n/a")
    assert host._gauge_cpu._pct is None
    assert host._gauge_ram._center_text == tr("n/a")
    assert host._bar_disk._detail == tr("n/a")
    assert host._bar_disk._pct is None


def test_apply_snapshot_keeps_the_last_ram_between_slow_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(ram_pct=71.0))
    host._apply_snapshot(_snapshot(ram_pct=None))

    assert host._gauge_ram._center_text == "71%"
    assert host._gauge_ram._pct == pytest.approx(71.0)


def test_apply_snapshot_keeps_the_last_disk_between_slow_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(disk_pct=80.0, disk_used_gb=51.0, disk_total_gb=64.0))
    host._apply_snapshot(_snapshot(disk_pct=None, disk_total_gb=None))

    assert host._bar_disk._detail == "51 / 64 GB"
    assert host._bar_disk._pct == pytest.approx(80.0)


@pytest.mark.parametrize(
    ("state", "pct", "text_key"),
    [
        ("running", 100.0, "Active"),
        ("checking", 60.0, "Checking"),
        ("paused", 50.0, "Paused"),
        ("stopped", 0.0, "Off"),
        ("unknown", 0.0, "Unknown"),
        ("bogus-state", 0.0, "Unknown"),
    ],
)
def test_apply_snapshot_maps_pod_state_to_the_pod_gauge(
    monkeypatch: pytest.MonkeyPatch, state: str, pct: float, text_key: str
) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(pod_state=state))

    assert host._gauge_pod._pct == pytest.approx(pct)
    assert host._gauge_pod._center_text == tr(text_key)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("running", "Protected — monitoring active"),
        ("checking", "Checking pod health…"),
        ("paused", "Pod is paused"),
        ("stopped", "Pod is stopped"),
        ("unknown", "Status unknown"),
        ("bogus-state", "Status unknown"),
    ],
)
def test_apply_snapshot_writes_the_matching_recovery_line(
    monkeypatch: pytest.MonkeyPatch, state: str, expected: str
) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(pod_state=state))

    assert host._recovery_label.text() == tr(expected)


def test_recovery_line_is_tinted_by_the_state_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(pod_state="running"))
    running_style = host._recovery_label.styleSheet()
    host._apply_snapshot(_snapshot(pod_state="stopped"))
    stopped_style = host._recovery_label.styleSheet()

    assert C.GREEN in running_style
    assert C.OVERLAY1 in stopped_style


def test_apply_snapshot_is_a_noop_before_the_gauges_exist() -> None:
    _ensure_qapp()
    host = DashHarness(_make_cfg())

    # Must not raise: the snapshot signal can land before the page is built.
    host._apply_snapshot(_snapshot(cpu_pct=50.0))

    assert getattr(host, "_gauge_pod", None) is None


# ----- Dashboard: workspace ----------------------------------------------


def test_workspace_apps_orders_pinned_before_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = [_app("word"), _app("excel"), _app("notepad")]
    host = _build_dash(monkeypatch, apps=apps, pinned=("excel",), recent=("notepad", "word"))

    assert [a.name for a in host._workspace_apps()] == ["excel", "notepad", "word"]


def test_workspace_apps_dedupes_a_pinned_app_that_is_also_recent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = [_app("word"), _app("excel")]
    host = _build_dash(monkeypatch, apps=apps, pinned=("word",), recent=("word", "excel"))

    assert [a.name for a in host._workspace_apps()] == ["word", "excel"]


def test_workspace_apps_ignores_names_with_no_installed_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch, apps=[_app("word")], pinned=("ghost", "word"))

    assert [a.name for a in host._workspace_apps()] == ["word"]


def test_workspace_apps_caps_the_tile_row_at_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = [_app(f"app{i}") for i in range(12)]
    host = _build_dash(monkeypatch, apps=apps, recent=tuple(a.name for a in apps))

    assert len(host._workspace_apps()) == 8


def test_populate_workspace_shows_the_empty_panel_with_no_apps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    assert host._workspace_holder.count() == 1
    panel = host._workspace_holder.itemAt(0).widget()
    assert panel.objectName() == "emptyState"


def test_populate_workspace_lays_the_tiles_out_in_a_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = [_app("word"), _app("excel"), _app("notepad")]
    host = _build_dash(monkeypatch, apps=apps, pinned=tuple(a.name for a in apps))

    grid_widget = host._workspace_holder.itemAt(0).widget()
    grid = grid_widget.layout()
    tiles = [
        grid.itemAt(i).widget()
        for i in range(grid.count())
        if isinstance(grid.itemAt(i).widget(), dash_mod._AppTile)
    ]
    assert [t._app.name for t in tiles] == ["word", "excel", "notepad"]


def test_populate_workspace_pads_an_underfull_last_row(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = [_app("word"), _app("excel"), _app("notepad")]
    host = _build_dash(monkeypatch, apps=apps, pinned=tuple(a.name for a in apps))

    grid = host._workspace_holder.itemAt(0).widget().layout()
    cols = host._workspace_cols()
    # 3 tiles + (cols - 3) transparent spacers so the row keeps left alignment.
    assert grid.count() == cols


def test_workspace_tile_left_click_launches_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QMouseEvent

    apps = [_app("word")]
    host = _build_dash(monkeypatch, apps=apps, pinned=("word",))
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()

    tile.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPoint(4, 4),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )

    assert [a.name for a in host.launched] == ["word"]


def test_populate_workspace_is_a_noop_before_the_card_exists() -> None:
    _ensure_qapp()
    host = DashHarness(_make_cfg())

    host._populate_workspace()

    assert getattr(host, "_workspace_cols_cur", None) is None


@pytest.mark.parametrize(
    ("width", "expected"),
    [(300, 3), (700, 4), (1100, 6), (2400, 8), (4000, 8)],
)
def test_workspace_columns_track_the_page_width(
    monkeypatch: pytest.MonkeyPatch, width: int, expected: int
) -> None:
    host = _build_dash(monkeypatch)
    host.pages.resize(width, 720)

    assert host._workspace_cols() == expected


def test_workspace_columns_fall_back_when_the_page_is_missing() -> None:
    _ensure_qapp()
    host = DashHarness(_make_cfg())
    host.pages = None

    assert host._workspace_cols() == 6  # the 1100px default


# ----- Dashboard: responsive reflow --------------------------------------


def test_reflow_stacks_the_top_row_on_a_narrow_window(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)
    host.pages.resize(200, 720)

    host._reflow_dashboard()

    assert host._dashboard_row1.direction() == QBoxLayout.Direction.TopToBottom


def test_reflow_restores_the_side_by_side_row_when_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)
    host.pages.resize(200, 720)
    host._reflow_dashboard()
    host.pages.resize(3000, 720)

    host._reflow_dashboard()

    assert host._dashboard_row1.direction() == QBoxLayout.Direction.LeftToRight


def test_reflow_rewraps_the_tiles_when_the_column_count_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = [_app(f"app{i}") for i in range(6)]
    host = _build_dash(monkeypatch, apps=apps, pinned=tuple(a.name for a in apps))
    assert host._workspace_cols_cur == 6

    host.pages.resize(600, 720)
    host._reflow_dashboard()

    assert host._workspace_cols_cur == 3
    grid = host._workspace_holder.itemAt(0).widget().layout()
    assert grid.rowCount() == 2


def test_reflow_is_a_noop_before_the_page_is_built() -> None:
    _ensure_qapp()
    host = DashHarness(_make_cfg())

    host._reflow_dashboard()

    assert getattr(host, "_dashboard_row1", None) is None
