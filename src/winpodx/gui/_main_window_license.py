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
    QLabel,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui import theme as theme_mod
from winpodx.gui._main_window_license_acks import _THIRD_PARTY_ACK, build_ack_card
from winpodx.gui._main_window_logs_ui import log_viewer_qss, viewer_card_qss
from winpodx.gui._main_window_secondary_style import (
    make_named_settings_group,
    make_value_label,
    mount_settings_column,
    restyle_settings_cards,
)
from winpodx.gui._widget_helpers import make_section_label, make_settings_card
from winpodx.utils.paths import bundle_dir

log = logging.getLogger(__name__)


class LicensePageMixin:
    """Builds the License tab — MIT text + third-party acknowledgments."""

    def _build_license_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(theme_mod.SCROLL_AREA)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = mount_settings_column(content)

        register = getattr(self, "_register_page_header", None)
        if callable(register):
            register(
                7,
                tr("License"),
                tr(
                    "WinPodX is MIT-licensed open source. See LICENSE in the source "
                    "tree for the canonical text."
                ),
            )

        layout.addWidget(self._build_license_summary())
        layout.addWidget(make_section_label(tr("License text")))
        layout.addWidget(self._build_license_section())

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
            f"background: transparent; color: {theme_mod.C.SUBTEXT0}; "
            f"font-size: {theme_mod.FONT_CAPTION}px;"
        )
        ack_intro.setWordWrap(True)
        layout.addWidget(ack_intro)

        ack_group, ack_stack = make_named_settings_group(tr("Upstream"))
        for entry in _THIRD_PARTY_ACK:
            ack_stack.addWidget(self._build_ack_card(*entry))
        layout.addWidget(ack_group)

        layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._license_page = page
        return page

    def _build_license_summary(self) -> QWidget:
        holder, layout = make_named_settings_group()
        layout.addWidget(
            make_settings_card("", tr("License"), action=make_value_label("MIT"), compact=True)
        )
        layout.addWidget(
            make_settings_card(
                "", tr("Upstream"), action=make_value_label("dockur/windows"), compact=True
            )
        )
        layout.addWidget(
            make_settings_card(
                "",
                tr("Third-party components"),
                action=make_value_label(str(len(_THIRD_PARTY_ACK))),
                compact=True,
            )
        )
        return holder

    def _build_license_section(self) -> QFrame:
        """Card wrapping the read-only MIT license text in the terminal panel."""
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setStyleSheet(viewer_card_qss())

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            theme_mod.SPACE_L, theme_mod.SPACE_L, theme_mod.SPACE_L, theme_mod.SPACE_L
        )
        card_layout.setSpacing(0)

        license_view = QTextEdit()
        license_view.setReadOnly(True)
        license_view.setStyleSheet(log_viewer_qss())
        license_view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        license_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        license_view.setPlainText(self._read_license_text())
        license_view.setFixedHeight(420)
        self._license_view = license_view
        card_layout.addWidget(license_view)
        return card

    def _restyle_license(self) -> None:
        root = getattr(self, "_license_page", None) or getattr(self, "page", None)
        if root is None:
            return
        restyle_settings_cards(root)
        view = getattr(self, "_license_view", None)
        if view is not None:
            view.setStyleSheet(log_viewer_qss())
            parent = view.parentWidget()
            if isinstance(parent, QFrame):
                parent.setStyleSheet(viewer_card_qss())

    def _build_ack_card(self, name: str, license_: str, purpose: str, url: str) -> QFrame:
        """Build one third-party acknowledgment card (name + license + link)."""
        return build_ack_card(name, license_, purpose, url)

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
