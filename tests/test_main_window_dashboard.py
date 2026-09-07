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
   - Dashboard: hero/metric-bar value formatting, the "n/a" fallbacks and the
     last-known-value caching for RAM + disk, pod-state to hero/recovery-line
     mapping, workspace tile ordering + wrapping, and responsive reflow.
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

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QWidget,
)

import winpodx.gui._main_window_dashboard as dash_mod  # noqa: E402
from winpodx.core.app import AppInfo  # noqa: E402
from winpodx.core.config import Config  # noqa: E402
from winpodx.core.i18n import tr  # noqa: E402
from winpodx.core.stats import ResourceSnapshot  # noqa: E402
from winpodx.gui import launcher_state  # noqa: E402
from winpodx.gui._main_window_dashboard import DashboardMixin  # noqa: E402
from winpodx.gui._widget_helpers import ElidingLabel  # noqa: E402
from winpodx.gui.theme import FOCUS_RING, HIT_TARGET, C  # noqa: E402

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
        self.started_pod = 0
        self.stopped_pod = 0
        self.switched: list[int] = []
        self.opened_desktop = 0
        self.refreshed_apps = 0
        # Only ``.width()`` is read off ``pages``.
        self.pages = QWidget(self)
        self.pages.resize(1100, 720)

    def _launch_app(self, app: AppInfo) -> None:
        self.launched.append(app)

    def _show_app_menu(self, app: AppInfo, pos: Any) -> None:
        self.menued.append(app)

    def _on_start_pod(self) -> None:
        self.started_pod += 1

    def _on_stop_pod(self) -> None:
        self.stopped_pod += 1

    def _switch_page(self, index: int) -> None:
        self.switched.append(index)

    def _on_open_desktop(self) -> None:
        self.opened_desktop += 1

    def _on_refresh_apps(self) -> None:
        self.refreshed_apps += 1


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


def _dashboard_frame(host: DashHarness, object_name: str) -> QFrame:
    frame = host.findChild(QFrame, object_name)
    assert frame is not None, f"Dashboard must expose QFrame#{object_name}"
    return frame


def _label_with_text(parent: QWidget, text: str) -> QLabel:
    label = next((child for child in parent.findChildren(QLabel) if child.text() == text), None)
    assert label is not None, f"Dashboard must show {text!r}"
    return label


def _reflow_dashboard_at(host: DashHarness, width: int) -> QScrollArea:
    scroll = host.findChild(QScrollArea)
    assert scroll is not None
    page = scroll.parentWidget()
    assert page is not None
    host.pages.resize(width, 720)
    page.setFixedSize(width, 720)
    host.resize(width, 720)
    host.show()
    QApplication.processEvents()
    host._reflow_dashboard()
    QApplication.processEvents()
    return scroll


def _dashboard_layout(host: DashHarness, attribute: str) -> QBoxLayout:
    layout = getattr(host, attribute, None)
    assert layout is not None, f"Dashboard must expose {attribute} for responsive reflow"
    assert isinstance(layout, QBoxLayout), f"{attribute} must be a QBoxLayout"
    return layout


# ----- Dashboard: page scaffolding ---------------------------------------


def test_dashboard_page_builds_its_live_status_workspace_and_reverse_open_regions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    assert host.findChild(QFrame, "podStatusHero") is not None
    assert host.findChild(QLabel, "podStatusLabel") is not None
    assert host.findChild(QLabel, "podStatusDetail") is not None
    assert host.findChild(QPushButton, "podPrimaryAction") is not None
    assert host.findChild(QFrame, "podMetricsCluster") is not None
    assert hasattr(host, "_bar_ram")
    assert hasattr(host, "_bar_cpu")
    assert host._bar_disk is not None
    assert host._recovery_label.text()
    assert host._reverse_open_check is not None
    assert host._workspace_holder.count() == 1  # the empty-state panel


def test_dashboard_target_anatomy_uses_named_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    host = _build_dash(monkeypatch)

    # When
    surfaces = {
        name: host.findChild(QFrame, name)
        for name in (
            "podStatusHero",
            "settingsActionList",
            "settingsActionRow",
            "workspaceSurface",
            "reverseOpenRow",
        )
    }
    labels = {name: host.findChild(QLabel, name) for name in ("podStatusLabel", "podStatusDetail")}
    action = host.findChild(QPushButton, "podPrimaryAction")
    metrics = host.findChild(QFrame, "podMetricsCluster")

    # Then
    assert all(surfaces.values())
    assert all(labels.values())
    assert action is not None
    assert metrics is not None


