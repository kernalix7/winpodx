# SPDX-License-Identifier: MIT
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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui._widget_helpers import add_shadow, make_page_header, make_section_label
from winpodx.gui.icons import load_icon
from winpodx.gui.theme import (
    FONT_BODY,
    FONT_CAPTION,
    FONT_HEADER,
    RADIUS_XS,
    SCROLL_AREA,
    SETTINGS_SECTION,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XS,
    SPACE_XXL,
    TERMINAL,
    TOOL_ACCENT,
    TOOL_ICON_FG,
    C,
    rgba,
)
from winpodx.utils.paths import bundle_dir

log = logging.getLogger(__name__)


# Hand-maintained acknowledgments. Each entry: (display_name, license,
# what-we-use-it-for, upstream_url). Kept short on purpose — the LICENSE
# file + upstream project pages are the canonical legal source; this is
# just a "who got us here" summary the user can scan in 10 seconds. The
# URL lets the user find the upstream source + its full license text.
#
# ``rdprrap`` is bundled in ``config/oem/`` and is technically a
# sibling project authored by the same maintainer (MIT, same
# copyright). It's listed here for transparency about what's
# inside the OEM zip — not because it's "third party" in the
# strict sense. Its own NOTICE file documents that portions are
# source-level ports of stascorp/rdpwrap (Apache-2.0), which is
# why that upstream is also listed below.
_THIRD_PARTY_ACK: tuple[tuple[str, str, str, str], ...] = (
    (
        "dockur/windows",
        "MIT",
        "Windows-in-Docker base image (pulled from Docker Hub at runtime, not bundled)",
        "https://github.com/dockur/windows",
    ),
    (
        "dockur/windows-arm",
        "MIT",
        "Windows-on-ARM container image for aarch64 hosts (Pi 5, Ampere) — runtime-pulled",
        "https://github.com/dockur/windows-arm",
    ),
    (
        "FreeRDP 3",
        "Apache-2.0",
        "RDP client with RemoteApp/RAIL (system-installed dependency)",
        "https://github.com/FreeRDP/FreeRDP",
    ),
    (
        "rdprrap",
        "MIT",
        "TermService DLL hook for multi-session RDP in the guest "
        "(same maintainer; bundled in OEM zip)",
        "https://github.com/kernalix7/rdprrap",
    ),
    (
        "stascorp/rdpwrap",
        "Apache-2.0",
        "Source-level ancestor of rdprrap — bundled rdprrap ports portions of rdpwrap",
        "https://github.com/stascorp/rdpwrap",
    ),
    (
        "llccd/TermWrap",
        "MIT",
        "Source-level ancestor of rdprrap's termwrap DLL (per rdprrap NOTICE section 2)",
        "https://github.com/llccd/TermWrap",
    ),
    (
        "llccd/RDPWrapOffsetFinder",
        "MIT",
        "Source-level ancestor of rdprrap's offset-finder tool (per rdprrap NOTICE section 3)",
        "https://github.com/llccd/RDPWrapOffsetFinder",
    ),
    (
        "PySide6 / Qt 6",
        "LGPL-3.0-only WITH Qt-LGPL-exception-1.1",
        "GUI framework — dynamically linked via import; LGPL §4(d) satisfied",
        "https://doc.qt.io/qtforpython/",
    ),
    (
        "electron/rcedit",
        "MIT (Copyright 2013 GitHub Inc.)",
        "Vendored Windows .exe resource editor for embedding per-slug reverse-open icons",
        "https://github.com/electron/rcedit",
    ),
    (
        "Pillow",
        "MIT-CMU",
        "PNG / SVG → ICO conversion for reverse-open (optional, only with the reverse-open extra)",
        "https://github.com/python-pillow/Pillow",
    ),
    (
        "cairosvg",
        "LGPL-3.0-or-later",
        "SVG rasterizer used during ICO build (optional, only with the reverse-open extra)",
        "https://github.com/Kozea/CairoSVG",
    ),
    (
        "pyxdg",
        "LGPL-2.0-only",
        "freedesktop .desktop file parser for host-app discovery (optional, reverse-open extra)",
        "https://gitlab.freedesktop.org/xdg/pyxdg",
    ),
    (
        "docker (docker-py)",
        "Apache-2.0",
        "Python client for the Docker Engine API (optional, only with the docker extra)",
        "https://github.com/docker/docker-py",
    ),
    (
        "tomli",
        "MIT",
        "TOML parser fallback for Python 3.9 / 3.10 (stdlib tomllib used on 3.11+)",
        "https://github.com/hukkin/tomli",
    ),
    (
        "getrandom (Rust crate)",
        "MIT OR Apache-2.0",
        "Crypto-quality randomness for the reverse-open Windows shim "
        "(statically linked into the vendored .exe)",
        "https://github.com/rust-random/getrandom",
    ),
    (
        "GitHub Primer Dark",
        "MIT (Copyright 2013 GitHub Inc.)",
        "Color palette inspiration for the GUI theme (see src/winpodx/gui/theme.py)",
        "https://github.com/primer/primitives",
    ),
)


