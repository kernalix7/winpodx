# SPDX-License-Identifier: MIT
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

The dialog is the user's primary feedback channel during the long
Phase 2 wait (up to 1800s of fresh-ISO download + Sysprep, plus
multi-reboot stabilisation). To make that wait feel non-frozen the
dialog renders:

* A 5-row phase checklist with per-row glyph (waiting / in-progress /
  done) and per-row elapsed timer.
* The current phase's attempt counter (Phase 1/2 polling iterations
  surface the probe outcome verbatim so the user sees "connection
  refused" vs "agent unhealthy: <reason>" vs "ok-but-token-not-ready").
* An expandable live tail of the dockur container's ``podman logs -f``
  stream so the user can watch Windows actually doing things during
  Sysprep, even on log levels below RAW.
* A wall-clock Elapsed counter at the bottom.

The pod-log expander reuses ``WinpodxWindow.log_signal``'s ``[pod]``
fan-out (PR #191) for any RAW-level lines already flowing, and ALSO
spawns its own short-lived ``podman logs -f`` subscription bounded to
the dialog's lifetime so the live tail is visible regardless of
``cfg.logging.level``. The dialog's tail is torn down on accept /
reject so we never leak a daemon thread or container handle.

Host-class contract (informal):
    cfg: winpodx.core.config.Config
    log_signal: Signal(str, str)          - winpodx logger fan-out
    bringup_phase: Signal(str, str)       - (phase_id, sub_detail)
    bringup_done: Signal(bool, str)       - (success, error_message)
    bringup_started: Signal()             - cross-thread dialog kickoff
    _bringup_cancel: threading.Event      - lazily created here

``bringup_phase`` carries a stable phase-ID slug as the first argument
(``phase_1_pod`` / ``phase_2_agent`` / ``phase_3_fixes`` /
``phase_4_discovery`` / ``phase_5_refresh``) so the dialog can route
the emission to the correct checklist row independent of the
human-readable copy. The previous (phase_label, sub_detail) contract
is preserved on the wire: the dialog looks the ID up in
``_PHASE_DEFS`` to find both the row index and the display label.

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
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)


# Per-phase poll cadence (seconds) -- short enough that the cancel
# event is honoured promptly, long enough that we don't hammer the
# transport during the long-tail Sysprep wait.
_POLL_CADENCE_SECS = 2.0


# Stable phase identifiers + their display labels. The phase ID is the
# wire contract between BringUpMixin and BringUpProgressDialog -- the
# label is presentational only. Adding a new phase = new entry here +
# matching ``_phaseN_*`` method on BringUpMixin.
_PHASE_DEFS: tuple[tuple[str, str], ...] = (
    ("phase_1_pod", "Pod ready"),
    ("phase_2_agent", "Agent ready"),
    ("phase_3_fixes", "Apply Windows runtime fixes"),
    ("phase_4_discovery", "App discovery"),
    ("phase_5_refresh", "Reverse-open refresh"),
)


def _phase_index(phase_id: str) -> int:
    """Return the 0-based index of ``phase_id`` in ``_PHASE_DEFS``.

    Returns -1 if the ID is unknown -- callers fall back to leaving
    the checklist untouched rather than crashing on a typo.
    """
    for idx, (pid, _label) in enumerate(_PHASE_DEFS):
        if pid == phase_id:
            return idx
    return -1


def _phase_label(phase_id: str) -> str:
    idx = _phase_index(phase_id)
    if idx < 0:
        return phase_id
    return _PHASE_DEFS[idx][1]


def _format_mmss(secs: float) -> str:
    """Render an elapsed-seconds value as ``M:SS`` (no leading zero on min)."""
    total = max(0, int(secs))
    return f"{total // 60}:{total % 60:02d}"