def test_dashboard_target_replaces_legacy_settings_section_anatomy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    host = _build_dash(monkeypatch)

    # When
    legacy_sections = host.findChildren(QFrame, "settingsSection")

    # Then
    assert legacy_sections == []


def test_dashboard_owns_one_vertical_scroll_area_without_horizontal_scroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    host = _build_dash(monkeypatch)

    # When
    scroll_areas = host.findChildren(QScrollArea)

    # Then
    assert len(scroll_areas) == 1
    assert scroll_areas[0].horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_dashboard_metrics_are_compact_stat_bars_with_accessible_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    host = _build_dash(monkeypatch)
    host._apply_snapshot(_snapshot(cpu_pct=37.4, ram_pct=61.8, disk_pct=45.0))

    # When
    metrics = _dashboard_frame(host, "podMetricsCluster")
    bars = [getattr(host, name, None) for name in ("_bar_ram", "_bar_cpu", "_bar_disk")]

    # Then
    assert all(bars)
    assert all(bar.parentWidget() is metrics for bar in bars)
    assert host._bar_ram.accessibleName() == tr("RAM")
    assert "62%" in host._bar_ram.accessibleDescription()
    assert host._bar_cpu.accessibleName() == tr("CPU")
    assert "37%" in host._bar_cpu.accessibleDescription()
    assert host._bar_disk.accessibleName() == tr("Disk C:")
    gauges = host.findChildren(dash_mod.RingGauge)
    assert len(gauges) == 3
    assert {gauge.accessibleName() for gauge in gauges} == {
        tr("RAM"),
        tr("CPU"),
        tr("Disk C:"),
    }


@pytest.mark.parametrize(
    "case",
    [
        ("stopped", "Start Pod", "started_pod"),
        ("running", "Stop Pod", "stopped_pod"),
    ],
)
def test_dashboard_hero_primary_action_routes_only_to_the_matching_pod_operation(
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, str],
) -> None:
    # Given
    state, label, expected_counter = case
    host = _build_dash(monkeypatch)
    host._apply_snapshot(_snapshot(pod_state=state))

    # When
    action = host.findChild(QPushButton, "podPrimaryAction")

    # Then
    assert action is not None
    assert action.text() == tr(label)
    assert action.minimumHeight() >= HIT_TARGET
    assert action.isEnabled() is True
    action.click()
    assert getattr(host, expected_counter) == 1
    opposite_counter = "stopped_pod" if expected_counter == "started_pod" else "started_pod"
    assert getattr(host, opposite_counter) == 0


@pytest.mark.parametrize("state", ["checking", "paused"])
def test_dashboard_hero_primary_action_is_disabled_for_transient_pod_states(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    # Given
    host = _build_dash(monkeypatch)
    host._apply_snapshot(_snapshot(pod_state=state))

    # When
    action = host.findChild(QPushButton, "podPrimaryAction")

    # Then
    assert action is not None
    assert action.isEnabled() is False


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


@pytest.mark.parametrize("width", [1100, 740])
def test_reverse_open_checkbox_keeps_a_44px_interactive_target_at_each_reflow_width(
    monkeypatch: pytest.MonkeyPatch,
    width: int,
) -> None:
    # Given
    host = _build_dash(monkeypatch)

    # When
    _reflow_dashboard_at(host, width)
    checkbox = host._reverse_open_check

    # Then
    assert checkbox.height() >= HIT_TARGET
    assert checkbox.minimumHeight() >= HIT_TARGET
    host.close()


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

    assert hasattr(host, "_bar_cpu")
    assert hasattr(host, "_bar_ram")
    assert host._bar_cpu._detail == "37%"
    assert host._bar_ram._detail == "62%"
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

    assert hasattr(host, "_bar_cpu")
    assert hasattr(host, "_bar_ram")
    assert host._bar_cpu._detail == tr("n/a")
    assert host._bar_cpu._pct is None
    assert host._bar_ram._detail == tr("n/a")
    assert host._bar_disk._detail == tr("n/a")
    assert host._bar_disk._pct is None


def test_apply_snapshot_keeps_the_last_ram_between_slow_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(ram_pct=71.0))
    host._apply_snapshot(_snapshot(ram_pct=None))

    assert hasattr(host, "_bar_ram")
    assert host._bar_ram._detail == "71%"
    assert host._bar_ram._pct == pytest.approx(71.0)


