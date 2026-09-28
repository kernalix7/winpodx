# SPDX-License-Identifier: MIT
"""Tests for the Win11 Settings-style GUI setup wizard."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget  # noqa: E402

from winpodx.core.config import Config  # noqa: E402
from winpodx.setup_wizard.host_state import HostState  # noqa: E402


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _ok_state() -> HostState:
    return HostState(
        in_kvm_group=True,
        kvm_group_exists=True,
        dev_kvm_present=True,
        dev_kvm_readable=True,
        subuid_configured=True,
        subgid_configured=True,
        kvm_module_persistent=True,
    )


def _fail_kvm() -> HostState:
    return HostState(
        in_kvm_group=True,
        kvm_group_exists=True,
        dev_kvm_present=False,
        dev_kvm_readable=False,
        subuid_configured=True,
        subgid_configured=True,
        kvm_module_persistent=False,
    )


def _fail_fixable() -> HostState:
    return HostState(
        in_kvm_group=False,
        kvm_group_exists=True,
        dev_kvm_present=True,
        dev_kvm_readable=False,
        subuid_configured=False,
        subgid_configured=False,
        kvm_module_persistent=False,
    )


def _patch_detect(monkeypatch: pytest.MonkeyPatch, detect) -> None:
    from winpodx.setup_wizard.host_state import PreflightIssue, PreflightReport

    monkeypatch.setattr("winpodx.gui._setup_wizard_prereq.detect_host_state", detect)
    monkeypatch.setattr("winpodx.setup_wizard.host_state.detect_host_state", detect)
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard_prereq.inspect_preflight",
        lambda *args, **kwargs: PreflightReport(
            tuple(
                PreflightIssue(
                    field,
                    field,
                    field in ("in_kvm_group", "subuid_configured", "subgid_configured"),
                )
                for field in detect().blocking_failures
            )
        ),
    )


def _wait_until(pred, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    while time.monotonic() < deadline:
        if pred():
            return
        if app is not None:
            app.processEvents()
        time.sleep(0.01)
    raise AssertionError("timed out waiting for wizard state")


def _wheel(widget: QWidget, delta_y: int) -> None:
    """Deliver one mouse-wheel notch (``delta_y`` in eighths of a degree) to ``widget``."""
    center = QPointF(widget.rect().center())
    event = QWheelEvent(
        center,
        widget.mapToGlobal(center),
        QPoint(0, 0),
        QPoint(0, delta_y),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


def _config_page():
    from winpodx.gui._setup_wizard_config import ConfigurationPage
    from winpodx.gui._setup_wizard_model import SetupAnswers

    return ConfigurationPage(
        SetupAnswers(
            win_version="11",
            language="English",
            region="en-001",
            keyboard="en-US",
            timezone="UTC",
            cpu_cores=4,
            ram_gb=8,
            disk_size="64G",
            rdp_user="Docker",
            tuning_profile="auto",
        )
    )


@pytest.fixture
def wizard(monkeypatch: pytest.MonkeyPatch):
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: None)
    monkeypatch.setattr("winpodx.setup_wizard.pkexec.apply_via_pkexec", lambda state: None)
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    yield dlg
    _wait_until(lambda: dlg._thread is None)
    dlg.close()


def test_page_order_and_next_back_gating(wizard) -> None:
    assert wizard.pages.count() == 6
    assert wizard.pages.currentIndex() == 0
    assert wizard.back_btn.isVisible() is False
    assert wizard.next_btn.isEnabled() is True

    wizard.next_btn.click()
    assert wizard.pages.currentIndex() == 1
    assert wizard.back_btn.isVisible() is True
    assert wizard.next_btn.isEnabled() is True

    wizard.next_btn.click()
    assert wizard.pages.currentIndex() == 2
    wizard.next_btn.click()
    assert wizard.pages.currentIndex() == 3
    assert wizard.next_btn.text()
    wizard.back_btn.click()
    assert wizard.pages.currentIndex() == 2


def test_prerequisites_block_next_until_required_items_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    holder = {"state": _fail_kvm()}
    _patch_detect(monkeypatch, lambda: holder["state"])
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: None)
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    dlg.next_btn.click()
    assert dlg.pages.currentIndex() == 1
    assert dlg.next_btn.isEnabled() is False
    assert dlg.prereq.can_proceed() is False

    holder["state"] = _ok_state()
    dlg.prereq._paint(holder["state"])
    assert dlg.prereq.can_proceed() is True
    assert dlg.next_btn.isEnabled() is True
    dlg.close()


def test_prerequisites_show_all_unfixable_preflight_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpodx.setup_wizard.host_state import PreflightIssue, PreflightReport

    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    issues = (
        PreflightIssue("cpu_virtualization", "Enable virtualization in firmware", False),
        PreflightIssue("ram", "At least 8 GiB RAM is required", False),
    )
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard_prereq.inspect_preflight",
        lambda *args, **kwargs: PreflightReport(issues),
    )
    from winpodx.gui._setup_wizard_prereq import PrerequisitesPage

    page = PrerequisitesPage()
    page.show()
    assert not page.can_proceed()
    assert all(
        issue.detail in " ".join(label.text() for label in page.findChildren(QLabel))
        for issue in issues
    )
    assert not page._fix_btn.isVisible()
    page.close()


def test_prerequisites_select_docker_when_podman_is_unavailable(monkeypatch, tmp_path) -> None:
    from winpodx.setup_wizard.host_state import PreflightReport
    from winpodx.utils.deps import DepCheck

    _ensure_qapp()
    monkeypatch.setattr(Config, "path", classmethod(lambda cls: tmp_path / "not-installed.toml"))
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard_prereq.check_all",
        lambda: {
            "podman": DepCheck("podman", False),
            "docker": DepCheck("docker", True),
        },
    )
    monkeypatch.setattr("winpodx.gui._setup_wizard_prereq.detect_host_state", _ok_state)
    seen = []
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard_prereq.inspect_preflight",
        lambda cfg: (seen.append(cfg.pod.backend), PreflightReport(()))[1],
    )
    from winpodx.gui._setup_wizard_prereq import PrerequisitesPage

    page = PrerequisitesPage()

    assert seen == ["docker"]
    assert page.can_proceed()
    page.close()


def test_optional_kvm_module_does_not_block_next(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    state = _ok_state()
    state = HostState(
        in_kvm_group=True,
        kvm_group_exists=True,
        dev_kvm_present=True,
        dev_kvm_readable=True,
        subuid_configured=True,
        subgid_configured=True,
        kvm_module_persistent=False,
    )
    _patch_detect(monkeypatch, lambda: state)
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: None)
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    dlg.next_btn.click()
    assert dlg.next_btn.isEnabled() is True
    dlg.close()


def test_fix_these_unblocks_after_simulated_pkexec(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    holder = {"state": _fail_fixable()}
    _patch_detect(monkeypatch, lambda: holder["state"])

    def _apply(state) -> None:
        holder["state"] = _ok_state()

    monkeypatch.setattr("winpodx.setup_wizard.pkexec.apply_via_pkexec", _apply)
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: None)
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    dlg.next_btn.click()
    assert dlg.next_btn.isEnabled() is False
    fix = dlg.findChild(QPushButton, "wizardFixPrereqs")
    assert fix is not None
    assert fix.isVisible() is True
    fix.click()
    _wait_until(lambda: dlg.prereq.can_proceed())
    _wait_until(lambda: dlg.prereq._thread is None)
    assert dlg.next_btn.isEnabled() is True
    dlg.close()


def test_install_calls_handle_setup_once_with_collected_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    seen: list = []
    progress_callbacks: list = []

    def fake_handle_setup(args, *, on_progress=None) -> None:
        seen.append(args)
        progress_callbacks.append(on_progress)

    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", fake_handle_setup)
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    dlg.config._cpu.setValue(12)
    dlg.config._ram.setValue(32)
    dlg.config._user.setText("Kim")
    dlg.next_btn.click()
    dlg.next_btn.click()
    dlg.next_btn.click()
    assert dlg.pages.currentIndex() == 3
    dlg.next_btn.click()
    _wait_until(lambda: dlg.pages.currentIndex() == 5)
    assert len(seen) == 1
    assert len(progress_callbacks) == 1
    assert callable(progress_callbacks[0])
    args = seen[0]
    assert args.non_interactive is True
    assert args.customize is False
    assert args.cpu_cores == 12
    assert args.ram_gb == 32
    assert args.rdp_user == "Kim"
    assert args.win_version
    assert args.language
    assert args.region
    assert args.keyboard
    assert args.timezone
    assert args.disk_size
    assert args.tuning_profile == "auto"
    assert args.update_image is False
    _wait_until(lambda: dlg._thread is None)
    dlg.close()


def test_failure_shows_error_state_without_closing(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)

    def _boom(args) -> None:
        raise RuntimeError("no podman")

    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", _boom)
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    dlg.next_btn.click()
    dlg.next_btn.click()
    dlg.next_btn.click()
    dlg.next_btn.click()
    _wait_until(lambda: dlg.pages.currentIndex() == 5)
    assert dlg.isVisible() is True
    assert dlg.finish.is_failure is True
    assert dlg.findChild(QPushButton, "wizardRetry") is not None
    _wait_until(lambda: dlg._thread is None)
    dlg.close()


def test_retry_returns_to_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    monkeypatch.setattr(
        "winpodx.cli.setup_cmd.handle_setup",
        lambda args: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    for _ in range(4):
        dlg.next_btn.click()
    _wait_until(lambda: dlg.pages.currentIndex() == 5)
    retry = dlg.findChild(QPushButton, "wizardRetry")
    assert retry is not None
    retry.click()
    assert dlg.pages.currentIndex() == 2
    _wait_until(lambda: dlg._thread is None)
    dlg.close()


def test_skip_rejects_without_calling_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    seen: list = []
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: seen.append(args))
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    dlg.skip_btn.click()
    assert seen == []
    assert dlg.result() != 0 or not dlg.isVisible()


def test_reinstall_prefills_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: None)
    monkeypatch.setattr("winpodx.cli.pod.handle_pod", lambda args: None)
    cfg = Config()
    cfg.pod.win_version = "10"
    cfg.pod.cpu_cores = 6
    cfg.pod.ram_gb = 8
    cfg.pod.language = "Korean"
    cfg.rdp.user = "Park"
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="reinstall", cfg=cfg)
    dlg.show()
    answers = dlg.config.answers()
    assert answers.win_version == "10"
    assert answers.cpu_cores == 6
    assert answers.ram_gb == 8
    assert answers.language == "Korean"
    assert answers.rdp_user == "Park"
    assert dlg.skip_btn.isVisible() is False
    dlg.close()


def test_unfocused_wheel_leaves_combo_value_unchanged() -> None:
    _ensure_qapp()
    page = _config_page()
    assert page._disk.hasFocus() is False
    assert page._disk.currentData() == "64G"

    _wheel(page._disk, -120)

    assert page._disk.currentData() == "64G"
    assert page.answers().disk_size == "64G"


def test_unfocused_wheel_leaves_spin_value_unchanged() -> None:
    _ensure_qapp()
    page = _config_page()
    assert page._cpu.hasFocus() is False
    assert page._cpu.value() == 4

    _wheel(page._cpu, 120)

    assert page._cpu.value() == 4
    assert page.answers().cpu_cores == 4


def test_focused_wheel_still_steps_spin_value() -> None:
    app = _ensure_qapp()
    page = _config_page()
    page.show()
    page.activateWindow()
    app.processEvents()
    page._cpu.setFocus()
    app.processEvents()
    assert page._cpu.hasFocus() is True

    _wheel(page._cpu, 120)

    assert page._cpu.value() == 5
    page.close()
