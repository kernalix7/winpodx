# SPDX-License-Identifier: MIT
"""Third-party acknowledgments for the License page."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui._main_window_secondary_style import apply_card_qss
from winpodx.gui.icons import load_icon

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
        "TOML parser fallback for Python 3.10 (stdlib tomllib used on 3.11+)",
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
        "cfg-if (Rust crate)",
        "MIT OR Apache-2.0",
        "Transitive dependency statically linked into the reverse-open Windows shim",
        "https://github.com/rust-lang/cfg-if",
    ),
    (
        "GitHub Primer Dark",
        "MIT (Copyright 2013 GitHub Inc.)",
        "Color palette inspiration for the GUI theme (see src/winpodx/gui/theme.py)",
        "https://github.com/primer/primitives",
    ),
    (
        "Microsoft Selawik",
        "OFL-1.1",
        "Bundled unmodified UI fallback font (Reserved Font Name: Selawik)",
        "https://github.com/microsoft/Selawik",
    ),
)


def build_ack_card(name: str, license_: str, purpose: str, url: str) -> QFrame:
    """Build one third-party acknowledgment card (name + license + link)."""
    row = QFrame()
    row.setObjectName("settingsCard")
    apply_card_qss(row)
    row_layout = QVBoxLayout(row)
    row_layout.setContentsMargins(
        theme_mod.SPACE_L, theme_mod.SPACE_L, theme_mod.SPACE_L, theme_mod.SPACE_L
    )
    row_layout.setSpacing(theme_mod.SPACE_S)

    header_row = QHBoxLayout()
    header_row.setContentsMargins(0, 0, 0, 0)
    header_row.setSpacing(theme_mod.SPACE_S)

    heading = QLabel(name)
    heading.setStyleSheet(
        f"background: transparent; color: {theme_mod.C.TEXT}; "
        f"font-size: {theme_mod.FONT_BODY}px; font-weight: 600;"
    )
    header_row.addWidget(heading, 0)

    chip = QLabel(license_)
    chip.setStyleSheet(
        f"background: {theme_mod.rgba(theme_mod.TOOL_ACCENT, 0.12)}; "
        f"color: {theme_mod.TOOL_ICON_FG};"
        f" border: 1px solid {theme_mod.rgba(theme_mod.TOOL_ACCENT, 0.26)};"
        f" border-radius: {theme_mod.RADIUS_XS}px; padding: 1px 7px;"
        f" font-size: {theme_mod.FONT_CAPTION}px; font-weight: 400;"
    )
    header_row.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
    header_row.addStretch(1)
    row_layout.addLayout(header_row)

    detail = QLabel(tr(purpose))
    detail.setStyleSheet(
        f"background: transparent; color: {theme_mod.C.SUBTEXT1}; "
        f"font-size: {theme_mod.FONT_BODY}px;"
    )
    detail.setWordWrap(True)
    row_layout.addWidget(detail)

    link_row = QHBoxLayout()
    link_row.setContentsMargins(0, theme_mod.SPACE_XS, 0, 0)
    link_row.setSpacing(theme_mod.SPACE_S)

    globe = QLabel()
    globe.setFixedSize(14, 14)
    globe.setPixmap(load_icon("globe", theme_mod.TOOL_ICON_FG, 14).pixmap(14, 14))
    globe.setStyleSheet("background: transparent;")
    globe.setAlignment(Qt.AlignmentFlag.AlignTop)
    link_row.addWidget(globe, 0, Qt.AlignmentFlag.AlignTop)

    link = QLabel(url)
    link.setStyleSheet(
        f"background: transparent; color: {theme_mod.C.SUBTEXT0}; "
        f"font-size: {theme_mod.FONT_CAPTION}px;"
    )
    link.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    link.setWordWrap(True)
    link_row.addWidget(link, 1)
    row_layout.addLayout(link_row)
    return row
