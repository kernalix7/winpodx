# SPDX-License-Identifier: MIT
"""Smoke tests for the Qt debloat picker dialog (#247 phase 3).

Runs headless via ``QApplication([])`` -- no display server required.
"""

from __future__ import annotations

import os
import re

import pytest

# Force the offscreen Qt platform plugin so tests run on CI / headless
# dev boxes without an X server. Must be set before QApplication ctor.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PySide6 = pytest.importorskip("PySide6")
from winpodx.core.debloat import load_catalog  # noqa: E402
from winpodx.core.i18n import tr  # noqa: E402
from winpodx.gui import theme  # noqa: E402
from winpodx.gui.debloat_picker import _PRESET_DESCRIPTIONS, DebloatPickerDialog  # noqa: E402
from winpodx.gui.theme import C  # noqa: E402


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
    def test_opens_with_roomy_starting_size(self, qapp, catalog):
        # #550: the picker must open large enough to show preset + items,
        # not at a cramped default.
        dlg = DebloatPickerDialog(catalog)
        try:
            assert dlg.width() >= 700
            assert dlg.height() >= 640
        finally:
            dlg.deleteLater()

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
            assert set(dlg.selected_items()) == set(catalog.items_for_preset("performance"))
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

    def test_direct_custom_selection_keeps_items_and_refreshes_description(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog)
        try:
            before_items = dlg.selected_items()
            before_desc = dlg._preset_desc.text()
            dlg._custom_button.setChecked(True)
            assert dlg.selected_items() == before_items
            assert dlg._custom_button.isChecked()
            assert not dlg._preset_buttons["normal"].isChecked()
            assert dlg._preset_desc.text() != before_desc
            assert dlg._preset_desc.text() == tr(_PRESET_DESCRIPTIONS["custom"])
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


def _css_decls(body: str) -> dict[str, str]:
    decls: dict[str, str] = {}
    for part in body.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        decls[key.strip()] = value.strip()
    return decls


def _css_rules(css: str) -> dict[str, str]:
    return {sel.strip(): body for sel, body in re.findall(r"([^{]+)\{([^}]*)\}", css)}


def _indicator_geometry(
    body: str, fallback: tuple[str, str, str] | None = None
) -> tuple[str, str, str]:
    decls = _css_decls(body)
    width = decls.get("width")
    height = decls.get("height")
    border = decls.get("border-width") or decls.get("border")
    border_width = border.split()[0] if border else None
    if fallback is not None:
        width = width or fallback[0]
        height = height or fallback[1]
        border_width = border_width or fallback[2]
    assert width is not None
    assert height is not None
    assert border_width is not None
    return width, height, border_width


class TestRadioIndicatorGeometry:
    def test_checked_and_unchecked_indicators_share_total_geometry(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog)
        try:
            rules = _css_rules(dlg.styleSheet())
            unchecked = _indicator_geometry(rules["QRadioButton::indicator"])
            checked_rule = rules["QRadioButton::indicator:checked"]
            checked = _indicator_geometry(checked_rule, fallback=unchecked)
            assert checked == unchecked
        finally:
            dlg.deleteLater()


class TestDialogTooltipStyle:
    def test_applied_stylesheet_includes_themed_tooltip(self, qapp, catalog):
        dlg = DebloatPickerDialog(catalog)
        try:
            rules = _css_rules(dlg.styleSheet())
            assert "QToolTip" in rules
            tooltip = rules["QToolTip"]
            assert C.SURFACE0 in tooltip
            assert C.TEXT in tooltip
            assert "1px solid" in tooltip
        finally:
            dlg.deleteLater()

    def test_tooltip_owning_labels_scope_stylesheet_to_object_name(self, qapp, catalog):
        from PySide6.QtWidgets import QLabel

        dlg = DebloatPickerDialog(catalog)
        try:
            owners = [label for label in dlg.findChildren(QLabel) if label.toolTip()]
            assert owners
            risk_badges = [label for label in owners if label.text() in {"LOW", "MEDIUM", "HIGH"}]
            assert risk_badges
            for label in owners:
                name = label.objectName()
                css = label.styleSheet()
                assert name
                scoped = f"QLabel#{name}"
                assert re.search(rf"{re.escape(scoped)}\s*\{{", css)
                leftover = css.replace(scoped, "")
                assert "QLabel {" not in leftover
                assert not re.match(r"\s*(background|color|font-size|padding)\s*:", css)
        finally:
            dlg.deleteLater()


_OLD_HEX = ("#0d1117", "#161b22", "#21262d", "#58a6ff", "#e6edf3")


def _joined_styles(widget) -> str:
    from PySide6.QtWidgets import QWidget

    parts = [widget.styleSheet() or ""]
    parts.extend(child.styleSheet() or "" for child in widget.findChildren(QWidget))
    return "\n".join(parts).lower()


def test_debloat_picker_has_no_legacy_hex_and_primary_is_32px(qapp, catalog):
    from PySide6.QtWidgets import QDialogButtonBox, QPushButton

    dlg = DebloatPickerDialog(catalog)
    try:
        styles = _joined_styles(dlg)
        for hex_color in _OLD_HEX:
            assert hex_color not in styles
        apply_btn = dlg.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Apply)
        assert isinstance(apply_btn, QPushButton)
        assert apply_btn.minimumHeight() >= 32
    finally:
        dlg.deleteLater()


def test_debloat_picker_rebuild_uses_current_theme(qapp, catalog):
    from PySide6.QtWidgets import QDialogButtonBox

    previous = theme.current_scheme()
    try:
        theme.rebuild("light")
        light = DebloatPickerDialog(catalog)
        light_primary = light.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Apply
        )
        assert theme.C.SURFACE0 in light.styleSheet()
        assert theme.C.BLUE in light_primary.styleSheet()
        assert "#2B2B2B" not in light.styleSheet()
        light.deleteLater()

        theme.rebuild("dark")
        dark = DebloatPickerDialog(catalog)
        dark_primary = dark.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Apply
        )
        assert theme.C.SURFACE0 in dark.styleSheet()
        assert theme.C.BLUE in dark_primary.styleSheet()
        assert "#F9F9F9" not in dark.styleSheet()
        dark.deleteLater()
    finally:
        theme.rebuild(previous)
