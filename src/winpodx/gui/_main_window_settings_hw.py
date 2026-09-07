# SPDX-License-Identifier: MIT
"""Hardware / tuning / disguise field builders for ``SettingsPageMixin``."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLineEdit

from winpodx.core.i18n import tr
from winpodx.gui.theme import COMBO


class SettingsHwMixin:
    """Create Hardware-group widgets (cpu, ram, edition, idle, tuning, disguise)."""

    def _create_hardware_fields(self) -> None:
        self.input_backend = QComboBox()
        self.input_backend.addItems(["podman", "docker", "manual"])
        self.input_backend.setCurrentText(self.cfg.pod.backend)

        self.input_cpu = QLineEdit(str(self.cfg.pod.cpu_cores))
        self.input_ram = QLineEdit(str(self.cfg.pod.ram_gb))
        self.input_idle = QLineEdit(str(self.cfg.pod.idle_timeout))
        self.input_idle_action = QComboBox()
        self.input_idle_action.setStyleSheet(COMBO)
        self.input_idle_action.addItem(tr("Pause (free CPU, keep RAM)"), "pause")
        self.input_idle_action.addItem(tr("Stop (free RAM, boots on next launch)"), "stop")
        raw_idle = self.cfg.pod.idle_action
        idle_action = raw_idle if raw_idle in ("pause", "stop") else "pause"
        self.input_idle_action.setCurrentIndex(self.input_idle_action.findData(idle_action))
        self.input_max_sessions = QLineEdit(str(self.cfg.pod.max_sessions))

        from winpodx.core.config import WIN_VERSION_LABELS

        self.input_win_version = QComboBox()
        for value, label in WIN_VERSION_LABELS.items():
            self.input_win_version.addItem(label, value)
        current_wv = self.cfg.pod.win_version
        idx = self.input_win_version.findData(current_wv)
        if idx >= 0:
            self.input_win_version.setCurrentIndex(idx)
        else:
            self.input_win_version.addItem(f"{current_wv} (custom)", current_wv)
            self.input_win_version.setCurrentIndex(self.input_win_version.count() - 1)
        self.input_win_version.setToolTip(
            tr(
                "Windows edition passed to dockur via VERSION env var.\n"
                "For curated editions, pick from this list.\n"
                "For custom dockur tags, edit win_version in winpodx.toml\n"
                "directly — see docs/ARCHITECTURE.md 'Advanced: Custom Windows ISO'.\n"
                "Changing this requires recreating the container."
            )
        )

        self.input_tuning_profile = QComboBox()
        for label, value in (
            (tr("Auto (recommended)"), "auto"),
            (tr("Performance (force pinning + no balloon)"), "performance"),
            (tr("Safe (Windows-guest-only tunings)"), "safe"),
            (tr("Off (baseline dockur defaults)"), "off"),
            (tr("Manual (edit winpodx.toml)"), "manual"),
        ):
            self.input_tuning_profile.addItem(label, value)
        current_tp = self.cfg.pod.tuning_profile
        tp_idx = self.input_tuning_profile.findData(current_tp)
        if tp_idx >= 0:
            self.input_tuning_profile.setCurrentIndex(tp_idx)
        else:
            self.input_tuning_profile.addItem(f"{current_tp} (unknown)", current_tp)
            self.input_tuning_profile.setCurrentIndex(self.input_tuning_profile.count() - 1)
        self.input_tuning_profile.setToolTip(
            tr(
                "Windows-on-KVM performance tuning.\n"
                "  auto         -- apply every host-supported knob, but respect\n"
                "                  idle-CPU + free-RAM gates (don't starve other\n"
                "                  host workloads).\n"
                "  performance  -- like auto + force CPU pinning + no-balloon\n"
                "                  regardless of host idle headroom. Use when\n"
                "                  this box is mostly dedicated to WinPodX.\n"
                "  safe         -- Windows-guest-only knobs (hv-*, virtio-rng,\n"
                "                  +invtsc, platform_tick) -- no host setup.\n"
                "  off          -- dockur defaults only.\n"
                "Changing this requires a container recreate -- the save flow\n"
                "will prompt."
            )
        )

        self.input_disguise_level = QComboBox()
        for label, value in (
            (tr("Standard VM — no hiding (best performance)"), "off"),
            (tr("Balanced — hide the hypervisor, no performance cost (recommended)"), "balanced"),
            (tr("Hardened — maximum hiding, slower (Hyper-V off)"), "max"),
        ):
            self.input_disguise_level.addItem(label, value)
        dl_idx = self.input_disguise_level.findData(self.cfg.pod.disguise_level)
        if dl_idx >= 0:
            self.input_disguise_level.setCurrentIndex(dl_idx)
        self.input_disguise_level.setStyleSheet(COMBO)
        self.input_disguise_level.setMinimumHeight(32)