def test_apply_snapshot_keeps_the_last_disk_between_slow_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(disk_pct=80.0, disk_used_gb=51.0, disk_total_gb=64.0))
    host._apply_snapshot(_snapshot(disk_pct=None, disk_total_gb=None))

    assert host._bar_disk._detail == "51 / 64 GB"
    assert host._bar_disk._pct == pytest.approx(80.0)


@pytest.mark.parametrize(
    ("state", "text_key"),
    [
        ("running", "Active"),
        ("checking", "Checking"),
        ("paused", "Paused"),
        ("stopped", "Off"),
        ("unknown", "Unknown"),
        ("bogus-state", "Unknown"),
    ],
)
def test_apply_snapshot_maps_pod_state_to_the_hero_label(
    monkeypatch: pytest.MonkeyPatch, state: str, text_key: str
) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(pod_state=state))

    label = host.findChild(QLabel, "podStatusLabel")
    assert label is not None
    assert label.text() == tr(text_key)


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


def test_running_snapshot_keeps_hero_ready_copy_distinct_from_recovery_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    host = _build_dash(monkeypatch)

    # When
    host._apply_snapshot(_snapshot(pod_state="running"))
    detail = host.findChild(QLabel, "podStatusDetail")

    # Then
    assert detail is not None
    assert detail.text() == tr("Pod is ready!")
    assert host._recovery_label.text() == tr("Protected — monitoring active")


def test_recovery_line_is_tinted_by_the_state_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(pod_state="running"))
    running_style = host._recovery_label.styleSheet()
    host._apply_snapshot(_snapshot(pod_state="stopped"))
    stopped_style = host._recovery_label.styleSheet()

    assert C.GREEN in running_style
    assert C.OVERLAY1 in stopped_style


def test_apply_snapshot_is_a_noop_before_the_dashboard_widgets_exist() -> None:
    _ensure_qapp()
    host = DashHarness(_make_cfg())

    # Must not raise: the snapshot signal can land before the page is built.
    host._apply_snapshot(_snapshot(cpu_pct=50.0))

    assert getattr(host, "_bar_cpu", None) is None
    assert getattr(host, "_bar_ram", None) is None


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


def test_workspace_surface_separates_pinned_and_recent_presentations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    apps = [_app("word"), _app("excel"), _app("notepad")]
    host = _build_dash(
        monkeypatch,
        apps=apps,
        pinned=("word", "excel"),
        recent=("word", "notepad"),
    )

    # When
    _reflow_dashboard_at(host, 1100)
    surface = _dashboard_frame(host, "workspaceSurface")
    pinned_section = _dashboard_frame(host, "pinnedWorkspaceSection")
    recent_section = _dashboard_frame(host, "recentWorkspaceSection")
    layout = surface.layout()
    assert layout is not None

    # Then
    assert layout.indexOf(pinned_section) < layout.indexOf(recent_section)
    assert _label_with_text(pinned_section, tr("Pinned")) is not None
    assert _label_with_text(recent_section, tr("Recent")) is not None
    assert [tile._app.name for tile in pinned_section.findChildren(dash_mod._AppTile)] == [
        "word",
        "excel",
    ]
    recent_tiles = [tile._app.name for tile in recent_section.findChildren(dash_mod._AppTile)]
    assert recent_tiles == ["notepad"]


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


def test_workspace_tile_applies_focus_rule_for_its_live_object_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch, apps=[_app("word")], pinned=("word",))
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()

    assert tile.objectName() == "appTileBtn"
    assert "QFrame#appTileBtn:focus" in tile.styleSheet()
    assert f"border: {FOCUS_RING};" in tile.styleSheet()


