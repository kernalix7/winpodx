# SPDX-License-Identifier: MIT
"""Follow the OS colour scheme and rebuild Fluent tokens on the GUI thread."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from winpodx.gui import theme

if TYPE_CHECKING:
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QMainWindow

_ENV = "WINPODX_COLOR_SCHEME"
_INSTANCE: ThemeManager | None = None


def detect_system_scheme(app: QApplication | None = None) -> str:
    """Return ``light`` or ``dark``.

    Honour ``WINPODX_COLOR_SCHEME`` first (tests / captures). Then Qt ≥6.5
    ``QStyleHints.colorScheme()``. ``Unknown`` falls back to the window
    colour lightness. Any failure defaults to ``light`` (Windows 11 default).
    """
    override = os.environ.get(_ENV, "").strip().lower()
    if override == theme.SCHEME_LIGHT or override == theme.SCHEME_DARK:
        return override
    try:
        return _detect_qt_scheme(app)
    except Exception:  # noqa: BLE001 — detection must never crash the GUI
        return theme.SCHEME_LIGHT


def _detect_qt_scheme(app: QApplication | None) -> str:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    gui_app = app if app is not None else QGuiApplication.instance()
    if gui_app is None:
        return theme.SCHEME_LIGHT
    hints_fn = getattr(gui_app, "styleHints", None)
    if callable(hints_fn):
        hints = hints_fn()
        color_scheme_fn = getattr(hints, "colorScheme", None)
        if callable(color_scheme_fn) and hasattr(Qt, "ColorScheme"):
            value = color_scheme_fn()
            if value == Qt.ColorScheme.Dark:
                return theme.SCHEME_DARK
            if value == Qt.ColorScheme.Light:
                return theme.SCHEME_LIGHT
            return _scheme_from_palette(gui_app)
    return _scheme_from_palette(gui_app)


def _scheme_from_palette(gui_app: QGuiApplication) -> str:
    from PySide6.QtGui import QPalette

    window = gui_app.palette().color(QPalette.ColorRole.Window)
    if window.lightness() < 128:
        return theme.SCHEME_DARK
    return theme.SCHEME_LIGHT


class ThemeManager(QObject):
    """Singleton that rebuilds Fluent tokens when the OS scheme changes.

    Mutation of widgets happens only on the GUI thread: connect
    ``scheme_changed`` from window code, never from a worker.
    """

    scheme_changed = Signal(str)

    def start(self, app: QApplication) -> None:
        """Follow the system scheme, apply Fusion + palette, watch for changes."""
        hints_fn = getattr(app, "styleHints", None)
        if callable(hints_fn):
            hints = hints_fn()
            changed = getattr(hints, "colorSchemeChanged", None)
            if changed is not None:
                changed.connect(self._on_system_scheme_changed)
        detected = detect_system_scheme(app)
        theme.rebuild(detected)
        theme.apply_to_app(app)

    def set_scheme(self, scheme: str) -> None:
        """Manually rebuild tokens, re-palette the running app, and emit."""
        theme.rebuild(scheme)
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            theme.apply_to_app(app)
        self.scheme_changed.emit(theme.current_scheme())

    def apply_to_window(self, window: QMainWindow) -> None:
        """Re-apply ``GLOBAL_STYLE`` to ``window.centralWidget()`` if present."""
        central_fn = getattr(window, "centralWidget", None)
        if not callable(central_fn):
            return
        central = central_fn()
        if central is not None:
            central.setStyleSheet(theme.GLOBAL_STYLE)

    def _on_system_scheme_changed(self, _scheme: int = 0) -> None:
        from PySide6.QtWidgets import QApplication

        self.set_scheme(detect_system_scheme(QApplication.instance()))


def instance() -> ThemeManager:
    """Return the process-wide ``ThemeManager``, creating it on first call."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = ThemeManager()
    return _INSTANCE
