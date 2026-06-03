from __future__ import annotations

from importlib import resources
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_ICON_CACHE: dict[tuple[str, str, int], QIcon] = {}


def _read_svg(name: str) -> bytes:
    filename = f"{name}.svg"
    try:
        return resources.files(__package__).joinpath(filename).read_bytes()
    except (AttributeError, FileNotFoundError, ModuleNotFoundError, OSError):
        return Path(__file__).with_name(filename).read_bytes()


def load_icon(name: str, color: str = "#ffffff", size: int = 16) -> QIcon:
    """Load, recolor, render, and cache a bundled WinPodX SVG icon."""
    key = (name, color, size)
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    svg = _read_svg(name).replace(b"currentColor", color.encode("utf-8"))
    renderer = QSvgRenderer(QByteArray(svg))
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()

    icon = QIcon(pixmap)
    _ICON_CACHE[key] = icon
    return icon
