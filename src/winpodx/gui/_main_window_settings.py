"""Settings-page mixin for ``WinpodxWindow``.

Holds the Settings-tab builder, the shared ``_settings_card`` factory,
the live budget-warning updater, and the save handler. Pulled out of
``main_window.py`` to keep that file focused on overall window
orchestration.

Host-class contract (only listed for readers; not enforced):
    cfg: winpodx.core.config.Config
    Widgets created here (input_user, input_ip, input_port, input_scale,
    input_dpi, input_pw_max_age, input_extra_flags, input_backend,
    input_cpu, input_ram, input_idle, input_max_sessions,
    budget_warning_label) are accessed only from this mixin.
    info_label: QLabel              — set by HeaderMixin.
    app_launched / app_launch_failed: Signal(str)
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.config import Config
from winpodx.gui._widget_helpers import add_shadow
from winpodx.gui.theme import (
    BTN_PRIMARY,
    COMBO,
    INPUT,
    SCROLL_AREA,
    SETTINGS_SECTION,
    C,
)


class SettingsPageMixin:
    """Settings page: builds the form, validates input, persists changes."""

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_AREA)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 28)

        title = QLabel("Settings")
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        layout.addWidget(title)

        sub = QLabel("Configure RDP and container settings")
        sub.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 13px;")
        layout.addWidget(sub)
        layout.addSpacing(20)

        cols = QHBoxLayout()
        cols.setSpacing(16)

        self.input_user = QLineEdit(self.cfg.rdp.user)
        self.input_ip = QLineEdit(self.cfg.rdp.ip)
        self.input_port = QLineEdit(str(self.cfg.rdp.port))
        self.input_scale = QComboBox()
        scale_options = [("100%", 100), ("140%", 140), ("180%", 180)]
        for label, val in scale_options:
            self.input_scale.addItem(label, val)
        current_scale = self.cfg.rdp.scale
        idx = next((i for i, (_, v) in enumerate(scale_options) if v == current_scale), 0)
        self.input_scale.setCurrentIndex(idx)

        self.input_dpi = QComboBox()
        dpi_options = [
            ("Auto", 0),
            ("100%  (96 DPI)", 100),
            ("125%  (120 DPI)", 125),
            ("150%  (144 DPI)", 150),
            ("175%  (168 DPI)", 175),
            ("200%  (192 DPI)", 200),
            ("250%  (240 DPI)", 250),
            ("300%  (288 DPI)", 300),
        ]
        for label, val in dpi_options:
            self.input_dpi.addItem(label, val)
        current_dpi = self.cfg.rdp.dpi
        idx = self.input_dpi.findData(current_dpi)
        if idx >= 0:
            self.input_dpi.setCurrentIndex(idx)
        elif current_dpi > 0:
            self.input_dpi.addItem(f"{current_dpi}%", current_dpi)
            self.input_dpi.setCurrentIndex(self.input_dpi.count() - 1)

        self.input_pw_max_age = QComboBox()
        pw_age_options = [
            ("Disabled", 0),
            ("1 day", 1),
            ("3 days", 3),
            ("7 days (default)", 7),
            ("14 days", 14),
            ("30 days", 30),
            ("90 days", 90),
        ]
        for label, val in pw_age_options:
            self.input_pw_max_age.addItem(label, val)
        current_age = self.cfg.rdp.password_max_age
        age_idx = self.input_pw_max_age.findData(current_age)
        if age_idx >= 0:
            self.input_pw_max_age.setCurrentIndex(age_idx)
        elif current_age > 0:
            self.input_pw_max_age.addItem(f"{current_age} days", current_age)
            self.input_pw_max_age.setCurrentIndex(self.input_pw_max_age.count() - 1)

        # Extra FreeRDP arguments — escape hatch for codec / cache / RAIL
        # tuning. Common case as of 2026-05-06: cachyos ships xfreerdp3
        # with WITH_VAAPI_H264_ENCODING=ON which crashes during RAIL
        # post_connect; setting `-gfx-h264` here forces RemoteFX fallback.
        # _filter_extra_flags in core/rdp.py applies the same allowlist
        # whether the value comes from this UI or the CLI's --extra-args,
        # so unsafe entries are dropped with a log warning rather than
        # passed to the FreeRDP command.
        self.input_extra_flags = QLineEdit(self.cfg.rdp.extra_flags)
        self.input_extra_flags.setPlaceholderText("/gfx:RFX +decorations")
        self.input_extra_flags.setToolTip(
            "Extra xfreerdp3 flags appended to every launch. Whitelist-filtered.\n"
            "Common toggles:\n"
            "  /gfx:RFX          force RemoteFX, skip H.264 negotiation\n"
            "                    (workaround for cachyos / experimental VAAPI\n"
            "                     builds where RemoteApp dies at post_connect)\n"
            "  +decorations      enable RemoteApp window decorations\n"
            "  -wallpaper        suppress Windows wallpaper rendering\n"
            "  -bitmap-cache     disable bitmap cache (less RAM, more bandwidth)\n"
            "See src/winpodx/core/rdp.py _BARE_FLAGS for the full allowlist."
        )

        rdp_card = self._settings_card(
            "▣  RDP Connection",
            "Remote Desktop Protocol settings",
            [
                ("Username", self.input_user),
                ("Host / IP", self.input_ip),
                ("Port", self.input_port),
                ("Scale %", self.input_scale),
                ("Windows DPI", self.input_dpi),
                ("Password Rotation", self.input_pw_max_age),
                ("Extra FreeRDP args", self.input_extra_flags),
            ],
        )
        cols.addWidget(rdp_card)

        self.input_backend = QComboBox()
        self.input_backend.addItems(["podman", "docker", "libvirt", "manual"])
        self.input_backend.setCurrentText(self.cfg.pod.backend)

        self.input_cpu = QLineEdit(str(self.cfg.pod.cpu_cores))
        self.input_ram = QLineEdit(str(self.cfg.pod.ram_gb))
        self.input_idle = QLineEdit(str(self.cfg.pod.idle_timeout))
        self.input_max_sessions = QLineEdit(str(self.cfg.pod.max_sessions))

        # Windows edition picker (#178). Editable so a power user can
        # type a value dockur added after winpodx was released — config
        # validation will accept it with a warning rather than reject.
        # Labels mirror dockur/windows' README ordering so users
        # cross-referencing the upstream docs see the same names.
        self.input_win_version = QComboBox()
        self.input_win_version.setEditable(True)
        # Win10+ kernel family only — see ``_KNOWN_WIN_VERSIONS`` in
        # ``core/config.py`` for the policy rationale. Pre-Win10
        # editions are intentionally not offered; if a user really
        # needs one, they can type the dockur tag into the editable
        # combo and config validation will pass it through with a
        # warning.
        win_version_options = [
            ("Windows 11", "11"),
            ("Windows 11 LTSC", "ltsc11"),
            ("Windows 11 IoT Enterprise LTSC", "iot11"),
            ("Windows 11 (Tiny11, debloated)", "tiny11"),
            ("Windows 10", "10"),
            ("Windows 10 LTSC", "ltsc10"),
            ("Windows 10 (Tiny10, debloated)", "tiny10"),
            ("Windows Server 2025", "2025"),
            ("Windows Server 2022", "2022"),
            ("Windows Server 2019", "2019"),
            ("Windows Server 2016", "2016"),
        ]
        for label, value in win_version_options:
            self.input_win_version.addItem(label, value)
        # Map current cfg value back onto the dropdown. If unknown
        # (custom dockur edition), insert it verbatim so the editable
        # combo still shows what's in winpodx.toml.
        current_wv = self.cfg.pod.win_version
        idx = self.input_win_version.findData(current_wv)
        if idx >= 0:
            self.input_win_version.setCurrentIndex(idx)
        else:
            self.input_win_version.addItem(current_wv, current_wv)
            self.input_win_version.setCurrentIndex(self.input_win_version.count() - 1)
        self.input_win_version.setToolTip(
            "Windows edition passed to dockur via VERSION env var.\n"
            "Pick from the list or type any value dockur supports.\n"
            "Changing this requires recreating the container."
        )

        pod_card = self._settings_card(
            "▨  Container / VM",
            "Backend and resource allocation",
            [
                ("Backend", self.input_backend),
                ("Windows Edition", self.input_win_version),
                ("CPU Cores", self.input_cpu),
                ("RAM (GB)", self.input_ram),
                ("Idle Timeout", self.input_idle),
                ("Max Sessions (1-50)", self.input_max_sessions),
            ],
        )
        cols.addWidget(pod_card)

        layout.addLayout(cols)

        # Reverse-open (#48) — Linux apps in the Windows guest's right-
        # click "Open with…" menu. The panel is self-contained — its
        # button handlers call into the host_open CLI handlers
        # directly, and the enable / allow / deny edits land on
        # ``self.cfg.reverse_open`` so the existing _save_settings()
        # persists them via the shared cfg.save() call.
        from winpodx.gui.reverse_open_panel import build_panel as _build_ropanel

        try:
            ropanel = _build_ropanel(self.cfg, parent=content)
            layout.addWidget(ropanel)
        except Exception:  # noqa: BLE001 — never block Settings rendering
            logging.getLogger(__name__).exception(
                "reverse-open panel failed to build; Settings page continues without it"
            )

        # Budget warning — only visible when max_sessions over-subscribes ram_gb.
        # Live-updates as the user types in either field.
        self.budget_warning_label = QLabel("")
        self.budget_warning_label.setWordWrap(True)
        self.budget_warning_label.setStyleSheet(
            f"color: {C.YELLOW if hasattr(C, 'YELLOW') else '#e5c07b'}; "
            f"background: transparent; font-size: 12px; padding: 4px 8px;"
        )
        self.budget_warning_label.setVisible(False)
        layout.addWidget(self.budget_warning_label)
        self.input_ram.textChanged.connect(self._update_budget_warning)
        self.input_max_sessions.textChanged.connect(self._update_budget_warning)
        self._update_budget_warning()

        layout.addSpacing(20)

        save_btn = QPushButton("Save Settings")
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.setFixedWidth(180)
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(save_btn)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page

    def _settings_card(
        self,
        title: str,
        subtitle: str,
        fields: list[tuple[str, QWidget]],
    ) -> QFrame:
        """Build a settings section card."""
        card = QFrame()
        card.setObjectName("settingsSection")
        card.setStyleSheet(
            SETTINGS_SECTION
            + f"QLabel {{ color: {C.TEXT}; font-size: 13px; background: transparent; }}"
            + INPUT
            + COMBO
        )
        add_shadow(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(4)

        header = QLabel(title)
        header.setStyleSheet(
            f"background: transparent; color: {C.BLUE}; font-size: 15px; font-weight: bold;"
        )
        layout.addWidget(header)

        sub = QLabel(subtitle)
        sub.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 11px;")
        layout.addWidget(sub)

        accent_line = QFrame()
        accent_line.setFixedHeight(1)
        accent_line.setStyleSheet(f"background: {C.SURFACE1};")
        layout.addWidget(accent_line)
        layout.addSpacing(14)

        form = QGridLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)

        for row, (label, widget) in enumerate(fields):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"background: transparent; color: {C.SUBTEXT0}; font-size: 13px;")
            form.addWidget(lbl, row, 0)
            form.addWidget(widget, row, 1)

        layout.addLayout(form)
        return card

    def _update_budget_warning(self) -> None:
        """Live-update the session memory budget warning label.

        Quiet when the estimate fits; shows a wrapped message when
        max_sessions over-subscribes ram_gb. Called whenever either
        spinbox text changes.
        """
        from winpodx.core.config import Config, check_session_budget

        try:
            sessions = int(self.input_max_sessions.text() or "10")
            ram = int(self.input_ram.text() or "4")
        except ValueError:
            self.budget_warning_label.setVisible(False)
            return

        tmp = Config()
        tmp.pod.max_sessions = max(1, min(50, sessions))
        tmp.pod.ram_gb = max(1, ram)
        msg = check_session_budget(tmp)
        if msg:
            self.budget_warning_label.setText(f"WARNING: {msg}")
            self.budget_warning_label.setVisible(True)
        else:
            self.budget_warning_label.setVisible(False)

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
                "Invalid Input",
                "Port, Scale, CPU, RAM, Idle Timeout, and Max Sessions must be numbers.",
            )
            return

        # Pull Windows edition: prefer the combo's data role (canonical
        # dockur tag) over its display text. If the user typed a custom
        # value, currentData() is None — fall back to the edit-line text.
        new_win_version = self.input_win_version.currentData()
        if not new_win_version:
            new_win_version = self.input_win_version.currentText().strip()

        old_cfg = Config.load()
        needs_container = (
            cpu != old_cfg.pod.cpu_cores
            or ram != old_cfg.pod.ram_gb
            or port != old_cfg.rdp.port
            or self.input_user.text() != old_cfg.rdp.user
            or new_win_version != old_cfg.pod.win_version
        )

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
        self.cfg.pod.max_sessions = max_sessions
        # Let __post_init__ clamp max_sessions to [1, 50] before save.
        self.cfg.pod.__post_init__()
        self.cfg.save()

        if needs_container and self.cfg.pod.backend in ("podman", "docker"):
            reply = QMessageBox.question(
                self,
                "Restart Container",
                "CPU, RAM, port, user, or Windows edition changed.\n"
                "Container must be recreated to apply.\n\nRestart now?",
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.info_label.setText("Recreating container...")
                QApplication.processEvents()

                def _recreate() -> None:
                    try:
                        from winpodx.cli.setup_cmd import (
                            _generate_compose,
                            _recreate_container,
                        )

                        _generate_compose(self.cfg)
                        _recreate_container(self.cfg)
                        self.app_launched.emit("Container restarted")
                    except Exception as e:  # noqa: BLE001
                        self.app_launch_failed.emit(f"Restart failed: {e}")

                threading.Thread(target=_recreate, daemon=True).start()
                return

        self.info_label.setText("Settings saved")
