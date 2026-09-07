# SPDX-License-Identifier: MIT
"""Offscreen WinUI inner-control rendering: menu, checkbox, list, progress."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QListWidget,
    QMenu,
    QProgressBar,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.gui import theme  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _restore_theme_scheme() -> None:
    previous = theme.current_scheme()
    yield
    theme.rebuild(previous)


def _close(a: QColor, b: QColor, tol: int = 40) -> bool:
    return (
        abs(a.red() - b.red()) <= tol
        and abs(a.green() - b.green()) <= tol
        and abs(a.blue() - b.blue()) <= tol
    )


def _any_pixel_close(image: QImage, want: QColor, *, tol: int = 40) -> bool:
    for y in range(image.height()):
        for x in range(image.width()):
            if _close(image.pixelColor(x, y), want, tol):
                return True
    return False


class _InnerHost(QWidget):
    """Host that applies GLOBAL_STYLE and owns one of each inner control."""

    def __init__(self) -> None:
        super().__init__()
        self.setStyleSheet(theme.GLOBAL_STYLE)
        layout = QVBoxLayout(self)
        self.checkbox = QCheckBox("Agree")
        self.checkbox.setChecked(True)
        self.radio = QRadioButton("Option")
        self.radio.setChecked(True)
        self.list_widget = QListWidget()
        self.list_widget.addItems(["Alpha", "Beta", "Gamma"])
        self.list_widget.setCurrentRow(0)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(60)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.radio)
        layout.addWidget(self.list_widget)
        layout.addWidget(self.progress)
        self.menu = QMenu(self)
        self.menu.setStyleSheet(theme.GLOBAL_STYLE)
        self.action_one = self.menu.addAction("First")
        self.menu.addSeparator()
        self.action_two = self.menu.addAction("Second")


@pytest.fixture
def host(qapp: QApplication) -> _InnerHost:
    widget = _InnerHost()
    widget.resize(320, 280)
    widget.show()
    qapp.processEvents()
    return widget


def test_menu_action_row_is_at_least_32px(qapp: QApplication, host: _InnerHost) -> None:
    host.menu.popup(QPoint(0, 0))
    qapp.processEvents()
    try:
        assert host.menu.actionGeometry(host.action_one).height() >= 32
        assert host.menu.actionGeometry(host.action_two).height() >= 32
    finally:
        host.menu.close()


def test_checked_checkbox_indicator_centre_is_accent(qapp: QApplication, host: _InnerHost) -> None:
    qapp.processEvents()
    image = host.checkbox.grab().toImage()
    accent = QColor(theme.C.BLUE)
    # Indicator is the leading 20×20; sample its centre plus neighbours.
    mid = image.height() // 2
    y0, y1 = max(0, mid - 6), min(image.height(), mid + 6)
    hits = [image.pixelColor(x, y) for x in range(4, 18) for y in range(y0, y1)]
    sample = hits[len(hits) // 2].name() if hits else "empty"
    assert any(_close(pixel, accent) for pixel in hits), (
        f"no accent pixel near indicator centre; sample={sample}"
    )


def test_list_selected_row_is_nav_selected_not_accent(qapp: QApplication, host: _InnerHost) -> None:
    qapp.processEvents()
    row = host.list_widget.visualItemRect(host.list_widget.item(0))
    image = host.list_widget.grab().toImage()
    # Skip the 3px accent pill on the left; sample the row fill.
    x = min(row.center().x(), image.width() - 1)
    y = min(max(row.center().y(), 0), image.height() - 1)
    pixel = image.pixelColor(x, y)
    accent = QColor(theme.C.BLUE)
    assert not _close(pixel, accent, tol=30)


def test_progress_chunk_is_accent(qapp: QApplication, host: _InnerHost) -> None:
    qapp.processEvents()
    image = host.progress.grab().toImage()
    accent = QColor(theme.C.BLUE)
    # 60% filled: a pixel at ~30% width on the track midline should be accent.
    x = max(1, int(image.width() * 0.3))
    y = image.height() // 2
    pixel = image.pixelColor(x, y)
    assert _close(pixel, accent) or _any_pixel_close(image, accent), pixel.name()
