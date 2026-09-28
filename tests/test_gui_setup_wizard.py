# SPDX-License-Identifier: MIT
"""Tests for the Win11 Settings-style GUI setup wizard."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, QSize, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QWidget  # noqa: E402

from winpodx.core import i18n  # noqa: E402
from winpodx.core.config import Config  # noqa: E402
from winpodx.gui import theme  # noqa: E402
from winpodx.gui._setup_wizard import SetupWizardDialog  # noqa: E402
from winpodx.gui._setup_wizard_model import SetupAnswers  # noqa: E402
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


def _answers(**overrides) -> SetupAnswers:
    base = dict(
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
        backend="podman",
        storage_path="",
        win_iso="",
    )
    base.update(overrides)
    return SetupAnswers(**base)


def _config_page():
    from dataclasses import replace

    from winpodx.gui._setup_wizard_config import ConfigurationPage
    from winpodx.gui._setup_wizard_model import collect_answers

    initial = replace(collect_answers(None), cpu_cores=4, ram_gb=8, disk_size="64G")
    return ConfigurationPage(initial)


def _is_locked(widget: QWidget) -> bool:
    """Read-only/disabled representation of a lockable value control."""
    read_only = getattr(widget, "isReadOnly", None)
    if callable(read_only) and read_only():
        return True
    return not widget.isEnabled()


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
    # #655: Configuration precedes Prerequisites so the host check validates
    # the backend / storage / ISO the user just chose.
    assert wizard.pages.count() == 6
    assert wizard.pages.currentIndex() == 0
    assert wizard.pages.widget(0) is wizard.welcome
    assert wizard.pages.widget(1) is wizard.config
    assert wizard.pages.widget(2) is wizard.prereq
    assert wizard.pages.widget(3) is wizard.review
    assert wizard.pages.widget(4) is wizard.install
    assert wizard.pages.widget(5) is wizard.finish
    assert wizard.back_btn.isVisible() is False
    assert wizard.next_btn.isEnabled() is True

    wizard.next_btn.click()
    assert wizard.pages.currentIndex() == 1
    assert wizard.pages.currentWidget() is wizard.config
    assert wizard.back_btn.isVisible() is True
    assert wizard.next_btn.isEnabled() is True

    wizard.next_btn.click()
    assert wizard.pages.currentIndex() == 2
    assert wizard.pages.currentWidget() is wizard.prereq
    wizard.next_btn.click()
    assert wizard.pages.currentIndex() == 3
    assert wizard.pages.currentWidget() is wizard.review
    assert wizard.next_btn.isEnabled() is True
    wizard.back_btn.click()
    assert wizard.pages.currentIndex() == 2
    assert wizard.pages.currentWidget() is wizard.prereq


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
    assert dlg.pages.currentWidget() is dlg.config
    dlg.next_btn.click()
    assert dlg.pages.currentIndex() == 2
    assert dlg.pages.currentWidget() is dlg.prereq
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
        lambda cfg, **_kwargs: (seen.append(cfg.pod.backend), PreflightReport(()))[1],
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
    dlg.next_btn.click()
    assert dlg.pages.currentIndex() == 2
    assert dlg.pages.currentWidget() is dlg.prereq
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
    dlg.next_btn.click()
    assert dlg.pages.currentIndex() == 2
    assert dlg.pages.currentWidget() is dlg.prereq
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
    assert dlg.pages.currentIndex() == 1
    assert dlg.pages.currentWidget() is dlg.config
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


def test_setup_answers_to_namespace_maps_backend_storage_and_iso() -> None:
    from winpodx.gui._setup_wizard_model import to_namespace

    answers = _answers(
        backend="docker",
        storage_path="/srv/winpodx-store",
        win_iso="/media/Win11_24H2.iso",
    )

    namespace = to_namespace(answers)

    assert namespace.backend == "docker"
    assert namespace.storage_path == "/srv/winpodx-store"
    assert namespace.win_iso == "/media/Win11_24H2.iso"


def test_to_namespace_leaves_blank_storage_and_iso_unset() -> None:
    from winpodx.gui._setup_wizard_model import to_namespace

    namespace = to_namespace(_answers(storage_path="", win_iso=""))

    assert not namespace.storage_path
    assert not namespace.win_iso


def test_collect_answers_exposes_backend_storage_and_iso() -> None:
    from winpodx.gui._setup_wizard_model import collect_answers

    answers = collect_answers(None)

    assert isinstance(answers.backend, str)
    assert isinstance(answers.storage_path, str)
    assert isinstance(answers.win_iso, str)
    assert answers.win_iso == ""


def test_collect_answers_reinstall_prefills_backend_and_storage() -> None:
    from winpodx.gui._setup_wizard_model import collect_answers

    cfg = Config()
    cfg.pod.backend = "docker"
    cfg.pod.storage_path = "/srv/winpodx-store"

    answers = collect_answers(cfg)

    assert answers.backend == "docker"
    assert answers.storage_path == "/srv/winpodx-store"


def test_fresh_configuration_backend_storage_and_iso_are_editable(wizard) -> None:
    for name in ("_backend", "_storage", "_iso"):
        control = getattr(wizard.config, name)
        assert not _is_locked(control), f"{name} must stay editable on a fresh install"


def test_reinstall_locks_backend_storage_and_iso(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: None)
    monkeypatch.setattr("winpodx.cli.pod.handle_pod", lambda args: None)
    cfg = Config()
    cfg.pod.backend = "docker"
    cfg.pod.storage_path = "/srv/winpodx-store"
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="reinstall", cfg=cfg)
    dlg.show()

    for name in ("_backend", "_storage", "_iso"):
        control = getattr(dlg.config, name)
        assert _is_locked(control), f"{name} must be read-only on reinstall"
    answers = dlg.config.answers()
    assert answers.backend == "docker"
    assert answers.storage_path == "/srv/winpodx-store"
    dlg.close()


def test_reinstall_confirmation_refusal_starts_no_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard_prereq.inspect_preflight",
        lambda *_a, **_k: __import__(
            "winpodx.setup_wizard.host_state", fromlist=["PreflightReport"]
        ).PreflightReport(()),
    )
    started: list[object] = []
    monkeypatch.setattr("winpodx.cli.pod.handle_pod", lambda *a, **k: started.append(a))
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard._confirm_reinstall_wipe",
        lambda *_a, **_k: False,
    )
    cfg = Config()
    cfg.pod.backend = "docker"
    cfg.pod.storage_path = "/srv/winpodx-store"
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="reinstall", cfg=cfg)
    dlg.show()
    for _ in range(4):
        dlg.next_btn.click()

    assert dlg.pages.currentIndex() == 3
    assert dlg._thread is None
    assert started == []
    dlg.close()


_LONG_STORAGE = "/srv/Windows storage/" + "project-archive/" * 12 + "<guest>"
_LONG_ISO = "/media/Windows installers/" + "release-archive/" * 12 + "Windows_11.iso"


@pytest.fixture(params=[("en", "light"), ("en", "dark"), ("ko", "light"), ("ko", "dark")])
def shown_wizard(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SetupWizardDialog]:
    app = _ensure_qapp()
    language, scheme = request.param
    previous_language = i18n.current_language()
    i18n.set_language(language)
    theme.rebuild(scheme)
    _patch_detect(monkeypatch, _ok_state)
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard.collect_answers",
        lambda _cfg: _answers(storage_path=_LONG_STORAGE, win_iso=_LONG_ISO),
    )
    dlg = SetupWizardDialog()
    dlg.resize(840, 640)
    dlg.show()
    app.processEvents()
    try:
        yield dlg
    finally:
        dlg.close()
        dlg.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        i18n.set_language(previous_language)


@pytest.mark.parametrize("reenter", [False, True])
def test_review_values_have_geometry_when_navigated_after_show(
    shown_wizard: SetupWizardDialog, reenter: bool
) -> None:
    # Given: a shown 840x640 wizard, optionally returning after editing a choice.
    dlg = shown_wizard
    app = _ensure_qapp()
    dlg.next_btn.click()
    app.processEvents()
    if reenter:
        dlg.next_btn.click()
        dlg.next_btn.click()
        app.processEvents()
        dlg.back_btn.click()
        dlg.back_btn.click()
        dlg.config._user.setText("Updated user")
    dlg.next_btn.click()
    app.processEvents()

    # When: Next enters Review dynamically rather than before dialog.show().
    dlg.next_btn.click()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    # Then: all twelve full values have distinct, reachable rectangles.
    assert dlg.size() == QSize(840, 640)
    assert dlg.pages.currentIndex() == 3
    assert len(dlg.review.findChildren(QFrame, "settingsSection")) == 1
    cards = dlg.review.findChildren(QFrame, "settingsCard")
    assert len(cards) == 12
    previous_bottom = -1
    for card in cards:
        value = card.accessibleDescription()
        assert value
        description = next(label for label in card.findChildren(QLabel) if label.text() == value)
        bounds = QRect(description.mapTo(card, QPoint()), description.size())
        assert description.isVisible()
        assert bounds.isValid() and card.rect().contains(bounds)
        assert description.height() >= description.heightForWidth(description.width())
        card_bounds = QRect(card.mapTo(dlg.review, QPoint()), card.size())
        assert card_bounds.top() > previous_bottom
        previous_bottom = card_bounds.bottom()
        dlg._scroll.ensureWidgetVisible(description)
        app.processEvents()
        viewport = dlg._scroll.viewport()
        visible_bounds = QRect(description.mapTo(viewport, QPoint()), description.size())
        assert viewport.rect().contains(visible_bounds)
    assert cards[-1].accessibleDescription() == ("Updated user" if reenter else "Docker")
    assert dlg._scroll.horizontalScrollBar().maximum() == 0


def test_configuration_timezone_title_and_combo_fit_when_shown(
    shown_wizard: SetupWizardDialog,
) -> None:
    # Given: the shown 840x640 English/Korean light/dark wizard.
    dlg = shown_wizard
    app = _ensure_qapp()

    # When: Configuration is opened and the timezone card is scrolled into view.
    dlg.next_btn.click()
    app.processEvents()
    combo = dlg.config._timezone
    card = next(
        card
        for card in dlg.config.findChildren(QFrame, "settingsCard")
        if card.action_widget is dlg.config._timezone
    )
    viewport = dlg._scroll.viewport()
    assert not viewport.rect().contains(QRect(card.mapTo(viewport, QPoint()), card.size()))
    dlg._scroll.ensureWidgetVisible(card)
    app.processEvents()

    # Then: full title and combo fit inside the card, stacked and reachable.
    title = card.title_label
    title_bounds = QRect(title.mapTo(card, QPoint()), title.size())
    combo_bounds = QRect(combo.mapTo(card, QPoint()), combo.size())
    assert title.isVisible() and combo.isVisible()
    title_width = title.fontMetrics().horizontalAdvance(i18n.tr("Timezone"))
    assert title_width <= title.contentsRect().width()
    assert title_bounds.isValid() and card.rect().contains(title_bounds)
    assert combo_bounds.isValid() and card.rect().contains(combo_bounds)
    assert combo_bounds.top() > title_bounds.bottom()
    assert viewport.rect().contains(QRect(card.mapTo(viewport, QPoint()), card.size()))
    assert dlg._scroll.verticalScrollBar().value() > 0
    assert dlg._scroll.horizontalScrollBar().maximum() == 0
    assert not dlg._scroll.horizontalScrollBar().isVisible()
    assert dlg.size() == QSize(840, 640)


def test_configuration_full_path_previews_when_values_are_long(
    shown_wizard: SetupWizardDialog,
) -> None:
    # Given: long storage and ISO paths in a shown fresh-install dialog.
    dlg = shown_wizard
    app = _ensure_qapp()

    # When: the user opens Configuration.
    dlg.next_btn.click()
    app.processEvents()

    # Then: editors start at the prefix and full selectable previews wrap below.
    for editor in (dlg.config._storage, dlg.config._iso):
        assert not editor.isReadOnly()
        assert editor.cursorPosition() == 0
        host = editor.parentWidget()
        assert host is not None
        preview = host.findChild(QLabel, "setupPathPreview")
        assert preview is not None and preview.isVisible()
        assert preview.text() == editor.text()
        assert preview.textFormat() == Qt.TextFormat.PlainText
        assert preview.wordWrap()
        assert preview.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
        assert editor.accessibleDescription() == editor.text()
        assert preview.height() >= preview.heightForWidth(preview.width())
        assert preview.height() > preview.fontMetrics().height()
        assert preview.geometry().top() > editor.geometry().bottom()
        dlg._scroll.ensureWidgetVisible(preview)
        app.processEvents()
        viewport = dlg._scroll.viewport()
        assert viewport.rect().contains(QRect(preview.mapTo(viewport, QPoint()), preview.size()))
    assert dlg.size() == QSize(840, 640)
    assert dlg._scroll.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize("replacement", ["/new/location", ""])
def test_path_preview_tracks_keyboard_edits_when_fresh(
    shown_wizard: SetupWizardDialog, replacement: str
) -> None:
    # Given: Configuration with populated path editors.
    dlg = shown_wizard
    dlg.next_btn.click()
    app = _ensure_qapp()
    app.processEvents()
    for editor in (dlg.config._storage, dlg.config._iso):
        # When: the user replaces or clears a path using the keyboard.
        editor.selectAll()
        QTest.keyClick(editor, Qt.Key.Key_Backspace)
        QTest.keyClicks(editor, replacement)
        app.processEvents()

        # Then: preview/accessibility follow the value without resetting the caret.
        host = editor.parentWidget()
        assert host is not None
        preview = host.findChild(QLabel, "setupPathPreview")
        assert preview is not None
        assert editor.text() == replacement
        assert preview.text() == replacement
        assert preview.isHidden() == (not replacement)
        assert editor.accessibleDescription() == replacement
        assert editor.cursorPosition() == len(replacement)


def test_reinstall_paths_stay_unchanged_when_typing() -> None:
    from winpodx.gui._setup_wizard_config import ConfigurationPage

    # Given: reinstall controls with long existing paths.
    app = _ensure_qapp()
    initial = _answers(storage_path=_LONG_STORAGE, win_iso=_LONG_ISO, backend="docker")
    page = ConfigurationPage(initial, reinstall=True)
    page.show()
    app.processEvents()
    try:
        # When: keyboard edits are attempted in each read-only path control.
        for editor in (page._storage, page._iso):
            editor.selectAll()
            QTest.keyClicks(editor, "/replacement")

        # Then: backend and both path values remain the original choices.
        assert not page._backend.isEnabled()
        assert page._storage.isReadOnly() and page._iso.isReadOnly()
        assert page.answers().backend == "docker"
        assert page.answers().storage_path == _LONG_STORAGE
        assert page.answers().win_iso == _LONG_ISO
    finally:
        page.close()
        page.deleteLater()
