# SPDX-License-Identifier: MIT
"""Windows 11-style caption bar: app icon, title, Minimize / Maximize / Close."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from winpodx.core.i18n import tr
from winpodx.gui import theme

_GLYPH = 10
_CLOSE_HOVER = "#C42B1C"
_CLOSE_PRESSED = "#B12A1B"


class _CaptionButton(QPushButton):
    """46x32 caption control drawing its own 1px Fluent glyph."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setObjectName(f"caption{kind.capitalize()}")
        self.setFixedSize(theme.CAPTION_BTN_W, theme.TITLE_BAR_H)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setProperty("restore", False)

    def _glyph_color(self) -> QColor:
        if self._kind == "close" and (self.isDown() or self.underMouse()):
            return QColor("#FFFFFF")
        return QColor(theme.C.TEXT)

    def _fill_color(self) -> QColor | None:
        if self.isDown():
            return QColor(_CLOSE_PRESSED) if self._kind == "close" else _tinted(theme.C.TEXT, 0.04)
        if self.underMouse():
            return QColor(_CLOSE_HOVER) if self._kind == "close" else _tinted(theme.C.TEXT, 0.06)
        return None

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        fill = self._fill_color()
        if fill is not None:
            painter.fillRect(self.rect(), fill)
        pen = QPen(self._glyph_color())
        pen.setWidth(1)
        painter.setPen(pen)
        x = (self.width() - _GLYPH) // 2
        y = (self.height() - _GLYPH) // 2
        if self._kind == "minimize":
            mid = self.height() // 2
            painter.drawLine(x, mid, x + _GLYPH, mid)
        elif self._kind == "maximize" and self.property("restore"):
            painter.drawRect(QRect(x, y + 2, _GLYPH - 3, _GLYPH - 3))
            painter.drawLine(x + 2, y, x + _GLYPH, y)
            painter.drawLine(x + _GLYPH, y, x + _GLYPH, y + _GLYPH - 2)
        elif self._kind == "maximize":
            painter.drawRect(QRect(x, y, _GLYPH - 1, _GLYPH - 1))
        else:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.drawLine(x, y, x + _GLYPH, y + _GLYPH)
            painter.drawLine(x, y + _GLYPH, x + _GLYPH, y)
        painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt signature
        super().leaveEvent(event)
        self.update()


def _tinted(hex_color: str, alpha: float) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


class TitleBar(QWidget):
    """Frameless-window caption strip; drag moves, double-click maximizes."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self.setObjectName("titleBar")
        self.setFixedHeight(theme.TITLE_BAR_H)
        self.setMouseTracking(True)

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SPACE_L, 0, 0, 0)
        row.setSpacing(theme.SPACE_S)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(16, 16)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        from winpodx.gui._main_window_navpane import _app_icon_pixmap

        pixmap = _app_icon_pixmap(16)
        if pixmap is not None:
            self.icon_label.setPixmap(pixmap)
        row.addWidget(self.icon_label)

        self.title_label = QLabel("WinPodX")
        self.title_label.setObjectName("titleBarText")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self.title_label)
        row.addStretch(1)

        self.btn_minimize = _CaptionButton("minimize", self)
        self.btn_maximize = _CaptionButton("maximize", self)
        self.btn_close = _CaptionButton("close", self)
        self.btn_minimize.setAccessibleName(tr("Minimize"))
        self.btn_maximize.setAccessibleName(tr("Maximize"))
        self.btn_close.setAccessibleName(tr("Close"))
        self.btn_minimize.clicked.connect(lambda: self._window.showMinimized())
        self.btn_maximize.clicked.connect(self._toggle_maximized)
        self.btn_close.clicked.connect(lambda: self._window.close())
        for btn in (self.btn_minimize, self.btn_maximize, self.btn_close):
            row.addWidget(btn)

        window.installEventFilter(self)
        self.restyle()

    def _toggle_maximized(self) -> None:
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self._sync_maximize_glyph()

    def _sync_maximize_glyph(self) -> None:
        self.btn_maximize.setProperty("restore", bool(self._window.isMaximized()))
        self.btn_maximize.setAccessibleName(
            tr("Restore") if self._window.isMaximized() else tr("Maximize")
        )
        self.btn_maximize.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._window and event.type() == QEvent.Type.WindowStateChange:
            self._sync_maximize_glyph()
        return False

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton and not self._window.isMaximized():
            handle = self._window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def restyle(self) -> None:
        self.setStyleSheet(
            f"QWidget#titleBar {{ background: {theme.nav_pane_color()}; }}"
            f"QLabel#titleBarText {{ background: transparent; color: {theme.C.TEXT}; "
            f"font-size: {theme.FONT_CAPTION}px; }}"
        )
        for btn in (self.btn_minimize, self.btn_maximize, self.btn_close):
            btn.update()
