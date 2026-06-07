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
(Phase 3-5) cannot be interrupted mid-call. Because those phases can't
honour Cancel, the dialog DISABLES its Cancel button (with an explanatory
tooltip) for the duration of a non-cancellable phase and re-enables it
only while a cancellable polling phase is active, so the user never gets
the false hope of a "Cancelling..." spinner that never lands.
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

from winpodx.core.i18n import tr
from winpodx.gui._widget_helpers import BusyDialog
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    BTN_PRIMARY,
    BTN_SECONDARY,
    DIALOG,
    FONT_CAPTION,
    FONT_SUBHEAD,
    PLAIN_TEXT,
    SPACE_M,
    SPACE_S,
    C,
)

log = logging.getLogger(__name__)


class _ChecklistIconLabel(QLabel):
    """SVG checklist marker that keeps a lightweight state string for tests."""

    def __init__(self) -> None:
        super().__init__()
        self._state_text = ""

    def set_status_icon(self, state_text: str, icon_name: str, color: str, size: int = 16) -> None:
        self._state_text = state_text
        self.setPixmap(load_icon(icon_name, color, size).pixmap(size, size))

    def clear_status_icon(self) -> None:
        self._state_text = ""
        self.clear()

    def text(self) -> str:
        return self._state_text


# Per-phase poll cadence (seconds) -- short enough that the cancel
# event is honoured promptly, long enough that we don't hammer the
# transport during the long-tail Sysprep wait.
_POLL_CADENCE_SECS = 2.0

# Discovery (phase 4) retry: on a fresh install the agent's /health can flicker
# (TermService / rdprrap reactivation restarts it), so the discovery /exec POST
# can land on a momentarily-closed socket ("Remote end closed connection without
# response"). That's transient — retry a few times before failing the bring-up,
# matching the agent_keepalive + apply-burst transient retries.
_DISCOVERY_MAX_ATTEMPTS = 3
_DISCOVERY_RETRY_SECS = 4.0


def _is_transient_discovery_error(exc: Exception) -> bool:
    """True when a discovery failure is a transient channel hiccup worth a retry.

    Only a genuine guest-side script failure (the script ran and exited non-zero)
    is non-transient — every channel / socket / agent-flicker error is retried.
    """
    return "Discovery script failed (rc=" not in str(exc)


# Stable phase identifiers + their display label / ETA hint / cancellable
# flag. The phase ID is the wire contract between BringUpMixin and
# BringUpProgressDialog -- the label + hint are presentational only.
# Adding a new phase = new entry here + matching ``_phaseN_*`` method on
# BringUpMixin.
#
# Each entry is ``(phase_id, label, eta_hint, cancellable)``:
#   * ``eta_hint`` is a static, honest "usually ~N" note shown next to the
#     row so a long wait reads as normal rather than hung. NOT a live ETA.
#   * ``cancellable`` is False for the blocking apply / discover / sync
#     phases (3-5) that can't honour the cancel event mid-call -- the
#     dialog disables Cancel while those run.
_PHASE_DEFS: tuple[tuple[str, str, str, bool], ...] = (
    ("phase_1_pod", "Pod ready", "usually ~1-2 min", True),
    (
        "phase_2_agent",
        "Agent ready",
        "usually a few min, up to ~30 min on a fresh install",
        True,
    ),
    ("phase_3_fixes", "Apply Windows runtime fixes", "usually ~1-2 min", False),
    ("phase_4_discovery", "App discovery", "usually ~1-2 min", False),
    ("phase_5_refresh", "Reverse-open refresh", "usually ~1-2 min", False),
)


def _phase_index(phase_id: str) -> int:
    """Return the 0-based index of ``phase_id`` in ``_PHASE_DEFS``.

    Returns -1 if the ID is unknown -- callers fall back to leaving
    the checklist untouched rather than crashing on a typo.
    """
    for idx, entry in enumerate(_PHASE_DEFS):
        if entry[0] == phase_id:
            return idx
    return -1


def _phase_label(phase_id: str) -> str:
    idx = _phase_index(phase_id)
    if idx < 0:
        return phase_id
    return _PHASE_DEFS[idx][1]


