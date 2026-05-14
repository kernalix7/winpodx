"""License-tab mixin for ``WinpodxWindow``.

Surfaces the project license (MIT) plus third-party acknowledgments
inside the GUI so the user can read what they're running on, what
they're allowed to do with winpodx, and which upstream projects
deserve credit — without leaving the app or hunting through the
source tree. Pulled into its own mixin file to stay consistent with
the per-page-builder pattern the rest of the GUI follows
(LibraryPageMixin / SettingsPageMixin / etc.).

Host-class contract (only listed for readers; not enforced):
    cfg: winpodx.core.config.Config   — only needed to look up
        bundle paths via ``winpodx.utils.paths.bundle_dir``.
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from winpodx.gui.theme import SCROLL_AREA, TERMINAL, C
from winpodx.utils.paths import bundle_dir

log = logging.getLogger(__name__)


# Hand-maintained acknowledgments. Each entry: (display_name, license,
# what-we-use-it-for). Kept short on purpose — the LICENSE file +
# upstream project pages are the canonical legal source; this is just
# a "who got us here" summary the user can scan in 10 seconds.
_THIRD_PARTY_ACK: tuple[tuple[str, str, str], ...] = (
    ("dockur/windows", "MIT", "Windows-in-Docker base image — the core VM stack winpodx wraps"),
    (
        "dockur/windows-arm",
        "MIT",
        "Windows-on-ARM container image for aarch64 hosts (Pi 5, Ampere)",
    ),
    (
        "FreeRDP 3",
        "Apache-2.0",
        "RDP client with RemoteApp/RAIL — bridges Windows apps to Linux desktops",
    ),
    ("rdprrap", "GPL-3.0", "RDPWrap-style TermService DLL hook for multi-session RDP in the guest"),
    ("PySide6 / Qt 6", "LGPL-3.0", "GUI framework — main window, tray, settings"),
    (
        "electron/rcedit",
        "MIT",
        "Vendored Windows .exe resource editor (icons embedded into per-slug reverse-open shims)",
    ),
    ("Catppuccin Mocha", "MIT", "Color palette used across the GUI"),
)


class LicensePageMixin:
    """Builds the License tab — MIT text + third-party acknowledgments."""

    def _build_license_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_AREA)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 28, 32, 32)
        layout.setSpacing(14)

        # --- License title -------------------------------------------------
        title = QLabel("License")
        title.setStyleSheet(
            f"background: transparent; color: {C.TEXT}; font-size: 22px; font-weight: bold;"
        )
        layout.addWidget(title)

        subtitle = QLabel(
            "winpodx is MIT-licensed open source. See LICENSE in the source "
            "tree for the canonical text."
        )
        subtitle.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 12px;")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # --- MIT license text ----------------------------------------------
        license_text = self._read_license_text()
        license_view = QTextEdit()
        license_view.setReadOnly(True)
        license_view.setStyleSheet(TERMINAL)
        license_view.setPlainText(license_text)
        license_view.setFixedHeight(260)
        layout.addWidget(license_view)

        # --- Third-party acknowledgments -----------------------------------
        ack_header = QLabel("Third-party components")
        ack_header.setStyleSheet(
            f"background: transparent; color: {C.BLUE};"
            " font-size: 15px; font-weight: bold; padding-top: 8px;"
        )
        layout.addWidget(ack_header)

        ack_intro = QLabel(
            "winpodx ships and depends on these upstream projects. Each is "
            "used under its own license; consult the upstream repository for "
            "the canonical text."
        )
        ack_intro.setStyleSheet(f"background: transparent; color: {C.OVERLAY0}; font-size: 12px;")
        ack_intro.setWordWrap(True)
        layout.addWidget(ack_intro)

        for name, license_, purpose in _THIRD_PARTY_ACK:
            row = QFrame()
            row.setStyleSheet(f"background: {C.SURFACE0}; border-radius: 6px; padding: 8px;")
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setSpacing(2)

            heading = QLabel(f"{name}  ·  {license_}")
            heading.setStyleSheet(
                f"background: transparent; color: {C.TEXT}; font-size: 13px; font-weight: bold;"
            )
            row_layout.addWidget(heading)

            detail = QLabel(purpose)
            detail.setStyleSheet(f"background: transparent; color: {C.SUBTEXT1}; font-size: 12px;")
            detail.setWordWrap(True)
            row_layout.addWidget(detail)

            layout.addWidget(row)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page

    def _read_license_text(self) -> str:
        """Return the project LICENSE contents, or a stub on failure.

        Resolved via ``bundle_dir()`` so the file is found in every
        install mode (source checkout, pip wheel, FHS package install,
        ``curl | bash`` drop). Falls back to a one-line stub when the
        bundle path can't be read so the tab never renders as blank
        — losing the inline copy is non-fatal because the canonical
        license still lives in the repo and the source tarball.
        """
        try:
            path = bundle_dir() / "LICENSE"
            return path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            log.warning("Could not read LICENSE from bundle_dir", exc_info=True)
            return (
                "winpodx is MIT-licensed. See the LICENSE file in the "
                "project repository for the canonical text:\n"
                "  https://github.com/kernalix7/winpodx/blob/main/LICENSE"
            )
