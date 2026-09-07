# SPDX-License-Identifier: MIT
"""Dashboard-local Start-style pinned / recent workspace board."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.app import AppInfo
from winpodx.core.i18n import tr
from winpodx.gui import launcher_state
from winpodx.gui._main_window_library import _AppTile
from winpodx.gui._widget_helpers import make_empty_panel
from winpodx.gui.theme import BTN_SECONDARY, FONT_BODY, SPACE_L, SPACE_M, SPACE_S, C


def _running_app_names() -> set[str]:
    try:
        from winpodx.core.process import list_active_sessions

        return {session.app_name for session in list_active_sessions()}
    except Exception:  # noqa: BLE001 -- dashboard tiles are best-effort
        return set()


class _RunningBadge(QLabel):
    """8px live-session dot, bottom-right of a dashboard tile."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("runningBadge")
        self.setFixedSize(8, 8)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(f"background: {C.GREEN}; border: none; border-radius: 4px;")
        parent.installEventFilter(self)

    def eventFilter(self, watched: QWidget, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._place()
        return False

    def showEvent(self, event: QEvent) -> None:  # noqa: N802
        self._place()
        super().showEvent(event)

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.move(parent.width() - 12, parent.height() - 12)


class _DashboardWorkspaceMixin:
    """Pinned-then-recent workspace surface with a width-derived tile grid."""

    def _build_workspace_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("workspaceSurface")
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._finish_dash_surface(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        lay.setSpacing(SPACE_M)
        self._pinned_section, self._pinned_holder = self._workspace_section(
            "pinnedWorkspaceSection", tr("Pinned")
        )
        self._recent_section, self._recent_holder = self._workspace_section(
            "recentWorkspaceSection", tr("Recent")
        )
        lay.addWidget(self._pinned_section)
        lay.addWidget(self._recent_section)
        # Compatibility seam: empty / pinned-first tests read this holder.
        self._workspace_holder = self._pinned_holder
        return card

    def _workspace_section(self, name: str, title: str) -> tuple[QFrame, QVBoxLayout]:
        section = QFrame()
        section.setObjectName(name)
        lay = QVBoxLayout(section)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(SPACE_S)
        label = QLabel(title)
        label.setStyleSheet(f"color: {C.TEXT}; font-size: {FONT_BODY}px; font-weight: 600;")
        lay.addWidget(label)
        holder = QVBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        holder.setSpacing(SPACE_M)
        wrap = QWidget()
        wrap.setLayout(holder)
        lay.addWidget(wrap)
        return section, holder

    def _workspace_cols(self) -> int:
        """Tile column count derived from the page width so the workspace wraps."""
        live = getattr(self, "_live_pages_width", None)
        pages = getattr(self, "pages", None)
        if callable(live):
            width = live()
        else:
            width = pages.width() if pages is not None else 1100
        content = max(300, width - 130)
        return max(3, min(8, content // 140))

    def _workspace_apps(self) -> list[AppInfo]:
        """Pinned apps first, then recent, de-duplicated, capped at 8."""
        by_name = {a.name: a for a in self.apps}
        ordered: list[AppInfo] = []
        seen: set[str] = set()
        for name in (*launcher_state.get_pinned(), *launcher_state.get_recent()):
            app = by_name.get(name)
            if app is not None and name not in seen:
                seen.add(name)
                ordered.append(app)
        return ordered[:8]

    def _populate_workspace(self) -> None:
        """(Re)build pinned then recent grids from current pin/recent state."""
        pinned_holder = getattr(self, "_pinned_holder", None)
        recent_holder = getattr(self, "_recent_holder", None)
        if pinned_holder is None or recent_holder is None:
            return
        self._clear_holder(pinned_holder)
        self._clear_holder(recent_holder)

        apps = self._workspace_apps()
        pinned_names = set(launcher_state.get_pinned())
        pinned = [a for a in apps if a.name in pinned_names]
        recent = [a for a in apps if a.name not in pinned_names]
        cols = self._workspace_cols()
        self._workspace_cols_cur = cols

        if not pinned and not recent:
            self._workspace_holder = pinned_holder
            self._pinned_section.setVisible(True)
            self._recent_section.setVisible(False)
            panel = make_empty_panel(
                tr("No pinned or recent apps yet"),
                tr("Launch an app or pin one from Applications to see it here."),
            )
            button = QPushButton(tr("Applications"))
            button.setStyleSheet(BTN_SECONDARY)
            button.setAccessibleName(tr("Applications"))
            switch = getattr(self, "_switch_page", None)
            if callable(switch):
                button.clicked.connect(lambda: switch(1))
            panel.layout().addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)
            pinned_holder.addWidget(panel)
            return

        self._pinned_section.setVisible(bool(pinned))
        self._recent_section.setVisible(bool(recent))
        if pinned:
            self._workspace_holder = pinned_holder
            self._fill_workspace_grid(pinned_holder, pinned, cols)
        if recent:
            if not pinned:
                self._workspace_holder = recent_holder
            self._fill_workspace_grid(recent_holder, recent, cols)

    def _clear_holder(self, holder: QVBoxLayout) -> None:
        while holder.count():
            item = holder.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def _fill_workspace_grid(self, holder: QVBoxLayout, apps: list[AppInfo], cols: int) -> None:
        grid = QGridLayout()
        grid.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(SPACE_M)
        grid.setContentsMargins(0, 0, 0, 0)
        tile_w = 104 + 2 * SPACE_S
        for c in range(min(len(apps), cols)):
            grid.setColumnMinimumWidth(c, tile_w)
        row_h = 0
        running = _running_app_names()
        for i, app in enumerate(apps):
            tile = _AppTile(app, on_launch=self._launch_app, on_menu=self._show_app_menu)
            if app.name in running:
                _RunningBadge(tile)
            grid.addWidget(tile, i // cols, i % cols)
            row_h = max(row_h, tile.height() or tile.sizeHint().height())
        n_rows = (len(apps) + cols - 1) // cols if apps else 0
        for r in range(n_rows):
            grid.setRowMinimumHeight(r, row_h)
        remainder = len(apps) % cols
        if remainder:
            for j in range(remainder, cols):
                spacer = QWidget()
                spacer.setStyleSheet("background: transparent;")
                grid.addWidget(spacer, len(apps) // cols, j)
        wrap = QWidget()
        wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        wrap.setLayout(grid)
        if n_rows:
            wrap.setMinimumHeight(n_rows * row_h + grid.verticalSpacing() * (n_rows - 1))
        holder.addWidget(wrap)
