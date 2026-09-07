# SPDX-License-Identifier: MIT
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.gui import _widget_helpers as helpers
from winpodx.gui import theme  # noqa: E402
from winpodx.gui.theme import SETTINGS_ROW_MIN  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def test_settings_card_anatomy_exposes_object_name_height_and_slots(qapp: QApplication) -> None:
    action = QPushButton("Go")

    card = helpers.make_settings_card("refresh", "Title", "Description", action=action)

    assert isinstance(card, QFrame)
    assert card.objectName() == "settingsCard"
    assert card.minimumHeight() >= SETTINGS_ROW_MIN
    assert card.title_label.text() == "Title"
    assert card.desc_label.text() == "Description"
    assert card.action_widget is action


def test_settings_card_custom_object_name_and_optional_chevron(qapp: QApplication) -> None:
    plain = helpers.make_settings_card("refresh", "Plain")
    with_chevron = helpers.make_settings_card(
        "refresh",
        "Open",
        chevron=True,
        object_name="settingsActionRow",
    )

    assert plain.objectName() == "settingsCard"
    assert with_chevron.objectName() == "settingsActionRow"
    assert getattr(plain, "chevron_widget", None) is None
    assert with_chevron.chevron_widget is not None


def test_toggle_switch_is_a_checkbox_with_hit_height_and_indicator_qss(
    qapp: QApplication,
) -> None:
    toggle = helpers.make_toggle_switch(checked=True)

    assert isinstance(toggle, QCheckBox)
    assert toggle.isChecked() is True
    assert toggle.text() == ""
    assert toggle.minimumHeight() >= 32
    assert type(toggle).__name__ == "ToggleSwitch"
    assert toggle.sizeHint().width() >= theme.TOGGLE_W


def test_settings_card_sets_toggle_accessible_name_from_title(qapp: QApplication) -> None:
    toggle = helpers.make_toggle_switch()

    card = helpers.make_settings_card("refresh", "Auto-recovery", action=toggle)

    assert card.action_widget is toggle
    assert toggle.accessibleName() == "Auto-recovery"


def test_settings_group_returns_label_and_card_layout(qapp: QApplication) -> None:
    widget, layout = helpers.make_settings_group("Section")

    assert isinstance(widget, QWidget)
    assert isinstance(layout, QVBoxLayout)
    assert layout.spacing() == 4
    labels = [child for child in widget.findChildren(QLabel) if child.text() == "Section"]
    assert labels


def test_settings_card_skips_icon_column_when_icon_is_empty(qapp: QApplication) -> None:
    card = helpers.make_settings_card("", "No Icon")

    assert card.title_label.text() == "No Icon"
    pixmaps = [lbl.pixmap() for lbl in card.findChildren(QLabel) if not lbl.pixmap().isNull()]
    assert pixmaps == []


def test_settings_card_keeps_toggle_on_the_title_row(qapp: QApplication) -> None:
    toggle = helpers.make_toggle_switch()
    card = helpers.make_settings_card("refresh", "Title", "Description", action=toggle)
    card.setFixedWidth(800)
    card.show()
    qapp.processEvents()
    card.adjustSize()
    qapp.processEvents()

    action = card.action_widget
    title = card.title_label
    assert action is not None
    assert card.height() <= 96
    assert action.geometry().right() >= card.width() - 40
    title_c = title.mapTo(card, title.rect().center())
    action_c = action.mapTo(card, action.rect().center())
    assert abs(action_c.y() - title_c.y()) <= 24


def test_compact_settings_card_is_48px_with_24px_icon(qapp: QApplication) -> None:
    action = QPushButton("Go")
    card = helpers.make_settings_card(
        "hardware",
        "Dongle",
        "1234:5678",
        action=action,
        compact=True,
        object_name="deviceCard",
    )

    assert card.objectName() == "deviceCard"
    assert card.minimumHeight() == 48
    assert card.layout().contentsMargins().left() == 0
    icon = next(lbl for lbl in card.findChildren(QLabel) if not lbl.pixmap().isNull())
    assert icon.width() == 24
    assert icon.height() == 24


def test_settings_card_long_title_elides_instead_of_widening_the_card(qapp: QApplication) -> None:
    long_title = "Intel Corporation Raptor Lake Dynamic Platform and Thermal Framework " * 3

    card = helpers.make_settings_card("", long_title, "id · bus", action=QPushButton("Attach"))
    card.resize(400, 68)
    card.show()
    qapp.processEvents()

    assert card.title_label.text() == long_title
    assert card.minimumSizeHint().width() < 400
    assert card.title_label.width() < 400
    assert card.title_label.toolTip() == long_title
