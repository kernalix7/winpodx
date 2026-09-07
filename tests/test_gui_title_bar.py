# SPDX-License-Identifier: MIT
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QWidget  # noqa: E402

from winpodx.gui import theme  # noqa: E402
from winpodx.gui._frameless import FramelessMixin, edges_at  # noqa: E402
from winpodx.gui._title_bar import TitleBar  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class _Host(FramelessMixin, QMainWindow):
    def __init__(self) -> None:
        QMainWindow.__init__(self)
        self.setCentralWidget(QWidget())
        self.title_bar = TitleBar(self)


def test_title_bar_anatomy_matches_windows_caption_controls(qapp: QApplication) -> None:
    host = _Host()
    bar = host.title_bar

    assert bar.objectName() == "titleBar"
    assert bar.height() == theme.TITLE_BAR_H == 32
    assert bar.title_label.text() == "WinPodX"
    assert not bar.icon_label.pixmap().isNull()
    names = [b.objectName() for b in bar.findChildren(QPushButton)]
    assert names == ["captionMinimize", "captionMaximize", "captionClose"]
    for btn in bar.findChildren(QPushButton):
        assert btn.size().width() == 46 and btn.size().height() == theme.TITLE_BAR_H
        assert btn.focusPolicy() == Qt.FocusPolicy.NoFocus
        assert btn.accessibleName()


def test_caption_buttons_drive_the_window(qapp: QApplication, monkeypatch) -> None:
    host = _Host()
    host.resize(600, 400)
    host.show()
    qapp.processEvents()
    bar = host.title_bar

    bar.btn_maximize.click()
    qapp.processEvents()
    assert host.windowState() & Qt.WindowState.WindowMaximized
    assert bar.btn_maximize.property("restore") is True
    bar.btn_maximize.click()
    qapp.processEvents()
    assert not (host.windowState() & Qt.WindowState.WindowMaximized)
    assert bar.btn_maximize.property("restore") is False

    minimized: list[bool] = []
    monkeypatch.setattr(host, "showMinimized", lambda: minimized.append(True))
    bar.btn_minimize.click()
    assert minimized == [True]

    closed: list[bool] = []
    monkeypatch.setattr(host, "close", lambda: closed.append(True))
    bar.btn_close.click()
    assert closed == [True]


def test_frameless_is_default_and_native_titlebar_env_opts_out(
    qapp: QApplication, monkeypatch
) -> None:
    monkeypatch.delenv("WINPODX_NATIVE_TITLEBAR", raising=False)
    host = _Host()
    host._install_frameless()
    assert host.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert host._frameless_active is True

    monkeypatch.setenv("WINPODX_NATIVE_TITLEBAR", "1")
    native = _Host()
    native._install_frameless()
    assert not (native.windowFlags() & Qt.WindowType.FramelessWindowHint)
    assert native._frameless_active is False


def test_edges_at_maps_the_resize_border(qapp: QApplication) -> None:
    margin = theme.RESIZE_MARGIN
    w, h = 800, 600
    assert edges_at(QPoint(2, 300), w, h, margin) == Qt.Edge.LeftEdge
    assert edges_at(QPoint(797, 300), w, h, margin) == Qt.Edge.RightEdge
    assert edges_at(QPoint(400, 1), w, h, margin) == Qt.Edge.TopEdge
    assert edges_at(QPoint(400, 598), w, h, margin) == Qt.Edge.BottomEdge
    assert edges_at(QPoint(1, 1), w, h, margin) == (Qt.Edge.TopEdge | Qt.Edge.LeftEdge)
    assert edges_at(QPoint(798, 598), w, h, margin) == (Qt.Edge.BottomEdge | Qt.Edge.RightEdge)
    assert edges_at(QPoint(400, 300), w, h, margin) == Qt.Edge(0)
