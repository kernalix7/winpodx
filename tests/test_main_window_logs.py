# SPDX-License-Identifier: MIT
"""Tests for the Logs-tab diagnostics buttons (LogsMixin).

Regression coverage for the bug where the Terminal-tab quick buttons
("Status" / "Pod logs" / "Inspect") hardcoded ``podman`` and so fired
``podman ...`` even when the user had selected the Docker backend.

The command construction lives in the pure ``_diagnostic_commands`` /
``_backend_cli`` helpers so it can be exercised headlessly — only the
module import needs Qt (the LogsMixin module imports PySide6 widgets at
top level), hence the importorskip.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QWidget,
)

from winpodx.core.config import Config  # noqa: E402
from winpodx.gui import theme  # noqa: E402
from winpodx.gui._main_window_logs import LogsMixin  # noqa: E402


class Harness(LogsMixin):
    """Bare host exposing only what the diagnostics helpers read."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg


def _cfg(backend: str, container: str = "winpodx-windows") -> Config:
    cfg = Config()
    cfg.pod.backend = backend
    cfg.pod.container_name = container
    return cfg


# ----- _backend_cli ------------------------------------------------------


def test_backend_cli_podman():
    assert Harness(_cfg("podman"))._backend_cli() == "podman"


def test_backend_cli_docker():
    assert Harness(_cfg("docker"))._backend_cli() == "docker"


def test_backend_cli_manual_falls_back_to_podman():
    # manual / raw-RDP has no container CLI; fall back to podman (inert).
    assert Harness(_cfg("manual"))._backend_cli() == "podman"


def test_backend_cli_unexpected_falls_back_to_podman():
    assert Harness(_cfg("libvirt"))._backend_cli() == "podman"


# ----- _diagnostic_commands honours the backend --------------------------


def _container_cmds(harness: Harness) -> list[list[str]]:
    """The list-shaped (shelled-out) commands among the quick buttons."""
    return [cmd for _label, cmd in harness._diagnostic_commands() if isinstance(cmd, list)]


def test_diagnostic_commands_use_docker_when_docker_backend():
    cmds = _container_cmds(Harness(_cfg("docker")))
    assert cmds, "expected at least one container command"
    # Every shelled-out container command must target docker, never podman.
    assert all(cmd[0] == "docker" for cmd in cmds)
    assert not any(cmd[0] == "podman" for cmd in cmds)


def test_diagnostic_commands_use_podman_when_podman_backend():
    cmds = _container_cmds(Harness(_cfg("podman")))
    assert cmds
    assert all(cmd[0] == "podman" for cmd in cmds)


def test_diagnostic_commands_cover_status_logs_inspect():
    """The three container probes are present and shaped as expected."""
    harness = Harness(_cfg("docker", container="my-win"))
    by_label = {label: cmd for label, cmd in harness._diagnostic_commands()}
    assert by_label["Status"] == ["docker", "ps", "-a", "--filter", "name=my-win"]
    assert by_label["Pod logs"] == ["docker", "logs", "--tail", "100", "my-win"]
    assert by_label["Inspect"] == ["docker", "inspect", "my-win"]
    # Non-command entries are preserved for the caller's signal wiring.
    assert by_label["App log"] == "tail_app_log"
    assert by_label["RDP Test"] is None
    assert by_label["Clear"] is None


def test_diagnostic_commands_track_renamed_container():
    cmds = _container_cmds(Harness(_cfg("podman", container="renamed-pod")))
    # The name appears either as a bare arg (logs / inspect) or inside the
    # ps --filter value (name=renamed-pod), so match against the joined line.
    assert all("renamed-pod" in " ".join(cmd) for cmd in cmds)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _PageHarness(LogsMixin):
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.log_signal = type("S", (), {"emit": staticmethod(lambda *_a, **_k: None)})()
        self.page = None

    def build_page(self):
        self.page = self._build_logs_page()
        return self.page


def test_logs_toolbar_controls_are_32px_and_terminal_object_is_unchanged(qapp):
    harness = _PageHarness(_cfg("podman"))
    page = harness.build_page()
    terminal = harness.log_output
    assert isinstance(terminal, QTextEdit)
    assert terminal.isReadOnly()
    for cls in (QPushButton, QComboBox, QLineEdit):
        for widget in page.findChildren(cls):
            assert widget.minimumHeight() >= 32
    assert harness.log_output is terminal


