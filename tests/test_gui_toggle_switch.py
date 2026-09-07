# SPDX-License-Identifier: MIT
"""Painted Windows 11 ToggleSwitch: geometry, thumb travel, and QCheckBox contract."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox  # noqa: E402

from winpodx.gui import theme  # noqa: E402
from winpodx.gui._toggle_switch import ToggleSwitch  # noqa: E402


@pytest.fixture
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _thumb_column(toggle: ToggleSwitch) -> int:
    image: QImage = toggle.grab().toImage()
    y = toggle.height() // 2
    want = QColor(theme.C.CRUST if toggle.isChecked() else theme.C.SUBTEXT1)
    cols = [x for x in range(theme.TOGGLE_W) if _close(image.pixelColor(x, y), want)]
    assert cols, "thumb colour not found on the track centreline"
    return (cols[0] + cols[-1]) // 2


def _close(a, b, tol: int = 24) -> bool:
    return (
        abs(a.red() - b.red()) <= tol
        and abs(a.green() - b.green()) <= tol
        and abs(a.blue() - b.blue()) <= tol
    )


def test_toggle_is_a_checkbox_with_fluent_track_geometry(qapp: QApplication) -> None:
    toggle = ToggleSwitch()

    assert isinstance(toggle, QCheckBox)
    assert toggle.sizeHint().width() >= theme.TOGGLE_W
    assert toggle.sizeHint().height() >= theme.CONTROL_HEIGHT_W11
    assert toggle.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_thumb_sits_left_when_off_and_right_when_on(qapp: QApplication) -> None:
    # Given
    toggle = ToggleSwitch()
    toggle.resize(theme.TOGGLE_W, theme.CONTROL_HEIGHT_W11)

    # When
    toggle.setChecked(False)
    left = _thumb_column(toggle)
    toggle.setChecked(True)
    right = _thumb_column(toggle)

    # Then
    assert left < theme.TOGGLE_W // 2 < right


def test_click_toggles_and_emits_once(qapp: QApplication) -> None:
    toggle = ToggleSwitch()
    toggle.resize(theme.TOGGLE_W, theme.CONTROL_HEIGHT_W11)
    seen: list[bool] = []
    toggle.toggled.connect(seen.append)

    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)

    assert seen == [True]
    assert toggle.isChecked() is True


def test_space_toggles_via_keyboard(qapp: QApplication) -> None:
    toggle = ToggleSwitch()
    toggle.show()
    toggle.setFocus()
    QApplication.processEvents()

    QTest.keyClick(toggle, Qt.Key.Key_Space)

    assert toggle.isChecked() is True
    toggle.close()


def test_track_uses_accent_when_on_and_neutral_when_off(qapp: QApplication) -> None:
    toggle = ToggleSwitch()
    toggle.resize(theme.TOGGLE_W, theme.CONTROL_HEIGHT_W11)
    y = toggle.height() // 2

    toggle.setChecked(True)
    on_px = toggle.grab().toImage().pixelColor(3, y)
    toggle.setChecked(False)
    off_px = toggle.grab().toImage().pixelColor(theme.TOGGLE_W - 4, y)

    assert _close(on_px, QColor(theme.C.BLUE))
    assert not _close(off_px, QColor(theme.C.BLUE))
