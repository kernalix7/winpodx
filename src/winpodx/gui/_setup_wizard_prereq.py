# SPDX-License-Identifier: MIT
"""Prerequisites page: live HostState checklist + one pkexec Fix button."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.backend.select import choose_backend
from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.gui import theme
from winpodx.gui._main_window_secondary_style import apply_w11_button
from winpodx.gui._settings_card import make_settings_card, make_settings_group
from winpodx.gui._setup_wizard_model import SetupAnswers, prereq_specs
from winpodx.gui._setup_wizard_worker import PkexecWorker
from winpodx.gui._widget_helpers import make_warning_callout
from winpodx.setup_wizard.host_state import HostState, detect_host_state, inspect_preflight
from winpodx.utils.deps import check_all


class PrerequisitesPage(QWidget):
    """Read-only host preflight; Next requires every blocking check to pass."""

    can_proceed_changed = Signal(bool)
    refreshed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if Config.path().exists():
            self._cfg = Config.load()
        else:
            self._cfg = Config()
            self._cfg.pod.backend = choose_backend(deps=check_all())
        self._state = detect_host_state()
        self._storage_path: Path | None = None
        self._iso_path: str | None = None
        self._revision = 0
        self._thread: QThread | None = None
        self._worker: PkexecWorker | None = None
        self._cards: dict[str, QFrame] = {}
        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._callout_host = QVBoxLayout()
        self._fix_btn = QPushButton(tr("Fix these"))
        self._fix_btn.setObjectName("wizardFixPrereqs")
        self._fix_btn.clicked.connect(self._on_fix)
        self._recheck_btn = QPushButton(tr("Recheck"))
        self._recheck_btn.setObjectName("wizardRecheckPrereqs")
        self._recheck_btn.clicked.connect(self.recheck)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_L)
        group, stack = make_settings_group(tr("Host checks"))
        for spec in prereq_specs():
            card = make_settings_card(self._icon_for(spec.field), spec.title, spec.note)
            self._cards[spec.field] = card
            stack.addWidget(card)
        root.addWidget(group)
        root.addLayout(self._callout_host)
        root.addWidget(self._hint)
        root.addWidget(self._recheck_btn)
        root.addWidget(self._fix_btn)
        root.addStretch(1)
        self._paint(self._state)

    def can_proceed(self) -> bool:
        """True when nothing on the host actually blocks a Windows install.

        Delegates to ``HostState.blocking_failures`` rather than re-deciding
        per row: a passing check can make another one moot. On a host whose
        ``/dev/kvm`` is world-accessible the user is not in the ``kvm`` group
        and never needs to be, and gating on the row alone stranded them on a
        failure no action could clear.
        """
        return self._report.ready

    def bind_answers(self, answers: SetupAnswers) -> None:
        """Inspect the choices under review, not an independently loaded default."""
        self._cfg.pod.backend = answers.backend or self._cfg.pod.backend
        self._cfg.pod.ram_gb = answers.ram_gb
        self._cfg.pod.disk_size = answers.disk_size
        self._storage_path = Path(answers.storage_path) if answers.storage_path else None
        self._iso_path = answers.win_iso or None
        self._revision += 1
        self.recheck()

    def recheck(self) -> None:
        """Probe the current selection. A newer selection discards this result."""
        revision = self._revision
        report = inspect_preflight(
            self._cfg, storage_path=self._storage_path, iso_path=self._iso_path
        )
        if revision != self._revision:
            return
        self._report = report
        self._paint(self._state, probe=False)

    def _restyle(self) -> None:
        apply_w11_button(self._recheck_btn, theme.BTN_SECONDARY, role="secondary")
        apply_w11_button(self._fix_btn, theme.BTN_PRIMARY, role="primary")
        self._hint.setStyleSheet(
            f"background: transparent; color: {theme.C.SUBTEXT1}; "
            f"font-size: {theme.FONT_CAPTION}px;"
        )
        self._paint(self._state, probe=False)

    def _icon_for(self, field: str) -> str:
        if field.startswith("dev_kvm") or field.startswith("kvm_"):
            return "hardware"
        if field.startswith("in_kvm"):
            return "hardware"
        return "gear"

    def _paint(self, state: HostState, *, probe: bool = True) -> None:
        from winpodx.gui._main_window_secondary_style import make_status_badge

        self._state = state
        while self._callout_host.count():
            item = self._callout_host.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if probe:
            self._report = inspect_preflight(
                self._cfg, storage_path=self._storage_path, iso_path=self._iso_path
            )
        blocking = {issue.key for issue in self._report.failures}
        for spec in prereq_specs():
            ok = bool(getattr(state, spec.field, spec.field not in blocking))
            card = self._cards[spec.field]
            # A row can fail without blocking: kvm group membership is moot
            # once /dev/kvm is already accessible. Red is reserved for the
            # failures that actually stop the install.
            blocks = spec.field in blocking
            color = theme.C.GREEN if ok else theme.C.RED if blocks else theme.C.YELLOW
            label = tr("Pass") if ok else tr("Fail") if blocks else tr("Optional")
            badge = make_status_badge(label, color)
            old = getattr(card, "action_widget", None)
            if old is not None:
                card.row_layout.removeWidget(old)
                old.deleteLater()
            card.row_layout.addWidget(badge)
            card.action_widget = badge
        failures = [
            f"{tr('Fixable') if issue.fixable else tr('Manual action')}: {tr(issue.detail)}"
            for issue in self._report.failures
        ]
        if failures:
            self._callout_host.addWidget(make_warning_callout("\n".join(failures)))
        fixable = any(issue.fixable for issue in self._report.failures)
        self._fix_btn.setVisible(fixable)
        self._fix_btn.setEnabled(fixable and self._thread is None)
        if "in_kvm_group" in blocking:
            self._hint.setText(
                tr(
                    "kvm group membership requires you to log out and back in "
                    "before it takes effect."
                )
            )
        else:
            self._hint.setText("")
        self.can_proceed_changed.emit(self.can_proceed())
        self.refreshed.emit()

    def _on_fix(self) -> None:
        if self._thread is not None:
            return
        self._fix_btn.setEnabled(False)
        thread = QThread(self)
        worker = PkexecWorker(self._state)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_fix_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self._cleanup_fix)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_fix_finished(self, state: HostState, error: str) -> None:
        if error:
            self._hint.setText(_pkexec_message(error))
        self._paint(state)

    def wait_for_worker(self) -> None:
        """Join the pkexec thread before the page is destroyed."""
        self._cleanup_fix()

    def _cleanup_fix(self) -> None:
        thread = self._thread
        if thread is not None:
            try:
                thread.wait()
            except RuntimeError:
                pass
        self._thread = None
        self._worker = None
        self._fix_btn.setEnabled(any(issue.fixable for issue in self._report.failures))


def _pkexec_message(error: str) -> str:
    lowered = error.lower()
    if "pkexec" in lowered and ("not found" in lowered or "path" in lowered):
        return tr("pkexec is not installed. Install polkit and try again.")
    if "dismissed" in lowered or "authentication failed" in lowered:
        return tr("Authentication was cancelled.")
    if "returned" in lowered or "timed out" in lowered:
        return tr("The elevated fix script failed.")
    return error
