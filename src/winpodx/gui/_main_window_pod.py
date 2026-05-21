# SPDX-License-Identifier: MIT
"""Pod-control + transport-status mixin for ``WinpodxWindow``.

Holds the methods that drive pod start/stop, the launch path
(`_launch_app`), and the 15s polling timer that paints the pod-state
chip + transport (agent / RDP) dots. Pulled out of ``main_window.py``
to keep that file focused on overall window orchestration.

Host-class contract (only listed for readers; not enforced):
    cfg: winpodx.core.config.Config
    apps: list[AppInfo]
    _pod_state: str
    _refresh_state: str
    _recently_launched: set[str]
    status_timer: QTimer            — created lazily by _start_status_timer.
    pod_status_updated: Signal(str, str)
    transport_status_updated: Signal(bool, bool, str)
    app_launched: Signal(str)
    app_launch_failed: Signal(str)
    info_label / btn_start / btn_stop / pod_dot / pod_label /
    info_pod_dot / info_pod_state / status_banner / banner_icon /
    banner_text / agent_dot / rdp_dot                   — built widgets.
    _on_refresh_apps()                                  — defined on AppCrudMixin.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QMessageBox

from winpodx.core.app import AppInfo
from winpodx.core.config import Config
from winpodx.core.pod import pod_status
from winpodx.gui.theme import C

log = logging.getLogger(__name__)


class PodStatusMixin:
    """Pod control + transport-status polling. Mix into ``WinpodxWindow``."""

    # Serializes ensure_ready + Popen spawn so concurrent launches don't race.
    _launch_lock = threading.Lock()

    def _launch_app(self, app: AppInfo) -> None:
        # Per-app cooldown debounced via QTimer; released 3s later.
        if app.name in self._recently_launched:
            self.app_launch_failed.emit("Just launched. Please wait a moment.")
            return
        self._recently_launched.add(app.name)
        QTimer.singleShot(3000, lambda n=app.name: self._recently_launched.discard(n))

        self.info_label.setText(f"Launching {app.full_name}...")

        def _do() -> None:
            # Lock guards ensure_ready + launch_app only; dropped before the wait.
            if not self._launch_lock.acquire(blocking=False):
                self.app_launch_failed.emit("Another app is launching, please wait.")
                return
            session = None
            try:
                from winpodx.core.provisioner import ensure_ready
                from winpodx.core.rdp import launch_app

                cfg = ensure_ready()
                session = launch_app(
                    cfg,
                    app.executable,
                    launch_uri=app.launch_uri or None,
                    wm_class_hint=app.wm_class_hint or None,
                    default_args=app.args or None,
                )
            except Exception:  # noqa: BLE001
                import traceback

                self.app_launch_failed.emit(traceback.format_exc()[-800:])
                return
            finally:
                # Drop lock before the 3s observation so other launches aren't gated.
                self._launch_lock.release()

            # Post-spawn wait: catch early FreeRDP crashes (auth, missing host, etc.).
            import time

            time.sleep(3)
            if session.process and session.process.poll() is not None:
                rc = session.process.returncode
                # 0 = normal exit, 128+signal = killed by signal.
                if rc == 0 or rc > 128:
                    self.app_launched.emit(app.full_name)
                else:
                    time.sleep(0.2)  # let reaper drain stderr
                    stderr = session.stderr_tail.decode(errors="replace")[-500:]
                    msg = f"FreeRDP exited with code {rc}"
                    if stderr:
                        msg += f"\n{stderr}"
                    self.app_launch_failed.emit(msg)
            else:
                self.app_launched.emit(app.full_name)

        threading.Thread(target=_do, daemon=True).start()

    def _on_start_pod(self) -> None:
        self.info_label.setText("Starting pod...")

        def _do() -> None:
            try:
                from winpodx.core.provisioner import ensure_ready

                ensure_ready()
                self._refresh_pod_status()
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(f"Pod start failed: {e}")

        threading.Thread(target=_do, daemon=True).start()

    def _on_stop_pod(self) -> None:
        from winpodx.core.process import list_active_sessions

        sessions = list_active_sessions()
        if sessions:
            names = ", ".join(s.app_name for s in sessions)
            reply = QMessageBox.question(
                self,
                "Active Sessions",
                f"Active sessions: {names}\nStop pod anyway?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.info_label.setText("Stopping pod...")

        def _do() -> None:
            from winpodx.core.pod import stop_pod

            cfg = Config.load()
            stop_pod(cfg)
            self._refresh_pod_status()

        threading.Thread(target=_do, daemon=True).start()

    def _start_status_timer(self) -> None:
        self._refresh_pod_status()
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self._refresh_pod_status)
        self.status_timer.start(15000)

    def _refresh_pod_status(self) -> None:
        def _do() -> None:
            try:
                cfg = Config.load()
                s = pod_status(cfg)
                self.pod_status_updated.emit(s.state.value, s.ip)
            except Exception:  # noqa: BLE001
                self.pod_status_updated.emit("error", "")
                self.transport_status_updated.emit(False, False, "")
                return

            # Probe transports in the same worker tick so the chip dots
            # stay synced with the pod state. Both probes are bounded
            # (~2s each); together they finish well inside the 15s timer.
            agent_ok = False
            agent_version = ""
            rdp_ok = False
            try:
                from winpodx.core.agent import AgentClient, AgentError

                client = AgentClient(cfg)
                try:
                    payload = client.health()
                    agent_ok = True
                    agent_version = str(payload.get("version", ""))
                except AgentError:
                    agent_ok = False
            except Exception:  # noqa: BLE001 — never break the timer
                log.debug("agent probe in status_timer failed", exc_info=True)
            try:
                from winpodx.core.pod import check_rdp_port

                rdp_ok = check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0)
            except Exception:  # noqa: BLE001
                log.debug("rdp probe in status_timer failed", exc_info=True)
            self.transport_status_updated.emit(agent_ok, rdp_ok, agent_version)

        threading.Thread(target=_do, daemon=True).start()

    @Slot(bool, bool, str)
    def _on_transport_status(self, agent_ok: bool, rdp_ok: bool, agent_version: str) -> None:
        """Paint the agent + RDP mini dots on the sidebar pod chip."""
        green = C.GREEN
        red = C.RED

        self.agent_dot.setStyleSheet(
            f"background: transparent; color: {green if agent_ok else red}; "
            f"font-size: 10px; font-weight: bold;"
        )
        if agent_ok:
            tip = f"Guest agent OK ({agent_version})" if agent_version else "Guest agent OK"
        else:
            tip = "Guest agent unreachable — host→guest commands fall back to FreeRDP RemoteApp"
        self.agent_dot.setToolTip(tip)

        self.rdp_dot.setStyleSheet(
            f"background: transparent; color: {green if rdp_ok else red}; "
            f"font-size: 10px; font-weight: bold;"
        )
        self.rdp_dot.setToolTip(
            "RDP port 3390 reachable"
            if rdp_ok
            else "RDP port 3390 unreachable — apps cannot launch"
        )

    @Slot(str, str)
    def _on_pod_status(self, state: str, ip: str) -> None:
        # v0.2.0.10: trigger auto-discovery once when pod transitions
        # to running AND the app list is empty. Solves the
        # fresh-install case where install.sh's wait-ready timed out
        # before Windows finished Sysprep — once GUI sees the pod
        # come up, kick off a scan in the background.
        if (
            state == "running"
            and self._pod_state != "running"
            and not self.apps
            and self._refresh_state == "idle"
        ):
            log.info("pod is now running and app list is empty — auto-firing discovery")
            QTimer.singleShot(2000, self._on_refresh_apps)

        self._pod_state = state
        colors = {
            "running": C.GREEN,
            "stopped": C.RED,
            "starting": C.YELLOW,
            "paused": C.PEACH,
            "error": C.RED,
        }
        color = colors.get(state, C.SUBTEXT0)
        ip_suffix = f" ({ip})" if ip and state == "running" else ""
        display = state + ip_suffix

        self.pod_dot.setStyleSheet(f"background: transparent; color: {color}; font-size: 10px;")
        self.pod_label.setText(display)
        self.pod_label.setStyleSheet(f"background: transparent; color: {color}; font-size: 12px;")

        self.info_pod_dot.setStyleSheet(f"background: transparent; color: {color}; font-size: 8px;")
        self.info_pod_state.setText(state)
        self.info_pod_state.setStyleSheet(
            f"background: transparent; color: {color}; font-size: 11px;"
        )

        self.btn_start.setEnabled(state == "stopped")
        self.btn_stop.setEnabled(state == "running")

        self.status_banner.setVisible(state != "running")
        if state == "paused":
            self.banner_icon.setText("⏸")
            self.banner_icon.setStyleSheet(
                f"background: transparent; color: {C.PEACH}; font-size: 14px;"
            )
            self.banner_text.setText("Pod is paused")
        elif state == "stopped":
            self.banner_icon.setText("⚠")
            self.banner_icon.setStyleSheet(
                f"background: transparent; color: {C.YELLOW}; font-size: 14px;"
            )
            self.banner_text.setText("Pod is not running")
        elif state == "starting":
            self.banner_icon.setText("▶")
            self.banner_icon.setStyleSheet(
                f"background: transparent; color: {C.BLUE}; font-size: 14px;"
            )
            self.banner_text.setText("Pod is starting...")
        elif state == "unresponsive":
            self.banner_icon.setText("⚠")
            self.banner_icon.setStyleSheet(
                f"background: transparent; color: {C.PEACH}; font-size: 14px;"
            )
            self.banner_text.setText(
                "Pod is alive but RDP is unresponsive — auto-recovering, or click Restart Pod"
            )
        elif state == "error":
            self.banner_icon.setText("✗")
            self.banner_icon.setStyleSheet(
                f"background: transparent; color: {C.RED}; font-size: 14px;"
            )
            self.banner_text.setText("Pod error")

    @Slot(str)
    def _on_app_launched(self, name: str) -> None:
        self.info_label.setText(f"{name} launched")

    @Slot(str)
    def _on_app_launch_failed(self, error: str) -> None:
        self.info_label.setText(f"Launch failed: {error}")
        QMessageBox.critical(self, "Launch Error", error)
