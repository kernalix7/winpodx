# SPDX-License-Identifier: MIT
"""Dashboard page mixin for ``WinpodxWindow``.

Windows 11 Settings + Start-inspired home: a dominant pod-status hero,
compact metric bars, a settings recovery row, a pinned/recent workspace
board, and a compact reverse-open row. Facade owns page assembly, reflow,
live refresh, snapshot painting, and hero action routing.

Host-class contract (provided by ``WinpodxWindow`` / sibling mixins):
    cfg: winpodx.core.config.Config
    apps: list[AppInfo]
    dashboard_updated: Signal(object)        — emits a ResourceSnapshot
    _launch_app(app) / _show_app_menu(app, pos)
    _on_start_pod() / _on_stop_pod()
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.core.stats import ResourceSnapshot, pod_resource_snapshot
from winpodx.gui._main_window_dashboard_sessions import _DashboardSessionsMixin
from winpodx.gui._main_window_dashboard_style import _DashboardStyleMixin
from winpodx.gui._main_window_dashboard_surfaces import (
    _RECOVERY_TEXT,
    _DashboardSurfacesMixin,
)
from winpodx.gui._main_window_dashboard_workspace import _DashboardWorkspaceMixin
from winpodx.gui._main_window_library import _AppTile
from winpodx.gui._ring_gauge import RingGauge
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    CONTENT_MAX_WIDTH,
    PAGE_MARGIN_X,
    SCROLL_AREA,
    SCROLL_GUTTER,
    SPACE_XL,
    C,
)

log = logging.getLogger(__name__)

# Re-exported for existing test identity (findChildren / isinstance).
__all__ = ["DashboardMixin", "RingGauge", "_AppTile"]

_REFRESH_MS = 5000
_WIDE_PX = 640

# Per-state look for the hero + auto-recovery line.
_POD_STATES = {
    "running": (100.0, "Active", "check", "GREEN"),
    "checking": (60.0, "Checking", "refresh", "PEACH"),
    "paused": (50.0, "Paused", "pause", "PEACH"),
    "stopped": (0.0, "Off", "power", "OVERLAY1"),
    "unknown": (0.0, "Unknown", "warning", "YELLOW"),
}

_HERO_ACTION = {
    "running": ("Stop Pod", True),
    "stopped": ("Start Pod", True),
}
_HERO_ACTION_FALLBACK = ("Start Pod", False)
_HERO_ROUTE = {
    "running": "_on_stop_pod",
    "stopped": "_on_start_pod",
}


class DashboardMixin(
    _DashboardSurfacesMixin,
    _DashboardWorkspaceMixin,
    _DashboardSessionsMixin,
    _DashboardStyleMixin,
):
    """Resource dashboard: hero + recovery row + workspace + reverse-open."""

    def _build_dashboard_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        # No right margin on the outer layout: the scroll area runs to the
        # window edge so its scrollbar sits at the far right.
        outer.setContentsMargins(0, 0, 0, SPACE_XL)
        outer.setSpacing(SPACE_XL)
        register = getattr(self, "_register_page_header", None)
        if callable(register):
            register(
                0,
                tr("Dashboard"),
                tr("Pod health, resources, and your workspace at a glance."),
            )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(SCROLL_AREA)

        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        body = QVBoxLayout(inner)
        body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        body.setContentsMargins(0, 0, PAGE_MARGIN_X - SCROLL_GUTTER, 0)
        body.setSpacing(SPACE_XL)
        inner.setMaximumWidth(CONTENT_MAX_WIDTH + PAGE_MARGIN_X - SCROLL_GUTTER)
        body.addWidget(self._build_status_hero())
        body.addWidget(self._build_quick_actions())
        body.addWidget(self._build_running_now())
        body.addWidget(self._build_reverse_open_card())
        body.addWidget(self._build_workspace_card())
        body.addWidget(self._build_settings_actions())
        body.addStretch(1)
        self._pod_primary_action.clicked.connect(self._on_hero_primary)

        scroll.setWidget(inner)
        self._dashboard_scroll = scroll
        outer.addWidget(scroll, 1)

        self._dashboard_timer = QTimer(self)
        self._dashboard_timer.setInterval(_REFRESH_MS)
        self._dashboard_timer.timeout.connect(self._refresh_dashboard)
        self._dashboard_refreshing = False

        self._populate_workspace()
        self._refresh_dashboard()
        self._reflow_dashboard()
        self._dashboard_timer.start()
        return page

    def _reflow_dashboard(self) -> None:
        """Stack hero / settings / reverse-open below ``_WIDE_PX``; rewrap tiles."""
        row1 = getattr(self, "_dashboard_row1", None)
        pages = getattr(self, "pages", None)
        if row1 is None or pages is None:
            return
        want = (
            QBoxLayout.Direction.LeftToRight
            if pages.width() >= _WIDE_PX
            else QBoxLayout.Direction.TopToBottom
        )
        for layout in (
            row1,
            getattr(self, "_settings_row_layout", None),
            getattr(self, "_reverse_open_layout", None),
        ):
            if layout is not None and layout.direction() != want:
                layout.setDirection(want)
        compact = pages.width() < _WIDE_PX
        self._reflow_quick_actions(compact)
        for name in ("_bar_ram", "_bar_cpu", "_bar_disk"):
            gauge = getattr(self, name, None)
            if gauge is not None:
                gauge.set_compact(compact)
        metrics = getattr(self, "_metrics_layout", None)
        if metrics is not None and metrics.direction() != QBoxLayout.Direction.LeftToRight:
            metrics.setDirection(QBoxLayout.Direction.LeftToRight)
        cluster = getattr(self, "_metrics_cluster", None)
        if cluster is not None:
            row1.setAlignment(
                cluster,
                Qt.AlignmentFlag.AlignHCenter if compact else Qt.AlignmentFlag.AlignVCenter,
            )

        cur = getattr(self, "_workspace_cols_cur", None)
        if cur is not None and self._workspace_cols() != cur:
            self._populate_workspace()

    def _on_hero_primary(self) -> None:
        handler = _HERO_ROUTE.get(getattr(self, "_hero_pod_state", ""))
        if handler is not None:
            getattr(self, handler)()

    def _on_reverse_open_toggled(self, checked: bool) -> None:
        try:
            self.cfg.reverse_open.enabled = bool(checked)
            self.cfg.save()
        except Exception as e:  # noqa: BLE001 -- never let a toggle crash the GUI
            log.warning("failed to persist reverse-open toggle: %s", e)
            return
        from types import SimpleNamespace

        from winpodx.cli.host_open import _cmd_start_listener, _cmd_stop_listener

        handler = _cmd_start_listener if checked else _cmd_stop_listener
        try:
            handler(SimpleNamespace(json=False))
        except Exception:  # noqa: BLE001 -- status reflects the outcome
            log.debug(
                "reverse-open %s on dashboard toggle failed (guest may be down)",
                "start" if checked else "stop",
                exc_info=True,
            )

    def _refresh_dashboard(self) -> None:
        """Probe pod resources off-thread; results land via ``dashboard_updated``."""
        if getattr(self, "_dashboard_refreshing", False):
            return
        self._dashboard_refreshing = True
        self._dashboard_tick = getattr(self, "_dashboard_tick", 0) + 1
        with_disk = self._dashboard_tick % 2 == 1
        pod_state = getattr(self, "_pod_state", None)

        def _work() -> None:
            try:
                snap = pod_resource_snapshot(self.cfg, pod_state=pod_state, with_disk=with_disk)
            except Exception as e:  # noqa: BLE001 -- snapshot is best-effort
                log.debug("dashboard snapshot failed: %s", e)
                snap = None
            finally:
                self._dashboard_refreshing = False
            if snap is not None:
                self.dashboard_updated.emit(snap)

        threading.Thread(target=_work, daemon=True).start()

    def _apply_snapshot(self, snap: ResourceSnapshot) -> None:
        """GUI-thread slot: paint the latest snapshot onto the widgets."""
        if getattr(self, "_bar_cpu", None) is None:
            return

        _pct, state_key, rec_icon, rec_attr = _POD_STATES.get(
            snap.pod_state, _POD_STATES["unknown"]
        )
        rec_color = getattr(C, rec_attr)
        self._pod_status_icon.setPixmap(load_icon(rec_icon, rec_color, 24).pixmap(24, 24))
        self._pod_status_label.setText(tr(state_key))
        recovery = tr(_RECOVERY_TEXT.get(snap.pod_state, _RECOVERY_TEXT["unknown"]))
        if snap.pod_state == "running":
            detail = tr("Pod is ready!")
        elif snap.pod_state == "stopped":
            detail = f"{tr('Pod is stopped')} — {tr('Start Pod')}"
        else:
            detail = recovery
        self._pod_status_detail.setText(detail)

        if snap.cpu_pct is None:
            self._bar_cpu.set_value(None, tr("n/a"))
        else:
            self._bar_cpu.set_value(snap.cpu_pct, f"{snap.cpu_pct:.0f}%")

        if snap.ram_pct is None:
            cached_ram = getattr(self, "_last_ram", None)
            if cached_ram is not None:
                self._bar_ram.set_value(cached_ram, f"{cached_ram:.0f}%")
            else:
                self._bar_ram.set_value(None, tr("n/a"))
        else:
            self._last_ram = snap.ram_pct
            self._bar_ram.set_value(snap.ram_pct, f"{snap.ram_pct:.0f}%")

        if snap.disk_pct is None or snap.disk_total_gb is None:
            cached = getattr(self, "_last_disk", None)
            if cached is not None:
                self._bar_disk.set_value(cached[2], f"{cached[1]:.0f} / {cached[0]:.0f} GB")
            else:
                self._bar_disk.set_value(None, tr("n/a"))
        else:
            self._last_disk = (snap.disk_total_gb, snap.disk_used_gb, snap.disk_pct)
            self._bar_disk.set_value(
                snap.disk_pct,
                f"{snap.disk_used_gb:.0f} / {snap.disk_total_gb:.0f} GB",
            )

        btn = self._pod_primary_action
        self._hero_pod_state = snap.pod_state
        self._apply_hero_tint(snap.pod_state)
        label, enabled = _HERO_ACTION.get(snap.pod_state, _HERO_ACTION_FALLBACK)
        btn.setText(tr(label))
        btn.setEnabled(enabled)

        self._apply_recovery_line(snap.pod_state, rec_icon, rec_color)
        self._refresh_running_now()