def test_workspace_tile_uses_strong_focus_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch, apps=[_app("word")], pinned=("word",))
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()

    assert tile.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_workspace_tile_exposes_full_accessible_name(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app("word")
    host = _build_dash(monkeypatch, apps=[app], pinned=(app.name,))
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()

    assert tile.accessibleName() == app.full_name


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space])
def test_workspace_tile_activation_key_launches_once(
    monkeypatch: pytest.MonkeyPatch, key: Qt.Key
) -> None:
    app = _app("word")
    host = _build_dash(monkeypatch, apps=[app], pinned=(app.name,))
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()
    event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    event.ignore()

    QApplication.sendEvent(tile, event)

    assert host.launched == [app]
    assert event.isAccepted() is True


def test_workspace_tile_activation_autorepeat_does_not_relaunch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app("word")
    host = _build_dash(monkeypatch, apps=[app], pinned=(app.name,))
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()
    initial = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Space,
        Qt.KeyboardModifier.NoModifier,
        " ",
        False,
        1,
    )
    repeated = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Space,
        Qt.KeyboardModifier.NoModifier,
        " ",
        True,
        1,
    )
    repeated.ignore()

    QApplication.sendEvent(tile, initial)
    QApplication.sendEvent(tile, repeated)

    assert host.launched == [app]
    assert repeated.isAccepted() is True


def test_workspace_tile_unrelated_key_preserves_qframe_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app("word")
    host = _build_dash(monkeypatch, apps=[app], pinned=(app.name,))
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()
    event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_A,
        Qt.KeyboardModifier.NoModifier,
    )
    event.ignore()

    QApplication.sendEvent(tile, event)

    assert host.launched == []
    assert event.isAccepted() is False


