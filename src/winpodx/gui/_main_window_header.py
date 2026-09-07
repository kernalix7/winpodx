# SPDX-License-Identifier: MIT
"""Unmounted header-chrome builder mixin for ``WinpodxWindow``.

The NavigationView pane lives in ``NavPaneMixin``. This mixin keeps the
legacy top strip (hidden, identity-stable pod widgets), the warning
banner, the info bar, and the log ticker.

Host-class contract (only listed for readers; not enforced):
    cfg: winpodx.core.config.Config
    apps: list[AppInfo]
    _switch_page(idx) -> None       — defined on the host class.
    _on_start_pod() / _on_stop_pod()  — defined on PodStatusMixin.
    Widgets created here (pod_dot, pod_label, agent_dot, rdp_dot,
    btn_start, btn_stop when the pane has not already created them,
    banner_icon, banner_text, banner_btn, info_label, info_pod_dot,
    info_pod_addr) are accessed from sibling mixins via ``self``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    BTN_PRIMARY,
    INFO_BAR,
    STATUS_BANNER_WARN,
    C,
)


class HeaderMixin:
    """Builds the (hidden) top strip, status banner, and info bar."""

    def _build_top_strip(self) -> QWidget:
        """Hidden compatibility strip; creates pod chrome only if missing."""
        bar = QWidget()
        bar.setObjectName("topStrip")
        bar.setStyleSheet(theme_mod.TOP_STRIP)
        bar.hide()
        if getattr(self, "pod_dot", None) is None:
            ensure = getattr(self, "_ensure_pod_chrome", None)
            if ensure is not None:
                ensure(bar)
        return bar

    def _build_status_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("statusBanner")
        banner.setStyleSheet(STATUS_BANNER_WARN)

        layout = QHBoxLayout(banner)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(12)

        self.banner_icon = QLabel()
        self.banner_icon.setFixedSize(16, 16)
        self.banner_icon.setPixmap(load_icon("warning", C.YELLOW, 16).pixmap(16, 16))
        self.banner_icon.setStyleSheet(f"background: transparent; color: {C.YELLOW};")
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
        self.info_pod_dot = QLabel()
        self.info_pod_dot.setFixedSize(8, 8)
        self.info_pod_dot.setPixmap(load_icon("dot", C.OVERLAY0, 8).pixmap(8, 8))
        self.info_pod_dot.setStyleSheet(f"background: transparent; color: {C.OVERLAY0};")
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
        self.log_bar_line1 = QLabel("", textFormat=Qt.TextFormat.PlainText)
        self.log_bar_line1.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT1};"
            " font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 10px;"
        )
        self.log_bar_line1.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.log_bar_line1)

        self.log_bar_line2 = QLabel("", textFormat=Qt.TextFormat.PlainText)
        self.log_bar_line2.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0};"
            " font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 10px;"
        )
        self.log_bar_line2.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.log_bar_line2)

        return bar
