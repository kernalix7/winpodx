"""Auto bring-up mixin for ``WinpodxWindow`` (v0.5.1).

After ``_save_settings`` triggers a container recreate (CPU / RAM / port /
user / Windows edition change), the freshly-recreated guest is missing:

  1. Booted Windows (different ISO if edition changed -> fresh download)
  2. Agent (.ps1 not up)
  3. rdprrap multi-session apply chain
  4. App discovery
  5. Reverse-open manifest (Linux apps in Windows "Open with...")

Pre-v0.5.1 the user had to run several CLI commands manually. This mixin
chains the five stages on a worker thread, emits granular progress
through two new signals (``bringup_phase`` + ``bringup_done``), and
fronts the progress to the user via :class:`BringUpProgressDialog`.

Host-class contract (informal):
    cfg: winpodx.core.config.Config
    log_signal: Signal(str, str)          - winpodx logger fan-out
    bringup_phase: Signal(str, str)       - (phase_label, sub_detail)
    bringup_done: Signal(bool, str)       - (success, error_message)
    bringup_started: Signal()             - cross-thread dialog kickoff
    _bringup_cancel: threading.Event      - lazily created here

Cancellation is best-effort: polling loops (Phase 1/2) honour the event
within a 2 s cycle, but the blocking apply / discover / sync calls
(Phase 3-5) cannot be interrupted mid-call. The dialog's Cancel button
disables itself with "Cancelling..." text so the user knows the request
landed.
"""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

log = logging.getLogger(__name__)


# Per-phase poll cadence (seconds) -- short enough that the cancel
# event is honoured promptly, long enough that we don't hammer the
# transport during the long-tail Sysprep wait.
_POLL_CADENCE_SECS = 2.0


class BringUpProgressDialog(QDialog):
    """Modal-ish progress dialog driven by ``bringup_phase`` / ``bringup_done``.

    Construction MUST happen on the GUI thread. The dialog connects to
    the host window's two new signals; the host owns the worker.
    """

    def __init__(self, parent, *, on_cancel) -> None:
        super().__init__(parent)
        self.setWindowTitle("Applying configuration change...")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(440)
        self._on_cancel = on_cancel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.header = QLabel("Starting...")
        self.header.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        self.sub_detail = QLabel("")
        self.sub_detail.setWordWrap(True)
        layout.addWidget(self.sub_detail)

        self.bar = QProgressBar()
        # Indeterminate -- min == max == 0.
        self.bar.setMinimum(0)
        self.bar.setMaximum(0)
        layout.addWidget(self.bar)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._handle_cancel)
        layout.addWidget(self.cancel_btn, alignment=Qt.AlignmentFlag.AlignRight)

    def _handle_cancel(self) -> None:
        # Cancellation is best-effort: long blocking subprocess calls
        # cannot be interrupted mid-flight. Disable the button and update
        # its label so the user knows the request registered.
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelling...")
        try:
            self._on_cancel()
        except Exception:  # noqa: BLE001
            log.exception("bring-up cancel callback raised")

    # ----- slots wired by BringUpMixin._wire_progress_dialog -------------

    def on_phase(self, phase_label: str, sub_detail: str) -> None:
        self.header.setText(phase_label)
        self.sub_detail.setText(sub_detail)

    def on_done(self, success: bool, error_msg: str) -> None:
        # Brief final state, then close. We don't auto-close on success
        # vs failure differently -- the bottom log bar / Terminal carries
        # the per-line history for users who want it.
        if success:
            self.header.setText("Done.")
            self.sub_detail.setText("Bring-up complete.")
        else:
            self.header.setText("Bring-up did not complete")
            self.sub_detail.setText(error_msg or "(no error message)")
        self.cancel_btn.setText("Close")
        self.cancel_btn.setEnabled(True)
        try:
            self.cancel_btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.cancel_btn.clicked.connect(self.accept)


