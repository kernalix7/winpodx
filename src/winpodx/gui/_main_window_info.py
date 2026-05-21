# SPDX-License-Identifier: MIT
"""Info-tab mixin for ``WinpodxWindow``.

Holds the methods that drive the Info tab: card scaffolding, health-card
rendering, gather_info worker orchestration, and auto-refresh timer
control. Pulled out of ``main_window.py`` to keep that file focused on
overall window orchestration.

Host-class contract (only listed for readers; not enforced):
    cfg: winpodx.core.config.Config
    _info_card_bodies: dict[str, QVBoxLayout]  — populated by _info_card.
    _info_busy / _info_thread / _info_worker / _info_auto_timer
        — managed entirely by this mixin (lazily created).
"""

from __future__ import annotations

from PySide6.QtCore import QThread, QTimer, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.gui._widget_helpers import add_shadow
from winpodx.gui.theme import BTN_GHOST, SCROLL_AREA, SETTINGS_SECTION, C
from winpodx.gui.workers import InfoWorker


class InfoPageMixin:
    """Info-tab behavior. Mix into ``WinpodxWindow``."""

    _HEALTH_BADGE_COLORS: dict[str, str] = {
        "ok": "#a6e3a1",  # Catppuccin GREEN
        "warn": "#f9e2af",  # YELLOW
        "fail": "#f38ba8",  # RED
        "skip": "#9399b2",  # SUBTEXT0
    }

    def _build_info_page(self) -> QWidget:
        """5-section system snapshot: System / Display / Dependencies / Pod / Config.

        Mirrors `winpodx info` via the shared `core.info.gather_info` helper.
        Pod section probes RDP/VNC ports + queries podman inspect, so the
        initial paint is async via QThread and the user can re-run on demand
        with the Refresh button.
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_AREA)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("Info")
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        header.addWidget(title)
        header.addStretch()

        refresh_btn = QPushButton("Refresh Info")
        refresh_btn.setIcon(QIcon.fromTheme("view-refresh"))
        refresh_btn.setStyleSheet(BTN_GHOST)
        refresh_btn.clicked.connect(self._refresh_info)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # Containers for the 5 cards. Initial population goes through
        # _refresh_info which dispatches a worker thread; until that thread
        # returns, each card shows "Loading...".
        self._info_cards: dict[str, QFrame] = {}
        self._info_card_bodies: dict[str, QVBoxLayout] = {}
        # Health goes first so the user lands on live state before the
        # static system snapshot. Each probe renders as `[OK] detail` with
        # a colored badge — matches the `winpodx check` CLI output.
        for key, label in [
            ("health", "Health"),
            ("system", "System"),
            ("display", "Display"),
            ("dependencies", "Dependencies"),
            ("pod", "Pod"),
            ("config", "Config"),
        ]:
            card = self._info_card(label)
            self._info_cards[key] = card
            layout.addWidget(card)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # v0.1.9.1: Defer the first fetch out of __init__. Calling
        # _refresh_info() synchronously here can race with the rest of
        # the main-window construction — the worker thread fires its
        # `done` signal back into a partially-built window and hits the
        # same QMessageBox font-lookup SEGV the Apps refresh path saw.
        QTimer.singleShot(0, self._refresh_info)
        return page

    def _info_card(self, title: str) -> QFrame:
        """Card scaffold with a title bar + an empty body layout we mutate later."""
        card = QFrame()
        card.setObjectName("infoSection")
        card.setStyleSheet(
            SETTINGS_SECTION
            + f"QLabel {{ color: {C.TEXT}; font-size: 13px; background: transparent; }}"
        )
        add_shadow(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(6)

        header = QLabel(title)
        header.setStyleSheet(
            f"background: transparent; color: {C.BLUE}; font-size: 15px; font-weight: bold;"
        )
        layout.addWidget(header)

        accent = QFrame()
        accent.setFixedHeight(1)
        accent.setStyleSheet(f"background: {C.SURFACE1};")
        layout.addWidget(accent)
        layout.addSpacing(8)

        body = QVBoxLayout()
        body.setSpacing(4)
        layout.addLayout(body)

        # Stash the body layout on the frame for later population.
        card.setProperty("info_body", body)
        self._info_card_bodies[title.lower()] = body
        # Initial placeholder
        loading = QLabel("Loading...")
        loading.setStyleSheet(f"color: {C.OVERLAY0};")
        body.addWidget(loading)
        return card

    def _render_health_card(self, probes: list[dict], overall: str) -> None:
        """Render a colored badge + detail row for each probe.

        Each row reads `[STATUS] probe_name — detail (Nms)` with the badge
        coloured by status. The overall verdict is shown as a header line so
        the user gets the gist without reading every row.
        """
        body = self._info_card_bodies.get("health")
        if body is None:
            return
        # Clear existing children.
        while body.count():
            item = body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not probes:
            empty = QLabel("No probes ran (health module unavailable).")
            empty.setStyleSheet(f"color: {C.OVERLAY0};")
            body.addWidget(empty)
            return

        overall_color = self._HEALTH_BADGE_COLORS.get(overall, C.SUBTEXT0)
        verdict = QLabel(f"Overall: {overall.upper() or 'UNKNOWN'}")
        verdict.setStyleSheet(f"color: {overall_color}; font-size: 13px; font-weight: bold;")
        body.addWidget(verdict)
        body.addSpacing(4)

        for p in probes:
            status = p.get("status", "")
            color = self._HEALTH_BADGE_COLORS.get(status, C.SUBTEXT0)
            row = QHBoxLayout()
            badge = QLabel(status.upper())
            badge.setFixedWidth(48)
            badge.setStyleSheet(
                f"color: {color}; font-size: 11px; font-weight: bold; background: transparent;"
            )
            name = QLabel(p.get("name", ""))
            name.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
            name.setFixedWidth(140)
            detail = QLabel(p.get("detail", ""))
            detail.setStyleSheet(f"color: {C.SUBTEXT1}; font-size: 12px;")
            detail.setWordWrap(True)
            duration = QLabel(f"{int(p.get('duration_ms', 0))}ms")
            duration.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 11px;")
            row.addWidget(badge, 0)
            row.addWidget(name, 0)
            row.addWidget(detail, 1)
            row.addWidget(duration, 0)
            holder = QWidget()
            holder.setLayout(row)
            body.addWidget(holder)

    def _set_info_card_rows(self, key: str, rows: list[tuple[str, str]]) -> None:
        """Replace the body of an info card with label/value rows."""
        body = self._info_card_bodies.get(key)
        if body is None:
            return
        # Clear existing children.
        while body.count():
            item = body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for label, value in rows:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 12px;")
            val = QLabel(value)
            val.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
            val.setWordWrap(True)
            row.addWidget(lbl, 0)
            row.addStretch()
            row.addWidget(val, 1)
            holder = QWidget()
            holder.setLayout(row)
            body.addWidget(holder)

    def _refresh_info(self) -> None:
        """Re-run gather_info on a worker thread; populate cards on completion."""
        # Reentrancy guard: ignore rapid re-clicks while a previous worker
        # is still in flight. The previous worker's `done` will land first
        # and then the user can refresh again. Without this guard, a fast
        # double-click leaks a QThread + worker pair and races the
        # _info_card_bodies mutation in _apply_info_snapshot.
        if getattr(self, "_info_busy", False):
            return
        self._info_busy = True

        thread = QThread(self)
        worker = InfoWorker(self.cfg)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._apply_info_snapshot)
        # done/failed both end the worker — chain quit + deleteLater on
        # both worker and thread so neither leaks across refreshes.
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.done.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # Clear the busy flag whichever way the worker finishes.
        worker.done.connect(self._on_info_done)
        worker.failed.connect(self._on_info_done)
        self._info_thread = thread
        self._info_worker = worker
        thread.start()

    @Slot()
    def _on_info_done(self, *_args) -> None:
        """Slot fired when the info worker finishes (success or failure)."""
        self._info_busy = False

    def _apply_info_snapshot(self, info: dict) -> None:
        """Map gather_info output into per-card row pairs."""
        self._render_health_card(info.get("health", []), info.get("health_overall", ""))
        sys_ = info.get("system", {})
        self._set_info_card_rows(
            "system",
            [
                ("winpodx", sys_.get("winpodx", "")),
                ("OEM bundle", sys_.get("oem_bundle", "")),
                ("rdprrap", sys_.get("rdprrap", "")),
                ("Distro", sys_.get("distro", "")),
                ("Kernel", sys_.get("kernel", "")),
            ],
        )
        disp = info.get("display", {})
        self._set_info_card_rows(
            "display",
            [
                ("Session type", disp.get("session_type", "")),
                ("Desktop env", disp.get("desktop_environment", "")),
                ("Wayland FreeRDP", disp.get("wayland_freerdp", "")),
                ("Raw scale", disp.get("raw_scale", "")),
                ("RDP scale", disp.get("rdp_scale", "")),
            ],
        )
        deps_rows = []
        for name, dep in info.get("dependencies", {}).items():
            ok = dep.get("found") == "true"
            path = dep.get("path") or ""
            value = ("OK " + path).strip() if ok else "MISSING"
            deps_rows.append((name, value))
        self._set_info_card_rows("dependencies", deps_rows)

        pod = info.get("pod", {})
        rdp_label = "reachable" if pod.get("rdp_reachable") else "unreachable"
        vnc_label = "reachable" if pod.get("vnc_reachable") else "unreachable"
        pod_rows = [
            ("State", str(pod.get("state", ""))),
        ]
        if pod.get("uptime"):
            pod_rows.append(("Started at", str(pod["uptime"])))
        pod_rows.extend(
            [
                (f"RDP {pod.get('rdp_port', '')}", rdp_label),
                (f"VNC {pod.get('vnc_port', '')}", vnc_label),
                ("Active sessions", str(pod.get("active_sessions", 0))),
            ]
        )
        self._set_info_card_rows("pod", pod_rows)

        conf = info.get("config", {})
        cfg_rows = [
            ("Path", str(conf.get("path", ""))),
            ("Backend", str(conf.get("backend", ""))),
            ("IP", f"{conf.get('ip', '')}:{conf.get('port', '')}"),
            ("User", str(conf.get("user", ""))),
            ("Scale", f"{conf.get('scale', '')}%"),
            ("Idle", f"{conf.get('idle_timeout', 0)}s"),
            ("Max sessions", str(conf.get("max_sessions", 0))),
            ("RAM (GB)", str(conf.get("ram_gb", 0))),
        ]
        warning = conf.get("budget_warning") or ""
        if warning:
            cfg_rows.append(("WARNING", warning))
        self._set_info_card_rows("config", cfg_rows)

    def _start_info_auto_refresh(self) -> None:
        """Begin polling Info-page probes every 30s; runs immediately once."""
        if getattr(self, "_info_auto_timer", None) is None:
            self._info_auto_timer = QTimer(self)
            self._info_auto_timer.timeout.connect(self._refresh_info)
        self._info_auto_timer.start(30000)
        # Kick the first refresh now so the user doesn't sit on stale data.
        self._refresh_info()

    def _stop_info_auto_refresh(self) -> None:
        timer = getattr(self, "_info_auto_timer", None)
        if timer is not None:
            timer.stop()
