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

from PySide6.QtCore import Qt, QThread, QTimer, Slot
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui._main_window_info_cards import _InfoCardsMixin
from winpodx.gui._main_window_secondary_style import (
    apply_w11_button,
    mount_settings_column,
)
from winpodx.gui.workers import InfoWorker


class InfoPageMixin(_InfoCardsMixin):
    """Info-tab behavior. Mix into ``WinpodxWindow``."""

    # Plain "what to install" hints appended to a MISSING dependency row so
    # the user isn't left guessing. Text-only (no buttons) by design — the
    # exact package name varies per distro, so we point at the upstream
    # rather than shell out a package-manager command.
    _DEP_INSTALL_HINTS: dict[str, str] = {
        "freerdp": "install FreeRDP 3+ (e.g. your distro's 'freerdp3' / 'freerdp' package)",
        "podman": "install Podman 4+ (your distro's 'podman' package) — the default backend",
        "docker": "install Docker Engine if you prefer the docker backend",
        "flatpak": "install 'flatpak' only if you use the Flatpak FreeRDP fallback",
        "kvm": "enable KVM (load the kvm module; add yourself to the 'kvm' group)",
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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(theme_mod.SCROLL_AREA)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = mount_settings_column(content)

        refresh_btn = QPushButton(tr("Refresh Info"))
        apply_w11_button(refresh_btn, theme_mod.BTN_GHOST, role="ghost")
        refresh_btn.setFixedHeight(theme_mod.CONTROL_HEIGHT_W11)
        refresh_btn.clicked.connect(self._refresh_info)
        self._info_refresh_btn = refresh_btn
        register = getattr(self, "_register_page_header", None)
        if callable(register):
            register(
                5,
                tr("Info"),
                tr("A live snapshot of your system, display, dependencies, and pod."),
                actions=refresh_btn,
            )
        else:
            refresh_btn.setParent(page)
            refresh_btn.hide()

        copy_btn = QPushButton(tr("Copy diagnostics"))
        apply_w11_button(copy_btn, theme_mod.BTN_PRIMARY, role="primary")
        copy_btn.clicked.connect(self._copy_diagnostics)
        self._info_copy_btn = copy_btn
        layout.addWidget(self._build_about_device_card(action=copy_btn))

        self._info_cards: dict[str, QFrame] = {}
        self._info_card_bodies: dict[str, QVBoxLayout] = {}
        for key, label in [
            ("health", "Health"),
            ("system", "System"),
            ("display", "Display"),
            ("dependencies", "Dependencies"),
            ("pod", "Pod"),
            ("config", "Config"),
        ]:
            card = self._info_card(label, key)
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
        self._info_page = page
        return page

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

    def _copy_diagnostics(self) -> None:
        """Copy the last snapshot as ``key: value`` lines to the clipboard."""
        snap = getattr(self, "_info_snapshot", None) or {}
        text = "\n".join(f"{key}: {value}" for key, value in snap.items())
        QApplication.clipboard().setText(text)

    def _apply_info_snapshot(self, info: dict) -> None:
        """Map gather_info output into per-card row pairs."""
        self._info_snapshot = info
        self._render_health_card(info.get("health", []), info.get("health_overall", ""))
        sys_ = info.get("system", {})
        version = getattr(self, "_info_version_label", None)
        if version is not None:
            version.setText(str(sys_.get("winpodx", "")))
        self._set_info_card_rows(
            "system",
            [
                ("WinPodX", sys_.get("winpodx", "")),
                (tr("OEM bundle"), sys_.get("oem_bundle", "")),
                ("rdprrap", sys_.get("rdprrap", "")),
                (tr("Distro"), sys_.get("distro", "")),
                (tr("Kernel"), sys_.get("kernel", "")),
            ],
        )
        disp = info.get("display", {})
        self._set_info_card_rows(
            "display",
            [
                (tr("Session type"), disp.get("session_type", "")),
                (tr("Desktop env"), disp.get("desktop_environment", "")),
                (tr("Wayland FreeRDP"), disp.get("wayland_freerdp", "")),
                (tr("Raw scale"), disp.get("raw_scale", "")),
                (tr("RDP scale"), disp.get("rdp_scale", "")),
            ],
        )
        deps_rows = []
        for name, dep in info.get("dependencies", {}).items():
            ok = dep.get("found") == "true"
            path = dep.get("path") or ""
            if ok:
                value = (tr("OK") + " " + path).strip()
            else:
                # Tack on a short, plain "how to install" hint so a MISSING
                # row is actionable without leaving the page.
                hint = self._DEP_INSTALL_HINTS.get(name)
                value = tr("MISSING") + (f" — {tr(hint)}" if hint else "")
            deps_rows.append((name, value))
        self._set_info_card_rows("dependencies", deps_rows)

        pod = info.get("pod", {})
        # "reachable" here means the TCP port accepts a connection — it does
        # NOT mean Windows has finished booting / the RemoteApp service is
        # ready. Spell that out so a user doesn't read "reachable" as "ready".
        rdp_label = tr("port open") if pod.get("rdp_reachable") else tr("port closed")
        vnc_label = tr("port open") if pod.get("vnc_reachable") else tr("port closed")
        pod_rows = [
            (tr("State"), str(pod.get("state", ""))),
        ]
        if pod.get("uptime"):
            pod_rows.append((tr("Started at"), str(pod["uptime"])))
        pod_rows.extend(
            [
                (tr("RDP {port}").format(port=pod.get("rdp_port", "")), rdp_label),
                (tr("VNC {port}").format(port=pod.get("vnc_port", "")), vnc_label),
                (tr("Active sessions"), str(pod.get("active_sessions", 0))),
                (
                    tr("Note"),
                    tr(
                        "Port open ≠ Windows ready — the guest may still be "
                        "booting after the port opens. Pod state flows: "
                        "stopped → running → paused (suspend) → running (resume)."
                    ),
                ),
            ]
        )
        self._set_info_card_rows("pod", pod_rows)

        conf = info.get("config", {})
        cfg_rows = [
            (tr("Path"), str(conf.get("path", ""))),
            (tr("Backend"), str(conf.get("backend", ""))),
            (tr("IP"), f"{conf.get('ip', '')}:{conf.get('port', '')}"),
            (tr("User"), str(conf.get("user", ""))),
            (tr("Scale"), f"{conf.get('scale', '')}%"),
            (tr("Idle"), f"{conf.get('idle_timeout', 0)}s"),
            (tr("Max sessions"), str(conf.get("max_sessions", 0))),
            (tr("RAM (GB)"), str(conf.get("ram_gb", 0))),
        ]
        warning = conf.get("budget_warning") or ""
        if warning:
            # Read-only mirror — the Settings page owns the RAM budget control.
            # Tag it "(see Settings)" so this doesn't read as a second place
            # to fix the same thing.
            cfg_rows.append((tr("WARNING"), warning + " " + tr("(adjust in Settings)")))
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
