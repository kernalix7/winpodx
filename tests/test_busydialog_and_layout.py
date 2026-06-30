# SPDX-License-Identifier: MIT
"""Regression tests for #550 (BusyDialog auto-close from a worker thread) and
#553 (word-wrap labels in a resizable scroll area must have a constant wrap
width so QBoxLayout::heightForWidth can't recurse to a SIGSEGV).

Runs headless via the offscreen Qt platform — no display server required.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

# Must be set before the QApplication ctor.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_busydialog_finish_from_worker_thread_closes_the_dialog():
    # #550: a bare worker thread has no Qt event loop, so the old
    # `QTimer.singleShot(0, dlg.finish)` never fired and the dialog hung open.
    # finish() is now signal-based, so a cross-thread call queues accept() onto
    # the GUI thread's exec() loop. The watchdog reject bounds the test so a
    # regression fails fast instead of hanging CI.
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog

    from winpodx.gui._widget_helpers import BusyDialog

    _qapp()
    dlg = BusyDialog(None, "Working", "Doing a thing...")

    def worker():
        time.sleep(0.05)  # let exec()'s loop start first (the realistic case)
        dlg.finish()  # cross-thread close

    threading.Thread(target=worker, daemon=True).start()
    QTimer.singleShot(3000, dlg.reject)  # watchdog: a hang would reject, not hang
    result = dlg.exec()

    assert result == QDialog.DialogCode.Accepted  # closed via finish(), not the watchdog


def test_busydialog_finish_is_signal_based():
    from winpodx.gui._widget_helpers import BusyDialog

    _qapp()
    dlg = BusyDialog(None, "T", "m")
    # The cross-thread-safe close path: a signal connected to accept().
    assert hasattr(dlg, "_close_requested")


def test_empty_panel_wrapped_labels_have_constant_width():
    # #553: word-wrap QLabels inside the resizable app-list scroll area must be
    # fixed-width, else their height tracks the viewport width and feeds back
    # into QBoxLayout::heightForWidth -> unbounded recursion -> SIGSEGV on 6.11.
    from PySide6.QtWidgets import QLabel

    from winpodx.gui._widget_helpers import make_empty_panel

    _qapp()
    panel = make_empty_panel("A title that is reasonably long", "Some body text here too")
    wrapped = [lbl for lbl in panel.findChildren(QLabel) if lbl.wordWrap()]
    assert wrapped, "empty panel should have word-wrap labels"
    for lbl in wrapped:
        # setFixedWidth makes min == max == the constant wrap width.
        assert lbl.minimumWidth() == lbl.maximumWidth() > 0, (
            "word-wrap label in a resizable scroll area must have a fixed width"
        )
