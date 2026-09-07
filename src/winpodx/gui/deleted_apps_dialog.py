# SPDX-License-Identifier: MIT
"""Restore-deleted-apps dialog (#530).

Deleting an app tombstones its slug (see ``core.app.suppress_app_slug``) so the
next discovery sweep won't resurrect it. This dialog is the un-delete UI: it
lists the tombstoned slugs and lets the user restore one or all of them. The
actual un-suppress + re-discovery is done by the caller via ``on_restore``.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui import theme
from winpodx.gui.theme import (
    CONTROL_HEIGHT_W11,
    FONT_TITLE,
    RADIUS_S,
    SPACE_M,
    SPACE_S,
    C,
)


class DeletedAppsDialog(QDialog):
    """List tombstoned (deleted) app slugs with per-row + bulk restore."""

    def __init__(
        self,
        parent=None,
        *,
        slugs: list[str],
        on_restore: Callable[[list[str]], None],
    ) -> None:
        super().__init__(parent)
        self._on_restore = on_restore
        self._rows: dict[str, QFrame] = {}
        self.setWindowTitle(tr("Deleted Apps"))
        self.setMinimumSize(440, 420)
        self.setStyleSheet(theme.DIALOG + f"QLabel {{ color: {C.TEXT}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(SPACE_M)

        title = QLabel(tr("Restore deleted apps"))
        title.setStyleSheet(f"color: {C.TEXT}; font-size: {FONT_TITLE}px; font-weight: 600;")
        layout.addWidget(title)
        sub = QLabel(
            tr(
                "Apps you removed are tombstoned so discovery won't re-add them. "
                "Restore brings one back on the next scan."
            )
        )
        sub.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 12px;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._list_host = QWidget()
        self._list_host.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(SPACE_S)
        scroll.setWidget(self._list_host)
        layout.addWidget(scroll, 1)

        self._empty_lbl = QLabel(tr("No deleted apps."))
        self._empty_lbl.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 12px;")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_lbl)
        self._empty_lbl.setVisible(False)

        for slug in sorted(slugs):
            self._add_row(slug)

        strip = QFrame()
        strip.setObjectName("dialogButtonStrip")
        palette = theme._PALETTE
        strip.setStyleSheet(
            f"QFrame#dialogButtonStrip {{ background: {palette.control_fill}; "
            f"border: none; border-top: 1px solid {palette.divider}; }}"
        )
        btn_row = QHBoxLayout(strip)
        btn_row.setContentsMargins(24, 24, 24, 24)
        btn_row.setSpacing(SPACE_S)
        self._restore_all_btn = QPushButton(tr("Restore all"))
        self._restore_all_btn.setStyleSheet(theme.BTN_PRIMARY)
        self._restore_all_btn.setMinimumSize(96, CONTROL_HEIGHT_W11)
        self._restore_all_btn.clicked.connect(self._on_restore_all)
        btn_row.addWidget(self._restore_all_btn)
        btn_row.addStretch()
        close = QPushButton(tr("Close"))
        close.setStyleSheet(theme.BTN_SECONDARY)
        close.setMinimumSize(96, CONTROL_HEIGHT_W11)
        close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        layout.addWidget(strip)

        self._refresh_empty_state()

    def _add_row(self, slug: str) -> None:
        row = QFrame()
        row.setStyleSheet(
            f"QFrame {{ background: {C.SURFACE0}; border-radius: {RADIUS_S}px; }}"
            f"QFrame:hover {{ background: {C.SURFACE1}; }}"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)
        name = QLabel(slug)
        name.setStyleSheet(f"background: transparent; color: {C.TEXT}; font-size: 13px;")
        rl.addWidget(name)
        rl.addStretch()
        restore = QPushButton(tr("Restore"))
        restore.setStyleSheet(theme.BTN_SECONDARY)
        restore.setMinimumHeight(CONTROL_HEIGHT_W11)
        restore.clicked.connect(lambda _=False, s=slug: self._on_restore_one(s))
        rl.addWidget(restore)
        self._list_layout.addWidget(row)
        self._rows[slug] = row

    def _on_restore_one(self, slug: str) -> None:
        self._on_restore([slug])
        row = self._rows.pop(slug, None)
        if row is not None:
            row.setParent(None)
            row.deleteLater()
        self._refresh_empty_state()

    def _on_restore_all(self) -> None:
        if not self._rows:
            return
        self._on_restore(list(self._rows))
        self.accept()

    def _refresh_empty_state(self) -> None:
        has_rows = bool(self._rows)
        self._empty_lbl.setVisible(not has_rows)
        self._restore_all_btn.setEnabled(has_rows)
