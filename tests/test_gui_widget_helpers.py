# SPDX-License-Identifier: MIT
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QWidget,
)

from winpodx.core.app import AppInfo
from winpodx.gui import _widget_helpers as helpers


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_eliding_label_renders_ellipsis_and_preserves_full_tooltip(qapp) -> None:
    full_text = "Microsoft Office Professional Plus Document Editor"
    label = helpers.ElidingLabel(full_text)

    label.show()
    label.resize(70, label.sizeHint().height())
    qapp.processEvents()

    assert label.text().endswith("…")
    assert label.text() != full_text
    assert label.toolTip() == full_text
    assert label.minimumSizeHint().width() == 0
    assert label.sizeHint().width() <= helpers.ElidingLabel._PREF_CAP


def test_eliding_label_replaces_rendered_text_when_full_text_changes(qapp) -> None:
    label = helpers.ElidingLabel("Old device name")
    label.resize(400, 30)

    label.set_full_text("새로운 장치 이름")
    qapp.processEvents()

    assert label.text() == "새로운 장치 이름"
    assert label.toolTip() == "새로운 장치 이름"


def test_columns_want_stack_uses_widget_size_hints(qapp) -> None:
    host = QWidget()
    columns = QHBoxLayout(host)
    for text in ("A moderately wide first column", "A much wider second column label"):
        child = QLabel(text)
        columns.addWidget(child)

    required = 2 * max(columns.itemAt(i).widget().sizeHint().width() for i in range(2))
    required += columns.spacing()
    assert helpers.columns_want_stack(columns, required - 1)
    assert not helpers.columns_want_stack(columns, required)


def test_wheel_guard_installs_strong_focus_on_nested_controls(qapp) -> None:
    root = QWidget()
    combo = QComboBox(root)
    combo.addItems(["One", "Two"])

    helpers.guard_wheel_scroll(root)

    assert combo.focusPolicy() == Qt.FocusPolicy.StrongFocus


@pytest.mark.parametrize(
    ("source", "expected"),
    [("discovered", "Detected"), ("bundled", "Bundled")],
)
def test_source_badge_renders_recognized_provenance(qapp, source: str, expected: str) -> None:
    app = AppInfo(name="word", full_name="Word", executable="word.exe", source=source)

    badge = helpers.make_source_badge(app)

    assert badge is not None
    assert badge.text() == expected


def test_source_badge_omits_unknown_provenance(qapp) -> None:
    app = AppInfo(name="word", full_name="Word", executable="word.exe", source="custom")

    assert helpers.make_source_badge(app) is None


def test_app_avatar_falls_back_to_initial_when_icon_is_missing(qapp) -> None:
    app = AppInfo(name="excel", full_name="Excel", executable="excel.exe")

    avatar = helpers.make_app_avatar(app, 40, radius=8, font_size=14)

    assert avatar.text() == "E"
    assert avatar.size().width() == 40
    assert avatar.size().height() == 40


def test_app_avatar_renders_existing_image(qapp, tmp_path: Path) -> None:
    path = tmp_path / "app.png"
    image = QImage(16, 16, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))
    app = AppInfo(name="paint", full_name="Paint", executable="paint.exe", icon_path=str(path))

    avatar = helpers.make_app_avatar(app, 42, radius=8, font_size=14)

    assert avatar.pixmap() is not None
    assert not avatar.pixmap().isNull()
    assert avatar.text() == ""


def test_toast_shows_message_with_requested_accent(qapp) -> None:
    parent = QWidget()
    parent.resize(500, 300)
    parent.show()

    helpers.show_toast(parent, "Settings saved", kind="success", msecs=60_000)
    qapp.processEvents()
    toast = parent.findChild(QLabel, "winpodxToast")

    assert toast is not None
    assert toast.text() == "Settings saved"
    assert helpers.C.GREEN in toast.styleSheet()
    assert toast.isVisible()
    assert toast.width() <= parent.width() - 48


def test_busy_dialog_exposes_message_progress_hint_and_cancel_button(qapp) -> None:
    cancelled = []
    dialog = helpers.BusyDialog(
        None,
        "Scanning",
        "Looking for applications",
        eta_hint="Usually under a minute",
        cancellable=True,
    )
    dialog.on_cancel(lambda: cancelled.append(True))

    button = dialog.findChild(QPushButton)
    progress = dialog.findChild(QProgressBar)
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    button.click()

    assert dialog.windowTitle() == "Scanning"
    assert "Looking for applications" in labels
    assert "Usually under a minute" in labels
    assert progress.minimum() == 0 and progress.maximum() == 0
    assert button.text() == "Cancelling..."
    assert not button.isEnabled()
    assert cancelled == [True]


def test_busy_dialog_updates_message_and_finish_accepts(qapp) -> None:
    dialog = helpers.BusyDialog(None, "Working", "First message")

    dialog.set_message("Second message")
    dialog.finish()

    assert dialog._msg.text() == "Second message"
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.findChildren(QPushButton) == []


def test_warning_callout_renders_text_and_danger_accent(qapp) -> None:
    callout = helpers.make_warning_callout("This removes the disk", level="danger")

    texts = [label.text() for label in callout.findChildren(QLabel)]
    assert "This removes the disk" in texts
    assert helpers.C.RED in callout.styleSheet()


def test_page_header_renders_title_subtitle_and_action(qapp) -> None:
    action = QPushButton("Refresh")

    header = helpers.make_page_header(
        "Applications", "Installed Windows apps", actions_widget=action
    )

    assert [label.text() for label in header.findChildren(QLabel)] == [
        "Applications",
        "Installed Windows apps",
    ]
    assert header.findChild(QPushButton).text() == "Refresh"


def test_empty_panel_cjk_labels_hold_fixed_width_in_resizable_scroll_area(qapp) -> None:
    title = "非常に長いアプリケーション名 한글 프로그램 이름 中文应用程序名称" * 3
    body = "검색 결과가 없습니다。別の検索語を入力してください。"
    panel = helpers.make_empty_panel(title, body, action_label="Retry", action_cb=lambda: None)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(panel)

    wrapped = [label for label in panel.findChildren(QLabel) if label.wordWrap()]
    assert [label.text() for label in wrapped] == [title, body]
    assert all(label.minimumWidth() == label.maximumWidth() == 400 for label in wrapped)
    assert panel.maximumWidth() == 460
    assert panel.findChild(QPushButton).text() == "Retry"


def test_actionable_error_returns_clicked_custom_button(qapp, monkeypatch) -> None:
    def choose_retry(box: QMessageBox) -> int:
        next(button for button in box.buttons() if button.text() == "Retry").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", choose_retry)

    selected = helpers.actionable_error(
        None,
        "Launch failed",
        "The guest is unavailable",
        actions=["View logs", "Retry", "Close"],
        detail="RDP port refused the connection",
    )

    assert selected == "Retry"
