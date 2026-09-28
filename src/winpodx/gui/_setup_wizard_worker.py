# SPDX-License-Identifier: MIT
"""Background workers for the setup wizard (handle_setup / pkexec)."""

from __future__ import annotations

import argparse
import copy

from PySide6.QtCore import QObject, Signal

from winpodx.core.config import Config
from winpodx.setup_wizard.host_state import HostState
from winpodx.setup_wizard.pkexec import (
    PkexecAuthDenied,
    PkexecScriptFailed,
    PkexecUnavailable,
)


class SetupWorker(QObject):
    """Run ``handle_setup`` or wipe+reinstall off the GUI thread."""

    finished = Signal(bool, str)
    progress = Signal(str, str)

    def __init__(self, args: argparse.Namespace, *, reinstall: bool) -> None:
        super().__init__()
        self._args = args
        self._reinstall = reinstall

    def _on_progress(self, stage: str, detail: str) -> None:
        # Runs on the worker thread: emitting the signal is the only
        # cross-thread-safe action; never touch a QWidget from here (#SIGSEGV).
        self.progress.emit(stage, detail)

    def run(self) -> None:
        """Blocking install. Emits ``finished(success, error)`` on the worker thread."""
        try:
            if self._reinstall:
                self._run_reinstall()
            else:
                from winpodx.cli.setup_cmd import handle_setup

                handle_setup(self._args, on_progress=self._on_progress)
        except SystemExit as exc:
            code = exc.code
            if code in (0, None):
                self.finished.emit(True, "")
            else:
                self.finished.emit(False, str(code))
            return
        except Exception as exc:  # noqa: BLE001 — boundary: Finish page must see it
            self.finished.emit(False, str(exc))
            return
        self.finished.emit(True, "")

    def _run_reinstall(self) -> None:
        from winpodx.cli.pod import handle_pod
        from winpodx.cli.setup_cmd import apply_setup_presets
        from winpodx.core.config import Config

        baseline = Config.load()
        candidate = copy.deepcopy(baseline)
        apply_setup_presets(candidate, self._args)
        _reject_reinstall_identity_change(baseline, self._args)
        handle_pod(
            argparse.Namespace(pod_command="reset", yes=True, redownload_iso=False),
            on_progress=self._on_progress,
            reset_config=candidate,
            expected_config=baseline,
        )


def _reject_reinstall_identity_change(baseline: Config, args: argparse.Namespace) -> None:
    """Refuse a reinstall that would move backend, storage, or installation media."""
    requested_backend = getattr(args, "backend", None)
    if requested_backend and requested_backend != baseline.pod.backend:
        raise RuntimeError("reinstall cannot change backend")
    requested_storage = getattr(args, "storage_path", None)
    current_storage = baseline.pod.storage_path or ""
    if requested_storage and requested_storage != current_storage:
        raise RuntimeError("reinstall cannot change storage")
    if getattr(args, "win_iso", None):
        raise RuntimeError("reinstall cannot replace installation media")


class PkexecWorker(QObject):
    """Run ``apply_via_pkexec`` off the GUI thread (one polkit prompt)."""

    finished = Signal(object, str)

    def __init__(self, state: HostState) -> None:
        super().__init__()
        self._state = state

    def run(self) -> None:
        """Apply fixable items, then re-detect. ``finished(state, error)``."""
        from winpodx.setup_wizard.host_state import detect_host_state
        from winpodx.setup_wizard.pkexec import apply_via_pkexec

        try:
            apply_via_pkexec(self._state)
        except PkexecUnavailable as exc:
            self.finished.emit(detect_host_state(), str(exc))
            return
        except PkexecAuthDenied as exc:
            self.finished.emit(detect_host_state(), str(exc))
            return
        except PkexecScriptFailed as exc:
            self.finished.emit(detect_host_state(), str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — boundary: page must stay up
            self.finished.emit(detect_host_state(), str(exc))
            return
        self.finished.emit(detect_host_state(), "")