class BringUpMixin:
    """Auto bring-up after container recreate.

    Mix into ``WinpodxWindow`` between ``AppCrudMixin`` and ``HeaderMixin``.
    The host class must declare the three signals listed in the module
    docstring.
    """

    # ----- public entry point --------------------------------------------

    def _run_full_bring_up(self) -> None:
        """Kick off the bring-up worker thread.

        Returns immediately. Phase progress is emitted via
        ``bringup_phase`` / ``bringup_done``. The dialog is opened on
        the GUI thread via ``bringup_started`` (so the caller can be
        on any thread).
        """
        # Lazy-allocate the cancel event. New event per run so a stale
        # "cancelled" flag from a prior bring-up can't trip the next.
        self._bringup_cancel = threading.Event()

        # Signal back to the GUI thread that a dialog should open. The
        # caller (``_save_settings`` worker thread) cannot construct
        # widgets directly. The slot is wired once during _setup_signals.
        try:
            self.bringup_started.emit()
        except Exception:  # noqa: BLE001
            # Signal not wired (test harness, etc.) -- continue, the
            # worker still runs without a dialog.
            log.debug("bringup_started.emit() failed; continuing without dialog")

        thread = threading.Thread(
            target=self._bringup_worker,
            name="winpodx-bringup",
            daemon=True,
        )
        thread.start()

    def _open_bringup_dialog(self) -> None:
        """GUI-thread slot: construct + show the progress dialog.

        Connected to ``bringup_started`` so the worker thread can ask
        for the dialog without touching Qt widgets directly.
        """
        try:
            dlg = BringUpProgressDialog(self, on_cancel=self._cancel_bringup)
        except Exception:  # noqa: BLE001
            log.exception("BringUpProgressDialog construction failed")
            return
        # Connect signals -- both fire on the GUI thread because the
        # worker emits cross-thread (Qt queues them automatically).
        self.bringup_phase.connect(dlg.on_phase)
        self.bringup_done.connect(dlg.on_done)
        dlg.show()
        # Track so a second save during an in-flight run doesn't leak.
        self._bringup_dialog = dlg

    def _cancel_bringup(self) -> None:
        """Set the cancel event so the worker exits at the next checkpoint."""
        ev = getattr(self, "_bringup_cancel", None)
        if ev is not None:
            ev.set()

    # ----- worker --------------------------------------------------------

    def _bringup_worker(self) -> None:
        """Run the 5-phase chain. Always exits via ``bringup_done.emit``."""
        try:
            if not self._phase1_wait_pod_ready():
                return
            if not self._phase2_wait_agent_settle():
                return
            if not self._phase3_apply_windows_fixes():
                return
            if not self._phase4_discovery_refresh():
                return
            if not self._phase5_reverse_open_sync():
                return
            self._emit_done(True, "")
        except Exception as exc:  # noqa: BLE001
            log.exception("bring-up worker crashed")
            self._emit_done(False, f"{exc.__class__.__name__}: {exc}")

    # ----- phase 1: wait pod ready ---------------------------------------

    def _phase1_wait_pod_ready(self) -> bool:
        from winpodx.core.pod import PodState, check_rdp_port, pod_status

        deadline = self.cfg.install.wait_ready_stage2_secs
        self._emit_phase("Waiting for Windows boot", f"Up to {deadline}s budget")

        import time

        started = time.monotonic()
        while True:
            if self._is_cancelled():
                return self._emit_cancelled()
            elapsed = time.monotonic() - started
            if elapsed >= deadline:
                self._emit_done(
                    False,
                    f"Pod did not become RUNNING within {deadline}s",
                )
                return False

            try:
                status = pod_status(self.cfg)
                state = status.state
            except Exception as e:  # noqa: BLE001
                state = None
                self._emit_phase(
                    "Waiting for Windows boot",
                    f"pod_status probe failed: {e}",
                )
            if state == PodState.RUNNING:
                try:
                    rdp_ok = check_rdp_port(self.cfg.rdp.ip, self.cfg.rdp.port, timeout=3.0)
                except Exception:  # noqa: BLE001
                    rdp_ok = False
                if rdp_ok:
                    self._emit_phase(
                        "Waiting for Windows boot",
                        f"RDP port {self.cfg.rdp.port}: ready",
                    )
                    return True
                self._emit_phase(
                    "Waiting for Windows boot",
                    f"RDP port {self.cfg.rdp.port}: probing... ({int(elapsed)}s)",
                )
            else:
                state_name = state.value if state is not None else "unknown"
                self._emit_phase(
                    "Waiting for Windows boot",
                    f"pod state: {state_name} ({int(elapsed)}s)",
                )

            self._sleep_cancellable(_POLL_CADENCE_SECS)

    # ----- phase 2: wait agent settle ------------------------------------

    def _phase2_wait_agent_settle(self) -> bool:
        from winpodx.core.agent import AgentClient

        deadline = self.cfg.install.wait_ready_stage3_secs
        self._emit_phase(
            "Waiting for agent + host token",
            f"Up to {deadline}s budget (fresh-ISO download + Sysprep)",
        )

        import time

        client = AgentClient(self.cfg)
        started = time.monotonic()
        while True:
            if self._is_cancelled():
                return self._emit_cancelled()
            elapsed = time.monotonic() - started
            if elapsed >= deadline:
                self._emit_done(
                    False,
                    f"Agent did not settle within {deadline}s",
                )
                return False

            health_ok = False
            health_detail = ""
            try:
                client.health()
                health_ok = True
                health_detail = "/health 200 OK"
            except Exception as e:  # noqa: BLE001
                health_detail = f"/health: {e.__class__.__name__}"

            token_ok = False
            token_detail = ""
            if health_ok:
                ok, msg = client.auth_ready()
                token_ok = ok
                token_detail = "token ready" if ok else f"token: {msg}"

            if health_ok and token_ok:
                self._emit_phase(
                    "Waiting for agent + host token",
                    f"{health_detail}; {token_detail}",
                )
                return True

            self._emit_phase(
                "Waiting for agent + host token",
                f"{health_detail}; {token_detail or 'awaiting /health'} ({int(elapsed)}s)",
            )
            self._sleep_cancellable(_POLL_CADENCE_SECS)

    # ----- phase 3: apply windows-side fixes -----------------------------

    def _phase3_apply_windows_fixes(self) -> bool:
        from winpodx.core import provisioner

        if self._is_cancelled():
            return self._emit_cancelled()

        self._emit_phase(
            "Apply Windows-side fixes",
            "rdprrap multi-session activation + RDP timeouts + OEM baseline",
        )
        try:
            results = provisioner.apply_windows_runtime_fixes(self.cfg)
        except Exception as exc:  # noqa: BLE001
            self._emit_done(False, f"apply_windows_runtime_fixes raised: {exc}")
            return False

        for name, outcome in results.items():
            self._emit_phase("Apply Windows-side fixes", f"{name}: {outcome}")

        # Any "failed:" entry is non-fatal -- the chain continues. The
        # detail line already logged the failure, and discovery / reverse
        # -open can still complete with a partially-applied fix set.
        return True

    # ----- phase 4: discovery refresh ------------------------------------

    def _phase4_discovery_refresh(self) -> bool:
        from winpodx.core import discovery as discovery_mod

        if self._is_cancelled():
            return self._emit_cancelled()

        self._emit_phase("Discover Windows apps", "Scanning guest Start Menu + AppX...")
        try:
            apps = discovery_mod.scan(self.cfg)
        except Exception as exc:  # noqa: BLE001
            self._emit_done(False, f"discovery.scan raised: {exc}")
            return False

        try:
            persisted = discovery_mod.persist_discovered(apps)
        except Exception as exc:  # noqa: BLE001
            self._emit_done(False, f"persist_discovered raised: {exc}")
            return False

        count = len(persisted) if persisted is not None else len(apps)
        self._emit_phase("Discover Windows apps", f"persisted {count} app profile(s)")
        return True

    # ----- phase 5: reverse-open sync ------------------------------------

    def _phase5_reverse_open_sync(self) -> bool:
        if self._is_cancelled():
            return self._emit_cancelled()

        if not getattr(self.cfg.reverse_open, "enabled", False):
            self._emit_phase(
                "Reverse-open sync",
                "reverse_open.enabled=false; skipping",
            )
            return True

        self._emit_phase(
            "Reverse-open sync",
            "Pushing Linux app manifest into the Windows registry...",
        )

        # The host-open refresh handler takes an argparse.Namespace. We
        # build a SimpleNamespace with the same shape so we can invoke
        # it without going through the CLI parser. Mirrors the call
        # shape used by ``reverse_open_panel._run_cli``.
        try:
            from winpodx.cli.host_open import _cmd_refresh
        except Exception as exc:  # noqa: BLE001
            self._emit_done(False, f"host_open import failed: {exc}")
            return False

        args = SimpleNamespace(
            json=False,
            skip_icons=False,
            include_nodisplay=False,
        )
        try:
            _cmd_refresh(args)
        except Exception as exc:  # noqa: BLE001
            self._emit_done(False, f"reverse-open refresh raised: {exc}")
            return False

        self._emit_phase("Reverse-open sync", "manifest pushed")
        return True

    # ----- helpers -------------------------------------------------------

    def _emit_phase(self, phase_label: str, sub_detail: str) -> None:
        """Emit ``bringup_phase`` + log a single line to the bottom bar."""
        try:
            self.bringup_phase.emit(phase_label, sub_detail)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.log_signal.emit(f"bring-up: {phase_label} -- {sub_detail}", "info")
        except Exception:  # noqa: BLE001
            pass

    def _emit_done(self, success: bool, error_msg: str) -> None:
        try:
            self.bringup_done.emit(success, error_msg)
        except Exception:  # noqa: BLE001
            pass
        if success:
            line = "bring-up: complete"
            level = "info"
        else:
            line = f"bring-up: failed ({error_msg})"
            level = "error"
        try:
            self.log_signal.emit(line, level)
        except Exception:  # noqa: BLE001
            pass

    def _emit_cancelled(self) -> bool:
        self._emit_done(False, "cancelled")
        return False

    def _is_cancelled(self) -> bool:
        ev = getattr(self, "_bringup_cancel", None)
        return bool(ev is not None and ev.is_set())

    def _sleep_cancellable(self, secs: float) -> None:
        """Sleep ``secs`` but wake immediately on cancel."""
        ev = getattr(self, "_bringup_cancel", None)
        if ev is None:
            import time

            time.sleep(secs)
            return
        ev.wait(timeout=secs)
