# SPDX-License-Identifier: MIT
"""Settings save handler for ``SettingsPageMixin``."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from winpodx.core.config import Config
from winpodx.core.i18n import tr


class SettingsSaveMixin:
    """Persist Settings form fields and prompt for recreate / wipe."""

    def _save_settings(self) -> None:
        try:
            port = int(self.input_port.text() or str(self.cfg.rdp.port))
            scale = self.input_scale.currentData()
            cpu = int(self.input_cpu.text() or "4")
            ram = int(self.input_ram.text() or "4")
            idle = int(self.input_idle.text() or "0")
            max_sessions = int(self.input_max_sessions.text() or "10")
        except ValueError:
            QMessageBox.warning(
                self,
                tr("Invalid Input"),
                tr("Port, Scale, CPU, RAM, Idle Timeout, and Max Sessions must be numbers."),
            )
            return

        new_win_version = self.input_win_version.currentData()
        new_language = self.input_language.currentData() or ""
        new_region = self.input_region.currentData() or ""
        new_keyboard = self.input_keyboard.currentData() or ""
        new_timezone = self.input_timezone.currentData() or ""
        new_tuning_profile = self.input_tuning_profile.currentData() or "auto"
        new_disguise_level = self.input_disguise_level.currentData() or "balanced"

        old_cfg = Config.load()
        needs_container = (
            cpu != old_cfg.pod.cpu_cores
            or ram != old_cfg.pod.ram_gb
            or port != old_cfg.rdp.port
            or self.input_user.text() != old_cfg.rdp.user
            or new_win_version != old_cfg.pod.win_version
            or new_timezone != old_cfg.pod.timezone
            or new_tuning_profile != old_cfg.pod.tuning_profile
            or new_disguise_level != old_cfg.pod.disguise_level
        )
        from winpodx.core.config import disguise_changes_devices

        disguise_device_wipe = disguise_changes_devices(
            old_cfg.pod.disguise_level, new_disguise_level
        )
        needs_wipe = (
            new_win_version != old_cfg.pod.win_version
            or new_language != old_cfg.pod.language
            or new_region != old_cfg.pod.region
            or new_keyboard != old_cfg.pod.keyboard
            or disguise_device_wipe
        )
        if needs_wipe:
            needs_container = True

        self.cfg.rdp.user = self.input_user.text()
        self.cfg.rdp.ip = self.input_ip.text()
        self.cfg.rdp.port = port
        self.cfg.rdp.scale = scale
        self.cfg.rdp.dpi = self.input_dpi.currentData()
        self.cfg.rdp.password_max_age = self.input_pw_max_age.currentData()
        self.cfg.rdp.extra_flags = self.input_extra_flags.text().strip()
        self.cfg.pod.backend = self.input_backend.currentText()
        self.cfg.pod.win_version = new_win_version
        self.cfg.pod.cpu_cores = cpu
        self.cfg.pod.ram_gb = ram
        self.cfg.pod.idle_timeout = idle
        self.cfg.pod.idle_action = self.input_idle_action.currentData() or "pause"
        self.cfg.pod.max_sessions = max_sessions
        self.cfg.pod.language = new_language
        self.cfg.pod.region = new_region
        self.cfg.pod.keyboard = new_keyboard
        self.cfg.pod.timezone = new_timezone
        self.cfg.pod.tuning_profile = new_tuning_profile
        self.cfg.pod.disguise_level = new_disguise_level
        from winpodx.cli.disguise import _DISGUISE_TAG, disguise_image_present

        if new_disguise_level == "max" and disguise_image_present(self.cfg):
            self.cfg.pod.disguise_image = _DISGUISE_TAG
        self.cfg.pod.__post_init__()
        self.cfg.save()

        if needs_container and self.cfg.pod.backend in ("podman", "docker"):
            if disguise_device_wipe:
                prompt = tr(
                    "⚠️  WIPE WARNING — read carefully.\n\n"
                    "Switching the bare-metal level to/from 'Hardened (max)' "
                    "changes the guest's virtual hardware (disk → SATA, network "
                    "→ e1000, GPU → std) to emulated devices.\n\n"
                    "The EXISTING Windows install CANNOT boot on the new "
                    "hardware, so this will DESTROY the Windows disk and "
                    "reinstall from scratch.\n\n"
                    "ALL apps, files, and settings inside the Windows VM will be "
                    "PERMANENTLY DELETED.\n\n"
                    "Reinstall takes ~5-10 minutes (ISO download + Sysprep). "
                    "There is no undo.\n\n"
                    "Wipe Windows and reinstall now?"
                )
            elif needs_wipe:
                prompt = tr(
                    "Windows edition or installation locale (language / "
                    "region / keyboard) changed.\n\n"
                    "These values are baked into Windows on the initial "
                    "install -- applying them requires destroying the "
                    "Windows disk and re-installing.\n\n"
                    "The Windows VM will reboot and re-install (~5-10 "
                    "minutes for ISO download + Sysprep + OEM apply).\n\n"
                    "Wipe and reinstall now?"
                )
            else:
                prompt = tr(
                    "CPU, RAM, port, user, or timezone changed.\n"
                    "Container must be recreated to apply (Windows disk "
                    "preserved).\n\nRestart now?"
                )
            build_disguise = new_disguise_level == "max" and not disguise_image_present(self.cfg)
            if build_disguise:
                prompt += tr(
                    "\n\nHardened mode will also build a patched-QEMU image locally "
                    "first (one-time, ~20-40 min). Progress shows in the setup window."
                )
            if needs_wipe:
                reply = QMessageBox.question(
                    self,
                    tr("Reinstall Windows"),
                    prompt,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
            else:
                reply = QMessageBox.question(self, tr("Restart Container"), prompt)
            if reply == QMessageBox.StandardButton.Yes:
                self.info_label.setText(
                    tr("Wiping Windows disk + recreating...")
                    if needs_wipe
                    else tr("Recreating container...")
                )
                self._clear_settings_dirty()
                self._run_full_bring_up(
                    recreate=True, wipe_storage=needs_wipe, build_disguise=build_disguise
                )
                return

            if disguise_device_wipe:
                self.cfg.pod.disguise_level = old_cfg.pod.disguise_level
                self.cfg.pod.__post_init__()
                self.cfg.save()
                self.input_disguise_level.blockSignals(True)
                self.input_disguise_level.setCurrentIndex(
                    max(0, self.input_disguise_level.findData(old_cfg.pod.disguise_level))
                )
                self.input_disguise_level.blockSignals(False)
                self.info_label.setText(
                    tr("Bare-metal level change cancelled (kept {level})").format(
                        level=old_cfg.pod.disguise_level
                    )
                )
                self._clear_settings_dirty()
                return

        self.info_label.setText(tr("Settings saved"))
        self._clear_settings_dirty()