class BringUpProgressDialog(QDialog):
    """Modal-ish progress dialog driven by ``bringup_phase`` / ``bringup_done``.

    Construction MUST happen on the GUI thread. The dialog connects to
    the host window's two new signals; the host owns the worker.
    """

    def __init__(self, parent, *, on_cancel, cfg=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Setting up Windows")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(560)
        self._on_cancel = on_cancel
        self._cfg = cfg

        # State for the checklist + elapsed math. ``_phase_started_at``
        # is monotonic-seconds keyed by phase index; ``_phase_done_at``
        # is the freeze-frame value once the row is ticked off.
        import time

        self._monotonic = time.monotonic
        self._dialog_started_at = self._monotonic()
        self._active_phase_idx: int = -1
        self._phase_started_at: dict[int, float] = {}
        self._phase_done_at: dict[int, float] = {}
        self._done = False

        # Pod-log subscription (lazy; started when the user expands the
        # log section). ``None`` while collapsed or after teardown.
        self._pod_tail_proc = None
        self._pod_tail_stop: Optional[threading.Event] = None
        # Auto-scroll follow flag -- flips off when the user scrolls
        # upwards so they can read prior lines without being yanked back.
        self._pod_tail_follow = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        # ----- header row (phase progress + label) -----------------------
        self.header = QLabel("Starting...")
        self.header.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        # Sub-detail: attempt counters / probe outcomes go here. Kept on
        # its own line so the header stays scannable.
        self.sub_detail = QLabel("")
        self.sub_detail.setWordWrap(True)
        layout.addWidget(self.sub_detail)

        self.bar = QProgressBar()
        # Indeterminate -- min == max == 0.
        self.bar.setMinimum(0)
        self.bar.setMaximum(0)
        layout.addWidget(self.bar)

        # ----- phase checklist ------------------------------------------
        self._mono_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)

        self._row_widgets: list[tuple[QLabel, QLabel, QLabel]] = []
        for idx, (_pid, label) in enumerate(_PHASE_DEFS):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            glyph = QLabel("   ")
            glyph.setFont(self._mono_font)
            glyph.setFixedWidth(20)

            name = QLabel(f"{idx + 1}. {label}")
            name.setFont(self._mono_font)

            elapsed = QLabel("")
            elapsed.setFont(self._mono_font)
            elapsed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            elapsed.setMinimumWidth(56)

            row_layout.addWidget(glyph)
            row_layout.addWidget(name, stretch=1)
            row_layout.addWidget(elapsed)
            layout.addWidget(row)

            self._row_widgets.append((glyph, name, elapsed))

        # ----- pod-log expander -----------------------------------------
        self.pod_log_toggle = QToolButton()
        self.pod_log_toggle.setText("Pod logs (live)")
        self.pod_log_toggle.setCheckable(True)
        self.pod_log_toggle.setChecked(False)
        self.pod_log_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.pod_log_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.pod_log_toggle.setStyleSheet("QToolButton { border: none; padding: 4px 0; }")
        self.pod_log_toggle.toggled.connect(self._on_pod_log_toggled)
        layout.addWidget(self.pod_log_toggle)

        self.pod_log_view = QPlainTextEdit()
        self.pod_log_view.setReadOnly(True)
        self.pod_log_view.setFont(self._mono_font)
        self.pod_log_view.setMaximumBlockCount(500)
        self.pod_log_view.setVisible(False)
        # Cap height so the dialog doesn't grow unbounded.
        self.pod_log_view.setFixedHeight(210)
        layout.addWidget(self.pod_log_view)

        # Track user-scroll so auto-follow yields when the user goes up.
        sb = self.pod_log_view.verticalScrollBar()
        if sb is not None:
            sb.valueChanged.connect(self._on_pod_log_scroll)

        # ----- footer: elapsed + cancel ---------------------------------
        footer = QWidget()
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)

        self.elapsed_label = QLabel("Elapsed 0:00")
        self.elapsed_label.setStyleSheet("color: #888;")
        footer_layout.addWidget(self.elapsed_label, stretch=1)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._handle_cancel)
        footer_layout.addWidget(self.cancel_btn)

        layout.addWidget(footer)

        # ----- timers ---------------------------------------------------
        # 1-second ticker drives both the wall-clock Elapsed label and
        # the per-row elapsed for the in-progress phase. Stops on
        # accept / reject so a closed dialog doesn't keep emitting.
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start()

    # ----- cancel ---------------------------------------------------------

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

    def on_phase(self, phase_id: str, sub_detail: str) -> None:
        """Receive a phase emission and update header + checklist.

        ``phase_id`` is the stable slug from ``_PHASE_DEFS``; legacy
        callers that emit a human label fall through the
        ``_phase_index`` lookup unchanged (-1 = unknown, ignored for
        checklist routing but still surfaced in the header).
        """
        idx = _phase_index(phase_id)
        label = _phase_label(phase_id)

        # Header: "Phase N / 5 . Label"
        if idx >= 0:
            self.header.setText(f"Phase {idx + 1} / {len(_PHASE_DEFS)}  -  {label}")
        else:
            # Legacy label form -- just show it directly.
            self.header.setText(phase_id)
        self.sub_detail.setText(sub_detail)

        if idx < 0:
            return

        now = self._monotonic()
        # Close out all previously-started rows up to (but not
        # including) the new active row. This makes phase transitions
        # cascade tick marks even if the worker skipped an intermediate
        # emission (defense in depth -- shouldn't happen on the happy
        # path).
        if idx != self._active_phase_idx:
            for prev_idx in range(idx):
                if prev_idx not in self._phase_done_at and prev_idx in self._phase_started_at:
                    self._phase_done_at[prev_idx] = now
            # Start the new row if it isn't already started.
            if idx not in self._phase_started_at:
                self._phase_started_at[idx] = now
            self._active_phase_idx = idx

        self._refresh_checklist()

    def on_done(self, success: bool, error_msg: str) -> None:
        # Brief final state, then close. We don't auto-close on success
        # vs failure differently -- the bottom log bar / Terminal carries
        # the per-line history for users who want it.
        self._done = True
        now = self._monotonic()
        # Tick all started rows that haven't been closed yet so the
        # final frame shows a clean checklist.
        if success:
            for idx in range(len(_PHASE_DEFS)):
                if idx in self._phase_started_at and idx not in self._phase_done_at:
                    self._phase_done_at[idx] = now
        else:
            # On failure, freeze the active row at its current elapsed
            # but don't tick later rows.
            if self._active_phase_idx >= 0:
                self._phase_done_at[self._active_phase_idx] = now

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

        self._refresh_checklist()
        self._stop_tick_timer()

    # ----- pod-log fan-out (called by host) ------------------------------

    def append_pod_log_line(self, line: str) -> None:
        """Receive one pod-log line and append it to the live view.

        Filtering / prefixing is the caller's responsibility; this
        method takes the line verbatim.
        """
        if not line:
            return
        self.pod_log_view.appendPlainText(line)
        if self._pod_tail_follow:
            sb = self.pod_log_view.verticalScrollBar()
            if sb is not None:
                sb.setValue(sb.maximum())

    # ----- timer + checklist refresh -------------------------------------

    def _on_tick(self) -> None:
        # Wall-clock elapsed label.
        elapsed = self._monotonic() - self._dialog_started_at
        self.elapsed_label.setText(f"Elapsed {_format_mmss(elapsed)}")
        # Per-row elapsed for the in-flight row.
        if not self._done and self._active_phase_idx >= 0:
            self._refresh_checklist()

    def _refresh_checklist(self) -> None:
        now = self._monotonic()
        for idx, (glyph, name, elapsed) in enumerate(self._row_widgets):
            started = self._phase_started_at.get(idx)
            done = self._phase_done_at.get(idx)
            if done is not None and started is not None:
                glyph.setText("v  ")
                name.setStyleSheet("")
                elapsed.setText(_format_mmss(done - started))
            elif started is not None:
                glyph.setText(">  ")
                name.setStyleSheet("font-weight: bold;")
                elapsed.setText(_format_mmss(now - started))
            else:
                glyph.setText("   ")
                name.setStyleSheet("color: #888;")
                elapsed.setText("")

    def _stop_tick_timer(self) -> None:
        try:
            self._tick_timer.stop()
        except Exception:  # noqa: BLE001
            pass

    # ----- pod log subscription ------------------------------------------

    def _on_pod_log_toggled(self, checked: bool) -> None:
        self.pod_log_view.setVisible(checked)
        if checked:
            self.pod_log_toggle.setArrowType(Qt.ArrowType.DownArrow)
            self._start_pod_tail()
        else:
            self.pod_log_toggle.setArrowType(Qt.ArrowType.RightArrow)
            self._stop_pod_tail()

    def _on_pod_log_scroll(self, value: int) -> None:
        sb = self.pod_log_view.verticalScrollBar()
        if sb is None:
            return
        # Within 2px of the bottom = still following.
        self._pod_tail_follow = value >= sb.maximum() - 2

    def _start_pod_tail(self) -> None:
        """Spawn a ``podman logs -f`` subprocess for the dialog's lifetime.

        Best-effort: if podman / docker isn't installed or the
        container isn't up yet, log into the view itself and bail.
        """
        if self._pod_tail_proc is not None:
            return  # already running
        if self._cfg is None:
            self.pod_log_view.appendPlainText(
                "(pod log unavailable: dialog has no config reference)"
            )
            return

        import subprocess

        backend = getattr(self._cfg.pod, "backend", "podman")
        container = getattr(self._cfg.pod, "container_name", "winpodx-windows")
        self.pod_log_view.appendPlainText(f"$ {backend} logs -f --tail 20 {container}")
        try:
            self._pod_tail_proc = subprocess.Popen(
                [backend, "logs", "-f", "--tail", "20", container],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as e:
            self.pod_log_view.appendPlainText(f"(pod tail unavailable: {e})")
            self._pod_tail_proc = None
            return

        self._pod_tail_stop = threading.Event()
        threading.Thread(
            target=self._drain_pod_tail,
            args=(self._pod_tail_proc, self._pod_tail_stop),
            name="winpodx-bringup-podlog",
            daemon=True,
        ).start()

    def _drain_pod_tail(self, proc, stop: threading.Event) -> None:
        """Background drain: read each line and marshal to GUI thread."""
        try:
            for line in iter(proc.stdout.readline, ""):
                if stop.is_set():
                    break
                line = line.rstrip()
                if not line:
                    continue
                # Marshal to GUI thread via QTimer.singleShot(0, ...).
                try:
                    QTimer.singleShot(0, lambda ln=line: self._safe_append_pod_line(f"[pod] {ln}"))
                except Exception:  # noqa: BLE001
                    # If the dialog was destroyed mid-flight the lambda
                    # closure can fire on a dead widget; ignore.
                    pass
        except Exception:  # noqa: BLE001
            log.debug("bring-up pod tail drain crashed", exc_info=True)

    def _safe_append_pod_line(self, line: str) -> None:
        # Guard against post-destruction emissions.
        try:
            self.append_pod_log_line(line)
        except RuntimeError:
            # Widget already deleted -- normal during dialog close.
            pass

    def _stop_pod_tail(self) -> None:
        stop = self._pod_tail_stop
        proc = self._pod_tail_proc
        self._pod_tail_proc = None
        self._pod_tail_stop = None
        if stop is not None:
            stop.set()
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # ----- cleanup on close ----------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override
        self._stop_tick_timer()
        self._stop_pod_tail()
        super().closeEvent(event)

    def accept(self) -> None:  # type: ignore[override]
        self._stop_tick_timer()
        self._stop_pod_tail()
        super().accept()

    def reject(self) -> None:  # type: ignore[override]
        self._stop_tick_timer()
        self._stop_pod_tail()
        super().reject()


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
            dlg = BringUpProgressDialog(self, on_cancel=self._cancel_bringup, cfg=self.cfg)
        except Exception:  # noqa: BLE001
            log.exception("BringUpProgressDialog construction failed")
            return
        # Connect signals -- both fire on the GUI thread because the
        # worker emits cross-thread (Qt queues them automatically).
        self.bringup_phase.connect(dlg.on_phase)
        self.bringup_done.connect(dlg.on_done)
        # RAW-level pod tail (#191) also flows through log_signal with
        # a ``[pod]`` prefix. Route those into the dialog's log view so
        # users who already have RAW on don't see duplicated entries
        # from this dialog's own short-lived tail. The dialog's own
        # tail dedupes by prefixing ``[pod]`` identically -- duplicates
        # are acceptable here because the dialog's tail only runs while
        # the expander is open.
        self.log_signal.connect(dlg.append_pod_log_line)
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
        self._emit_phase("phase_1_pod", f"Up to {deadline}s budget")

        import time

        started = time.monotonic()
        attempt = 0
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

            attempt += 1
            try:
                status = pod_status(self.cfg)
                state = status.state
            except Exception as e:  # noqa: BLE001
                state = None
                self._emit_phase(
                    "phase_1_pod",
                    f"Attempt {attempt} - pod_status probe failed: {e}",
                )
            if state == PodState.RUNNING:
                try:
                    rdp_ok = check_rdp_port(self.cfg.rdp.ip, self.cfg.rdp.port, timeout=3.0)
                except Exception:  # noqa: BLE001
                    rdp_ok = False
                if rdp_ok:
                    self._emit_phase(
                        "phase_1_pod",
                        f"Attempt {attempt} - RDP port {self.cfg.rdp.port}: ready",
                    )
                    return True
                self._emit_phase(
                    "phase_1_pod",
                    f"Attempt {attempt} - RDP port {self.cfg.rdp.port}: probing...",
                )
            else:
                state_name = state.value if state is not None else "unknown"
                self._emit_phase(
                    "phase_1_pod",
                    f"Attempt {attempt} - pod state: {state_name}",
                )

            self._sleep_cancellable(_POLL_CADENCE_SECS)

    # ----- phase 2: wait agent settle ------------------------------------

    def _phase2_wait_agent_settle(self) -> bool:
        from winpodx.core.agent import AgentClient

        deadline = self.cfg.install.wait_ready_stage3_secs
        self._emit_phase(
            "phase_2_agent",
            f"Up to {deadline}s budget (fresh-ISO download + Sysprep)",
        )

        import time

        client = AgentClient(self.cfg)
        started = time.monotonic()
        attempt = 0
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

            attempt += 1
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
                    "phase_2_agent",
                    f"Attempt {attempt} - {health_detail}; {token_detail}",
                )
                return True

            self._emit_phase(
                "phase_2_agent",
                f"Attempt {attempt} - {health_detail}; {token_detail or 'awaiting /health'}",
            )
            self._sleep_cancellable(_POLL_CADENCE_SECS)

    # ----- phase 3: apply windows-side fixes -----------------------------

    def _phase3_apply_windows_fixes(self) -> bool:
        from winpodx.core import provisioner

        if self._is_cancelled():
            return self._emit_cancelled()

        self._emit_phase(
            "phase_3_fixes",
            "rdprrap multi-session activation + RDP timeouts + OEM baseline",
        )
        try:
            results = provisioner.apply_windows_runtime_fixes(self.cfg)
        except Exception as exc:  # noqa: BLE001
            self._emit_done(False, f"apply_windows_runtime_fixes raised: {exc}")
            return False

        for name, outcome in results.items():
            self._emit_phase("phase_3_fixes", f"{name}: {outcome}")

        # Any "failed:" entry is non-fatal -- the chain continues. The
        # detail line already logged the failure, and discovery / reverse
        # -open can still complete with a partially-applied fix set.
        return True

    # ----- phase 4: discovery refresh ------------------------------------

    def _phase4_discovery_refresh(self) -> bool:
        from winpodx.core import discovery as discovery_mod

        if self._is_cancelled():
            return self._emit_cancelled()

        self._emit_phase("phase_4_discovery", "Scanning guest Start Menu + AppX...")
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
        self._emit_phase("phase_4_discovery", f"persisted {count} app profile(s)")
        return True

    # ----- phase 5: reverse-open sync ------------------------------------

    def _phase5_reverse_open_sync(self) -> bool:
        if self._is_cancelled():
            return self._emit_cancelled()

        if not getattr(self.cfg.reverse_open, "enabled", False):
            self._emit_phase(
                "phase_5_refresh",
                "reverse_open.enabled=false; skipping",
            )
            return True

        self._emit_phase(
            "phase_5_refresh",
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

        self._emit_phase("phase_5_refresh", "manifest pushed")
        return True

    # ----- helpers -------------------------------------------------------

    def _emit_phase(self, phase_id: str, sub_detail: str) -> None:
        """Emit ``bringup_phase`` + log a single line to the bottom bar.

        First argument is the stable phase-ID slug (see ``_PHASE_DEFS``);
        the dialog resolves it to a row index and display label.
        """
        try:
            self.bringup_phase.emit(phase_id, sub_detail)
        except Exception:  # noqa: BLE001
            pass
        try:
            human_label = _phase_label(phase_id)
            self.log_signal.emit(f"bring-up: {human_label} -- {sub_detail}", "info")
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
