# SPDX-License-Identifier: MIT
"""Fluent restyle pins for ``gui.app_dialog.AppProfileDialog``."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from winpodx.gui import theme  # noqa: E402
from winpodx.gui.app_dialog import AppProfileDialog  # noqa: E402

_OLD_HEX = ("#0d1117", "#161b22", "#21262d", "#58a6ff", "#e6edf3")


def _ensure_qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_app_dialog_has_no_legacy_hex_and_primary_is_32px() -> None:
    _ensure_qapp()
    dlg = AppProfileDialog()
    try:
        styles = [dlg.styleSheet() or ""]
        styles.extend(child.styleSheet() or "" for child in dlg.findChildren(QWidget))
        joined = "\n".join(styles).lower()
        for hex_color in _OLD_HEX:
            assert hex_color not in joined
        primary = next(
            btn for btn in dlg.findChildren(QPushButton) if btn.text() in {"Create", "Save"}
        )
        assert primary.minimumHeight() >= 32
    finally:
        dlg.deleteLater()


def test_app_dialog_rebuild_uses_current_theme() -> None:
    _ensure_qapp()
    previous = theme.current_scheme()
    try:
        theme.rebuild("light")
        light = AppProfileDialog()
        light_primary = next(
            btn for btn in light.findChildren(QPushButton) if btn.text() == "Create"
        )
        assert theme.C.MANTLE in light.styleSheet()
        assert theme.C.BLUE in light_primary.styleSheet()
        assert "#2B2B2B" not in light.styleSheet()
        light.deleteLater()

        theme.rebuild("dark")
        dark = AppProfileDialog()
        dark_primary = next(btn for btn in dark.findChildren(QPushButton) if btn.text() == "Create")
        assert "#191919" in dark.styleSheet()
        assert theme.C.BLUE in dark_primary.styleSheet()
        dark.deleteLater()
    finally:
        theme.rebuild(previous)


def test_app_dialog_has_a_button_strip_and_32px_fields() -> None:
    from PySide6.QtWidgets import QFrame, QLineEdit

    _ensure_qapp()
    dlg = AppProfileDialog()
    try:
        strip = dlg.findChild(QFrame, "dialogButtonStrip")
        assert strip is not None
        for field in dlg.findChildren(QLineEdit):
            assert field.minimumHeight() >= 32
        primary = next(
            btn for btn in dlg.findChildren(QPushButton) if btn.text() in {"Create", "Save"}
        )
        assert primary.minimumWidth() >= 96
        assert primary.minimumHeight() >= 32
    finally:
        dlg.deleteLater()
