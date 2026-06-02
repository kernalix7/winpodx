# SPDX-License-Identifier: MIT
"""Maintenance-tab mixin for ``WinpodxWindow``.

Holds the Tools-tab page builder, the shared ``_make_action_row``
factory, and the slot handlers driven by its buttons: lock-file
cleanup, Windows Update enable/disable, time sync, suspend / resume,
debloat, Windows-side runtime fixes, and "open Windows desktop".
Pulled out of ``main_window.py`` to keep that file focused on
overall window orchestration.

Host-class contract (only listed for readers; not enforced):
    info_label: QLabel              — the small status text below buttons.
    app_launched: Signal(str)
    app_launch_failed: Signal(str)
    pod_status_updated: Signal(str, str)
    _refresh_pod_status() -> None   — defined on PodStatusMixin.
    _update_status_label / _btn_enable_updates / _btn_disable_updates
        — created by _build_maintenance_page below.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.gui._widget_helpers import BusyDialog, add_shadow, make_warning_callout
from winpodx.gui.theme import (
    ACTION_ROW,
    BTN_DANGER,
    BTN_PRIMARY,
    SCROLL_AREA,
    C,
    accent_color,
)


def _confirm_with_callout(
    parent: QWidget,
    title: str,
    body: str,
    callout: str,
    *,
    level: str = "warn",
) -> bool:
    """Yes/No confirm with an inline warning callout above the prompt.

    Reuses the shared ``make_warning_callout`` banner so the risk is
    visible *before* the user clicks Yes, rather than buried in a plain
    QMessageBox body. Returns True only when the user confirms.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(420)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 18, 20, 16)
    lay.setSpacing(12)

    lay.addWidget(make_warning_callout(callout, level=level))

    msg = QLabel(body)
    msg.setWordWrap(True)
    msg.setStyleSheet(f"color: {C.TEXT}; font-size: 13px; background: transparent;")
    lay.addWidget(msg)

    btn_row = QHBoxLayout()
    btn_row.addStretch(1)
    cancel = QPushButton(tr("Cancel"))
    cancel.clicked.connect(dlg.reject)
    btn_row.addWidget(cancel)
    proceed = QPushButton(tr("Proceed"))
    proceed.setStyleSheet(BTN_DANGER if level == "danger" else BTN_PRIMARY)
    proceed.clicked.connect(dlg.accept)
    btn_row.addWidget(proceed)
    lay.addLayout(btn_row)

    return dlg.exec() == QDialog.DialogCode.Accepted


