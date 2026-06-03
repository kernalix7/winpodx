# SPDX-License-Identifier: MIT
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

from PySide6.QtCore import Qt
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
from winpodx.core.i18n import tr
from winpodx.gui._widget_helpers import add_shadow, make_page_header, make_warning_callout
from winpodx.gui.theme import (
    BTN_PRIMARY,
    CHECKBOX,
    COMBO,
    FONT_BODY,
    FONT_CAPTION,
    FONT_HEADER,
    INPUT,
    RADIUS_S,
    SCROLL_AREA,
    SETTINGS_SECTION,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XL,
    SPACE_XS,
    SPACE_XXL,
    C,
)

# Curated dropdown options for the Localization section (#254 phase 3).
# Mirrors dockur/windows' README ordering so cross-referencing the
# upstream docs sees the same names. Empty string is reserved as the
# "Auto (detected)" sentinel and is prepended by ``_build_locale_combo``.
# Out-of-list values from hand-edited TOML surface as "(custom)" entries
# via the same fallback pattern win_version uses above.
_DOCKUR_LANGUAGES: list[tuple[str, str]] = [
    ("English", "English"),
    ("Arabic", "Arabic"),
    ("Bulgarian", "Bulgarian"),
    ("Chinese (Simplified)", "Chinese"),
    ("Chinese (Traditional)", "Traditional Chinese"),
    ("Croatian", "Croatian"),
    ("Czech", "Czech"),
    ("Danish", "Danish"),
    ("Dutch", "Dutch"),
    ("Estonian", "Estonian"),
    ("Finnish", "Finnish"),
    ("French", "French"),
    ("German", "German"),
    ("Greek", "Greek"),
    ("Hebrew", "Hebrew"),
    ("Hungarian", "Hungarian"),
    ("Italian", "Italian"),
    ("Japanese", "Japanese"),
    ("Korean", "Korean"),
    ("Latvian", "Latvian"),
    ("Lithuanian", "Lithuanian"),
    ("Norwegian", "Norwegian"),
    ("Polish", "Polish"),
    ("Portuguese", "Portuguese"),
    ("Portuguese (Brazil)", "Brazilian Portuguese"),
    ("Romanian", "Romanian"),
    ("Russian", "Russian"),
    ("Serbian", "Serbian"),
    ("Slovak", "Slovak"),
    ("Slovenian", "Slovenian"),
    ("Spanish", "Spanish"),
    ("Spanish (Mexico)", "Mexican Spanish"),
    ("Swedish", "Swedish"),
    ("Thai", "Thai"),
    ("Turkish", "Turkish"),
    ("Ukrainian", "Ukrainian"),
]

_DOCKUR_REGIONS: list[tuple[str, str]] = [
    ("English (World) — en-001", "en-001"),
    ("English (US) — en-US", "en-US"),
    ("English (UK) — en-GB", "en-GB"),
    ("Arabic (SA) — ar-SA", "ar-SA"),
    ("Chinese (CN) — zh-CN", "zh-CN"),
    ("Chinese (TW) — zh-TW", "zh-TW"),
    ("Czech (CZ) — cs-CZ", "cs-CZ"),
    ("Danish (DK) — da-DK", "da-DK"),
    ("Dutch (NL) — nl-NL", "nl-NL"),
    ("Finnish (FI) — fi-FI", "fi-FI"),
    ("French (FR) — fr-FR", "fr-FR"),
    ("German (DE) — de-DE", "de-DE"),
    ("Greek (GR) — el-GR", "el-GR"),
    ("Hebrew (IL) — he-IL", "he-IL"),
    ("Hungarian (HU) — hu-HU", "hu-HU"),
    ("Italian (IT) — it-IT", "it-IT"),
    ("Japanese (JP) — ja-JP", "ja-JP"),
    ("Korean (KR) — ko-KR", "ko-KR"),
    ("Norwegian (NO) — nb-NO", "nb-NO"),
    ("Polish (PL) — pl-PL", "pl-PL"),
    ("Portuguese (PT) — pt-PT", "pt-PT"),
    ("Portuguese (BR) — pt-BR", "pt-BR"),
    ("Russian (RU) — ru-RU", "ru-RU"),
    ("Spanish (ES) — es-ES", "es-ES"),
    ("Spanish (MX) — es-MX", "es-MX"),
    ("Swedish (SE) — sv-SE", "sv-SE"),
    ("Thai (TH) — th-TH", "th-TH"),
    ("Turkish (TR) — tr-TR", "tr-TR"),
    ("Ukrainian (UA) — uk-UA", "uk-UA"),
]

