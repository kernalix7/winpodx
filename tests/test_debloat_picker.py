# SPDX-License-Identifier: MIT
"""Smoke tests for the Qt debloat picker dialog (#247 phase 3).

Runs headless via ``QApplication([])`` -- no display server required.
"""

from __future__ import annotations

import os

import pytest

# Force the offscreen Qt platform plugin so tests run on CI / headless
# dev boxes without an X server. Must be set before QApplication ctor.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PySide6 = pytest.importorskip("PySide6")
from winpodx.core.debloat import load_catalog  # noqa: E402
from winpodx.gui.debloat_picker import DebloatPickerDialog  # noqa: E402


def _ensure_qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(scope="module")
def qapp():
    return _ensure_qapp()


@pytest.fixture
def catalog():
    return load_catalog()


class TestDialogInitialState:
    def test_opens_with_normal_preset_seeded(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog)
        try:
            normal_members = set(catalog.items_for_preset("normal"))
            assert set(dlg.selected_items()) == normal_members
            # Radio: normal must be checked.
            assert dlg._preset_buttons["normal"].isChecked()
            assert not dlg._custom_button.isChecked()
        finally:
            dlg.deleteLater()

    def test_initial_preset_can_be_overridden(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog, initial_preset="speed")
        try:
            speed_members = set(catalog.items_for_preset("speed"))
            assert set(dlg.selected_items()) == speed_members
            assert dlg._preset_buttons["speed"].isChecked()
        finally:
            dlg.deleteLater()

    def test_invalid_initial_preset_falls_back_to_normal(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog, initial_preset="bogus")
        try:
            normal_members = set(catalog.items_for_preset("normal"))
            assert set(dlg.selected_items()) == normal_members
        finally:
            dlg.deleteLater()


class TestPresetToggling:
    def test_clicking_preset_reseeds_checkboxes(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog)
        try:
            dlg._preset_buttons["performance"].setChecked(True)
            assert set(dlg.selected_items()) == set(
                catalog.items_for_preset("performance")
            )
        finally:
            dlg.deleteLater()

    def test_toggling_a_checkbox_flips_to_custom(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog)
        try:
            assert dlg._preset_buttons["normal"].isChecked()
            # Flip OneDrive (not in normal preset).
            dlg._item_boxes["onedrive"].setChecked(True)
            assert dlg._custom_button.isChecked()
            assert not dlg._preset_buttons["normal"].isChecked()
        finally:
            dlg.deleteLater()


class TestSelectedItems:
    def test_returns_catalog_order(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog, initial_preset="speed")
        try:
            ordered = dlg.selected_items()
            expected_order = [name for name in catalog.items if name in ordered]
            assert ordered == expected_order
        finally:
            dlg.deleteLater()

    def test_empty_selection_when_all_unchecked(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog)
        try:
            for box in dlg._item_boxes.values():
                box.setChecked(False)
            assert dlg.selected_items() == []
        finally:
            dlg.deleteLater()