class MaintenanceMixin:
    """Maintenance-tab behavior. Mix into ``WinpodxWindow``."""

    def _build_maintenance_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_AREA)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 24, 32, 20)

        title = QLabel(tr("Tools"))
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        layout.addWidget(title)

        sub = QLabel(tr("System maintenance and pod management"))
        sub.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 13px;")
        layout.addWidget(sub)
        layout.addSpacing(20)

        grp1 = QLabel(tr("Pod Management"))
        grp1.setStyleSheet(
            "background: transparent;"
            f" color: {C.SUBTEXT0};"
            " font-size: 11px;"
            " font-weight: bold;"
            " text-transform: uppercase;"
        )
        layout.addWidget(grp1)
        layout.addSpacing(8)

        pod_tools = [
            ("⏸", tr("Suspend Pod"), tr("Pause container (keeps memory)"), self._on_suspend),
            ("▶", tr("Resume Pod"), tr("Unpause a suspended container"), self._on_resume),
            ("▣", tr("Full Desktop"), tr("Launch full Windows desktop"), self._on_open_desktop),
        ]
        for i, (icon, label, desc, handler) in enumerate(pod_tools):
            layout.addWidget(self._make_action_row(icon, label, desc, handler, i))

        layout.addSpacing(20)

        grp2 = QLabel(tr("System"))
        grp2.setStyleSheet(
            "background: transparent;"
            f" color: {C.SUBTEXT0};"
            " font-size: 11px;"
            " font-weight: bold;"
            " text-transform: uppercase;"
        )
        layout.addWidget(grp2)
        layout.addSpacing(8)

        sys_tools = [
            ("✧", tr("Clean Locks"), tr("Remove Office lock files"), self._on_cleanup),
            ("◷", tr("Sync Time"), tr("Force Windows clock sync"), self._on_timesync),
            ("◆", tr("Debloat"), tr("Disable telemetry & ads"), self._on_debloat),
            (
                "⚙",
                tr("Apply Windows Fixes"),
                tr("Re-apply network + remote-desktop service fixes to the guest (safe to repeat)"),
                self._on_apply_fixes,
            ),
            (
                "⊕",
                tr("Grow Disk"),
                tr("Add space to the Windows disk and extend C: to fill it"),
                self._on_grow_disk,
            ),
            (
                "↻",
                tr("Sync Guest"),
                tr(
                    "Push WinPodX's updated guest files into the running Windows "
                    "guest (no reinstall; agent restarts briefly)"
                ),
                self._on_sync_guest,
            ),
        ]
        for i, (icon, label, desc, handler) in enumerate(sys_tools):
            layout.addWidget(self._make_action_row(icon, label, desc, handler, i + 3))

        layout.addSpacing(20)

        grp3 = QLabel(tr("Windows Update"))
        grp3.setStyleSheet(
            "background: transparent;"
            f" color: {C.SUBTEXT0};"
            " font-size: 11px;"
            " font-weight: bold;"
            " text-transform: uppercase;"
        )
        layout.addWidget(grp3)
        layout.addSpacing(8)

        update_row = QFrame()
        update_row.setObjectName("actionRow")
        update_row.setStyleSheet(ACTION_ROW)
        update_row.setMinimumHeight(64)
        rl = QHBoxLayout(update_row)
        rl.setContentsMargins(16, 8, 16, 8)

        update_icon = QLabel("⇅")
        update_icon.setFixedSize(36, 36)
        update_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        update_icon.setStyleSheet(
            f"background: {accent_color(7)}; color: {C.TEXT}; border-radius: 8px; font-size: 16px;"
        )
        rl.addWidget(update_icon)

        col = QVBoxLayout()
        col.setSpacing(2)
        lbl = QLabel(tr("Windows Update"))
        lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 14px; font-weight: 600;"
        )
        col.addWidget(lbl)
        self._update_status_label = QLabel(tr("Checking..."))
        self._update_status_label.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 12px;"
        )
        col.addWidget(self._update_status_label)
        rl.addLayout(col, 1)

        self._btn_enable_updates = QPushButton(tr("Enable"))
        self._btn_enable_updates.setStyleSheet(BTN_PRIMARY)
        self._btn_enable_updates.setFixedWidth(90)
        self._btn_enable_updates.clicked.connect(self._on_enable_updates)
        rl.addWidget(self._btn_enable_updates)

        self._btn_disable_updates = QPushButton(tr("Disable"))
        self._btn_disable_updates.setStyleSheet(BTN_DANGER)
        self._btn_disable_updates.setFixedWidth(90)
        self._btn_disable_updates.clicked.connect(self._on_disable_updates)
        rl.addWidget(self._btn_disable_updates)

        # Shown only when the status probe can't reach the guest (state
        # unknown): the Enable / Disable buttons are meaningless until we
        # know the current state, so we hide them and offer a re-probe.
        self._btn_retry_updates = QPushButton(tr("Retry"))
        self._btn_retry_updates.setStyleSheet(BTN_PRIMARY)
        self._btn_retry_updates.setFixedWidth(90)
        self._btn_retry_updates.clicked.connect(self._refresh_update_status)
        self._btn_retry_updates.setVisible(False)
        rl.addWidget(self._btn_retry_updates)

        layout.addWidget(update_row)

        self._refresh_update_status()

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page

    def _make_action_row(
        self,
        icon: str,
        label: str,
        desc: str,
        handler: object,
        color_idx: int,
    ) -> QFrame:
        """Build a single tool action row."""
        row = QFrame()
        row.setObjectName("actionRow")
        row.setStyleSheet(ACTION_ROW)
        row.setMinimumHeight(64)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        add_shadow(row, blur=12, y=2, alpha=35)

        rl = QHBoxLayout(row)
        rl.setContentsMargins(16, 0, 20, 0)
        rl.setSpacing(16)

        color = accent_color(color_idx)
        icon_circle = QLabel(icon)
        icon_circle.setFixedSize(36, 36)
        icon_circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_circle.setStyleSheet(
            f"background: {color}; color: {C.CRUST}; border-radius: 18px; font-size: 16px;"
        )
        rl.addWidget(icon_circle)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_lbl = QLabel(label)
        title_lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 14px; font-weight: bold;"
        )
        text_col.addWidget(title_lbl)
        desc_lbl = QLabel(desc)
        desc_lbl.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        text_col.addWidget(desc_lbl)
        rl.addLayout(text_col)
        rl.addStretch()

        arrow = QLabel("›")
        arrow.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 20px;")
        rl.addWidget(arrow)

        row.mousePressEvent = lambda ev, h=handler: h()
        return row

    def _run_busy_op(
        self,
        title: str,
        message: str,
        work: object,
        *,
        eta_hint: str = "",
    ) -> None:
        """Run a long maintenance op on a worker thread behind a BusyDialog.

        ``work`` is a no-argument callable executed off the Qt main thread
        (it owns its own success / failure ``app_launched`` / ``app_launch_
        failed`` emission). The modal BusyDialog stays up for the duration so
        the user can see the op is working, with an honest ``eta_hint``; it is
        closed from the GUI thread via ``QTimer.singleShot`` when the worker
        returns.
        """
        dlg = BusyDialog(self, title, message, eta_hint=eta_hint)

        def _do() -> None:
            try:
                work()
            finally:
                # Marshal the close back onto the GUI thread.
                QTimer.singleShot(0, dlg.finish)

        threading.Thread(target=_do, daemon=True).start()
        dlg.exec()

    def _on_cleanup(self) -> None:
        from winpodx.core.daemon import cleanup_lock_files

        removed = cleanup_lock_files()
        msg = (
            tr("Removed {n} lock files").format(n=len(removed))
            if removed
            else tr("No lock files found")
        )
        self.info_label.setText(msg)

    def _on_grow_disk(self) -> None:
        """Grow the Windows virtual disk by one increment + extend C: (#318).

        Mirrors ``winpodx pod grow-disk``: confirm, then run the stop /
        recreate / extend lifecycle on a worker thread so the UI stays
        responsive (the op reboots the guest and can take minutes).
        """
        from winpodx.core.disk import DiskError, compute_grow_target

        cfg = Config.load()
        if cfg.pod.backend not in ("podman", "docker"):
            QMessageBox.information(
                self,
                tr("Grow Disk"),
                tr(
                    "Disk grow is only supported on the podman / docker backends, not {backend!r}."
                ).format(backend=cfg.pod.backend),
            )
            return
        try:
            new_size = compute_grow_target(cfg)
        except DiskError as e:
            QMessageBox.information(self, tr("Grow Disk"), tr("Cannot grow disk: {e}").format(e=e))
            return

        if not _confirm_with_callout(
            self,
            tr("Grow Disk"),
            tr(
                "Grow the Windows disk {old} → {new}?\n\n"
                "This stops the pod, recreates the container so the virtual disk "
                "grows, then extends C: to fill it. Windows data is preserved."
            ).format(old=cfg.pod.disk_size, new=new_size),
            tr(
                "The guest reboots and any running Windows apps are killed. This "
                "can take a few minutes — don't close WinPodX until it finishes."
            ),
            level="danger",
        ):
            return

        self.info_label.setText(
            tr("Growing disk {old} → {new}...").format(old=cfg.pod.disk_size, new=new_size)
        )

        def _do() -> None:
            from winpodx.core.disk import DiskError, grow_disk

            try:
                result = grow_disk(cfg)
            except DiskError as e:
                self.app_launch_failed.emit(tr("Grow failed: {e}").format(e=e))
                return
            if result.partition_extended:
                self.app_launched.emit(
                    tr("Disk grown {old} → {new}; C: extended to fill.").format(
                        old=result.old_size, new=result.new_size
                    )
                )
            else:
                self.app_launched.emit(
                    tr("Disk grown {old} → {new}. ").format(
                        old=result.old_size, new=result.new_size
                    )
                    + (result.note or tr("C: not extended yet."))
                )

        self._run_busy_op(
            tr("Grow Disk"),
            tr("Growing disk {old} → {new}...").format(old=cfg.pod.disk_size, new=new_size),
            _do,
            eta_hint=tr("Reboots the guest; typically takes a few minutes."),
        )

    def _on_sync_guest(self) -> None:
        """Push refreshed guest artifacts into the running guest (guest-sync).

        Runs the deliver / fixes / agent-restart lifecycle on a worker thread.
        """
        cfg = Config.load()
        if cfg.pod.backend not in ("podman", "docker"):
            QMessageBox.information(
                self,
                tr("Sync Guest"),
                tr("Guest sync is only supported on podman / docker, not {backend!r}.").format(
                    backend=cfg.pod.backend
                ),
            )
            return

        reply = QMessageBox.question(
            self,
            tr("Sync Guest"),
            tr(
                "Push this host's updated guest files (agent, urlacl, rdprrap, "
                "registry fixes) into the running Windows guest? The agent restarts "
                "briefly at the end. Windows data is untouched.\n\nProceed?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.info_label.setText(tr("Syncing guest..."))

        def _do() -> None:
            from winpodx.core.guest_sync import GuestSyncError, sync_guest

            try:
                results = sync_guest(cfg, force=True)
            except GuestSyncError as e:
                self.app_launch_failed.emit(tr("Guest sync failed: {e}").format(e=e))
                return
            failed = [k for k, v in results.items() if v.startswith("failed")]
            if failed:
                self.app_launch_failed.emit(
                    tr("Guest sync had failures: {detail}").format(detail=", ".join(failed))
                )
            else:
                self.app_launched.emit(tr("Guest synced; agent restarting (~5s)."))

        self._run_busy_op(
            tr("Sync Guest"),
            tr("Pushing updated guest files into the running Windows guest..."),
            _do,
            eta_hint=tr("Agent restarts briefly at the end; usually under a minute."),
        )

    def _refresh_update_status(self) -> None:
        def _do() -> None:
            from winpodx.core.updates import get_update_status

            cfg = Config.load()
            status = get_update_status(cfg)
            if status == "enabled":
                self._update_status_label.setText(tr("Windows Update is enabled"))
                self._btn_enable_updates.setVisible(True)
                self._btn_disable_updates.setVisible(True)
                self._btn_retry_updates.setVisible(False)
                self._btn_enable_updates.setEnabled(False)
                self._btn_disable_updates.setEnabled(True)
            elif status == "disabled":
                self._update_status_label.setText(tr("Windows Update is disabled"))
                self._btn_enable_updates.setVisible(True)
                self._btn_disable_updates.setVisible(True)
                self._btn_retry_updates.setVisible(False)
                self._btn_enable_updates.setEnabled(True)
                self._btn_disable_updates.setEnabled(False)
            else:
                # Can't reach the guest -- the current state is unknown, so
                # Enable / Disable would be a guess. Hide them and offer a
                # re-probe instead of leaving both in an ambiguous state.
                self._update_status_label.setText(
                    tr("Can't check status — start the pod, then Retry.")
                )
                self._btn_enable_updates.setVisible(False)
                self._btn_disable_updates.setVisible(False)
                self._btn_retry_updates.setVisible(True)
                self._btn_retry_updates.setEnabled(True)

        threading.Thread(target=_do, daemon=True).start()

    def _on_enable_updates(self) -> None:
        self._update_status_label.setText(tr("Enabling Windows Update..."))
        self._btn_enable_updates.setEnabled(False)
        self._btn_disable_updates.setEnabled(False)

        def _do() -> None:
            from winpodx.core.updates import enable_updates

            cfg = Config.load()
            ok = enable_updates(cfg)
            if ok:
                self.app_launched.emit(tr("Windows Update enabled"))
            else:
                self.app_launch_failed.emit(tr("Failed to enable Windows Update"))
            self._refresh_update_status()

        threading.Thread(target=_do, daemon=True).start()

    def _on_disable_updates(self) -> None:
        if not _confirm_with_callout(
            self,
            tr("Disable Windows Update"),
            tr(
                "This will stop Windows Update services and block update domains. "
                "You can re-enable it any time from this page."
            ),
            tr(
                "No security updates will be installed until you re-enable Windows "
                "Update. Only disable this if you understand the risk."
            ),
            level="danger",
        ):
            return

        self._update_status_label.setText(tr("Disabling Windows Update..."))
        self._btn_enable_updates.setEnabled(False)
        self._btn_disable_updates.setEnabled(False)

        def _do() -> None:
            from winpodx.core.updates import disable_updates

            cfg = Config.load()
            ok = disable_updates(cfg)
            if ok:
                self.app_launched.emit(tr("Windows Update disabled"))
            else:
                self.app_launch_failed.emit(tr("Failed to disable Windows Update"))
            self._refresh_update_status()

        threading.Thread(target=_do, daemon=True).start()

    def _on_timesync(self) -> None:
        from winpodx.core.daemon import sync_windows_time

        ok = sync_windows_time(Config.load())
        self.info_label.setText(tr("Time synced") if ok else tr("Time sync failed"))

    def _on_suspend(self) -> None:
        from winpodx.core.daemon import suspend_pod

        ok = suspend_pod(Config.load())
        self.info_label.setText(tr("Pod suspended") if ok else tr("Suspend failed"))
        self._refresh_pod_status()

    def _on_resume(self) -> None:
        from winpodx.core.daemon import resume_pod

        ok = resume_pod(Config.load())
        self.info_label.setText(tr("Pod resumed") if ok else tr("Resume failed"))
        self._refresh_pod_status()

    def _on_debloat(self) -> None:
        """Open the debloat picker dialog and run the selection (#247 P3).

        Replaces the pre-P3 single-button "run normal preset" behaviour
        with a richer dialog that surfaces every catalog item + risk
        badge + preset radio. The dialog itself is pure UI; this
        handler is responsible for taking the accepted selection and
        firing the orchestrator payload via run_via_transport.
        """
        from winpodx.core.debloat import DebloatCatalogError, load_catalog
        from winpodx.gui.debloat_picker import DebloatPickerDialog

        try:
            catalog = load_catalog()
        except DebloatCatalogError as e:
            QMessageBox.warning(self, tr("Debloat"), tr("Catalog error: {e}").format(e=e))
            return

        dialog = DebloatPickerDialog(catalog, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selection = dialog.selected_items()
        if not selection:
            return

        self.info_label.setText(tr("Running debloat ({n} item(s))...").format(n=len(selection)))

        def _do() -> None:
            from winpodx.core.debloat import (
                DebloatCatalogError as _CatalogError,
            )
            from winpodx.core.debloat import (
                build_run_script,
            )
            from winpodx.core.windows_exec import WindowsExecError, run_via_transport

            cfg = Config.load()
            try:
                payload = build_run_script(catalog, selection)
            except _CatalogError as e:
                self.app_launch_failed.emit(tr("Debloat payload build error: {e}").format(e=e))
                return

            description = "debloat (" + ",".join(selection) + ")"
            try:
                result = run_via_transport(cfg, payload, description=description, timeout=300)
            except WindowsExecError as e:
                self.app_launch_failed.emit(tr("Debloat channel failure: {e}").format(e=e))
                return

            if result.rc == 0:
                self.app_launched.emit(
                    tr("Debloat complete ({n} item(s))").format(n=len(selection))
                )
            else:
                self.app_launch_failed.emit(
                    tr("Debloat failed (rc={rc}): {detail}").format(
                        rc=result.rc,
                        detail=result.stderr.strip() or result.stdout.strip()[:200],
                    )
                )
            self.pod_status_updated.emit("running", cfg.rdp.ip)

        self._run_busy_op(
            tr("Debloat"),
            tr("Running debloat ({n} item(s)) inside the Windows guest...").format(
                n=len(selection)
            ),
            _do,
            eta_hint=tr("Runs guest-side via the agent; usually under a minute."),
        )

    def _on_apply_fixes(self) -> None:
        """v0.1.9.3: Apply Windows-side runtime fixes to the existing pod.

        Same idempotent helpers fired by `winpodx pod apply-fixes` and by
        `provisioner.ensure_ready` — but on demand from the GUI for users
        whose migrate short-circuited with "already current" so the
        Windows VM never received the OEM v7+v8 fixes.
        """
        self.info_label.setText(tr("Applying Windows-side fixes..."))

        def _do() -> None:
            from winpodx.core.pod import PodState, pod_status
            from winpodx.core.provisioner import apply_windows_runtime_fixes

            cfg = Config.load()
            try:
                state = pod_status(cfg).state
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(tr("Apply fixes failed (pod probe): {e}").format(e=e))
                return

            if state != PodState.RUNNING:
                self.app_launch_failed.emit(
                    tr(
                        "Pod is not running — start it first via the Apps page or "
                        "`winpodx pod start --wait`."
                    )
                )
                return

            try:
                results = apply_windows_runtime_fixes(cfg)
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(tr("Apply fixes raised: {e}").format(e=e))
                return

            ok_count = sum(1 for v in results.values() if v == "ok")
            total = len(results)
            failed = [k for k, v in results.items() if v != "ok"]
            if failed:
                detail = ", ".join(failed)
                self.app_launch_failed.emit(
                    tr("Apply fixes: {ok}/{total} OK; failed: {detail}").format(
                        ok=ok_count, total=total, detail=detail
                    )
                )
            else:
                self.app_launched.emit(
                    tr("Windows-side fixes applied ({ok}/{total} OK)").format(
                        ok=ok_count, total=total
                    )
                )

        self._run_busy_op(
            tr("Apply Windows Fixes"),
            tr("Re-applying network + remote-desktop service fixes to the guest..."),
            _do,
            eta_hint=tr("Safe to repeat; usually a few seconds."),
        )

    def _on_open_desktop(self) -> None:
        self.info_label.setText(tr("Opening Windows desktop..."))

        def _do() -> None:
            try:
                from winpodx.core.provisioner import ensure_ready
                from winpodx.core.rdp import launch_desktop

                cfg = ensure_ready()
                launch_desktop(cfg)
                self.app_launched.emit(tr("Windows Desktop"))
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(str(e))

        threading.Thread(target=_do, daemon=True).start()