_DOCKUR_KEYBOARDS: list[tuple[str, str]] = _DOCKUR_REGIONS

# Subset of CLDR's IANA list -- the ~50 zones most users actually live
# in. Hand-edited TOML can carry any IANA name; out-of-list values get
# the "(custom)" tag at build time. Sourced from windows_zones.toml's
# coverage, sorted by UTC offset for scannability.
_COMMON_TIMEZONES: list[tuple[str, str]] = [
    ("(GMT-10:00) Honolulu — Pacific/Honolulu", "Pacific/Honolulu"),
    ("(GMT-09:00) Anchorage — America/Anchorage", "America/Anchorage"),
    ("(GMT-08:00) Los Angeles — America/Los_Angeles", "America/Los_Angeles"),
    ("(GMT-08:00) Vancouver — America/Vancouver", "America/Vancouver"),
    ("(GMT-07:00) Denver — America/Denver", "America/Denver"),
    ("(GMT-07:00) Phoenix — America/Phoenix", "America/Phoenix"),
    ("(GMT-06:00) Chicago — America/Chicago", "America/Chicago"),
    ("(GMT-06:00) Mexico City — America/Mexico_City", "America/Mexico_City"),
    ("(GMT-05:00) New York — America/New_York", "America/New_York"),
    ("(GMT-05:00) Toronto — America/Toronto", "America/Toronto"),
    ("(GMT-04:00) Halifax — America/Halifax", "America/Halifax"),
    ("(GMT-03:00) São Paulo — America/Sao_Paulo", "America/Sao_Paulo"),
    ("(GMT-03:00) Buenos Aires — America/Argentina/Buenos_Aires", "America/Argentina/Buenos_Aires"),
    ("(GMT+00:00) London — Europe/London", "Europe/London"),
    ("(GMT+00:00) UTC", "UTC"),
    ("(GMT+01:00) Berlin — Europe/Berlin", "Europe/Berlin"),
    ("(GMT+01:00) Paris — Europe/Paris", "Europe/Paris"),
    ("(GMT+01:00) Madrid — Europe/Madrid", "Europe/Madrid"),
    ("(GMT+01:00) Rome — Europe/Rome", "Europe/Rome"),
    ("(GMT+01:00) Amsterdam — Europe/Amsterdam", "Europe/Amsterdam"),
    ("(GMT+02:00) Athens — Europe/Athens", "Europe/Athens"),
    ("(GMT+02:00) Helsinki — Europe/Helsinki", "Europe/Helsinki"),
    ("(GMT+02:00) Cairo — Africa/Cairo", "Africa/Cairo"),
    ("(GMT+02:00) Johannesburg — Africa/Johannesburg", "Africa/Johannesburg"),
    ("(GMT+03:00) Moscow — Europe/Moscow", "Europe/Moscow"),
    ("(GMT+03:00) Istanbul — Europe/Istanbul", "Europe/Istanbul"),
    ("(GMT+03:00) Riyadh — Asia/Riyadh", "Asia/Riyadh"),
    ("(GMT+04:00) Dubai — Asia/Dubai", "Asia/Dubai"),
    ("(GMT+05:30) Kolkata — Asia/Kolkata", "Asia/Kolkata"),
    ("(GMT+07:00) Bangkok — Asia/Bangkok", "Asia/Bangkok"),
    ("(GMT+08:00) Singapore — Asia/Singapore", "Asia/Singapore"),
    ("(GMT+08:00) Hong Kong — Asia/Hong_Kong", "Asia/Hong_Kong"),
    ("(GMT+08:00) Shanghai — Asia/Shanghai", "Asia/Shanghai"),
    ("(GMT+08:00) Taipei — Asia/Taipei", "Asia/Taipei"),
    ("(GMT+09:00) Seoul — Asia/Seoul", "Asia/Seoul"),
    ("(GMT+09:00) Tokyo — Asia/Tokyo", "Asia/Tokyo"),
    ("(GMT+09:30) Adelaide — Australia/Adelaide", "Australia/Adelaide"),
    ("(GMT+10:00) Sydney — Australia/Sydney", "Australia/Sydney"),
    ("(GMT+10:00) Melbourne — Australia/Melbourne", "Australia/Melbourne"),
    ("(GMT+12:00) Auckland — Pacific/Auckland", "Pacific/Auckland"),
]


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
        layout.setContentsMargins(SPACE_XXL, 0, SPACE_XXL, SPACE_XL)
        layout.setSpacing(SPACE_M)

        save_btn = QPushButton(tr("Save Settings"))
        save_btn.setStyleSheet(BTN_PRIMARY)
        save_btn.setFixedWidth(180)
        save_btn.clicked.connect(self._save_settings)
        layout.addWidget(
            make_page_header(
                tr("Settings"),
                tr("Configure RDP and container settings"),
                actions_widget=save_btn,
            )
        )

        cols = QHBoxLayout()
        cols.setSpacing(SPACE_L)

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

        rdp_card = self._settings_card(
            tr("▣  RDP Connection"),
            tr("Remote Desktop Protocol settings"),
            [
                (tr("Username"), self.input_user),
                (tr("Host / IP"), self.input_ip),
                (tr("Port"), self.input_port),
                (tr("Scale %"), self.input_scale),
                (tr("Windows DPI"), self.input_dpi),
                (tr("Password Rotation"), self.input_pw_max_age),
                (tr("Extra FreeRDP args"), self.input_extra_flags),
            ],
        )
        cols.addWidget(rdp_card)

        self.input_backend = QComboBox()
        self.input_backend.addItems(["podman", "docker", "manual"])
        self.input_backend.setCurrentText(self.cfg.pod.backend)

        self.input_cpu = QLineEdit(str(self.cfg.pod.cpu_cores))
        self.input_ram = QLineEdit(str(self.cfg.pod.ram_gb))
        self.input_idle = QLineEdit(str(self.cfg.pod.idle_timeout))
        self.input_max_sessions = QLineEdit(str(self.cfg.pod.max_sessions))

        # Windows edition picker (#178). Read-only combo — the dark
        # theme renders an editable QComboBox's drop-down arrow with
        # poor contrast and the dropdown affordance disappears
        # visually (smoke-tested on Tumbleweed 2026-05-14: users
        # interpret the editable field as a text input and don't see
        # the picker). Custom dockur tags outside the curated list
        # are still supported via direct ``winpodx.toml`` edit (see
        # ``docs/ARCHITECTURE.md`` "Advanced: Custom Windows ISO");
        # config validation passes them through with a one-line
        # WARN regardless of how the value got there.
        # Labels mirror dockur/windows' README ordering so users
        # cross-referencing the upstream docs see the same names.
        self.input_win_version = QComboBox()
        # Edition list pulled from ``WIN_VERSION_LABELS`` in ``core/config.py``
        # so the dropdown stays in sync with the validator + CLI help text.
        # Win10+ kernel family only — see the comment on ``_KNOWN_WIN_VERSIONS``
        # for the policy rationale. Pre-Win10 editions are intentionally not
        # offered.
        from winpodx.core.config import WIN_VERSION_LABELS

        for value, label in WIN_VERSION_LABELS.items():
            self.input_win_version.addItem(label, value)
        # Map current cfg value back onto the dropdown. If unknown
        # (custom dockur edition set via toml edit), append a "(custom)"-
        # tagged entry so the visible state matches winpodx.toml even
        # though the curated list doesn't carry that tag.
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

        # Localization picks (#254 phase 3). Language / Region / Keyboard
        # land on the Windows guest only during first install (dockur's
        # USERNAME / LANGUAGE / REGION / KEYBOARD env vars are first-
        # boot-only), so a change here requires a container recreate
        # with --wipe-storage to actually reach the guest. Timezone is
        # OEM-applied via tzutil and applies on every (re)create. The
        # save handler surfaces the recreate prompt when any localization
        # row is dirty, mirroring the existing edition / cpu / ram path.
        self.input_language = self._build_locale_combo(
            cfg_value=self.cfg.pod.language,
            options=_DOCKUR_LANGUAGES,
            empty_label=tr("Auto (English)"),
        )
        self.input_language.setToolTip(
            tr(
                "Windows installation language (dockur LANGUAGE env). "
                "Applied on first install only; changing this on an existing "
                "guest requires `winpodx pod recreate --wipe-storage`."
            )
        )
        self.input_region = self._build_locale_combo(
            cfg_value=self.cfg.pod.region,
            options=_DOCKUR_REGIONS,
            empty_label=tr("Auto (en-001)"),
        )
        self.input_region.setToolTip(
            tr("Windows locale region in BCP-47 form (dockur REGION env). First-install only.")
        )
        self.input_keyboard = self._build_locale_combo(
            cfg_value=self.cfg.pod.keyboard,
            options=_DOCKUR_KEYBOARDS,
            empty_label=tr("Auto (en-US)"),
        )
        self.input_keyboard.setToolTip(
            tr("Windows keyboard layout (dockur KEYBOARD env). First-install only.")
        )

        from winpodx.utils.locale import detect_timezone

        detected_tz = detect_timezone()
        self.input_timezone = self._build_locale_combo(
            cfg_value=self.cfg.pod.timezone,
            options=_COMMON_TIMEZONES,
            empty_label=tr("Auto (detected: {tz})").format(tz=detected_tz),
        )
        self.input_timezone.setToolTip(
            tr(
                "Windows guest timezone (IANA name). Empty = host autodetect "
                "at compose time. Applied via OEM `tzutil /s <id>` on every "
                "container (re)create -- unlike language/region/keyboard, "
                "this does NOT require --wipe-storage."
            )
        )

        # #245: tuning profile dropdown -- maps to cfg.pod.tuning_profile.
        # PR A: "performance" added; relocated from Container/VM card into
        # its own dedicated card so the summary panel + dropdown live
        # inside one frame (the previous orphan label outside the card
        # looked unmoored).
        self.input_tuning_profile = QComboBox()
        tuning_options = [
            (tr("Auto (recommended)"), "auto"),
            (tr("Performance (force pinning + no balloon)"), "performance"),
            (tr("Safe (Windows-guest-only tunings)"), "safe"),
            (tr("Off (baseline dockur defaults)"), "off"),
            (tr("Manual (edit winpodx.toml)"), "manual"),
        ]
        for label, value in tuning_options:
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

        # PR B (UI polish): split the old "Container / VM" card into
        # two narrower cards. The combined card had 10 form rows --
        # taller than the 7-row RDP card next to it and visually
        # asymmetric. Splitting hardware (Backend / Edition / CPU /
        # RAM / Idle / Max Sessions = 6 rows) from localization
        # (Language / Region / Keyboard / Timezone = 4 rows) gives a
        # roughly height-balanced two-column row up top, with the
        # Localization card flowing full-width below.
        hardware_card = self._settings_card(
            tr("▨  Hardware"),
            tr("Backend, edition, and resource allocation"),
            [
                (tr("Backend"), self.input_backend),
                (tr("Windows Edition"), self.input_win_version),
                (tr("CPU Cores"), self.input_cpu),
                (tr("RAM (GB)"), self.input_ram),
                (tr("Idle Timeout"), self.input_idle),
                (tr("Max Sessions (1-50)"), self.input_max_sessions),
            ],
        )
        # Always-visible RAM budget summary on the Hardware card, so the
        # session math is shown up front rather than only surfacing when
        # the over-subscription warning fires. Reuses the same
        # estimate_session_memory() math as check_session_budget().
        self.budget_summary_label = QLabel("")
        self.budget_summary_label.setWordWrap(True)
        self.budget_summary_label.setStyleSheet(
            f"color: {C.SUBTEXT0}; background: transparent; font-size: {FONT_CAPTION}px;"
        )
        hardware_card.layout().addWidget(self.budget_summary_label)
        cols.addWidget(hardware_card)

        # Surface the recreate consequence before the user edits Port /
        # CPU / RAM / Edition rather than only inside the save-time
        # confirm dialog. Changing Edition additionally triggers a disk
        # wipe (covered by the Localization callout's wording too).
        recreate_callout = make_warning_callout(
            tr(
                "Changing Port, CPU, RAM, Edition or Tuning Profile recreates the "
                "container (Windows reboots, ~1-2 min). Changing the Edition also "
                "wipes the Windows disk and reinstalls (~5-10 min)."
            ),
            level="warn",
        )
        layout.addWidget(recreate_callout)

        layout.addLayout(cols)

        # Language / Region / Keyboard are first-install-only env knobs:
        # applying a change destroys the Windows disk and reinstalls.
        # Timezone is OEM-applied on every (re)create and does NOT wipe.
        locale_callout = make_warning_callout(
            tr(
                "Changing Language, Region or Keyboard wipes the Windows disk and "
                "reinstalls (~5-10 min) — these only apply on a fresh install. "
                "Timezone applies on the next recreate without a wipe."
            ),
            level="danger",
        )
        layout.addWidget(locale_callout)

        localization_card = self._settings_card(
            tr("🌐  Localization"),
            tr("Windows install language / region / keyboard / timezone"),
            [
                (tr("Language"), self.input_language),
                (tr("Region"), self.input_region),
                (tr("Keyboard"), self.input_keyboard),
                (tr("Timezone"), self.input_timezone),
            ],
        )
        layout.addWidget(localization_card)

        # #245 + PR A: Performance Tuning lives in its own card below
        # the two top cards. Card contains the dropdown + a live
        # detection summary panel rendering `format_tuning_summary()`
        # output. Renders once at build time -- users wanting a fresh
        # probe re-open Settings.
        try:
            from winpodx.utils.specs import (
                detect_tuning_capability,
                format_tuning_summary,
                recommend_tuning_profile,
            )

            tuning_cap = detect_tuning_capability(
                vm_cpu_cores=self.cfg.pod.cpu_cores, vm_ram_gb=self.cfg.pod.ram_gb
            )
            tuning_summary = format_tuning_summary(
                tuning_cap,
                recommend_tuning_profile(tuning_cap, user_pref=self.cfg.pod.tuning_profile),
            )
        except Exception:  # noqa: BLE001 -- never block Settings rendering
            tuning_summary = tr("  (tuning detection failed; see `winpodx info` for details)")
        tuning_card = self._build_tuning_card(self.input_tuning_profile, tuning_summary)
        layout.addWidget(tuning_card)

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

        # "Applies immediately" subsection. The controls below (autostart,
        # UI language; the reverse-open enable checkbox above behaves the
        # same) persist the moment you change them — unlike the form fields
        # above, which only commit when you click "Save Settings". The
        # header makes that split explicit so the Save button's scope isn't
        # ambiguous.
        layout.addSpacing(SPACE_M)
        applies_now_header = QLabel(tr("Applies immediately"))
        applies_now_header.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; "
            f"font-size: {FONT_CAPTION}px; font-weight: 500;"
        )
        layout.addWidget(applies_now_header)
        applies_now_caption = QLabel(
            tr("These take effect right away — no need to click Save Settings.")
        )
        applies_now_caption.setWordWrap(True)
        applies_now_caption.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: {FONT_CAPTION}px;"
        )
        layout.addWidget(applies_now_caption)

        # Autostart-at-login toggle. File existence under
        # ``~/.config/autostart/winpodx-tray.desktop`` is the source of
        # truth -- no cfg.toml field needed, and the user can drop the
        # .desktop file by hand to opt out without launching the GUI.
        from PySide6.QtWidgets import QCheckBox

        from winpodx.desktop.autostart import (
            is_autostart_enabled,
            set_autostart,
        )

        self.checkbox_autostart_tray = QCheckBox(
            tr("Start the Windows pod at login (launches the tray + boots the pod)")
        )
        self.checkbox_autostart_tray.setChecked(is_autostart_enabled())
        self.checkbox_autostart_tray.setStyleSheet(CHECKBOX)

        def _on_autostart_toggled(checked: bool) -> None:
            # Apply immediately — no Save Settings click needed. Unified
            # toggle: installs/removes the tray autostart entry AND flips
            # cfg.pod.auto_start so the tray brings the pod up on login.
            try:
                set_autostart(bool(checked))
            except OSError as e:
                logging.getLogger(__name__).warning("Could not toggle autostart: %s", e)

        self.checkbox_autostart_tray.toggled.connect(_on_autostart_toggled)
        layout.addWidget(self.checkbox_autostart_tray)

        # winpodx UI language (the tray / GUI / CLI text itself -- distinct
        # from the *guest* install language above). Default 'auto' follows
        # the host locale. Applied to new winpodx processes (a note tells the
        # user to restart; live-retranslating an already-built window is out
        # of scope).
        from winpodx.core.config import _UI_LANGUAGES

        _LANG_LABELS = {
            "auto": tr("Auto (system language)"),
            "en": "English",
            "ko": "한국어",
            "zh": "中文",
            "ja": "日本語",
            "de": "Deutsch",
            "fr": "Français",
            "it": "Italiano",
        }
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel(tr("WinPodX UI language")))
        self.input_ui_language = QComboBox()
        self.input_ui_language.setStyleSheet(COMBO)
        for code in _UI_LANGUAGES:
            self.input_ui_language.addItem(_LANG_LABELS.get(code, code), code)
        cur = self.cfg.ui.language if self.cfg.ui.language in _UI_LANGUAGES else "auto"
        self.input_ui_language.setCurrentIndex(self.input_ui_language.findData(cur))

        def _on_ui_language_changed(idx: int) -> None:
            code = self.input_ui_language.itemData(idx)
            if not code:
                return
            try:
                from winpodx.core.config import Config
                from winpodx.core.i18n import set_language

                c = Config.load()
                c.ui.language = code
                c.save()
                set_language(code)
                self.cfg.ui.language = code
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).warning("Could not set UI language: %s", e)

        self.input_ui_language.currentIndexChanged.connect(_on_ui_language_changed)
        lang_row.addWidget(self.input_ui_language, 1)
        layout.addLayout(lang_row)
        layout.addWidget(QLabel(tr("Restart WinPodX (tray / GUI) to apply the language change.")))

        # Budget warning — only visible when max_sessions over-subscribes ram_gb.
        # Live-updates as the user types in either field.
        self.budget_warning_label = QLabel("")
        self.budget_warning_label.setWordWrap(True)
        self.budget_warning_label.setStyleSheet(
            f"color: {C.YELLOW}; background: transparent; "
            f"font-size: {FONT_CAPTION}px; padding: {SPACE_XS}px {SPACE_S}px;"
        )
        self.budget_warning_label.setVisible(False)
        layout.addWidget(self.budget_warning_label)
        self.input_ram.textChanged.connect(self._update_budget_warning)
        self.input_max_sessions.textChanged.connect(self._update_budget_warning)
        self._update_budget_warning()

        layout.addSpacing(SPACE_L)

        save_caption = QLabel(
            tr("Persists the form fields above. The ‘Applies immediately’ controls save on change.")
        )
        save_caption.setWordWrap(True)
        save_caption.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: {FONT_CAPTION}px;"
        )
        layout.addWidget(save_caption)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page

    def _build_tuning_card(self, profile_combo: QComboBox, summary_text: str) -> QFrame:
        """Build the Performance Tuning settings card (#245, PR A).

        Same visual shell as :meth:`_settings_card` so it slots into the
        Settings page without theme drift, but adds a read-only monospace
        summary panel below the dropdown -- inside the same frame, so the
        previously-orphan summary label is no longer floating outside any
        card.
        """
        card = QFrame()
        card.setObjectName("settingsSection")
        card.setStyleSheet(
            SETTINGS_SECTION
            + f"QLabel {{ color: {C.TEXT}; font-size: {FONT_BODY}px; background: transparent; }}"
            + INPUT
            + COMBO
        )
        add_shadow(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_XS)

        header = QLabel(tr("◨  Performance Tuning"))
        header.setStyleSheet(
            f"background: transparent; color: {C.BLUE}; "
            f"font-size: {FONT_HEADER}px; font-weight: 600;"
        )
        layout.addWidget(header)

        sub = QLabel(tr("QEMU + Windows-on-KVM knob preset"))
        sub.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: {FONT_CAPTION}px;"
        )
        layout.addWidget(sub)

        accent_line = QFrame()
        accent_line.setFixedHeight(1)
        accent_line.setStyleSheet(f"background: {C.SURFACE1};")
        layout.addWidget(accent_line)
        layout.addSpacing(SPACE_M)

        form = QGridLayout()
        form.setVerticalSpacing(SPACE_S + 2)
        form.setHorizontalSpacing(SPACE_M)
        form.setColumnMinimumWidth(0, 150)
        lbl = QLabel(tr("Profile"))
        lbl.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; font-size: {FONT_BODY}px;"
        )
        form.addWidget(lbl, 0, 0, alignment=Qt.AlignmentFlag.AlignRight)
        form.addWidget(profile_combo, 0, 1)
        layout.addLayout(form)

        layout.addSpacing(SPACE_M)

        summary_header = QLabel(tr("Detection summary (this host)"))
        summary_header.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; "
            f"font-size: {FONT_CAPTION}px; font-weight: 500;"
        )
        layout.addWidget(summary_header)

        # Inner frame so the monospace block reads as a code panel rather
        # than free-floating text. Subtle surface tint + slight padding
        # gives it visual containment without competing with the card
        # frame itself.
        summary_frame = QFrame()
        summary_frame.setStyleSheet(
            f"background: {C.MANTLE}; border-radius: {RADIUS_S}px; padding: {SPACE_S - 2}px;"
        )
        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(SPACE_S + 2, SPACE_S, SPACE_S + 2, SPACE_S)
        summary_layout.setSpacing(0)
        self.tuning_summary_label = QLabel(summary_text)
        self.tuning_summary_label.setStyleSheet(
            f"background: transparent; font-family: 'JetBrainsMono Nerd Font', "
            f"'Cascadia Code', 'Fira Code', monospace; "
            f"font-size: {FONT_CAPTION}px; color: {C.SUBTEXT1};"
        )
        self.tuning_summary_label.setWordWrap(False)
        summary_layout.addWidget(self.tuning_summary_label)
        layout.addWidget(summary_frame)

        return card

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
            + f"QLabel {{ color: {C.TEXT}; font-size: {FONT_BODY}px; background: transparent; }}"
            + INPUT
            + COMBO
        )
        add_shadow(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_XS)

        header = QLabel(title)
        header.setStyleSheet(
            f"background: transparent; color: {C.BLUE}; "
            f"font-size: {FONT_HEADER}px; font-weight: 600;"
        )
        layout.addWidget(header)

        sub = QLabel(subtitle)
        sub.setStyleSheet(
            f"background: transparent; color: {C.OVERLAY0}; font-size: {FONT_CAPTION}px;"
        )
        layout.addWidget(sub)

        accent_line = QFrame()
        accent_line.setFixedHeight(1)
        accent_line.setStyleSheet(f"background: {C.SURFACE1};")
        layout.addWidget(accent_line)
        layout.addSpacing(SPACE_M + 2)

        form = QGridLayout()
        form.setVerticalSpacing(SPACE_S + 2)
        form.setHorizontalSpacing(SPACE_M)
        form.setColumnMinimumWidth(0, 150)

        for row, (label, widget) in enumerate(fields):
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"background: transparent; color: {C.SUBTEXT0}; font-size: {FONT_BODY}px;"
            )
            form.addWidget(lbl, row, 0, alignment=Qt.AlignmentFlag.AlignRight)
            form.addWidget(widget, row, 1)

        layout.addLayout(form)
        return card

    def _update_budget_warning(self) -> None:
        """Live-update the session memory budget warning label.

        Quiet when the estimate fits; shows a wrapped message when
        max_sessions over-subscribes ram_gb. Also refreshes the
        always-visible one-line budget summary on the Hardware card.
        Called whenever either spinbox text changes.
        """
        from winpodx.core.config import (
            Config,
            check_session_budget,
            estimate_session_memory,
        )

        try:
            sessions = int(self.input_max_sessions.text() or "10")
            ram = int(self.input_ram.text() or "4")
        except ValueError:
            self.budget_warning_label.setVisible(False)
            if hasattr(self, "budget_summary_label"):
                self.budget_summary_label.setText("")
            return

        clamped_sessions = max(1, min(50, sessions))
        clamped_ram = max(1, ram)

        # Always-visible budget math (~100 MB/session + ~2 GB guest base).
        if hasattr(self, "budget_summary_label"):
            est = estimate_session_memory(clamped_sessions)
            per_session_mb = 100
            self.budget_summary_label.setText(
                tr("Budget: {sessions} sessions x ~{per} MB + base ≈ {est:.1f} of {ram} GB").format(
                    sessions=clamped_sessions,
                    per=per_session_mb,
                    est=est,
                    ram=clamped_ram,
                )
            )

        tmp = Config()
        tmp.pod.max_sessions = clamped_sessions
        tmp.pod.ram_gb = clamped_ram
        msg = check_session_budget(tmp)
        if msg:
            self.budget_warning_label.setText(tr("WARNING: {msg}").format(msg=msg))
            self.budget_warning_label.setVisible(True)
        else:
            self.budget_warning_label.setVisible(False)

    def _build_locale_combo(
        self,
        *,
        cfg_value: str,
        options: list[tuple[str, str]],
        empty_label: str,
    ) -> QComboBox:
        """Build a localization dropdown with an ``Auto`` first option.

        Storage contract: the empty string ``""`` represents "let
        compose-time autodetect pick the value". Selecting the first
        ("Auto ...") row stores ``""``; any other row stores the
        canonical dockur value from the (label, value) tuple list.
        Out-of-list ``cfg_value`` is appended as a ``(custom)`` entry
        and shown selected -- mirrors the existing ``input_win_version``
        handling so hand-edited TOML values stay round-trippable.
        """
        combo = QComboBox()
        combo.addItem(empty_label, "")
        for label, value in options:
            combo.addItem(label, value)

        idx = combo.findData(cfg_value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            # Out-of-curated-list value from hand-edited TOML -- expose
            # it as a (custom) entry so the visible state matches the
            # underlying config.
            combo.addItem(f"{cfg_value} (custom)", cfg_value)
            combo.setCurrentIndex(combo.count() - 1)
        return combo

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

        # Pull Windows edition from the combo's data role (canonical
        # dockur tag). Read-only combo so currentData() always matches
        # one of the curated tags or the (custom)-tagged entry that was
        # injected for an out-of-list winpodx.toml value at build time.
        new_win_version = self.input_win_version.currentData()

        # Localization picks (#254 phase 3). Empty string == autodetect.
        new_language = self.input_language.currentData() or ""
        new_region = self.input_region.currentData() or ""
        new_keyboard = self.input_keyboard.currentData() or ""
        new_timezone = self.input_timezone.currentData() or ""
        new_tuning_profile = self.input_tuning_profile.currentData() or "auto"

        old_cfg = Config.load()
        # ``needs_container`` is true when any first-boot env knob is
        # dirty. Language / region / keyboard / edition only take effect
        # on a fresh Windows install -- the recreate prompt below warns
        # the user that a plain recreate won't reach the guest, and the
        # --wipe-storage flow is the only path that does. Timezone is
        # OEM-applied via tzutil on every container (re)create so it's
        # treated alongside CPU / RAM / port / user as "recreate
        # without wipe is enough".
        needs_container = (
            cpu != old_cfg.pod.cpu_cores
            or ram != old_cfg.pod.ram_gb
            or port != old_cfg.rdp.port
            or self.input_user.text() != old_cfg.rdp.user
            or new_win_version != old_cfg.pod.win_version
            or new_timezone != old_cfg.pod.timezone
            # #245: tuning_profile changes the QEMU ARGUMENTS env in
            # compose.yaml. Container recreate is required to pick up
            # the new -cpu sub-options (+vmx/+svm, hv-*, +invtsc) and
            # -device args (virtio-rng-pci).
            or new_tuning_profile != old_cfg.pod.tuning_profile
        )
        needs_wipe = (
            new_win_version != old_cfg.pod.win_version
            or new_language != old_cfg.pod.language
            or new_region != old_cfg.pod.region
            or new_keyboard != old_cfg.pod.keyboard
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
        self.cfg.pod.max_sessions = max_sessions
        self.cfg.pod.language = new_language
        self.cfg.pod.region = new_region
        self.cfg.pod.keyboard = new_keyboard
        self.cfg.pod.timezone = new_timezone
        self.cfg.pod.tuning_profile = new_tuning_profile
        # Let __post_init__ clamp max_sessions to [1, 50] before save.
        self.cfg.pod.__post_init__()
        self.cfg.save()

        if needs_container and self.cfg.pod.backend in ("podman", "docker"):
            if needs_wipe:
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
            reply = QMessageBox.question(
                self,
                tr("Restart Container"),
                prompt,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.info_label.setText(
                    tr("Wiping Windows disk + recreating...")
                    if needs_wipe
                    else tr("Recreating container...")
                )
                QApplication.processEvents()
                wipe_storage = needs_wipe

                def _recreate() -> None:
                    try:
                        from winpodx.cli.pod import _wipe_pod_storage
                        from winpodx.cli.setup_cmd import (
                            _generate_compose,
                            _recreate_container,
                        )
                        from winpodx.core.pod import stop_pod

                        if wipe_storage:
                            # Stop before wipe -- can't remove a volume
                            # that's still attached to a running container,
                            # and bind-mount contents under an active
                            # container risk EBUSY on rmtree.
                            stop_pod(self.cfg)
                            _wipe_pod_storage(self.cfg)

                        _generate_compose(self.cfg)
                        _recreate_container(self.cfg)
                        self.app_launched.emit(
                            tr("Container recreated; Windows reinstalling")
                            if wipe_storage
                            else tr("Container restarted")
                        )
                    except Exception as e:  # noqa: BLE001
                        self.app_launch_failed.emit(tr("Restart failed: {e}").format(e=e))
                        return
                    # v0.5.1: the freshly-recreated guest has no booted
                    # Windows / no agent / no rdprrap / no apps yet. Run
                    # the full bring-up chain (wait pod -> wait agent ->
                    # apply Windows fixes -> discover apps -> reverse-
                    # open sync) on its own worker thread. The call
                    # returns immediately; the user sees progress in
                    # ``BringUpProgressDialog`` (opened on the GUI thread
                    # via the ``bringup_started`` signal).
                    try:
                        self._run_full_bring_up()
                    except Exception as e:  # noqa: BLE001
                        self.app_launch_failed.emit(tr("Bring-up kickoff failed: {e}").format(e=e))

                threading.Thread(target=_recreate, daemon=True).start()
                return

        self.info_label.setText(tr("Settings saved"))
