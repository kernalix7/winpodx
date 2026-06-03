# SPDX-License-Identifier: MIT
"""Header-chrome builder mixin for ``WinpodxWindow``.

Holds the methods that build the persistent top-of-window chrome:
the top navigation bar (logo + tabs + pod chip + start/stop buttons),
the warning banner shown while the pod is not running, and the slim
info bar (status text + backend + resource summary). Pulled out of
``main_window.py`` to keep that file focused on overall window
orchestration.

Host-class contract (only listed for readers; not enforced):
    cfg: winpodx.core.config.Config
    apps: list[AppInfo]
    _switch_page(idx) -> None       — defined on the host class.
    _on_start_pod() / _on_stop_pod()  — defined on PodStatusMixin.
    Widgets created here (nav_buttons, pod_dot, pod_label, agent_dot,
    rdp_dot, btn_start, btn_stop, banner_icon, banner_text, banner_btn,
    info_label, info_pod_dot, info_pod_addr) are accessed from sibling
    mixins via the shared ``self`` instance.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui.theme import (
    BTN_PRIMARY,
    INFO_BAR,
    POD_CHIP,
    POD_CTRL,
    STATUS_BANNER_WARN,
    TAB_BTN,
    TOP_BAR,
    C,
)


class HeaderMixin:
    """Builds the top bar, status banner, and info bar."""

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setStyleSheet(TOP_BAR)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(0)

        from winpodx.desktop.icons import bundled_data_path

        icon_path = bundled_data_path("winpodx-icon.svg")
        if icon_path is not None:
            renderer = QSvgRenderer(str(icon_path))
            pixmap = QPixmap(QSize(28, 24))
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            logo_icon = QLabel()
            logo_icon.setPixmap(pixmap)
            logo_icon.setStyleSheet("background: transparent;")
            layout.addWidget(logo_icon)
            layout.addSpacing(8)

        logo_text = QLabel("WinPodX")
        logo_text.setStyleSheet(
            f"background: transparent; color: {C.TEXT};"
            " font-size: 16px; font-weight: 600;"
            " letter-spacing: 0px;"
        )
        layout.addWidget(logo_text)
        layout.addSpacing(28)

        tab_container = QWidget()
        tab_container.setStyleSheet(TAB_BTN)
        tabs = QHBoxLayout(tab_container)
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(0)

        self.nav_buttons: list[QPushButton] = []
        for label, idx in [
            ("Apps", 0),
            ("Settings", 1),
            ("Tools", 2),
            ("Terminal", 3),
            ("Info", 4),
            ("License", 5),
            # Devices page is appended last so the nav-position == page-index
            # invariant _switch_page relies on holds (see _main_window_nav).
            ("Devices", 6),
        ]:
            btn = QPushButton(tr(label))
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, i=idx: self._switch_page(i))
            tabs.addWidget(btn)
            self.nav_buttons.append(btn)

        self.nav_buttons[0].setChecked(True)
        layout.addWidget(tab_container)
        layout.addStretch()

        chip = QFrame()
        chip.setObjectName("podChip")
        chip.setStyleSheet(POD_CHIP)
        chip_l = QHBoxLayout(chip)
        chip_l.setContentsMargins(12, 4, 8, 4)
        chip_l.setSpacing(8)

        self.pod_dot = QLabel("●")
        self.pod_dot.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; font-size: 10px;"
        )
        self.pod_dot.setToolTip(tr("Pod state"))
        chip_l.addWidget(self.pod_dot)

        self.pod_label = QLabel(tr("checking"))
        self.pod_label.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; font-size: 12px;"
        )
        chip_l.addWidget(self.pod_label)

        # Mini transport indicators — small colored dots next to the pod
        # chip showing agent + RDP reachability so the user can see at a
        # glance whether host→guest commands will succeed (agent dot) or
        # fall back to FreeRDP RemoteApp (RDP dot). Updated by the same
        # 15s status_timer that drives pod_dot, plus a quick agent probe.
        self.agent_dot = QLabel("A")
        self.agent_dot.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 10px; font-weight: 500;"
        )
        self.agent_dot.setToolTip(tr("Guest agent (HTTP /health) — probing…"))
        chip_l.addWidget(self.agent_dot)

        self.rdp_dot = QLabel("R")
        self.rdp_dot.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 10px; font-weight: 500;"
        )
        self.rdp_dot.setToolTip(tr("RDP port (3390) — probing…"))
        chip_l.addWidget(self.rdp_dot)

        ctrl_w = QWidget()
        ctrl_w.setStyleSheet(POD_CTRL)
        ctrl_l = QHBoxLayout(ctrl_w)
        ctrl_l.setContentsMargins(4, 0, 0, 0)
        ctrl_l.setSpacing(2)

        self.btn_start = QPushButton("▶")
        self.btn_start.setToolTip(tr("Start Pod"))
        self.btn_start.clicked.connect(self._on_start_pod)
        ctrl_l.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■")
        self.btn_stop.setToolTip(tr("Stop Pod"))
        self.btn_stop.clicked.connect(self._on_stop_pod)
        ctrl_l.addWidget(self.btn_stop)

        chip_l.addWidget(ctrl_w)
        layout.addWidget(chip)

        return bar

    def _build_status_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("statusBanner")
        banner.setStyleSheet(STATUS_BANNER_WARN)

        layout = QHBoxLayout(banner)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(12)

        self.banner_icon = QLabel("⚠")
        self.banner_icon.setStyleSheet(
            f"background: transparent; color: {C.YELLOW}; font-size: 14px;"
        )
        layout.addWidget(self.banner_icon)

        self.banner_text = QLabel(tr("Pod is not running"))
        self.banner_text.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; font-size: 12px;"
        )
        layout.addWidget(self.banner_text)
        layout.addStretch()

        # Kept as an instance attribute so the degraded-transport state can
        # relabel it "Restart" (recovery) vs the default "Start Now". The
        # action is the same ensure_ready() path either way.
        self.banner_btn = QPushButton(tr("Start Now"))
        self.banner_btn.setStyleSheet(BTN_PRIMARY)
        self.banner_btn.clicked.connect(self._on_start_pod)
        layout.addWidget(self.banner_btn)

        banner.setVisible(True)
        return banner

    def _build_info_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("infoBar")
        bar.setStyleSheet(INFO_BAR)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(14)

        self.info_label = QLabel(tr("{n} apps available").format(n=len(self.apps)))
        self.info_label.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;"
        )
        layout.addWidget(self.info_label)
        layout.addStretch()

        # The pod *state* word lives authoritatively in the top-bar chip
        # and the status banner; repeating it here was pure noise. Keep
        # only the tiny colour dot (a glanceable health indicator, not a
        # word) and show the pod IP/address instead — complementary info
        # the chip/banner don't surface. _on_pod_status fills it in.
        self.info_pod_dot = QLabel("●")
        self.info_pod_dot.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 8px;"
        )
        layout.addWidget(self.info_pod_dot)

        self.info_pod_addr = QLabel("")
        self.info_pod_addr.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;"
        )
        layout.addWidget(self.info_pod_addr)

        sep = QLabel("│")
        sep.setStyleSheet(f"background: transparent; color: {C.SURFACE1}; font-size: 11px;")
        layout.addWidget(sep)

        backend_lbl = QLabel(f"{self.cfg.pod.backend}")
        backend_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        layout.addWidget(backend_lbl)

        res_lbl = QLabel(f"{self.cfg.pod.cpu_cores} CPU · {self.cfg.pod.ram_gb} GB")
        res_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        layout.addWidget(res_lbl)

        return bar

    def _build_log_bar(self) -> QWidget:
        """Always-visible 2-line log ticker at the very bottom of the window.

        Shows the latest two lines emitted via ``log_signal`` regardless
        of which page is active. The Python ``winpodx`` logger feeds it
        through the always-on ``tail -F winpodx.log`` worker; when
        ``cfg.logging.level == "RAW"`` the parallel ``podman logs -f``
        tail also feeds it, with ``[pod]`` prefix on each line.

        The log level dropdown on the Terminal page controls what gets
        WRITTEN to ``winpodx.log`` (and therefore what reaches this
        bar). RAW additionally enables the pod-log stream.
        """
        bar = QWidget()
        bar.setObjectName("logBar")
        # Terminal-ish background so the bar reads as a log surface and
        # doesn't compete visually with the info_bar above it.
        bar.setStyleSheet(
            f"#logBar {{ background: {C.CRUST}; border-top: 1px solid {C.SURFACE0}; }}"
        )
        bar.setFixedHeight(38)

        layout = QVBoxLayout(bar)
        layout.setContentsMargins(24, 4, 24, 4)
        layout.setSpacing(0)

        # Two lines stacked: most-recent on top, previous below it
        # (one tick of history). The "previous" line is dimmer so the
        # eye snaps to the freshest line first.
        self.log_bar_line1 = QLabel("")
        self.log_bar_line1.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT1};"
            " font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 10px;"
        )
        self.log_bar_line1.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.log_bar_line1)

        self.log_bar_line2 = QLabel("")
        self.log_bar_line2.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0};"
            " font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 10px;"
        )
        self.log_bar_line2.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.log_bar_line2)

        return bar