def _phase_eta_hint(phase_id: str) -> str:
    """Return the static "usually ~N" hint for ``phase_id`` (``""`` if none)."""
    idx = _phase_index(phase_id)
    if idx < 0:
        return ""
    return _PHASE_DEFS[idx][2]


def _phase_cancellable(phase_id: str) -> bool:
    """Whether ``phase_id`` honours the cancel event (unknown -> False)."""
    idx = _phase_index(phase_id)
    if idx < 0:
        return False
    return _PHASE_DEFS[idx][3]


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
        self.setWindowTitle(tr("Setting up Windows"))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(560)
        self.setStyleSheet(DIALOG + PLAIN_TEXT)
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
        layout.setSpacing(SPACE_M)

        # ----- header row (phase progress + label) -----------------------
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(SPACE_S)
        self.header_icon = QLabel()
        self.header_icon.setFixedSize(20, 20)
        self.header_icon.setStyleSheet("background: transparent;")
        self.header_icon.hide()
        header_row.addWidget(self.header_icon)

        self.header = QLabel(tr("Starting..."))
        self.header.setStyleSheet(f"font-size: {FONT_SUBHEAD}px; font-weight: 600;")
        self.header.setWordWrap(True)
        header_row.addWidget(self.header, 1)
        layout.addLayout(header_row)

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

        self._row_widgets: list[tuple[_ChecklistIconLabel, QLabel, QLabel]] = []
        for idx, (_pid, label, eta_hint, _cancellable) in enumerate(_PHASE_DEFS):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(SPACE_S)

            glyph = _ChecklistIconLabel()
            glyph.setFont(self._mono_font)
            glyph.setFixedSize(20, 20)
            glyph.setAlignment(Qt.AlignmentFlag.AlignTop)

            # Name + a static "usually ~N" hint stacked beneath it so a
            # long wait reads as normal rather than hung.
            name_col = QWidget()
            name_layout = QVBoxLayout(name_col)
            name_layout.setContentsMargins(0, 0, 0, 0)
            name_layout.setSpacing(0)

            name = QLabel(f"{idx + 1}. {tr(label)}")
            name.setFont(self._mono_font)

            hint = QLabel(tr(eta_hint) if eta_hint else "")
            hint.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: {FONT_CAPTION}px;")
            name_layout.addWidget(name)
            name_layout.addWidget(hint)

            elapsed = QLabel("")
            elapsed.setFont(self._mono_font)
            elapsed.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
            elapsed.setMinimumWidth(56)

            row_layout.addWidget(glyph)
            row_layout.addWidget(name_col, stretch=1)
            row_layout.addWidget(elapsed)
            layout.addWidget(row)

            self._row_widgets.append((glyph, name, elapsed))

        # ----- pod-log expander -----------------------------------------
        self.pod_log_toggle = QToolButton()
        self.pod_log_toggle.setText(tr("Pod logs (live)"))
        self.pod_log_toggle.setCheckable(True)
        self.pod_log_toggle.setChecked(False)
        self.pod_log_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.pod_log_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.pod_log_toggle.setStyleSheet(
            f"QToolButton {{ color: {C.SUBTEXT1}; border: none; padding: 6px 0; }}"
            f"QToolButton:hover {{ color: {C.TEXT}; }}"
        )
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

        self.elapsed_label = QLabel(tr("Elapsed 0:00"))
        self.elapsed_label.setStyleSheet(f"color: {C.SUBTEXT0};")
        footer_layout.addWidget(self.elapsed_label, stretch=1)

        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.setStyleSheet(BTN_SECONDARY)
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

    def _apply_cancel_state(self, cancellable: bool) -> None:
        """Enable/disable Cancel for the active phase.

        Polling phases honour the cancel event, so Cancel stays live.
        Blocking phases can't be interrupted -- disable the button and
        explain why via a tooltip so the user doesn't expect a response.
        Skipped once the dialog is done (the button is repurposed to Close).
        """
        if self._done:
            return
        if cancellable:
            self.cancel_btn.setEnabled(True)
            self.cancel_btn.setToolTip("")
        else:
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setToolTip(tr("This step can't be interrupted"))

    def _handle_cancel(self) -> None:
        # Cancellation is best-effort: long blocking subprocess calls
        # cannot be interrupted mid-flight. Disable the button and update
        # its label so the user knows the request registered.
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText(tr("Cancelling..."))
        self.cancel_btn.setStyleSheet(BTN_SECONDARY)
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
            self.header.setText(
                tr("Phase {n} / {total}  -  {label}").format(
                    n=idx + 1, total=len(_PHASE_DEFS), label=tr(label)
                )
            )
        else:
            # Legacy label form -- just show it directly.
            self.header.setText(phase_id)
        self.sub_detail.setText(sub_detail)

        if idx < 0:
            return

        # Cancel correctness: only the polling phases (1-2) honour the
        # cancel event. The blocking apply / discover / sync phases (3-5)
        # can't be interrupted mid-call, so disable Cancel while they run
        # rather than leave the user staring at a "Cancelling..." spinner
        # that never lands.
        self._apply_cancel_state(_phase_cancellable(phase_id))

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
            # Make the finished state unmistakable: a ticked header plus a
            # plain-language "you can launch apps now" sub-line, and a
            # determinate full bar so the progress no longer reads as busy.
            self.header.setText(tr("✓ Ready"))
            self.header.setText(self.header.text().removeprefix("✓ "))
            self.header_icon.setPixmap(load_icon("check", C.GREEN, 20).pixmap(20, 20))
            self.header_icon.show()
            self.header.setStyleSheet(
                f"font-size: {FONT_SUBHEAD}px; font-weight: 600; color: {C.GREEN};"
            )
            self.sub_detail.setText(tr("Windows is ready — you can launch apps now."))
            self.bar.setMaximum(1)
            self.bar.setValue(1)
        else:
            self.header_icon.hide()
            self.header.setText(tr("Bring-up did not complete"))
            self.header.setStyleSheet(
                f"font-size: {FONT_SUBHEAD}px; font-weight: 600; color: {C.RED};"
            )
            self.sub_detail.setText(error_msg or tr("(no error message)"))
        self.cancel_btn.setText(tr("Close"))
        self.cancel_btn.setStyleSheet(BTN_PRIMARY)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setToolTip("")
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
        self.elapsed_label.setText(tr("Elapsed {time}").format(time=_format_mmss(elapsed)))
        # Per-row elapsed for the in-flight row.
        if not self._done and self._active_phase_idx >= 0:
            self._refresh_checklist()

    def _refresh_checklist(self) -> None:
        now = self._monotonic()
        for idx, (glyph, name, elapsed) in enumerate(self._row_widgets):
            started = self._phase_started_at.get(idx)
            done = self._phase_done_at.get(idx)
            if done is not None and started is not None:
                glyph.set_status_icon("✓  ", "check", C.GREEN)
                glyph.setStyleSheet(f"color: {C.GREEN};")
                name.setStyleSheet("")
                elapsed.setText(_format_mmss(done - started))
            elif started is not None:
                glyph.set_status_icon(">  ", "play", C.BLUE)
                glyph.setStyleSheet(f"color: {C.BLUE};")
                name.setStyleSheet("font-weight: 500;")
                elapsed.setText(_format_mmss(now - started))
            else:
                glyph.clear_status_icon()
                glyph.setStyleSheet("")
                name.setStyleSheet(f"color: {C.SUBTEXT0};")
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
                tr("(pod log unavailable: dialog has no config reference)")
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
            self.pod_log_view.appendPlainText(tr("(pod tail unavailable: {error})").format(error=e))
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

    # ----- first-run "winpodx setup" progress ----------------------------
    #
    # The first-run Auto / Customize flow (NavigationMixin._run_first_run_
    # setup) runs ``winpodx setup`` on a worker thread and used to surface
    # only bottom-bar log lines, so the window felt frozen for the 5-10 min
    # of ISO download + Sysprep. These helpers front that wait with a
    # BusyDialog so it's obviously working; the streamed logs keep flowing
    # underneath into the bottom bar / Terminal exactly as before.

    def _show_setup_busy_dialog(self) -> None:
        """GUI-thread: show a BusyDialog over the long ``winpodx setup`` run.

        Idempotent -- a second call while one is showing is a no-op. Must be
        called on the GUI thread (the first-run prompt handler already is).
        """
        if getattr(self, "_setup_busy_dialog", None) is not None:
            return
        try:
            dlg = BusyDialog(
                self,
                tr("Setting up Windows"),
                tr("Setting up Windows — this can take 5-10 minutes (ISO download + Sysprep)."),
                eta_hint=tr("You can watch progress in the log bar below."),
            )
        except Exception:  # noqa: BLE001
            log.exception("setup BusyDialog construction failed")
            return
        self._setup_busy_dialog = dlg
        dlg.show()

    def _finish_setup_busy_dialog(self) -> None:
        """Dismiss the setup BusyDialog. Safe to call from any thread.

        The first-run setup work runs on a worker thread; marshal the close
        onto the GUI thread via ``QTimer.singleShot(0, ...)`` so we never
        touch a widget off-thread.
        """
        QTimer.singleShot(0, self._close_setup_busy_dialog)

    def _close_setup_busy_dialog(self) -> None:
        """GUI-thread: actually close + drop the setup BusyDialog reference."""
        dlg = getattr(self, "_setup_busy_dialog", None)
        self._setup_busy_dialog = None
        if dlg is None:
            return
        try:
            dlg.finish()
        except Exception:  # noqa: BLE001
            log.debug("setup BusyDialog finish() raised", exc_info=True)

    # ----- worker --------------------------------------------------------

    def _bringup_worker(self) -> None:
        """Run the 5-phase chain. Always exits via ``bringup_done.emit``.

        0.6.0 item I — DEFERRED, NOT migrated to ``finish_provisioning``.
        ----------------------------------------------------------------
        The roadmap folds this 5th copy of the wait → settle → apply →
        discovery → reverse-open chain into ``core.provisioner.finish_
        provisioning`` so there is one place to fix bugs. The unified helper
        grew an ``on_progress(stage, detail)`` callback specifically so this
        worker can wire its ``bringup_phase`` signal to it. BUT this worker
        has three capabilities the helper does not yet expose, and the task
        constraint is explicit: keep the legacy path rather than guess and
        break it silently. Migrate once the helper grows the missing hooks:

          1. Cooperative cancellation — every phase checks ``_is_cancelled``
             and sleeps via ``_sleep_cancellable`` so the user's Cancel
             button stops the chain promptly. ``finish_provisioning`` has no
             cancel hook; routing through it would make Cancel a no-op until
             the current blocking stage returns.
          2. Per-attempt streaming with custom budgets — phases 1/2 stream
             "Attempt N - ..." lines and use ``cfg.install.wait_ready_stage2
             _secs`` / ``stage3_secs`` budgets, plus a token-readiness check
             (``AgentClient.auth_ready``) the helper's soft settle poll
             doesn't perform.
          3. Distinct per-phase failure surfacing — each phase emits its own
             ``bringup_done(False, "<phase> raised: ...")`` so the dialog
             shows which stage broke.

        TODO(0.6.x): add ``cancel_event`` + richer ``on_progress`` (attempt
        index, budget) to ``finish_provisioning``, then replace this body
        with a single ``finish_provisioning(self.cfg, wait_timeout=...,
        require_agent=True, with_reverse_open=cfg.reverse_open.enabled,
        with_discovery=True, retries=..., on_progress=self._on_provision_
        progress, cancel_event=self._bringup_cancel)`` call and drop the
        phase methods below.
        """
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

    def _dockur_progress(self) -> tuple[str | None, str | None, bool]:
        """Peek at the dockur container log: ``(qemu_error, progress, installing)``.

        * ``qemu_error`` — the latest fatal ``ERROR: qemu-system-...`` line if the
          container is failing to boot (e.g. a rejected ``-device``), else None.
          With dockur's ``restart: unless-stopped`` a bad QEMU arg boot-loops, so
          this lets phase 1 fail fast with the real reason instead of waiting out
          the whole budget.
        * ``progress`` — a short human line (latest ``❯`` status, or
          ``Downloading Windows NN%`` from the wget dot-rows) for the dialog.
        * ``installing`` — True when dockur is actively downloading/installing, so
          phase 1 can extend its budget past the normal pod-ready window.
        """
        import re
        import subprocess

        if self._cfg is None:
            return None, None, False
        backend = getattr(self._cfg.pod, "backend", "podman")
        container = getattr(self._cfg.pod, "container_name", "winpodx-windows")
        try:
            r = subprocess.run(
                [backend, "logs", "--tail", "50", container],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None, None, False
        raw = (r.stdout or "") + (r.stderr or "")
        raw = re.sub(r"\x1b\[[0-9;]*m", "", raw).replace("\r", "\n")
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            return None, None, False

        qemu_error = None
        for ln in reversed(lines):
            if "ERROR: qemu-system" in ln:
                qemu_error = ln.split("ERROR:", 1)[1].strip()
                break

        _MARKERS = ("Downloading", "Extracting", "Building", "Booting", "Install")
        installing = any(any(m in ln for m in _MARKERS) for ln in lines[-12:])

        progress = None
        pct = None
        for ln in reversed(lines):  # latest wget dot-row percentage
            m = re.search(r"\b(\d{1,3})%", ln)
            if m and ("K " in ln or "M " in ln or "%" in ln) and ln[:1].isdigit():
                pct = m.group(1)
                break
        status = None
        for ln in reversed(lines):  # latest dockur ❯ status line
            if ln.startswith("❯") or any(ln.startswith(m) for m in _MARKERS):
                status = ln.lstrip("❯ ").strip()
                break
        if pct is not None and (status is None or "Download" in (status or "")):
            progress = f"Downloading Windows: {pct}%"
        elif status:
            progress = status[:80]
        return qemu_error, progress, installing

    def _phase1_wait_pod_ready(self) -> bool:
        from winpodx.core.pod import PodState, check_rdp_port, pod_status

        base = self.cfg.install.wait_ready_stage2_secs
        # A fresh install (ISO download + Sysprep) runs well past the normal
        # pod-ready window; allow up to the stage-3 fresh-install budget while
        # dockur is actively installing so the dialog doesn't time out mid-setup.
        fresh = max(self.cfg.install.wait_ready_stage3_secs, base)
        self._emit_phase("phase_1_pod", f"Up to {base}s budget")

        import time

        started = time.monotonic()
        attempt = 0
        err_streak = 0
        while True:
            if self._is_cancelled():
                return self._emit_cancelled()
            attempt += 1
            try:
                state = pod_status(self.cfg).state
            except Exception:  # noqa: BLE001
                state = None

            qemu_error, progress, installing = self._dockur_progress()

            # Fail fast on a boot-looping QEMU error (e.g. a rejected -device) —
            # don't wait out the whole budget for a container that can't start.
            if qemu_error:
                err_streak += 1
                if err_streak >= 3:
                    self._emit_done(False, f"Pod failed to boot — QEMU: {qemu_error}")
                    return False
            else:
                err_streak = 0

            if state == PodState.RUNNING:
                try:
                    rdp_ok = check_rdp_port(self.cfg.rdp.ip, self.cfg.rdp.port, timeout=3.0)
                except Exception:  # noqa: BLE001
                    rdp_ok = False
                if rdp_ok:
                    self._emit_phase("phase_1_pod", f"Attempt {attempt} - RDP ready")
                    return True

            state_name = state.value if state is not None else "unknown"
            detail = progress or f"pod state: {state_name}"
            self._emit_phase("phase_1_pod", f"Attempt {attempt} - {detail}")

            # Generous budget while a fresh install is in flight; the normal
            # pod-ready budget otherwise.
            deadline = fresh if installing else base
            if time.monotonic() - started >= deadline:
                self._emit_done(False, f"Pod not ready within {int(deadline)}s ({detail})")
                return False

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
        apps = None
        for attempt in range(1, _DISCOVERY_MAX_ATTEMPTS + 1):
            if self._is_cancelled():
                return self._emit_cancelled()
            try:
                apps = discovery_mod.scan(self.cfg)
                break
            except Exception as exc:  # noqa: BLE001
                fatal = not _is_transient_discovery_error(exc) or attempt == _DISCOVERY_MAX_ATTEMPTS
                if fatal:
                    self._emit_done(False, f"discovery.scan raised: {exc}")
                    return False
                # Transient agent-channel hiccup (e.g. /health flickered mid-scan)
                # — wait for the agent to re-settle and try again.
                self._emit_phase(
                    "phase_4_discovery",
                    f"Attempt {attempt} - agent channel closed mid-scan; retrying...",
                )
                self._sleep_cancellable(_DISCOVERY_RETRY_SECS)
        if apps is None:  # cancelled inside the loop
            return self._emit_cancelled()

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
