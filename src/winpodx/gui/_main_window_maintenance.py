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

from PySide6.QtCore import Qt
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
from winpodx.gui._widget_helpers import add_shadow
from winpodx.gui.theme import (
    ACTION_ROW,
    BTN_DANGER,
    BTN_PRIMARY,
    SCROLL_AREA,
    C,
    accent_color,
)


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

        title = QLabel("Tools")
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        layout.addWidget(title)

        sub = QLabel("System maintenance and pod management")
        sub.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 13px;")
        layout.addWidget(sub)
        layout.addSpacing(20)

        grp1 = QLabel("Pod Management")
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
            ("⏸", "Suspend Pod", "Pause container (keeps memory)", self._on_suspend),
            ("▶", "Resume Pod", "Unpause a suspended container", self._on_resume),
            ("▣", "Full Desktop", "Launch full Windows desktop", self._on_open_desktop),
        ]
        for i, (icon, label, desc, handler) in enumerate(pod_tools):
            layout.addWidget(self._make_action_row(icon, label, desc, handler, i))

        layout.addSpacing(20)

        grp2 = QLabel("System")
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
            ("✧", "Clean Locks", "Remove Office lock files", self._on_cleanup),
            ("◷", "Sync Time", "Force Windows clock sync", self._on_timesync),
            ("◆", "Debloat", "Disable telemetry & ads", self._on_debloat),
            (
                "⚙",
                "Apply Windows Fixes",
                "Re-apply RDP timeout / NIC / TermService recovery to existing pod",
                self._on_apply_fixes,
            ),
            (
                "⊕",
                "Grow Disk",
                "Add space to the Windows disk and extend C: to fill it",
                self._on_grow_disk,
            ),
            (
                "↻",
                "Sync Guest",
                "Push host updates (agent, fixes, rdprrap) into the guest -- no reinstall",
                self._on_sync_guest,
            ),
        ]
        for i, (icon, label, desc, handler) in enumerate(sys_tools):
            layout.addWidget(self._make_action_row(icon, label, desc, handler, i + 3))

        layout.addSpacing(20)

        grp3 = QLabel("Windows Update")
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
        lbl = QLabel("Windows Update")
        lbl.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 14px; font-weight: 600;"
        )
        col.addWidget(lbl)
        self._update_status_label = QLabel("Checking...")
        self._update_status_label.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: 12px;"
        )
        col.addWidget(self._update_status_label)
        rl.addLayout(col, 1)

        self._btn_enable_updates = QPushButton("Enable")
        self._btn_enable_updates.setStyleSheet(BTN_PRIMARY)
        self._btn_enable_updates.setFixedWidth(90)
        self._btn_enable_updates.clicked.connect(self._on_enable_updates)
        rl.addWidget(self._btn_enable_updates)

        self._btn_disable_updates = QPushButton("Disable")
        self._btn_disable_updates.setStyleSheet(BTN_DANGER)
        self._btn_disable_updates.setFixedWidth(90)
        self._btn_disable_updates.clicked.connect(self._on_disable_updates)
        rl.addWidget(self._btn_disable_updates)

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

    def _on_cleanup(self) -> None:
        from winpodx.core.daemon import cleanup_lock_files

        removed = cleanup_lock_files()
        msg = f"Removed {len(removed)} lock files" if removed else "No lock files found"
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
                "Grow Disk",
                f"Disk grow is only supported on the podman / docker backends, "
                f"not {cfg.pod.backend!r}.",
            )
            return
        try:
            new_size = compute_grow_target(cfg)
        except DiskError as e:
            QMessageBox.information(self, "Grow Disk", f"Cannot grow disk: {e}")
            return

        reply = QMessageBox.question(
            self,
            "Grow Disk",
            f"Grow the Windows disk {cfg.pod.disk_size} → {new_size}?\n\n"
            "This stops the pod, recreates the container so the virtual disk "
            "grows, then extends C: to fill it. Windows data is preserved, but "
            "the guest will reboot and this can take a few minutes.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.info_label.setText(f"Growing disk {cfg.pod.disk_size} → {new_size}...")

        def _do() -> None:
            from winpodx.core.disk import DiskError, grow_disk

            try:
                result = grow_disk(cfg)
            except DiskError as e:
                self.app_launch_failed.emit(f"Grow failed: {e}")
                return
            if result.partition_extended:
                self.app_launched.emit(
                    f"Disk grown {result.old_size} → {result.new_size}; C: extended to fill."
                )
            else:
                self.app_launched.emit(
                    f"Disk grown {result.old_size} → {result.new_size}. "
                    + (result.note or "C: not extended yet.")
                )

        threading.Thread(target=_do, daemon=True).start()

    def _on_sync_guest(self) -> None:
        """Push refreshed guest artifacts into the running guest (guest-sync).

        Runs the deliver / fixes / agent-restart lifecycle on a worker thread.
        """
        cfg = Config.load()
        if cfg.pod.backend not in ("podman", "docker"):
            QMessageBox.information(
                self,
                "Sync Guest",
                f"Guest sync is only supported on podman / docker, not {cfg.pod.backend!r}.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Sync Guest",
            "Push this host's updated guest files (agent, urlacl, rdprrap, "
            "registry fixes) into the running Windows guest? The agent restarts "
            "briefly at the end. Windows data is untouched.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.info_label.setText("Syncing guest...")

        def _do() -> None:
            from winpodx.core.guest_sync import GuestSyncError, sync_guest

            try:
                results = sync_guest(cfg, force=True)
            except GuestSyncError as e:
                self.app_launch_failed.emit(f"Guest sync failed: {e}")
                return
            failed = [k for k, v in results.items() if v.startswith("failed")]
            if failed:
                self.app_launch_failed.emit("Guest sync had failures: " + ", ".join(failed))
            else:
                self.app_launched.emit("Guest synced; agent restarting (~5s).")

        threading.Thread(target=_do, daemon=True).start()

    def _refresh_update_status(self) -> None:
        def _do() -> None:
            from winpodx.core.updates import get_update_status

            cfg = Config.load()
            status = get_update_status(cfg)
            if status == "enabled":
                self._update_status_label.setText("Windows Update is enabled")
                self._btn_enable_updates.setEnabled(False)
                self._btn_disable_updates.setEnabled(True)
            elif status == "disabled":
                self._update_status_label.setText("Windows Update is disabled")
                self._btn_enable_updates.setEnabled(True)
                self._btn_disable_updates.setEnabled(False)
            else:
                self._update_status_label.setText("Status unknown (container not running?)")
                self._btn_enable_updates.setEnabled(True)
                self._btn_disable_updates.setEnabled(True)

        threading.Thread(target=_do, daemon=True).start()

    def _on_enable_updates(self) -> None:
        self._update_status_label.setText("Enabling Windows Update...")
        self._btn_enable_updates.setEnabled(False)
        self._btn_disable_updates.setEnabled(False)

        def _do() -> None:
            from winpodx.core.updates import enable_updates

            cfg = Config.load()
            ok = enable_updates(cfg)
            if ok:
                self.app_launched.emit("Windows Update enabled")
            else:
                self.app_launch_failed.emit("Failed to enable Windows Update")
            self._refresh_update_status()

        threading.Thread(target=_do, daemon=True).start()

    def _on_disable_updates(self) -> None:
        reply = QMessageBox.question(
            self,
            "Disable Windows Update",
            "This will stop Windows Update services and block update domains.\n"
            "Security updates will NOT be installed while disabled.\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._update_status_label.setText("Disabling Windows Update...")
        self._btn_enable_updates.setEnabled(False)
        self._btn_disable_updates.setEnabled(False)

        def _do() -> None:
            from winpodx.core.updates import disable_updates

            cfg = Config.load()
            ok = disable_updates(cfg)
            if ok:
                self.app_launched.emit("Windows Update disabled")
            else:
                self.app_launch_failed.emit("Failed to disable Windows Update")
            self._refresh_update_status()

        threading.Thread(target=_do, daemon=True).start()

    def _on_timesync(self) -> None:
        from winpodx.core.daemon import sync_windows_time

        ok = sync_windows_time(Config.load())
        self.info_label.setText("Time synced" if ok else "Time sync failed")

    def _on_suspend(self) -> None:
        from winpodx.core.daemon import suspend_pod

        ok = suspend_pod(Config.load())
        self.info_label.setText("Pod suspended" if ok else "Suspend failed")
        self._refresh_pod_status()

    def _on_resume(self) -> None:
        from winpodx.core.daemon import resume_pod

        ok = resume_pod(Config.load())
        self.info_label.setText("Pod resumed" if ok else "Resume failed")
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
            QMessageBox.warning(self, "Debloat", f"Catalog error: {e}")
            return

        dialog = DebloatPickerDialog(catalog, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selection = dialog.selected_items()
        if not selection:
            return

        self.info_label.setText(f"Running debloat ({len(selection)} item(s))...")

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
                self.app_launch_failed.emit(f"Debloat payload build error: {e}")
                return

            description = "debloat (" + ",".join(selection) + ")"
            try:
                result = run_via_transport(cfg, payload, description=description, timeout=300)
            except WindowsExecError as e:
                self.app_launch_failed.emit(f"Debloat channel failure: {e}")
                return

            if result.rc == 0:
                self.app_launched.emit(f"Debloat complete ({len(selection)} item(s))")
            else:
                self.app_launch_failed.emit(
                    f"Debloat failed (rc={result.rc}): "
                    f"{result.stderr.strip() or result.stdout.strip()[:200]}"
                )
            self.pod_status_updated.emit("running", cfg.rdp.ip)

        threading.Thread(target=_do, daemon=True).start()

    def _on_apply_fixes(self) -> None:
        """v0.1.9.3: Apply Windows-side runtime fixes to the existing pod.

        Same idempotent helpers fired by `winpodx pod apply-fixes` and by
        `provisioner.ensure_ready` — but on demand from the GUI for users
        whose migrate short-circuited with "already current" so the
        Windows VM never received the OEM v7+v8 fixes.
        """
        self.info_label.setText("Applying Windows-side fixes...")

        def _do() -> None:
            from winpodx.core.pod import PodState, pod_status
            from winpodx.core.provisioner import apply_windows_runtime_fixes

            cfg = Config.load()
            try:
                state = pod_status(cfg).state
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(f"Apply fixes failed (pod probe): {e}")
                return

            if state != PodState.RUNNING:
                self.app_launch_failed.emit(
                    "Pod is not running — start it first via the Apps page or "
                    "`winpodx pod start --wait`."
                )
                return

            try:
                results = apply_windows_runtime_fixes(cfg)
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(f"Apply fixes raised: {e}")
                return

            ok_count = sum(1 for v in results.values() if v == "ok")
            total = len(results)
            failed = [k for k, v in results.items() if v != "ok"]
            if failed:
                detail = ", ".join(failed)
                self.app_launch_failed.emit(f"Apply fixes: {ok_count}/{total} OK; failed: {detail}")
            else:
                self.app_launched.emit(f"Windows-side fixes applied ({ok_count}/{total} OK)")

        threading.Thread(target=_do, daemon=True).start()

    def _on_open_desktop(self) -> None:
        self.info_label.setText("Opening Windows desktop...")

        def _do() -> None:
            try:
                from winpodx.core.provisioner import ensure_ready
                from winpodx.core.rdp import launch_desktop

                cfg = ensure_ready()
                launch_desktop(cfg)
                self.app_launched.emit("Windows Desktop")
            except Exception as e:  # noqa: BLE001
                self.app_launch_failed.emit(str(e))

        threading.Thread(target=_do, daemon=True).start()