def test_workspace_tile_is_reachable_in_tab_chain() -> None:
    _ensure_qapp()
    app = _app("word")
    launched: list[AppInfo] = []
    container = QWidget()
    layout = QHBoxLayout(container)
    before = QPushButton("Before")
    tile = dash_mod._AppTile(app, on_launch=launched.append, on_menu=lambda _app, _pos: None)
    after = QPushButton("After")
    layout.addWidget(before)
    layout.addWidget(tile)
    layout.addWidget(after)
    QWidget.setTabOrder(before, tile)
    QWidget.setTabOrder(tile, after)
    container.show()
    before.setFocus()
    QApplication.processEvents()

    QTest.keyClick(before, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is tile
    QTest.keyClick(tile, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is after

    container.close()


@pytest.mark.parametrize("width", [740, 1100])
def test_workspace_unbroken_name_has_visible_ellipsis_at_dashboard_width(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    full_name = "PowerPointVeryLongUnbrokenNameWithoutSpaces"
    app = AppInfo(name="powerpoint", full_name=full_name, executable="C:\\powerpoint.exe")
    host = _build_dash(monkeypatch, apps=[app], pinned=(app.name,))
    host.pages.resize(width, 720)
    host._reflow_dashboard()
    QApplication.processEvents()
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()
    label = next(child for child in tile.findChildren(QLabel) if child.width() == 104)
    scroll = host.findChild(QScrollArea)

    assert label.text().endswith("…")
    assert label.text() != full_name
    assert tile.toolTip() == full_name
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_workspace_spaced_ascii_name_keeps_full_wrapped_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full_name = "Microsoft PowerPoint Professional"
    app = AppInfo(name="powerpoint", full_name=full_name, executable="C:\\powerpoint.exe")
    host = _build_dash(monkeypatch, apps=[app], pinned=(app.name,))
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()
    label = next(child for child in tile.findChildren(QLabel) if child.width() == 104)

    assert label.text() == full_name
    assert label.wordWrap() is True
    assert "…" not in label.text()
    assert tile.toolTip() == full_name


@pytest.mark.parametrize("width", [740, 1100])
def test_workspace_cjk_name_keeps_fixed_width_wrapping(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    full_name = "超長い名前テスト文字列"
    app = AppInfo(name="cjk-app", full_name=full_name, executable="C:\\cjk.exe")
    host = _build_dash(monkeypatch, apps=[app], pinned=(app.name,))
    host.pages.resize(width, 720)
    host._reflow_dashboard()
    QApplication.processEvents()
    grid = host._workspace_holder.itemAt(0).widget().layout()
    tile = grid.itemAt(0).widget()
    label = next(child for child in tile.findChildren(QLabel) if child.width() == 104)
    scroll = host.findChild(QScrollArea)

    assert label.text() == full_name
    assert label.wordWrap() is True
    assert tile.toolTip() == full_name
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_workspace_wrapped_labels_allocate_font_metric_height(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cjk_name = "メモ帳ノートパッド超長い名前テスト文字列"
    ascii_name = "Microsoft PowerPoint Professional"
    cjk = AppInfo(name="cjk-notepad", full_name=cjk_name, executable="C:\\cjk.exe")
    ppt = AppInfo(name="powerpoint", full_name=ascii_name, executable="C:\\ppt.exe")
    host = _build_dash(monkeypatch, apps=[cjk, ppt], pinned=(cjk.name, ppt.name))
    scroll = host.findChild(QScrollArea)
    page = scroll.parentWidget()
    wrap_names = (cjk_name, ascii_name)

    try:
        for width, expected_cols in ((1100, 6), (740, 4)):
            host.pages.resize(width, 720)
            page.setFixedSize(width, 720)
            host.resize(width, 720)
            host.show()
            QApplication.processEvents()
            host._reflow_dashboard()
            QApplication.processEvents()

            assert host._workspace_cols_cur == expected_cols
            assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            assert scroll.horizontalScrollBar().maximum() == 0

            grid = host._workspace_holder.itemAt(0).widget().layout()
            tiles = [
                grid.itemAt(i).widget()
                for i in range(grid.count())
                if isinstance(grid.itemAt(i).widget(), dash_mod._AppTile)
            ]
            by_name = {tile._app.full_name: tile for tile in tiles}
            for full_name in wrap_names:
                tile = by_name[full_name]
                label = next(child for child in tile.findChildren(QLabel) if child.width() == 104)
                wrap_flags = label.alignment() | Qt.TextFlag.TextWordWrap
                required = (
                    label.fontMetrics().boundingRect(0, 0, 104, 0, wrap_flags, full_name).height()
                )

                assert label.text() == full_name
                assert tile.toolTip() == full_name
                assert isinstance(label, QLabel)
                assert not isinstance(label, ElidingLabel)
                assert label.wordWrap() is True
                assert label.width() == 104
                assert required > label.fontMetrics().lineSpacing()
                assert label.height() >= required, (
                    f"{full_name!r} at {width}px: allocated {label.height()} < required {required}"
                )
                assert tile.rect().contains(label.geometry())
    finally:
        host.close()


def test_populate_workspace_pads_an_underfull_last_row(monkeypatch: pytest.MonkeyPatch) -> None:
    apps = [_app("word"), _app("excel"), _app("notepad")]
    host = _build_dash(monkeypatch, apps=apps, pinned=tuple(a.name for a in apps))

    grid = host._workspace_holder.itemAt(0).widget().layout()
    cols = host._workspace_cols()
    # 3 tiles + (cols - 3) transparent spacers so the row keeps left alignment.
    assert grid.count() == cols


def test_workspace_tile_left_click_launches_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from PySide6.QtCore import QPoint
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


def test_dashboard_reflow_keeps_hero_settings_and_reverse_open_horizontal_at_1100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    host = _build_dash(monkeypatch)

    # When
    scroll = _reflow_dashboard_at(host, 1100)
    hero_layout = _dashboard_layout(host, "_dashboard_hero_layout")
    settings_layout = _dashboard_layout(host, "_settings_row_layout")
    reverse_open_layout = _dashboard_layout(host, "_reverse_open_layout")

    # Then
    assert hero_layout.direction() == QBoxLayout.Direction.LeftToRight
    assert settings_layout.direction() == QBoxLayout.Direction.LeftToRight
    assert reverse_open_layout.direction() == QBoxLayout.Direction.LeftToRight
    assert host._workspace_cols_cur == 6
    assert scroll.horizontalScrollBar().maximum() == 0
    host.close()


def test_dashboard_reflow_stacks_hero_settings_and_reverse_open_at_560(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    host = _build_dash(monkeypatch)

    # When
    scroll = _reflow_dashboard_at(host, 560)
    hero_layout = _dashboard_layout(host, "_dashboard_hero_layout")
    settings_layout = _dashboard_layout(host, "_settings_row_layout")
    reverse_open_layout = _dashboard_layout(host, "_reverse_open_layout")

    # Then
    assert hero_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert settings_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert reverse_open_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert host._workspace_cols_cur == 3
    assert scroll.horizontalScrollBar().maximum() == 0
    host.close()


def test_dashboard_at_740_keeps_the_first_pinned_row_inside_the_top_viewport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    apps = [_app(f"app{index}") for index in range(4)]
    host = _build_dash(monkeypatch, apps=apps, pinned=tuple(app.name for app in apps))

    # When
    scroll = _reflow_dashboard_at(host, 740)
    scroll.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    pinned_holder = getattr(host, "_pinned_holder", None)
    assert pinned_holder is not None
    grid_widget = pinned_holder.itemAt(0).widget()
    assert grid_widget is not None
    grid = grid_widget.layout()
    assert grid is not None
    viewport = scroll.viewport()
    tile_rects = {
        tile: QRect(tile.mapTo(viewport, QPoint()), tile.size())
        for index in range(grid.count())
        if isinstance(tile := grid.itemAt(index).widget(), dash_mod._AppTile)
    }
    assert len(tile_rects) == 4
    first_row_top = min(rect.top() for rect in tile_rects.values())
    first_row = [tile for tile, rect in tile_rects.items() if rect.top() == first_row_top]
    first_row_rects = [tile_rects[tile] for tile in first_row]
    label_rects = [
        QRect(label.mapTo(viewport, QPoint()), label.size())
        for tile in first_row
        for label in tile.findChildren(QLabel)
    ]

    # Then
    assert scroll.horizontalScrollBar().maximum() == 0
    assert all(
        viewport.rect().top() <= rect.top() and rect.bottom() <= viewport.rect().bottom()
        for rect in (*first_row_rects, *label_rects)
    )
    host.close()


@pytest.mark.parametrize("width", [740, 1100])
def test_workspace_tiles_do_not_intersect_at_dashboard_width(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    apps = [_app(f"app{index}") for index in range(6)]
    host = _build_dash(monkeypatch, apps=apps, pinned=tuple(app.name for app in apps))
    _reflow_dashboard_at(host, width)
    QApplication.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    grid_widget = host._workspace_holder.itemAt(0).widget()
    grid = grid_widget.layout()
    tiles = [
        grid.itemAt(i).widget()
        for i in range(grid.count())
        if isinstance(grid.itemAt(i).widget(), dash_mod._AppTile)
    ]
    assert len(tiles) == 6
    visible = [
        tile
        for tile in host.findChildren(dash_mod._AppTile)
        if not tile.isHidden() and tile.width() > 0 and tile.height() > 0
    ]
    assert set(visible) <= set(tiles)
    rects = [tile.geometry() for tile in tiles]
    for i, left in enumerate(rects):
        for right in rects[i + 1 :]:
            assert not left.intersects(right)
    for tile in tiles:
        label = next(child for child in tile.findChildren(QLabel) if child.width() == 104)
        assert tile.height() >= 32 + label.height() + 16
        assert tile.rect().contains(label.geometry())
    host.close()


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


# ----- Dashboard: Win11 Settings anatomy ---------------------------------


def test_device_card_contains_a_64px_icon_label(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)

    hero = _dashboard_frame(host, "podStatusHero")
    icon = hero.findChild(QLabel, "deviceIcon")

    assert icon is not None
    assert icon.minimumWidth() == 56
    assert icon.minimumHeight() == 56
    assert icon.width() == 56 or icon.sizeHint().width() == 56 or icon.maximumWidth() == 56


def test_recovery_card_is_settings_action_row_inside_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    group = _dashboard_frame(host, "settingsActionList")
    row = _dashboard_frame(host, "settingsActionRow")

    assert group.isAncestorOf(row)


def test_reverse_open_card_is_a_painted_toggle_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    card = _dashboard_frame(host, "reverseOpenRow")
    toggle = host._reverse_open_check

    assert card.isAncestorOf(toggle)
    assert type(toggle).__name__ == "ToggleSwitch"
    assert toggle.minimumHeight() >= HIT_TARGET


def test_reverse_open_toggle_stays_on_the_title_row_at_1100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)
    _reflow_dashboard_at(host, 1100)
    card = _dashboard_frame(host, "reverseOpenRow")
    toggle = host._reverse_open_check
    title = card.title_label
    title_c = title.mapTo(card, title.rect().center())
    action_c = toggle.mapTo(card, toggle.rect().center())

    assert card.height() <= 96
    assert abs(action_c.y() - title_c.y()) <= 24
    host.close()


def test_reverse_open_toggle_stays_on_the_title_row_when_reflow_is_ttb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)
    _reflow_dashboard_at(host, 560)
    card = _dashboard_frame(host, "reverseOpenRow")
    toggle = host._reverse_open_check
    title = card.title_label
    title_c = title.mapTo(card, title.rect().center())
    action_c = toggle.mapTo(card, toggle.rect().center())

    assert host._reverse_open_layout.direction() == QBoxLayout.Direction.TopToBottom
    assert card.height() <= 96
    assert abs(action_c.y() - title_c.y()) <= 24
    host.close()


def test_dashboard_restyle_applies_scheme_surface0_to_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpodx.gui import theme as theme_mod

    host = _build_dash(monkeypatch)
    surface = _dashboard_frame(host, "workspaceSurface")
    previous = theme_mod.current_scheme()
    try:
        theme_mod.rebuild("light")
        host._restyle_dashboard()
        assert theme_mod.C.SURFACE0 == "#FFFFFF"
        assert "#FFFFFF" in surface.styleSheet()

        theme_mod.rebuild("dark")
        host._restyle_dashboard()
        assert theme_mod.C.SURFACE0 == "#2B2B2B"
        assert "#2B2B2B" in surface.styleSheet()
    finally:
        theme_mod.rebuild(previous)


def _mix_over(fg: str, bg: str, alpha: float) -> str:
    value_fg = fg.lstrip("#")
    value_bg = bg.lstrip("#")
    fr, fg_, fb = (int(value_fg[i : i + 2], 16) for i in (0, 2, 4))
    br, bg_, bb = (int(value_bg[i : i + 2], 16) for i in (0, 2, 4))
    r = round(fr * alpha + br * (1.0 - alpha))
    g = round(fg_ * alpha + bg_ * (1.0 - alpha))
    b = round(fb * alpha + bb * (1.0 - alpha))
    return f"#{r:02x}{g:02x}{b:02x}"


def test_quick_actions_card_has_four_ghost_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)
    card = _dashboard_frame(host, "quickActions")
    buttons = card.findChildren(QPushButton)
    labels = [btn.text() for btn in buttons]

    assert len(buttons) == 4
    assert labels == [
        tr("Full Desktop"),
        tr("Refresh Apps"),
        tr("Terminal / Logs"),
        tr("Settings"),
    ]


def test_quick_actions_route_to_existing_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)
    card = _dashboard_frame(host, "quickActions")
    by_label = {btn.text(): btn for btn in card.findChildren(QPushButton)}

    by_label[tr("Full Desktop")].click()
    by_label[tr("Refresh Apps")].click()
    by_label[tr("Terminal / Logs")].click()
    by_label[tr("Settings")].click()

    assert host.opened_desktop == 1
    assert host.refreshed_apps == 1
    assert host.switched == [4, 2]


def test_hero_ring_captions_fit_inside_gauge_rects(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)
    for name in ("_bar_ram", "_bar_cpu", "_bar_disk"):
        gauge = getattr(host, name)
        gauge.resize(gauge.sizeHint())
        cap = gauge.caption_rect()
        assert cap.y() >= 0
        assert cap.x() >= 0
        assert cap.y() + cap.height() <= gauge.height()
        assert cap.x() + cap.width() <= gauge.width()


def test_hero_background_tints_with_pod_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.gui import theme as theme_mod

    host = _build_dash(monkeypatch)
    hero = _dashboard_frame(host, "podStatusHero")
    host._apply_snapshot(_snapshot(pod_state="running"))
    running = _mix_over(theme_mod.C.GREEN, theme_mod.C.SURFACE0, 0.06)
    assert running.lower() in hero.styleSheet().lower()

    host._apply_snapshot(_snapshot(pod_state="stopped"))
    stopped = _mix_over(theme_mod.C.SUBTEXT0, theme_mod.C.SURFACE0, 0.06)
    assert stopped.lower() in hero.styleSheet().lower()
    assert running.lower() not in hero.styleSheet().lower()


def test_pinned_tile_shows_running_badge_for_live_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "winpodx.core.process.list_active_sessions",
        lambda: [SimpleNamespace(app_name="word")],
    )
    apps = [_app("word"), _app("excel")]
    host = _build_dash(monkeypatch, apps=apps, pinned=("word", "excel"))
    host.show()
    QApplication.processEvents()
    grid = host._workspace_holder.itemAt(0).widget().layout()
    word = grid.itemAt(0).widget()
    excel = grid.itemAt(1).widget()
    word_badge = word.findChild(QLabel, "runningBadge")
    excel_badge = excel.findChild(QLabel, "runningBadge")

    assert word_badge is not None
    assert not word_badge.isHidden()
    assert excel_badge is None or excel_badge.isHidden()


def test_empty_workspace_offers_applications_button(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)
    panel = host._workspace_holder.itemAt(0).widget()
    button = next(
        (child for child in panel.findChildren(QPushButton) if child.text() == tr("Applications")),
        None,
    )

    assert panel.objectName() == "emptyState"
    assert button is not None
    button.click()
    assert host.switched == [1]


def test_restyle_dashboard_reapplies_quick_actions_and_hero_tint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpodx.gui import theme as theme_mod

    host = _build_dash(monkeypatch)
    host._apply_snapshot(_snapshot(pod_state="running"))
    previous = theme_mod.current_scheme()
    try:
        theme_mod.rebuild("light")
        host._restyle_dashboard()
        card = _dashboard_frame(host, "quickActions")
        hero = _dashboard_frame(host, "podStatusHero")
        tint = _mix_over(theme_mod.C.GREEN, theme_mod.C.SURFACE0, 0.06)
        assert theme_mod.C.SURFACE0 == "#FFFFFF"
        assert "#FFFFFF" in card.styleSheet() or tint.lower() in hero.styleSheet().lower()
        assert tint.lower() in hero.styleSheet().lower()
    finally:
        theme_mod.rebuild(previous)


def test_stopped_hero_detail_includes_start_pod_next_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _build_dash(monkeypatch)

    host._apply_snapshot(_snapshot(pod_state="stopped"))
    detail = host.findChild(QLabel, "podStatusDetail")

    assert detail is not None
    assert tr("Pod is stopped") in detail.text()
    assert tr("Start Pod") in detail.text()
    assert host._recovery_label.text() == tr("Pod is stopped")


def test_running_now_hidden_without_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: [])
    host = _build_dash(monkeypatch)
    section = host.findChild(QFrame, "runningNow")

    assert section is not None
    assert section.isHidden()


def test_running_now_lists_live_sessions_with_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpodx.core.process import TrackedProcess

    killed: list[str] = []
    monkeypatch.setattr(
        "winpodx.core.process.list_active_sessions",
        lambda: [TrackedProcess(app_name="word", pid=4242)],
    )
    monkeypatch.setattr(
        "winpodx.core.process.kill_session",
        lambda name, expected_pid=None: killed.append(name) or True,
    )
    apps = [_app("word")]
    host = _build_dash(monkeypatch, apps=apps)
    host.show()
    QApplication.processEvents()
    section = host.findChild(QFrame, "runningNow")
    assert section is not None
    assert not section.isHidden()
    labels = [lbl.text() for lbl in section.findChildren(QLabel)]
    assert any("word" in text.lower() or "Word" in text for text in labels)
    assert any("4242" in text for text in labels)
    terminate = next(
        (btn for btn in section.findChildren(QPushButton) if btn.text() == tr("Terminate")),
        None,
    )
    assert terminate is not None
    assert 40 <= terminate.parentWidget().height() <= 48
    terminate.click()
    assert killed == ["word"]


def test_recovery_row_is_compact_48px(monkeypatch: pytest.MonkeyPatch) -> None:
    host = _build_dash(monkeypatch)
    host.show()
    QApplication.processEvents()
    row = _dashboard_frame(host, "settingsActionRow")

    assert row.minimumHeight() == 48
    assert row.height() == 48
