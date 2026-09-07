# SPDX-License-Identifier: MIT
"""RDP / connection field builders for ``SettingsPageMixin``."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLineEdit

from winpodx.core.i18n import tr


class SettingsRdpMixin:
    """Create Connection-group widgets (host, port, user, scale, DPI, flags)."""

    def _create_rdp_fields(self) -> None:
        self.input_user = QLineEdit(self.cfg.rdp.user)
        self.input_ip = QLineEdit(self.cfg.rdp.ip)
        self.input_ip.setToolTip(
            tr(
                "Address FreeRDP connects to for the Windows guest. The default\n"
                "127.0.0.1 reaches the local container's forwarded RDP port.\n"
                "Use a non-loopback address only for a remote/manual backend —\n"
                "the RDP port must be reachable at that address."
            )
        )
        self.input_port = QLineEdit(str(self.cfg.rdp.port))
        self.input_scale = QComboBox()
        scale_options = [("100%", 100), ("140%", 140), ("180%", 180)]
        for label, val in scale_options:
            self.input_scale.addItem(label, val)
        current_scale = self.cfg.rdp.scale
        idx = next((i for i, (_, v) in enumerate(scale_options) if v == current_scale), 0)
        self.input_scale.setCurrentIndex(idx)
        self.input_scale.setToolTip(
            tr(
                "Client-side zoom applied by FreeRDP after the guest renders.\n"
                "Use this on a HiDPI Linux display to enlarge a normal-DPI\n"
                "Windows desktop. Crisp text, but the guest still thinks it\n"
                "is at 100% — for true guest-side scaling set Windows DPI instead."
            )
        )

        self.input_dpi = QComboBox()
        dpi_options = [
            (tr("Auto"), 0),
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
        self.input_dpi.setToolTip(
            tr(
                "Guest-side scaling: tells Windows to render UI at this DPI.\n"
                "Use this (not Scale %) when you want larger, sharp Windows UI\n"
                "and apps that respect system DPI. Auto picks a value from the\n"
                "detected Linux display scale."
            )
        )

        self.input_pw_max_age = QComboBox()
        pw_age_options = [
            (tr("Disabled"), 0),
            (tr("1 day"), 1),
            (tr("3 days"), 3),
            (tr("7 days (default)"), 7),
            (tr("14 days"), 14),
            (tr("30 days"), 30),
            (tr("90 days"), 90),
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
        self.input_pw_max_age.setToolTip(
            tr(
                "Auto-rotate the Windows RDP account password after this many\n"
                "days. On the next launch past the limit WinPodX generates a new\n"
                "password, recreates the container to apply it, and rolls back on\n"
                "failure. Disabled keeps the current password indefinitely."
            )
        )

        self.input_extra_flags = QLineEdit(self.cfg.rdp.extra_flags)
        self.input_extra_flags.setPlaceholderText("/gfx:RFX +decorations")
        self.input_extra_flags.setToolTip(
            tr(
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
        )