class LicensePageMixin:
    """Builds the License tab — MIT text + third-party acknowledgments."""

    def _build_license_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLL_AREA)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(SPACE_XXL, 0, SPACE_XXL, SPACE_XXL)
        layout.setSpacing(SPACE_L)

        # --- Page header ---------------------------------------------------
        layout.addWidget(
            make_page_header(
                tr("License"),
                tr(
                    "WinPodX is MIT-licensed open source. See LICENSE in the source "
                    "tree for the canonical text."
                ),
            )
        )

        # --- MIT license text ----------------------------------------------
        layout.addWidget(make_section_label(tr("License text")))
        layout.addWidget(self._build_license_section())

        # --- Third-party acknowledgments -----------------------------------
        layout.addWidget(make_section_label(tr("Third-party components")))

        ack_intro = QLabel(
            tr(
                "WinPodX ships and depends on these upstream projects. Each is "
                "used under its own license; the upstream link below points at the "
                "source and its canonical license text. The full MIT text for "
                "WinPodX itself is in the LICENSE box above (and in LICENSE in the "
                "source tree)."
            )
        )
        ack_intro.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; font-size: {FONT_CAPTION}px;"
        )
        ack_intro.setWordWrap(True)
        layout.addWidget(ack_intro)

        for entry in _THIRD_PARTY_ACK:
            layout.addWidget(self._build_ack_card(*entry))

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        return page

    def _build_license_section(self) -> QFrame:
        """Card wrapping the read-only MIT license text in the terminal panel."""
        card = QFrame()
        card.setObjectName("settingsSection")
        card.setStyleSheet(SETTINGS_SECTION)
        add_shadow(card, blur=12, y=2, alpha=26)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        card_layout.setSpacing(0)

        license_view = QTextEdit()
        license_view.setReadOnly(True)
        license_view.setStyleSheet(TERMINAL)
        license_view.setPlainText(self._read_license_text())
        license_view.setFixedHeight(260)
        card_layout.addWidget(license_view)
        return card

    def _build_ack_card(self, name: str, license_: str, purpose: str, url: str) -> QFrame:
        """Build one third-party acknowledgment card (name + license + link)."""
        row = QFrame()
        row.setObjectName("settingsSection")
        row.setStyleSheet(SETTINGS_SECTION)
        add_shadow(row, blur=10, y=1, alpha=28)
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(SPACE_L, SPACE_M, SPACE_L, SPACE_M)
        row_layout.setSpacing(SPACE_S)

        # Header line: project name (medium) + a calm license chip.
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(SPACE_S)

        heading = QLabel(name)
        heading.setStyleSheet(
            f"background: transparent; color: {C.TEXT};"
            f" font-size: {FONT_HEADER}px; font-weight: 500;"
        )
        header_row.addWidget(heading, 0)

        chip = QLabel(license_)
        chip.setStyleSheet(
            f"background: {rgba(TOOL_ACCENT, 0.12)}; color: {TOOL_ICON_FG};"
            f" border: 1px solid {rgba(TOOL_ACCENT, 0.26)};"
            f" border-radius: {RADIUS_XS}px; padding: 1px 7px;"
            f" font-size: {FONT_CAPTION}px; font-weight: 400;"
        )
        header_row.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        header_row.addStretch(1)
        row_layout.addLayout(header_row)

        detail = QLabel(tr(purpose))
        detail.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT1}; font-size: {FONT_BODY}px;"
        )
        detail.setWordWrap(True)
        row_layout.addWidget(detail)

        # Upstream source link. Rendered as selectable text (not an
        # auto-opening hyperlink) so winpodx never initiates a network
        # call — the user copies the URL to find the source + its
        # license themselves. The globe glyph reads as "external source"
        # without the loud saturated link-blue.
        link_row = QHBoxLayout()
        link_row.setContentsMargins(0, SPACE_XS, 0, 0)
        link_row.setSpacing(SPACE_S)

        globe = QLabel()
        globe.setFixedSize(14, 14)
        globe.setPixmap(load_icon("globe", TOOL_ICON_FG, 14).pixmap(14, 14))
        globe.setStyleSheet("background: transparent;")
        globe.setAlignment(Qt.AlignmentFlag.AlignTop)
        link_row.addWidget(globe, 0, Qt.AlignmentFlag.AlignTop)

        link = QLabel(url)
        link.setStyleSheet(
            f"background: transparent; color: {C.SUBTEXT0}; font-size: {FONT_CAPTION}px;"
        )
        link.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        link.setWordWrap(True)
        link_row.addWidget(link, 1)
        row_layout.addLayout(link_row)
        return row

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
            return tr(
                "WinPodX is MIT-licensed. See the LICENSE file in the "
                "project repository for the canonical text:\n"
                "  https://github.com/kernalix7/winpodx/blob/main/LICENSE"
            )
