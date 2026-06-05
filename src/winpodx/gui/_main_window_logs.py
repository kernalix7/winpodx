# SPDX-License-Identifier: MIT
"""Logs-page mixin for ``WinpodxWindow``.

Holds the methods that drive the Logs tab: appending coloured rows, running
allow-listed shell commands, tailing ``podman logs`` / the winpodx app log,
and the bottom input box. Pulled out of ``main_window.py`` to keep that
file focused on overall window orchestration. The mixin is intentionally
not inheritable on its own — it relies on attributes its host class
configures (``log_output``, ``log_signal``, ``cmd_input``, ``cfg``).

Host-class contract (only listed for readers; not enforced):
    log_output: QTextEdit       — the on-screen log surface.
    log_signal: Signal(str, str) — cross-thread log append channel.
    cmd_input: QLineEdit         — the command entry widget.
    cfg: winpodx.core.config.Config
    _tail_proc / _tail_stop      — managed entirely by this mixin.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.gui._widget_helpers import make_page_header
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    BTN_GHOST,
    BTN_PRIMARY,
    COMBO,
    INPUT,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    SPACE_XXL,
    TERMINAL,
    C,
)

log = logging.getLogger(__name__)


class LogsMixin:
    """Logs-tab behavior. Mix into ``WinpodxWindow``."""

    # Allowlist for the bottom command input — anything outside this set is
    # rejected before exec. Keep narrow; this is a debug surface, not a
    # general shell.
    _ALLOWED_COMMANDS = {
        "podman",
        "docker",
        "winpodx",
        "podman-compose",
        "docker-compose",
        "xfreerdp",
        "xfreerdp3",
        "wlfreerdp",
        "wlfreerdp3",
        "systemctl",
        "journalctl",
        "ss",
        "ip",
        "ping",
    }

    def _log_append(self, text: str, color: str = C.SUBTEXT1) -> None:
        """Append colored text to the log output."""
        import html

        safe = html.escape(text)
        self.log_output.append(f'<span style="color:{color}">{safe}</span>')
        sb = self.log_output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _run_log_cmd(self, cmd: list[str]) -> None:
        """Run command and show output in terminal."""
        import subprocess

        self._log_append(f"$ {' '.join(cmd)}", C.BLUE)

        def _do() -> None:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.stdout.strip():
                    self.log_signal.emit(result.stdout.strip(), C.SUBTEXT1)
                if result.stderr.strip():
                    self.log_signal.emit(result.stderr.strip(), C.YELLOW)
                if result.returncode != 0:
                    self.log_signal.emit(f"Exit code: {result.returncode}", C.RED)
            except subprocess.TimeoutExpired:
                self.log_signal.emit("Command timed out (30s)", C.RED)
            except FileNotFoundError:
                self.log_signal.emit(f"Command not found: {cmd[0]}", C.RED)

        threading.Thread(target=_do, daemon=True).start()

    # v0.2.0.10: live log streaming. The Pod logs button shows the last
    # 100 lines, but for first-install / debug the user wants to watch
    # the container output as Windows downloads / Sysprep / boots, and
    # also see winpodx's own application log (under XDG state) so they
    # can correlate guest events with host actions.
    def _on_follow_pod_log(self) -> None:
        import subprocess

        self._on_stop_tail()
        self._log_append(
            f"$ podman logs -f --tail 50 {self.cfg.pod.container_name} (Stop tail to end)",
            C.BLUE,
        )
        try:
            self._tail_proc = subprocess.Popen(
                [
                    self.cfg.pod.backend,
                    "logs",
                    "-f",
                    "--tail",
                    "50",
                    self.cfg.pod.container_name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as e:
            self._log_append(f"Could not start tail: {e}", C.RED)
            return
        self._tail_stop = threading.Event()
        threading.Thread(target=self._drain_tail, args=(self._tail_proc,), daemon=True).start()

    def _on_tail_app_log(self) -> None:
        from winpodx.utils.paths import config_dir

        log_path = config_dir() / "winpodx.log"
        self._log_append(f"$ tail {log_path}", C.BLUE)
        if not log_path.exists():
            self._log_append(
                "(no app log file yet — WinPodX writes to it after the next CLI / GUI action)",
                C.OVERLAY0,
            )
            return
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self._log_append(f"Could not read app log: {e}", C.RED)
            return
        # Show only the last ~200 lines so we don't drown the view.
        lines = content.splitlines()[-200:]
        for line in lines:
            self._log_append(line, C.SUBTEXT1)

    def _on_follow_app_log(self) -> None:
        import subprocess

        from winpodx.utils.paths import config_dir

        log_path = config_dir() / "winpodx.log"
        self._on_stop_tail()
        self._log_append(f"$ tail -F {log_path} (Stop tail to end)", C.BLUE)
        # Pre-create the file so `tail -F` doesn't loop on FileNotFoundError.
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
        try:
            self._tail_proc = subprocess.Popen(
                ["tail", "-F", "-n", "50", str(log_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as e:
            self._log_append(f"Could not start tail: {e}", C.RED)
            return
        self._tail_stop = threading.Event()
        threading.Thread(target=self._drain_tail, args=(self._tail_proc,), daemon=True).start()

    def _drain_tail(self, proc) -> None:  # type: ignore[no-untyped-def]
        try:
            for line in iter(proc.stdout.readline, ""):
                if self._tail_stop.is_set():
                    break
                line = line.rstrip()
                if line:
                    self.log_signal.emit(line, C.SUBTEXT1)
        except Exception:  # noqa: BLE001
            log.debug("tail drain crashed", exc_info=True)
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def _on_stop_tail(self) -> None:
        proc = getattr(self, "_tail_proc", None)
        stop = getattr(self, "_tail_stop", None)
        if proc is None:
            return
        if stop is not None:
            stop.set()
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self._log_append("(tail stopped)", C.OVERLAY0)
        self._tail_proc = None

    def _on_cmd_enter(self) -> None:
        """Handle command input (allowlist-based)."""
        import shlex

        text = self.cmd_input.text().strip()
        if not text:
            return
        self.cmd_input.clear()

        try:
            cmd = shlex.split(text)
        except ValueError as e:
            self._log_append(f"Parse error: {e}", C.RED)
            return

        if not cmd or cmd[0] not in self._ALLOWED_COMMANDS:
            allowed = ", ".join(sorted(self._ALLOWED_COMMANDS))
            # Explain *why* it was blocked: this is a debug surface, not a
            # general shell, so only a safe allowlist runs.
            self._log_append(
                tr(
                    "Blocked: this is a debug terminal, not a general shell — only a "
                    "safe allowlist runs. Allowed commands: {allowed}"
                ).format(allowed=allowed),
                C.RED,
            )
            return

        self._run_log_cmd(cmd)

    def _build_logs_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(SPACE_XXL, 0, SPACE_XXL, SPACE_XL)
        layout.setSpacing(SPACE_M)

        actions = QWidget()
        actions_l = QHBoxLayout(actions)
        actions_l.setContentsMargins(0, 0, 0, 0)
        actions_l.setSpacing(SPACE_S)

        # Log level dropdown — changes both what gets written to
        # ``~/.config/winpodx/winpodx.log`` (which the "Live (app)"
        # button tails) AND what the running CLI / GUI logger emits.
        # Persists to ``cfg.logging.level`` so subsequent winpodx
        # invocations honour the choice. Default is INFO; DEBUG
        # surfaces the chatty per-tick probe / state logs (useful
        # when triaging an "agent not ready" / "starting" stuck state).
        level_label = QLabel(tr("Log level:"))
        level_label.setStyleSheet(f"background: transparent; color: {C.SUBTEXT0}; font-size: 12px;")
        actions_l.addWidget(level_label)
        self.input_log_level = QComboBox()
        self.input_log_level.setStyleSheet(COMBO)
        self.input_log_level.setFixedWidth(140)
        for value in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "RAW"):
            # RAW = DEBUG + ``podman logs -f`` of the pod container
            # interleaved into this terminal. Useful when the answer
            # is in dockur / QEMU / Windows-side output, not in the
            # winpodx Python logger.
            self.input_log_level.addItem(value, value)
            if value == "RAW":
                # Per-item hover hint so the user knows what RAW adds before
                # selecting it.
                self.input_log_level.setItemData(
                    self.input_log_level.count() - 1,
                    tr(
                        "RAW = DEBUG plus a live tail of the container's logs "
                        "(podman logs -f). Use when triaging boot / QEMU issues."
                    ),
                    Qt.ItemDataRole.ToolTipRole,
                )
        current_level = self.cfg.logging.level
        idx = self.input_log_level.findData(current_level)
        if idx >= 0:
            self.input_log_level.setCurrentIndex(idx)
        self.input_log_level.setToolTip(
            tr(
                "Set the WinPodX logger level. Lower (DEBUG) shows more\n"
                "detail in the log file + this terminal; higher (ERROR)\n"
                "shows only errors. Change persists to winpodx.toml so\n"
                "future CLI / GUI runs honour the choice. Applied live —\n"
                "no WinPodX restart needed."
            )
        )
        self.input_log_level.currentIndexChanged.connect(self._on_log_level_changed)
        actions_l.addWidget(self.input_log_level)

        # Route container name through cfg so renamed pods still work.
        # v0.5.1: dropped the "Live (app)" / "Live (pod)" / "Stop tail"
        # buttons — the always-on tails started at WinpodxWindow.__init__
        # already stream into this terminal + the bottom log bar, so
        # those buttons would be redundant (and prone to fighting with
        # the always-on tails). What's left is one-shot diagnostics.
        container = self.cfg.pod.container_name
        # Non-command tooltips for the buttons that don't shell out a list.
        special_tips = {
            "App log": tr("Show the tail of WinPodX's own log file"),
            "RDP Test": tr("Probe the RDP port (TCP handshake) for the configured guest"),
            "Clear": tr("Clear this terminal view"),
        }
        quick = [
            ("Status", ["podman", "ps", "-a", "--filter", f"name={container}"]),
            ("Pod logs", ["podman", "logs", "--tail", "100", container]),
            ("App log", "tail_app_log"),
            ("Inspect", ["podman", "inspect", container]),
            ("RDP Test", None),
            ("Clear", None),
        ]
        for label, cmd in quick:
            btn = QPushButton(tr(label))
            btn.setStyleSheet(BTN_GHOST)
            # Tooltip states the actual command the button runs, so the user
            # can see (and learn) what each shortcut does.
            if isinstance(cmd, list):
                btn.setToolTip(tr("Runs: {cmd}").format(cmd=" ".join(cmd)))
            elif label in special_tips:
                btn.setToolTip(special_tips[label])
            if label == "Clear":
                btn.clicked.connect(lambda: self.log_output.clear())
            elif label == "RDP Test":
                btn.clicked.connect(self._on_rdp_test)
            elif cmd == "tail_app_log":
                btn.clicked.connect(self._on_tail_app_log)
            else:
                btn.clicked.connect(lambda _, c=cmd: self._run_log_cmd(c))
            actions_l.addWidget(btn)

        layout.addWidget(make_page_header(tr("Terminal"), actions_widget=actions))

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet(TERMINAL)
        # Readable floor derived from the font: keep room for ~40 monospace
        # columns (one character cell each) so a narrow window scrolls the
        # terminal rather than crushing its text. This propagates up to the
        # window's own minimum size, so the window can't shrink past it.
        cell = self.log_output.fontMetrics().horizontalAdvance("0") or 8
        self.log_output.setMinimumWidth(cell * 40)
        # Long log lines scroll horizontally inside the terminal instead of
        # wrapping into a crushed block.
        self.log_output.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.log_output)

        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(SPACE_S)

        prompt = QLabel()
        prompt.setFixedSize(16, 16)
        prompt.setPixmap(load_icon("prompt", C.BLUE, 16).pixmap(16, 16))
        prompt.setStyleSheet(f"background: transparent; color: {C.BLUE};")
        cmd_row.addWidget(prompt)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText(
            tr("Enter command (e.g. podman logs {container})").format(
                container=self.cfg.pod.container_name
            )
        )
        self.cmd_input.setStyleSheet(
            INPUT
            + f"""
            QLineEdit {{
                background: {C.CRUST}; color: {C.TEXT};
                border: 1px solid {C.SURFACE0}; border-radius: 8px;
                padding: 10px 14px;
                font-family: 'JetBrains Mono', 'Fira Code', monospace;
                font-size: 13px;
            }}
            QLineEdit:focus {{ border-color: {C.BLUE}; }}
        """
        )
        self.cmd_input.returnPressed.connect(self._on_cmd_enter)
        cmd_row.addWidget(self.cmd_input)

        run_btn = QPushButton(tr("Run"))
        run_btn.setStyleSheet(BTN_PRIMARY)
        run_btn.clicked.connect(self._on_cmd_enter)
        cmd_row.addWidget(run_btn)

        layout.addLayout(cmd_row)
        return page

    def _on_log_level_changed(self, *_args) -> None:
        """Apply the dropdown's new log level live + persist to config.

        Runs in the GUI thread (combo's ``currentIndexChanged`` signal).
        Reconfigures the existing winpodx logger so the level change
        takes effect immediately — no winpodx restart needed. Persists
        to ``cfg.logging.level`` so future CLI / GUI invocations pick
        the same level.

        ``RAW`` is a special level: the Python logger is set to
        ``DEBUG`` (so all winpodx log calls flow to the file), AND the
        Terminal additionally tails ``podman logs -f`` for the pod
        container so dockur / QEMU / Windows-side messages interleave
        with winpodx's own log lines. Transitions between RAW and any
        other level start / stop the auxiliary pod tail.
        """
        from winpodx.utils.logging import setup_logging

        new_level = self.input_log_level.currentData()
        if not new_level or new_level == self.cfg.logging.level:
            return
        was_raw = self.cfg.logging.is_raw()
        try:
            self.cfg.logging.level = new_level
            self.cfg.logging.__post_init__()  # re-validate / normalise
            self.cfg.save()
        except Exception as exc:  # noqa: BLE001
            self._log_append(f"Could not persist log level: {exc}", C.RED)
            return
        # Re-apply on the live logger (setup_logging knows to update
        # in-place when handlers already exist).
        try:
            setup_logging(level=self.cfg.logging.numeric_level())
        except Exception as exc:  # noqa: BLE001
            self._log_append(f"Could not update logger: {exc}", C.RED)
            return
        self._log_append(f"Log level set to {self.cfg.logging.level}", C.BLUE)

        # Manage the auxiliary pod-log tail based on RAW state transition.
        is_now_raw = self.cfg.logging.is_raw()
        if is_now_raw and not was_raw:
            self._start_raw_pod_tail()
        elif not is_now_raw and was_raw:
            self._stop_raw_pod_tail()

    def _start_raw_pod_tail(self) -> None:
        """Start a parallel ``podman logs -f`` stream into the Terminal.

        Kept entirely separate from the primary ``_tail_proc`` (which is
        the app-log or pod-log tail driven by the quick-buttons). Lives
        in ``_tail_proc_raw`` / ``_tail_stop_raw`` so the user can
        switch between Live (app) / Live (pod) / RAW without stomping
        on each other.

        Failures are non-fatal — RAW just degrades to "DEBUG-only" for
        the winpodx logger when podman isn't installed or the
        container isn't running.
        """
        import subprocess

        if getattr(self, "_tail_proc_raw", None) is not None:
            return  # already running
        self._log_append(
            f"[RAW] $ podman logs -f --tail 20 {self.cfg.pod.container_name}",
            C.BLUE,
        )
        try:
            self._tail_proc_raw = subprocess.Popen(
                [
                    self.cfg.pod.backend,
                    "logs",
                    "-f",
                    "--tail",
                    "20",
                    self.cfg.pod.container_name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as e:
            self._log_append(f"[RAW] pod tail unavailable: {e}", C.YELLOW)
            self._tail_proc_raw = None
            return
        self._tail_stop_raw = threading.Event()
        threading.Thread(
            target=self._drain_raw_pod_tail,
            args=(self._tail_proc_raw,),
            daemon=True,
        ).start()

    def _drain_raw_pod_tail(self, proc) -> None:  # type: ignore[no-untyped-def]
        """Drain the RAW pod-log stream, prefix each line so it's
        distinguishable from winpodx's own log lines."""
        try:
            for line in iter(proc.stdout.readline, ""):
                if (
                    getattr(self, "_tail_stop_raw", None) is not None
                    and self._tail_stop_raw.is_set()
                ):
                    break
                line = line.rstrip()
                if line:
                    self.log_signal.emit(f"[pod] {line}", C.OVERLAY0)
        except Exception:  # noqa: BLE001
            log.debug("RAW pod tail drain crashed", exc_info=True)
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def _stop_raw_pod_tail(self) -> None:
        """Stop the auxiliary pod tail (RAW → non-RAW transition)."""
        proc = getattr(self, "_tail_proc_raw", None)
        stop = getattr(self, "_tail_stop_raw", None)
        if proc is None:
            return
        if stop is not None:
            stop.set()
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self._log_append("[RAW] pod tail stopped", C.OVERLAY0)
        self._tail_proc_raw = None

    def _on_rdp_test(self) -> None:
        self._log_append("$ Testing RDP connection...", C.BLUE)

        def _do() -> None:
            cfg = Config.load()
            from winpodx.core.pod import check_rdp_port

            ok = check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=5)
            if ok:
                self.log_signal.emit(f"RDP OK: {cfg.rdp.ip}:{cfg.rdp.port}", C.GREEN)
            else:
                self.log_signal.emit(f"RDP FAIL: {cfg.rdp.ip}:{cfg.rdp.port}", C.RED)

        threading.Thread(target=_do, daemon=True).start()
