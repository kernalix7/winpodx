"""Pure widget-construction helpers extracted from main_window.py.

These functions have no dependency on ``WinpodxWindow`` state — they take
a target widget or an ``AppInfo`` and return a configured Qt widget. They
were ``@staticmethod`` on ``WinpodxWindow`` historically; pulling them
into a module-level home keeps ``main_window.py`` focused on orchestration
and lets the helpers be reused from future GUI screens without dragging
the main-window class along.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QWidget

from winpodx.core.app import AppInfo
from winpodx.gui.theme import C, avatar_color


def add_shadow(
    widget: QWidget,
    blur: int = 16,
    y: int = 3,
    alpha: int = 45,
) -> None:
    """Apply subtle drop shadow for depth effect."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(blur)
    shadow.setOffset(0, y)
    shadow.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(shadow)


def make_source_badge(app: AppInfo) -> QLabel | None:
    """Pill badge marking app provenance: Detected (from scan) vs Bundled.

    Returns ``None`` when ``AppInfo.source`` is absent (older cores) or
    equals an unrecognised provenance, so legacy apps stay unannotated.
    """
    source = getattr(app, "source", "bundled")
    if source == "discovered":
        text = "Detected"
        bg = C.SAPPHIRE
        fg = C.CRUST
    elif source == "bundled":
        text = "Bundled"
        bg = C.SURFACE2
        fg = C.SUBTEXT1
    else:
        return None

    badge = QLabel(text)
    badge.setStyleSheet(
        f"background: {bg}; color: {fg};"
        " border-radius: 7px;"
        " font-size: 9px; font-weight: bold;"
        " padding: 2px 7px;"
        " letter-spacing: 0.3px;"
    )
    return badge


def make_app_avatar(app: AppInfo, size: int, *, radius: int, font_size: int) -> QLabel:
    """Build the avatar label for an app row/card.

    When ``app.icon_path`` points at a real PNG / SVG, render the icon
    scaled to ``size`` with a subtle surface background for contrast.
    Otherwise fall back to the colored single-letter avatar (legacy look
    for apps without a discovered icon).
    """
    avatar = QLabel()
    avatar.setFixedSize(size, size)
    avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

    icon_path = (app.icon_path or "").strip()
    pixmap: QPixmap | None = None
    if icon_path and Path(icon_path).is_file():
        pad = max(4, size // 7)
        inner = size - pad * 2
        try:
            if icon_path.lower().endswith(".svg"):
                renderer = QSvgRenderer(icon_path)
                if renderer.isValid():
                    pm = QPixmap(inner, inner)
                    pm.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(pm)
                    renderer.render(painter)
                    painter.end()
                    pixmap = pm
            else:
                pm = QPixmap(icon_path)
                if not pm.isNull():
                    pixmap = pm.scaled(
                        inner,
                        inner,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
        except Exception:  # noqa: BLE001
            pixmap = None

    if pixmap is not None and not pixmap.isNull():
        avatar.setPixmap(pixmap)
        avatar.setStyleSheet(f"background: {C.SURFACE1}; border-radius: {radius}px; padding: 0px;")
        return avatar

    # Fallback: legacy colored letter avatar.
    color = avatar_color(app.name)
    letter = app.full_name[0].upper() if app.full_name else "?"
    avatar.setText(letter)
    avatar.setStyleSheet(
        f"background: {color};"
        f" color: {C.CRUST};"
        f" border-radius: {radius}px;"
        f" font-size: {font_size}px; font-weight: bold;"
    )
    return avatar
