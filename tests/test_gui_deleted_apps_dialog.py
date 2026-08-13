# SPDX-License-Identifier: MIT
"""Tests for gui.deleted_apps_dialog — the un-delete UI for tombstoned app slugs.

The dialog owns no business logic: it lists tombstoned slugs and hands the chosen
ones back through ``on_restore``. These tests drive the real widgets under the
offscreen platform and click the actual buttons rather than calling the slots.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from winpodx.gui.deleted_apps_dialog import DeletedAppsDialog  # noqa: E402


def _ensure_qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _make(slugs):
    _ensure_qapp()
    restored: list[list[str]] = []
    dialog = DeletedAppsDialog(slugs=list(slugs), on_restore=restored.append)
    return dialog, restored


def _row_restore_button(dialog, slug: str):
    from PySide6.QtWidgets import QPushButton

    return dialog._rows[slug].findChild(QPushButton)


def test_rows_are_created_for_every_slug() -> None:
    dialog, _ = _make(["word", "excel"])

    assert set(dialog._rows) == {"word", "excel"}


def test_rows_are_listed_in_sorted_order() -> None:
    dialog, _ = _make(["word", "acrobat", "excel"])

    assert list(dialog._rows) == ["acrobat", "excel", "word"]


def test_empty_state_is_shown_when_nothing_is_tombstoned() -> None:
    dialog, _ = _make([])

    assert dialog._empty_lbl.isVisibleTo(dialog) is True
    assert dialog._restore_all_btn.isEnabled() is False


def test_empty_state_is_hidden_when_rows_exist() -> None:
    dialog, _ = _make(["word"])

    assert dialog._empty_lbl.isVisibleTo(dialog) is False
    assert dialog._restore_all_btn.isEnabled() is True


def test_restoring_one_reports_only_that_slug() -> None:
    dialog, restored = _make(["word", "excel"])

    _row_restore_button(dialog, "word").click()

    assert restored == [["word"]]


def test_restoring_one_drops_its_row() -> None:
    dialog, _ = _make(["word", "excel"])

    _row_restore_button(dialog, "word").click()

    assert list(dialog._rows) == ["excel"]


def test_restoring_the_last_row_returns_to_the_empty_state() -> None:
    dialog, _ = _make(["word"])

    _row_restore_button(dialog, "word").click()

    assert dialog._empty_lbl.isVisibleTo(dialog) is True
    assert dialog._restore_all_btn.isEnabled() is False


def test_restore_all_reports_every_remaining_slug() -> None:
    dialog, restored = _make(["word", "excel"])

    dialog._restore_all_btn.click()

    assert restored == [["excel", "word"]]


def test_restore_all_excludes_an_already_restored_slug() -> None:
    dialog, restored = _make(["word", "excel"])

    _row_restore_button(dialog, "word").click()
    dialog._restore_all_btn.click()

    assert restored == [["word"], ["excel"]]


def test_restore_all_closes_the_dialog() -> None:
    from PySide6.QtWidgets import QDialog

    dialog, _ = _make(["word"])

    dialog._restore_all_btn.click()

    assert dialog.result() == QDialog.DialogCode.Accepted


def test_restore_all_is_a_noop_with_no_rows() -> None:
    dialog, restored = _make([])

    dialog._on_restore_all()

    assert restored == []
