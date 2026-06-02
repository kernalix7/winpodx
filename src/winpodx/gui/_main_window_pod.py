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
    info_pod_dot / info_pod_addr / status_banner / banner_icon /
    banner_text / banner_btn / agent_dot / rdp_dot      — built widgets.
    _on_refresh_apps()                                  — defined on AppCrudMixin.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QMessageBox

from winpodx.core.app import AppInfo
from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.core.pod import pod_status
from winpodx.gui._widget_helpers import show_toast
from winpodx.gui.theme import C

log = logging.getLogger(__name__)


class PodStatusMixin:
    """Pod control + transport-status polling. Mix into ``WinpodxWindow``."""

    # Serializes ensure_ready + Popen spawn so concurrent launches don't race.
    _launch_lock = threading.Lock()

    def _launch_app(self, app: AppInfo) -> None:
        # Per-app cooldown debounced via QTimer; released 3s later.
        if app.name in self._recently_launched:
            self.app_launch_failed.emit(tr("Just launched. Please wait a moment."))
            return
        self._recently_launched.add(app.name)
        QTimer.singleShot(3000, lambda n=app.name: self._recently_launched.discard(n))

        self.info_label.setText(tr("Launching {app}...").format(app=app.full_name))
        show_toast(self, tr("Launching {app}…").format(app=app.full_name), kind="info")

        def _do() -> None:
            # Lock guards ensure_ready + launch_app only; dropped before the wait.
            if not self._launch_lock.acquire(blocking=False):
                self.app_launch_failed.emit(tr("Another app is launching, please wait."))
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
                    msg = tr("FreeRDP exited with code {code}").format(code=rc)
                    if stderr:
                        msg += f"\n{stderr}"
                    self.app_launch_failed.emit(msg)
            else:
                self.app_launched.emit(app.full_name)

        threading.Thread(target=_do, daemon=True).start()

    def _on_start_pod(self) -> None:
        self.info_label.setText(tr("Starting pod..."))

        def _do() -> None:
            try:
                from winpodx.core.provisioner import ensure_ready

                ensure_ready()
                self._refresh_pod_status()
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(tr("Pod start failed: {error}").format(error=e))

        threading.Thread(target=_do, daemon=True).start()

    def _on_stop_pod(self) -> None:
        from winpodx.core.process import list_active_sessions

        sessions = list_active_sessions()
        if sessions:
            names = ", ".join(s.app_name for s in sessions)
            reply = QMessageBox.question(
                self,
                tr("Active Sessions"),
                tr("Active sessions: {names}\nStop pod anyway?").format(names=names),
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.info_label.setText(tr("Stopping pod..."))

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
        """Paint the agent + RDP mini dots on the pod chip.

        Colour semantics, by anxiety level:
          green  = reachable;
          orange = agent down but RDP up — host→guest still works via the
                   FreeRDP RemoteApp fallback, so apps launch fine (no panic);
          red    = RDP down — apps genuinely can't launch.
        Reserving red for the launch-breaking case keeps the indicator honest.
        """
        # Cache so the banner (running-but-degraded) can re-derive itself.
        self._last_agent_ok = agent_ok
        self._last_rdp_ok = rdp_ok

        green = C.GREEN
        red = C.RED

        # Agent down while RDP is up is a soft/fallback condition → orange.
        if agent_ok:
            agent_color = green
        elif rdp_ok:
            agent_color = C.PEACH
        else:
            agent_color = red
        self.agent_dot.setStyleSheet(
            f"background: transparent; color: {agent_color}; font-size: 10px; font-weight: bold;"
        )
        if agent_ok:
            tip = (
                tr("Guest agent OK ({version})").format(version=agent_version)
                if agent_version
                else tr("Guest agent OK")
            )
        elif rdp_ok:
            tip = tr("Agent down — using FreeRDP fallback (apps still launch)")
        else:
            tip = tr("Guest agent unreachable — host→guest commands fall back to FreeRDP RemoteApp")
        self.agent_dot.setToolTip(tip)

        self.rdp_dot.setStyleSheet(
            f"background: transparent; color: {green if rdp_ok else red}; "
            f"font-size: 10px; font-weight: bold;"
        )
        self.rdp_dot.setToolTip(
            tr("RDP port 3390 reachable")
            if rdp_ok
            else tr("RDP port 3390 unreachable — apps cannot launch")
        )

        # Re-evaluate the banner: a RUNNING pod with a dead transport must
        # surface as degraded, not silently hide behind the green chip.
        if getattr(self, "_pod_state", "") == "running":
            self._apply_status_banner()

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
        # User-visible label: "checking" is a transient probe, not a stuck
        # state — read it as "probing…" so it doesn't look frozen.
        label = tr("probing…") if state == "checking" else state
        ip_suffix = f" ({ip})" if ip and state == "running" else ""
        display = label + ip_suffix

        self.pod_dot.setStyleSheet(f"background: transparent; color: {color}; font-size: 10px;")
        self.pod_label.setText(display)
        self.pod_label.setStyleSheet(f"background: transparent; color: {color}; font-size: 12px;")

        # Info bar no longer repeats the state word (chip + banner own it);
        # it shows the pod IP instead. Keep the colour dot as a glanceable
        # health cue.
        self.info_pod_dot.setStyleSheet(f"background: transparent; color: {color}; font-size: 8px;")
        self.info_pod_addr.setText(ip if ip and state == "running" else "")

        self.btn_start.setEnabled(state == "stopped")
        self.btn_stop.setEnabled(state == "running")

        self._apply_status_banner()

    def _set_banner(self, icon: str, icon_color: str, text: str, *, restart: bool = False) -> None:
        """Paint the status banner row (icon + text + button label)."""
        self.banner_icon.setText(icon)
        self.banner_icon.setStyleSheet(
            f"background: transparent; color: {icon_color}; font-size: 14px;"
        )
        self.banner_text.setText(text)
        # Same ensure_ready() action either way; only the label differs so a
        # running-but-degraded pod reads as a repair, not a cold start.
        self.banner_btn.setText(tr("Restart") if restart else tr("Start Now"))

    def _apply_status_banner(self) -> None:
        """Derive the banner from pod state + last transport probe.

        The chip and this banner are the authoritative state surfaces.
        When the pod is RUNNING the banner normally hides, but if the
        transport probe says RDP (or the agent) is unreachable we keep it
        visible in a distinct "degraded" form so the user understands why
        launches might stall — with a Restart affordance.
        """
        state = self._pod_state
        if state == "running":
            rdp_ok = getattr(self, "_last_rdp_ok", True)
            agent_ok = getattr(self, "_last_agent_ok", True)
            if not rdp_ok:
                self.status_banner.setVisible(True)
                self._set_banner(
                    "⚠",
                    C.RED,
                    tr("Pod is running but RDP is unreachable — apps can't launch. Try Restart."),
                    restart=True,
                )
            elif not agent_ok:
                self.status_banner.setVisible(True)
                self._set_banner(
                    "⚠",
                    C.PEACH,
                    tr(
                        "Pod is running but the guest agent is unreachable — "
                        "launches use the FreeRDP fallback. Restart to repair."
                    ),
                    restart=True,
                )
            else:
                self.status_banner.setVisible(False)
            return

        self.status_banner.setVisible(True)
        if state == "paused":
            self._set_banner("⏸", C.PEACH, tr("Pod is paused"))
        elif state == "stopped":
            self._set_banner("⚠", C.YELLOW, tr("Pod is not running"))
        elif state == "starting":
            self._set_banner("▶", C.BLUE, tr("Pod is starting..."))
        elif state == "unresponsive":
            self._set_banner(
                "⚠",
                C.PEACH,
                tr("Pod is alive but RDP is unresponsive — auto-recovering, or click Restart"),
                restart=True,
            )
        elif state == "error":
            self._set_banner("✗", C.RED, tr("Pod error"))
        else:
            # "checking" / unknown transient — show a neutral probing row
            # rather than the alarming "not running" copy.
            self._set_banner("…", C.SUBTEXT0, tr("Probing pod state…"))

    @Slot(str)
    def _on_app_launched(self, name: str) -> None:
        self.info_label.setText(tr("{name} launched").format(name=name))
        show_toast(self, tr("{name} launched").format(name=name), kind="success")

    @Slot(str)
    def _on_app_launch_failed(self, error: str) -> None:
        self.info_label.setText(tr("Launch failed: {error}").format(error=error))
        show_toast(self, tr("Launch failed"), kind="error")
        QMessageBox.critical(self, tr("Launch Error"), error)
