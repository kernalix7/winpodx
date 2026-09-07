# SPDX-License-Identifier: MIT
"""Main-window geometry policy: nav overlay below the breakpoint + ratio-derived minimum."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QFrame, QScrollArea, QWidget

from winpodx.gui import theme
from winpodx.gui._main_window_navpane import NAV_COMPACT_BELOW
from winpodx.gui._widget_helpers import fit_fluid_wraps


class _LightDismiss(QObject):
    """Collapse the overlaid nav pane on any press outside it (WinUI light-dismiss)."""

    def __init__(self, host: ShellGeometryMixin) -> None:
        super().__init__(host)
        self._host = host

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.MouseButtonPress or not isinstance(watched, QWidget):
            return False
        host = self._host
        if not host._nav_overlay_active() or watched.window() is not host:
            return False
        pane = host.sidebar
        if isinstance(event, QMouseEvent):
            local = pane.mapFromGlobal(event.globalPosition().toPoint())
            if pane.rect().contains(local):
                return False
        host._collapse_nav_overlay()
        return False


class ShellGeometryMixin:
    """Keeps the nav pane from squeezing pages and derives the window minimum from content."""

    def _nav_overlay_active(self) -> bool:
        compact = bool(getattr(self, "_nav_is_compact", False))
        return self.width() < NAV_COMPACT_BELOW and not compact

    def _mount_nav_pane(self, body: QWidget, pane: QWidget) -> QWidget:
        slot = QWidget(body)
        slot.setObjectName("navSlot")
        slot.setFixedWidth(pane.width())
        self._nav_slot = slot
        self._nav_body = body
        pane.setParent(body)
        self._light_dismiss = _LightDismiss(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self._light_dismiss)
        return slot

    def _layout_nav_pane(self) -> None:
        slot = getattr(self, "_nav_slot", None)
        body = getattr(self, "_nav_body", None)
        pane = getattr(self, "sidebar", None)
        if slot is None or body is None or pane is None:
            return
        overlay = self._nav_overlay_active()
        slot_w = theme.NAV_PANE_COMPACT if overlay else pane.width()
        if slot.width() != slot_w:
            slot.setFixedWidth(slot_w)
        title_h = self.title_bar.height() if getattr(self, "_frameless_active", False) else 0
        wanted = QRect(slot.x(), slot.y(), pane.width(), self.height() - title_h)
        if pane.geometry() != wanted:
            pane.setGeometry(wanted)
        if pane.property("overlay") != overlay:
            pane.setProperty("overlay", overlay)
            pane.raise_()
        edge = self._nav_overlay_edge(pane)
        edge.setGeometry(pane.width() - 1, 0, 1, pane.height())
        if edge.isVisible() != overlay:
            edge.setVisible(overlay)
            edge.raise_()

    def _nav_overlay_edge(self, pane: QWidget) -> QFrame:
        edge = getattr(self, "_nav_edge", None)
        if edge is None:
            edge = QFrame(pane)
            edge.setObjectName("navOverlayEdge")
            edge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._nav_edge = edge
            edge.setStyleSheet(
                f"QFrame#navOverlayEdge {{ background: {theme.rgba(theme.C.TEXT, 0.12)}; }}"
            )
        return edge

    def _collapse_nav_overlay(self) -> None:
        self._nav_user_compact = True
        self._set_nav_compact_mode(True)
        self._layout_nav_pane()

    def _scroll_bodies(self) -> list[tuple[QScrollArea, QWidget]]:
        return [(a, a.widget()) for a in self.findChildren(QScrollArea) if a.widget() is not None]

    def _live_pages_width(self) -> int:
        # The page stack's width as it *will* be after the pending layout pass;
        # inside resizeEvent ``pages.width()`` still reports the previous size,
        # which made column counts oscillate and rebuild grids every resize.
        slot = getattr(self, "_nav_slot", None)
        slot_w = slot.width() if slot is not None else theme.NAV_PANE_WIDTH
        return self.width() - slot_w - theme.PAGE_MARGIN_X

    def _content_column(self) -> int:
        # Derived from the window, not the scroll viewport: inside resizeEvent
        # the viewport still has its previous width, and the column never
        # exceeds CONTENT_MAX_WIDTH (+ the gutter-adjusted right margin).
        gutter = self.style().pixelMetric(self.style().PixelMetric.PM_ScrollBarExtent)
        live = self._live_pages_width() - gutter
        return min(theme.CONTENT_MAX_WIDTH + theme.PAGE_MARGIN_X - theme.SCROLL_GUTTER, live)

    def _fit_wrapped_text(self) -> None:
        column = self._content_column()
        for _area, inner in self._scroll_bodies():
            fit_fluid_wraps(inner, column)

    def _chrome_width(self) -> int:
        gutter = self.style().pixelMetric(self.style().PixelMetric.PM_ScrollBarExtent)
        return theme.NAV_PANE_COMPACT + 2 * theme.PAGE_MARGIN_X + gutter

    def _content_floor_width(self, column: int) -> int:
        # Measure with wrapped captions squeezed to the candidate column so the
        # floor reflects controls (buttons, fields), not the current wrap width.
        # Cached: the candidate column is a constant, and re-squeezing visible
        # captions on every resize event made the page visibly re-wrap twice.
        bodies = self._scroll_bodies()
        key = (column, len(bodies))
        cached = getattr(self, "_floor_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        widest = 0
        for _area, inner in bodies:
            fit_fluid_wraps(inner, column)
            widest = max(widest, inner.minimumSizeHint().width())
        floor = self._chrome_width() + widest
        self._floor_cache = (key, floor)
        self._fit_wrapped_text()
        return floor

    def _apply_window_minimum(self) -> None:
        pref_w, pref_h = getattr(self, "_preferred_size", (0, 0))
        ratio_w = int(pref_w * theme.MIN_SHRINK_RATIO)
        ratio_h = int(pref_h * theme.MIN_SHRINK_RATIO)
        floor_w = self._content_floor_width(ratio_w - self._chrome_width())
        pane = getattr(self, "sidebar", None)
        title_h = self.title_bar.height() if getattr(self, "_frameless_active", False) else 0
        floor_h = (pane.minimumSizeHint().height() if pane is not None else 0) + title_h
        wanted = QSize(max(ratio_w, floor_w), max(ratio_h, floor_h))
        if self.minimumSize() != wanted:
            self.setMinimumSize(wanted)

    def _on_nav_pane_toggled(self) -> None:
        self._layout_nav_pane()
        if not self._nav_overlay_active():
            self._reflow_pages()

    def _switch_page_light_dismiss(self) -> None:
        if self._nav_overlay_active():
            self._collapse_nav_overlay()
