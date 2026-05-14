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

from winpodx.gui.theme import C

log = logging.getLogger(__name__)


class LogsMixin:
    """Logs-tab behavior. Mix into ``WinpodxWindow``."""

    # Allowlist for the bottom command input — anything outside this set is
    # rejected before exec. Keep narrow; this is a debug surface, not a
    # general shell.
    _ALLOWED_COMMANDS = {
        "podman",
        "docker",
        "virsh",
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
                "(no app log file yet — winpodx writes to it after the next CLI / GUI action)",
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
            self._log_append(f"Blocked: allowed commands: {allowed}", C.RED)
            return

        self._run_log_cmd(cmd)