def test_logs_toolbar_controls_do_not_overlap_at_780px(qapp):
    harness = _PageHarness(_cfg("podman"))
    page = harness.build_page()
    page.resize(780, 720)
    page.show()
    qapp.processEvents()
    toolbar = page.findChild(QWidget, "logsToolbar")

    assert toolbar is not None
    controls = [
        widget
        for widget in toolbar.findChildren(QWidget)
        if isinstance(widget, (QLabel, QPushButton, QComboBox))
    ]
    rects = [widget.rect().translated(widget.mapTo(toolbar, QPoint(0, 0))) for widget in controls]
    assert all(widget.height() >= 32 for widget in controls)
    assert all(
        not left.intersects(right) for i, left in enumerate(rects) for right in rects[i + 1 :]
    )


def test_logs_level_combo_uses_live_theme_combo_chevron(qapp):
    harness = _PageHarness(_cfg("podman"))
    harness.build_page()
    combo = harness.input_log_level
    ss = combo.styleSheet()
    assert "QComboBox::down-arrow" in ss
    assert "chevron-down.svg" in ss or "QComboBox::down-arrow" in theme.COMBO
    assert ss.count("::down-arrow") == theme.COMBO.count("::down-arrow")
    assert ss.count("::drop-down") == theme.COMBO.count("::drop-down")
    assert combo.minimumHeight() >= 32
    assert combo.minimumWidth() >= 120
    theme.rebuild("dark")
    harness._restyle_logs()
    assert "chevron-down" in harness.input_log_level.styleSheet()
    assert "QComboBox::down-arrow" in harness.input_log_level.styleSheet()
    theme.rebuild("light")
    harness._restyle_logs()
    assert "chevron-down" in harness.input_log_level.styleSheet()


def test_logs_toolbar_buttons_are_icon_ghosts_with_tooltips(qapp):
    harness = _PageHarness(_cfg("podman"))
    page = harness.build_page()
    toolbar = page.findChild(QWidget, "logsToolbar")
    assert toolbar is not None
    ghosts = [
        btn
        for btn in toolbar.findChildren(QPushButton)
        if btn.accessibleName() in {"Inspect", "RDP Test", "Clear"}
    ]
    names = {btn.accessibleName() for btn in ghosts}
    for label in ("Inspect", "RDP Test", "Clear"):
        assert label in names
    for btn in ghosts:
        assert btn.toolTip()
        assert not btn.text()
        assert not btn.icon().isNull()
        assert btn.minimumHeight() >= 32
        assert btn.minimumWidth() >= 32


def test_logs_viewer_wraps_and_hides_horizontal_scrollbar(qapp):
    harness = _PageHarness(_cfg("podman"))
    harness.build_page()
    viewer = harness.log_output
    assert viewer.lineWrapMode() == QTextEdit.LineWrapMode.WidgetWidth
    assert viewer.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert theme.C.MANTLE in viewer.styleSheet()


def test_logs_command_bar_is_pinned_below_the_viewer(qapp):
    harness = _PageHarness(_cfg("podman"))
    page = harness.build_page()
    page.resize(780, 720)
    page.show()
    qapp.processEvents()
    bar = page.findChild(QWidget, "logsCommandBar")
    assert bar is not None
    run = next(btn for btn in bar.findChildren(QPushButton) if btn.text() == "Run")
    assert theme.C.BLUE in run.styleSheet()
    assert run.minimumHeight() >= 32
    assert harness.cmd_input.minimumHeight() >= 32
    viewer_bottom = harness.log_output.mapTo(page, QPoint(0, harness.log_output.height())).y()
    bar_top = bar.mapTo(page, QPoint(0, 0)).y()
    assert bar_top >= viewer_bottom


def test_logs_source_is_a_segmented_pod_logs_app_log_row(qapp):
    harness = _PageHarness(_cfg("podman"))
    page = harness.build_page()
    toolbar = page.findChild(QWidget, "logsToolbar")
    assert toolbar is not None
    source = {
        btn.accessibleName(): btn for btn in toolbar.findChildren(QPushButton) if btn.isCheckable()
    }
    assert "Pod logs" in source
    assert "App log" in source
    for btn in source.values():
        assert btn.text()
        assert btn.minimumHeight() >= 32
    source["Pod logs"].click()
    qapp.processEvents()
    assert source["Pod logs"].isChecked()
    assert not source["App log"].isChecked()


def test_logs_command_history_remembers_last_entries(qapp):
    harness = _PageHarness(_cfg("podman"))
    harness.build_page()
    harness._run_log_cmd = lambda _cmd: None
    harness.cmd_input.setText("podman ps")
    harness._on_cmd_enter()
    assert harness._cmd_history[0] == "podman ps"
    harness.cmd_input.setText("docker logs winpodx-windows")
    harness._on_cmd_enter()
    assert harness._cmd_history[:2] == ["docker logs winpodx-windows", "podman ps"]
    completer = harness.cmd_input.completer()
    assert completer is not None
    assert completer.model().rowCount() == 2
