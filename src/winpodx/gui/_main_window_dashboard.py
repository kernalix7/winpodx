# SPDX-License-Identifier: MIT
"""Dashboard page mixin for ``WinpodxWindow``.

The Dashboard is the launcher home: an at-a-glance resource centre (pod
state + live CPU/RAM against the configured cap + guest disk usage), the
auto-recovery status, the user's workspace (pinned + recent apps as
launchable tiles), and the reverse-open master toggle. It is modelled on
the design mockup -- everything lives *inside* the window (no floating
overlays).

Host-class contract (provided by ``WinpodxWindow`` / sibling mixins):
    cfg: winpodx.core.config.Config
    apps: list[AppInfo]
    dashboard_updated: Signal(object)        — emits a ResourceSnapshot
    _launch_app(app) / _show_app_menu(app, pos)
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import AppInfo
from winpodx.core.i18n import tr
from winpodx.core.stats import ResourceSnapshot, pod_resource_snapshot
from winpodx.gui import launcher_state
from winpodx.gui._main_window_library import _AppTile
from winpodx.gui._ring_gauge import RingGauge, StatBar
from winpodx.gui._widget_helpers import (
    add_shadow,
    columns_want_stack,
    make_empty_panel,
    make_page_header,
)
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    CHECKBOX,
    FONT_BODY,
    FONT_CAPTION,
    FONT_HEADER,
    SCROLL_AREA,
    SECTION_CARD,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    SPACE_XXL,
    C,
)

log = logging.getLogger(__name__)

# Live-gauge refresh cadence while the Dashboard is the visible page. The
# probe runs off-thread so this only schedules work, never blocks paint.
_REFRESH_MS = 5000

# Per-state look for the pod ring + auto-recovery line. (gauge_pct, gauge_text
# key, recovery icon, recovery text key, recovery color).
_POD_STATES = {
    "running": (100.0, "Active", "check", C.GREEN),
    "checking": (60.0, "Checking", "refresh", C.PEACH),
    "paused": (50.0, "Paused", "pause", C.PEACH),
    "stopped": (0.0, "Off", "stop", C.OVERLAY1),
    "unknown": (0.0, "Unknown", "warning", C.YELLOW),
}


class DashboardMixin:
    """Resource dashboard: gauges + auto-recovery + workspace + reverse-open."""

    # -- page ------------------------------------------------------------- #

    def _build_dashboard_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        # No right margin on the outer layout: the scroll area runs to the
        # window edge so its scrollbar sits at the far right. The card column
        # gets its right gap from the inner body margin instead, so the
        # scrollbar never overlaps a card's rounded corner.
        outer.setContentsMargins(SPACE_XXL, 0, 0, SPACE_XL)
        outer.setSpacing(SPACE_M)
        outer.addWidget(
            make_page_header(
                tr("Dashboard"),
                tr("Pod health, resources, and your workspace at a glance."),
            )
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(SCROLL_AREA)

        inner = QWidget()
        body = QVBoxLayout(inner)
        body.setContentsMargins(0, 0, SPACE_XXL, 0)
        body.setSpacing(SPACE_L)

        # Row 1: resource centre (wide) + auto-recovery (narrow). Stacks
        # vertically on narrow windows (see _reflow_dashboard) so the three
        # gauges never cramp under the 2:1 split at the minimum width.
        row1 = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        row1.setSpacing(SPACE_L)
        self._dashboard_row1 = row1
        row1.addWidget(self._build_resource_card(), 2)
        row1.addWidget(self._build_recovery_card(), 1)
        body.addLayout(row1)

        # Row 2: workspace (pinned + recent apps).
        body.addWidget(self._build_workspace_card())

        # Row 3: reverse-open control.
        body.addWidget(self._build_reverse_open_card())
        body.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # Live refresh while this page is shown. The nav starts/stops it on page
        # switch, but the Dashboard is the default page shown at startup -- no
        # switch fires for it -- so start the timer here too, or the panel would
        # only ever auto-refresh after navigating away and back.
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
        """Reflow the dashboard to the current width. Stacks the resource +
        auto-recovery cards vertically when too narrow for the 2:1 row, and
        re-wraps the workspace tile grid when the column count changes. Driven
        by the window resizeEvent; idempotent."""
        row1 = getattr(self, "_dashboard_row1", None)
        pages = getattr(self, "pages", None)
        if row1 is None or pages is None:
            return
        want = (
            QBoxLayout.Direction.TopToBottom
            if columns_want_stack(row1, pages.width())
            else QBoxLayout.Direction.LeftToRight
        )
        if row1.direction() != want:
            row1.setDirection(want)

        # Re-wrap the workspace tiles when the available width changes the
        # column count (only rebuilds when it actually changes — cheap on resize).
        cur = getattr(self, "_workspace_cols_cur", None)
        if cur is not None and self._workspace_cols() != cur:
            self._populate_workspace()

    # -- card scaffolding ------------------------------------------------- #

    def _dash_card(self, title: str, icon_name: str) -> tuple[QFrame, QVBoxLayout]:
        """Build a titled section card; return ``(card, content_layout)``."""
        card = QFrame()
        card.setObjectName("settingsSection")
        card.setStyleSheet(SECTION_CARD)
        add_shadow(card, blur=14, y=2, alpha=35)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        lay.setSpacing(SPACE_M)

        head = QWidget()
        head_l = QHBoxLayout(head)
        head_l.setContentsMargins(0, 0, 0, 0)
        head_l.setSpacing(SPACE_S)
        icon = QLabel()
        icon.setFixedSize(16, 16)
        icon.setPixmap(load_icon(icon_name, C.SUBTEXT0, 16).pixmap(16, 16))
        head_l.addWidget(icon)
        label = QLabel(title)
        label.setStyleSheet(f"color: {C.TEXT}; font-size: {FONT_HEADER}px; font-weight: 600;")
        head_l.addWidget(label)
        head_l.addStretch(1)
        lay.addWidget(head)

        return card, lay

    # -- resource centre -------------------------------------------------- #

    def _build_resource_card(self) -> QFrame:
        card, lay = self._dash_card(tr("Resource centre"), "performance")

        self._gauge_pod = RingGauge(tr("Pod"), C.GREEN)
        self._gauge_ram = RingGauge(tr("RAM"), C.BLUE)
        self._gauge_cpu = RingGauge(tr("CPU"), C.MAUVE)

        gauges = QHBoxLayout()
        gauges.setSpacing(SPACE_L)
        for g in (self._gauge_pod, self._gauge_ram, self._gauge_cpu):
            gauges.addWidget(g, 1)
        lay.addLayout(gauges, 1)

        self._bar_disk = StatBar(tr("Disk C:"), C.PEACH)
        lay.addWidget(self._bar_disk)
        return card

    # -- auto-recovery ---------------------------------------------------- #

    def _build_recovery_card(self) -> QFrame:
        card, lay = self._dash_card(tr("Auto-recovery"), "refresh")

        line = QWidget()
        line_l = QHBoxLayout(line)
        line_l.setContentsMargins(0, 0, 0, 0)
        line_l.setSpacing(SPACE_S)
        self._recovery_icon = QLabel()
        self._recovery_icon.setFixedSize(18, 18)
        line_l.addWidget(self._recovery_icon)
        self._recovery_label = QLabel(tr("Monitoring"))
        self._recovery_label.setWordWrap(True)
        self._recovery_label.setStyleSheet(
            f"color: {C.SUBTEXT1}; font-size: {FONT_BODY}px; font-weight: 500;"
        )
        line_l.addWidget(self._recovery_label, 1)
        lay.addWidget(line)

        hint = QLabel(
            tr("WinPodX watches the pod and repairs an unresponsive RDP session automatically.")
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C.OVERLAY0}; font-size: {FONT_CAPTION}px;")
        lay.addWidget(hint)
        lay.addStretch(1)
        return card

    # -- workspace -------------------------------------------------------- #

    def _build_workspace_card(self) -> QFrame:
        card, lay = self._dash_card(tr("Workspace"), "grid")
        # Tiles flow into a width-derived grid (rebuilt by _populate_workspace,
        # re-flowed on resize) so they wrap onto more rows instead of forcing a
        # horizontal scrollbar / clipping on narrow or fractionally-scaled
        # windows.
        self._workspace_holder = QVBoxLayout()
        self._workspace_holder.setSpacing(SPACE_M)
        self._workspace_holder.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setLayout(self._workspace_holder)
        lay.addWidget(holder)
        return card

    def _workspace_cols(self) -> int:
        """Tile column count derived from the page width so the workspace wraps
        instead of scrolling horizontally on narrow / scaled windows."""
        pages = getattr(self, "pages", None)
        width = pages.width() if pages is not None else 1100
        content = max(300, width - 130)  # page + card margins + scrollbar
        return max(3, min(8, content // 140))  # ~140px per tile (tile + spacing)

    def _workspace_apps(self) -> list[AppInfo]:
        """Pinned apps first, then recent, de-duplicated, capped at 8."""
        by_name = {a.name: a for a in self.apps}
        ordered: list[AppInfo] = []
        seen: set[str] = set()
        for name in (*launcher_state.get_pinned(), *launcher_state.get_recent()):
            app = by_name.get(name)
            if app is not None and name not in seen:
                seen.add(name)
                ordered.append(app)
        return ordered[:8]

    def _populate_workspace(self) -> None:
        """(Re)build the workspace tile grid from current pin/recent state."""
        holder = getattr(self, "_workspace_holder", None)
        if holder is None:
            return
        while holder.count():
            item = holder.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        apps = self._workspace_apps()
        if not apps:
            holder.addWidget(
                make_empty_panel(
                    tr("No pinned or recent apps yet"),
                    tr("Launch an app or pin one from All apps to see it here."),
                )
            )
            self._workspace_cols_cur = self._workspace_cols()
            return

        cols = self._workspace_cols()
        self._workspace_cols_cur = cols
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(SPACE_M)
        grid.setContentsMargins(0, 0, 0, 0)
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        for i, app in enumerate(apps):
            grid.addWidget(
                _AppTile(app, on_launch=self._launch_app, on_menu=self._show_app_menu),
                i // cols,
                i % cols,
            )
        # Pad the final row so tiles keep their natural size + left alignment
        # instead of stretching to fill an underfull last row.
        remainder = len(apps) % cols
        if remainder:
            for j in range(remainder, cols):
                spacer = QWidget()
                spacer.setStyleSheet("background: transparent;")
                grid.addWidget(spacer, len(apps) // cols, j)

        grid_widget = QWidget()
        grid_widget.setLayout(grid)
        holder.addWidget(grid_widget)

    # -- reverse-open ----------------------------------------------------- #

    def _build_reverse_open_card(self) -> QFrame:
        card, lay = self._dash_card(tr("Reverse-open"), "reverse-associations")
        self._reverse_open_check = QCheckBox(tr("Open Linux files in their matching Windows app"))
        self._reverse_open_check.setStyleSheet(CHECKBOX)
        try:
            self._reverse_open_check.setChecked(bool(self.cfg.reverse_open.enabled))
        except AttributeError:
            self._reverse_open_check.setChecked(False)
        self._reverse_open_check.toggled.connect(self._on_reverse_open_toggled)
        lay.addWidget(self._reverse_open_check)

        hint = QLabel(
            tr("Right-click a file on Linux and send it to the Windows app registered for it.")
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {C.OVERLAY0}; font-size: {FONT_CAPTION}px;")
        lay.addWidget(hint)
        return card

    def _on_reverse_open_toggled(self, checked: bool) -> None:
        try:
            self.cfg.reverse_open.enabled = bool(checked)
            self.cfg.save()
        except Exception as e:  # noqa: BLE001 -- never let a toggle crash the GUI
            log.warning("failed to persist reverse-open toggle: %s", e)

    # -- live refresh ----------------------------------------------------- #

    def _refresh_dashboard(self) -> None:
        """Probe pod resources off-thread; results land via ``dashboard_updated``.

        Reuses the GUI's already-tracked ``self._pod_state`` (no redundant, slow
        pod re-probe) and only runs the expensive guest disk probe every ~6th
        tick (~30 s) — disk changes slowly and the guest /exec round-trip is the
        slow part, so hammering it every 5 s made the whole dashboard lag.
        Guarded so an in-flight probe isn't stacked by the timer. Never raises.
        """
        if getattr(self, "_dashboard_refreshing", False):
            return
        self._dashboard_refreshing = True
        self._dashboard_tick = getattr(self, "_dashboard_tick", 0) + 1
        # Guest RAM + disk share one agent round-trip; refresh ~every 10 s (was
        # 30 s) so RAM in particular tracks more closely. CPU stays every tick.
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
                # Marshal onto the GUI thread via the host signal.
                self.dashboard_updated.emit(snap)

        threading.Thread(target=_work, daemon=True).start()

    def _apply_snapshot(self, snap: ResourceSnapshot) -> None:
        """GUI-thread slot: paint the latest snapshot onto the widgets."""
        if getattr(self, "_gauge_pod", None) is None:
            return

        pct, state_key, rec_icon, rec_color = _POD_STATES.get(
            snap.pod_state, _POD_STATES["unknown"]
        )
        self._gauge_pod.set_value(pct, tr(state_key))

        if snap.cpu_pct is None:
            self._gauge_cpu.set_value(None, tr("n/a"))
        else:
            self._gauge_cpu.set_value(snap.cpu_pct, f"{snap.cpu_pct:.0f}%")

        if snap.ram_pct is None:
            # RAM comes from the guest agent on the slow cadence (like disk);
            # keep the last value between probes instead of blanking to "n/a".
            cached_ram = getattr(self, "_last_ram", None)
            if cached_ram is not None:
                self._gauge_ram.set_value(cached_ram, f"{cached_ram:.0f}%")
            else:
                self._gauge_ram.set_value(None, tr("n/a"))
        else:
            self._last_ram = snap.ram_pct
            self._gauge_ram.set_value(snap.ram_pct, f"{snap.ram_pct:.0f}%")

        if snap.disk_pct is None or snap.disk_total_gb is None:
            # Disk is probed on a slower cadence; keep showing the last value
            # between probes instead of blanking back to "n/a".
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

        self._apply_recovery_line(snap.pod_state, rec_icon, rec_color)

    def _apply_recovery_line(self, pod_state: str, icon_name: str, color: str) -> None:
        recovery_text = {
            "running": tr("Protected — monitoring active"),
            "checking": tr("Checking pod health…"),
            "paused": tr("Pod is paused"),
            "stopped": tr("Pod is stopped"),
            "unknown": tr("Status unknown"),
        }.get(pod_state, tr("Status unknown"))
        self._recovery_icon.setPixmap(load_icon(icon_name, color, 18).pixmap(18, 18))
        self._recovery_label.setText(recovery_text)
        self._recovery_label.setStyleSheet(
            f"color: {color}; font-size: {FONT_BODY}px; font-weight: 500;"
        )
