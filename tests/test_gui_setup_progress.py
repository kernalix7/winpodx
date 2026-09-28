# SPDX-License-Identifier: MIT
"""Regression tests for setup-wizard chrome and install-progress rendering.

Two confirmed defects on current main, each locked here by behaviour
(widgets / signals), never by implementation prose:

1. ``SetupWizardDialog`` does not use the default frameless ``TitleBar``
   chrome the rest of the GUI uses, and ignores ``WINPODX_NATIVE_TITLEBAR=1``.
2. The wizard never connects worker progress into the ``InstallPage``
   checklist / visible log, so multi-stage progression is invisible.

A third test locks the reinstall route: ``handle_pod`` reset progress
(recreate steps + the same provisioning stages) must reach the same
``InstallPage`` log and checklist while the install is still running.

Everything is patched at the lookup site; no real podman/docker/system
state is touched.
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from collections.abc import Callable

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from winpodx.core.config import Config  # noqa: E402
from winpodx.setup_wizard.host_state import HostState, PreflightReport  # noqa: E402

# The real finish_provisioning stage slugs on the podman/docker path, in order,
# each with a distinct detail so log + checklist advancement is verifiable. The
# manual-backend-only "backend" early-return stage is not on this path.
_FINISH_PROVISIONING_STAGES: tuple[tuple[str, str], ...] = (
    ("wait_ready", "Waiting for the pod to become responsive..."),
    ("agent_settle", "Guest agent settling..."),
    ("apply_fixes", "Applying Windows-side runtime fixes..."),
    ("discovery", "Discovering and registering apps..."),
    ("reverse_open", "Starting reverse-open listener..."),
)

# What ``handle_pod(reset)`` streams before finish_provisioning's own stages:
# the recreate steps and the reset hand-off. Neither slug owns a checklist row,
# so they must reach the visible log without disturbing row 0.
_RESET_PRE_STAGES: tuple[tuple[str, str], ...] = (
    ("recreate", "Stopping pod..."),
    ("recreate", "Regenerating compose.yaml from current config..."),
    ("recreate", "Starting pod with new compose..."),
    ("reset", "Re-running provisioning on the fresh guest..."),
)
_RESET_DONE_DETAIL = "Reset complete. The guest is provisioned and ready."


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


def _patch_detect(monkeypatch: pytest.MonkeyPatch, detect) -> None:
    monkeypatch.setattr("winpodx.gui._setup_wizard_prereq.detect_host_state", detect)
    monkeypatch.setattr("winpodx.setup_wizard.host_state.detect_host_state", detect)


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


# --------------------------------------------------------------------------
# Defect 1 — SetupWizardDialog chrome: frameless TitleBar + native opt-out
# --------------------------------------------------------------------------


def test_wizard_uses_frameless_titlebar_chrome_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: None)
    monkeypatch.delenv("WINPODX_NATIVE_TITLEBAR", raising=False)
    from winpodx.gui._frameless import FramelessMixin
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    try:
        # Given: the default (no native-titlebar) environment.
        # Then: the wizard opts into the same frameless chrome as the main UI.
        assert isinstance(dlg, FramelessMixin)
        assert getattr(dlg, "_frameless_active", False) is True
        assert bool(dlg.windowFlags() & Qt.WindowType.FramelessWindowHint) is True
        assert getattr(dlg, "title_bar", None) is not None
        assert dlg.title_bar.objectName() == "titleBar"
    finally:
        dlg.close()


def test_wizard_honors_native_titlebar_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: None)
    monkeypatch.setenv("WINPODX_NATIVE_TITLEBAR", "1")
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    try:
        # Given: WINPODX_NATIVE_TITLEBAR=1.
        # Then: the wizard keeps native decorations (no frameless hint).
        assert getattr(dlg, "_frameless_active", True) is False
        assert bool(dlg.windowFlags() & Qt.WindowType.FramelessWindowHint) is False
    finally:
        dlg.close()


# --------------------------------------------------------------------------
# DialogChrome (DD-004) — embedded InstallPage stays chrome-free
# --------------------------------------------------------------------------


def test_install_page_progress_has_no_titlebar_when_embedded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    from winpodx.gui._setup_wizard_pages import InstallPage
    from winpodx.gui._title_bar import TitleBar

    page = InstallPage(on_cancel=lambda: None, cfg=None)
    page.begin()
    try:
        dlg = page.progress
        assert dlg is not None
        assert dlg.title_bar is None
        assert dlg.findChildren(TitleBar) == []
        assert not (dlg.windowFlags() & Qt.WindowType.FramelessWindowHint)
        assert dlg.windowModality() == Qt.WindowModality.NonModal
        assert dlg.chrome_height == 0
        # The checklist rows are still fully populated without any chrome.
        assert len(dlg._row_widgets) == len(_FINISH_PROVISIONING_STAGES)
        assert dlg.header.text() != ""
    finally:
        page.deleteLater()


def test_wizard_install_page_stays_chrome_free_inside_the_framed_wizard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)

    def _fake_handle_setup(args, *, on_progress=None) -> None:
        assert on_progress is not None
        for stage, detail in _FINISH_PROVISIONING_STAGES:
            on_progress(stage, detail)

    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", _fake_handle_setup)
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard_prereq.inspect_preflight",
        lambda *_a, **_k: PreflightReport(()),
    )
    from winpodx.gui._setup_wizard import SetupWizardDialog
    from winpodx.gui._title_bar import TitleBar

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    try:
        for _ in range(4):
            dlg.next_btn.click()
        _wait_until(lambda: dlg.pages.currentIndex() == 5)

        assert dlg.findChildren(TitleBar) == [dlg.title_bar]
        assert dlg.install.progress.title_bar is None
    finally:
        _wait_until(lambda: dlg._thread is None)
        dlg.close()


# --------------------------------------------------------------------------
# Defect 2 — wizard wires worker progress into checklist + visible log
# --------------------------------------------------------------------------


def test_wizard_reflects_worker_progress_in_checklist_and_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)

    def _fake_handle_setup(args, *, on_progress=None) -> None:
        assert on_progress is not None
        for stage, detail in _FINISH_PROVISIONING_STAGES:
            on_progress(stage, detail)

    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", _fake_handle_setup)
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard_prereq.inspect_preflight",
        lambda *_a, **_k: PreflightReport(()),
    )
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="first-run")
    dlg.show()
    try:
        for _ in range(4):
            dlg.next_btn.click()
        _wait_until(lambda: dlg.pages.currentIndex() == 5)

        progress = dlg.install.progress
        assert progress is not None

        # Then: every stage's distinct detail reached the visible log, and each
        #       of the five stages advanced its own checklist row (all five
        #       custom rows started, not just the first two).
        log_text = dlg.install.log_text()
        assert "pod log unavailable" not in log_text
        for _stage, detail in _FINISH_PROVISIONING_STAGES:
            assert detail in log_text

        started_rows = len(progress._phase_started_at)
        assert started_rows == len(_FINISH_PROVISIONING_STAGES)
        assert dlg._scroll.verticalScrollBar().maximum() == 0
    finally:
        _wait_until(lambda: dlg._thread is None)
        dlg.close()


# --------------------------------------------------------------------------
# Reinstall route — handle_pod(reset) progress reaches the same log/checklist
# --------------------------------------------------------------------------


def test_reinstall_wizard_streams_handle_pod_progress_before_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_qapp()
    _patch_detect(monkeypatch, _ok_state)

    calls: list[str] = []
    fake_cfg = Config()
    fake_cfg.pod.backend = "podman"
    preset_targets: list[Config] = []
    saved: list[Config] = []
    received: list[tuple[argparse.Namespace, Callable[[str, str], None] | None]] = []
    release = threading.Event()

    def _fake_load(cls: type[Config]) -> Config:
        calls.append("load")
        return fake_cfg

    def _fake_save(self: Config) -> None:
        calls.append("save")
        saved.append(self)

    def _fake_presets(cfg: Config, args: argparse.Namespace) -> list[str]:
        calls.append("presets")
        preset_targets.append(cfg)
        return []

    def _unexpected_handle_setup(args: argparse.Namespace, *, on_progress=None) -> None:
        calls.append("handle_setup")

    def _fake_handle_pod(
        args: argparse.Namespace,
        *,
        on_progress: Callable[[str, str], None] | None = None,
        reset_config: Config | None = None,
        expected_config: Config | None = None,
    ) -> None:
        calls.append("handle_pod")
        received.append((args, on_progress))
        assert expected_config is fake_cfg
        assert reset_config is not fake_cfg
        if on_progress is None:
            return
        for stage, detail in _RESET_PRE_STAGES:
            on_progress(stage, detail)
        for stage, detail in _FINISH_PROVISIONING_STAGES:
            on_progress(stage, detail)
        if not release.wait(timeout=5.0):
            raise TimeoutError("test never released the fake reset")
        on_progress("reset", _RESET_DONE_DETAIL)

    monkeypatch.setattr(Config, "load", classmethod(_fake_load))
    monkeypatch.setattr(Config, "save", _fake_save)
    monkeypatch.setattr("winpodx.cli.setup_cmd.apply_setup_presets", _fake_presets)
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", _unexpected_handle_setup)
    monkeypatch.setattr("winpodx.cli.pod.handle_pod", _fake_handle_pod)
    monkeypatch.setattr("winpodx.gui._setup_wizard._confirm_reinstall_wipe", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "winpodx.gui._setup_wizard_prereq.inspect_preflight",
        lambda *_a, **_k: PreflightReport(()),
    )
    from winpodx.gui._setup_wizard import SetupWizardDialog

    dlg = SetupWizardDialog(None, mode="reinstall", cfg=fake_cfg)
    dlg.show()
    try:
        # Given: a reinstall wizard driven through Welcome -> Review -> Install.
        for _ in range(4):
            dlg.next_btn.click()
        progress = dlg.install.progress
        assert progress is not None

        # Then: the worker hands handle_pod a confirmed, ISO-keeping reset
        #       plus the progress callback (not the bare CLI call).
        _wait_until(lambda: bool(received))
        args, on_progress = received[0]
        assert on_progress is not None
        assert args.pod_command == "reset"
        assert args.yes is True
        assert args.redownload_iso is False

        # When: the fake reset has streamed every stage but has not returned.
        last_detail = _FINISH_PROVISIONING_STAGES[-1][1]
        _wait_until(lambda: last_detail in dlg.install.log_text())

        # Then: the install is still running and the user can already see it.
        assert dlg.pages.currentIndex() == 4
        assert dlg._thread is not None and dlg._thread.isRunning()
        assert progress._done is False
        assert not progress.pod_log_view.isVisible()
        log_text = dlg.install.log_text()
        assert "pod log unavailable" not in log_text
        for stage, detail in _RESET_PRE_STAGES:
            assert f"[{stage}] {detail}" in log_text
        for stage, detail in _FINISH_PROVISIONING_STAGES:
            assert f"[{stage}] {detail}" in log_text
        assert log_text.index("[recreate] Stopping pod...") < log_text.index("[wait_ready]")
        assert set(progress._phase_started_at) == set(range(len(_FINISH_PROVISIONING_STAGES)))
        assert progress._active_phase_idx == len(_FINISH_PROVISIONING_STAGES) - 1

        # When: the reset returns.
        release.set()
        _wait_until(lambda: dlg.pages.currentIndex() == 5)

        # Then: the reinstall route ran in order, and both the checklist and
        #       the Finish page report success.
        assert calls == ["load", "presets", "handle_pod"]
        assert preset_targets[0] is not fake_cfg
        assert saved == []
        assert f"[reset] {_RESET_DONE_DETAIL}" in dlg.install.log_text()
        assert progress._done is True
        assert len(progress._phase_done_at) == len(_FINISH_PROVISIONING_STAGES)
        assert dlg.finish.is_failure is False
        open_apps = dlg.finish.findChild(QPushButton, "wizardOpenApps")
        retry = dlg.finish.findChild(QPushButton, "wizardRetry")
        assert open_apps is not None and open_apps.isVisibleTo(dlg.finish)
        assert retry is not None and not retry.isVisibleTo(dlg.finish)
    finally:
        release.set()
        _wait_until(lambda: dlg._thread is None)
        dlg.close()


# --------------------------------------------------------------------------
# Reinstall pre-stages — recreate/reset get a human header, not "Phase 1/5"
# --------------------------------------------------------------------------
# handle_pod(reset) streams `recreate` and `reset` stages that own no
# checklist row. On main InstallPage.on_setup_progress only touches header +
# sub_detail for mapped stages, so these unmapped stages leave the header on
# the misleading "Phase 1 / 5 . Download & install Windows" and never surface
# their own detail. The approved behaviour gives each a human header while
# still routing the raw detail to sub_detail + the log and leaving the
# checklist untouched (they aren't provisioning phases).

# "agent_settle" -> row 1 (index 1). Row 0 is already seeded by begin(), so a
# stage that lands on a *later* row proves mapped routing still advances the
# checklist even after an unmapped pre-stage passed through.
_MAPPED_LATER_STAGE = _FINISH_PROVISIONING_STAGES[1][0]


def _fresh_install_page():
    _ensure_qapp()
    from winpodx.gui._setup_wizard_pages import InstallPage

    page = InstallPage(on_cancel=lambda: None, cfg=None)
    page.begin()
    return page


def test_recreate_stage_shows_preparing_header_detail_and_log_no_advance() -> None:
    page = _fresh_install_page()
    dlg = page.progress
    try:
        # Given: the freshly-embedded install page (no phase advanced yet).
        started_before = dict(dlg._phase_started_at)

        # When: a `recreate` pre-stage arrives.
        page.on_setup_progress("recreate", "Stopping pod...")

        # Then: the header reads the human "Preparing Windows", the raw detail
        #       lands in sub_detail and the log, and no checklist row started.
        assert dlg.header.text() == "Preparing Windows"
        assert dlg.sub_detail.text() == "Stopping pod..."
        assert "[recreate] Stopping pod..." in dlg.pod_log_view.toPlainText()
        assert dict(dlg._phase_started_at) == started_before
    finally:
        page.deleteLater()


def test_reset_stage_shows_reinstalling_header_detail_and_log_no_advance() -> None:
    page = _fresh_install_page()
    dlg = page.progress
    try:
        started_before = dict(dlg._phase_started_at)

        # When: a `reset` pre-stage arrives.
        page.on_setup_progress("reset", "Re-running provisioning on the fresh guest...")

        # Then: header reads "Reinstalling Windows"; detail + log carry it; the
        #       checklist stays where it was.
        assert dlg.header.text() == "Reinstalling Windows"
        assert dlg.sub_detail.text() == "Re-running provisioning on the fresh guest..."
        assert (
            "[reset] Re-running provisioning on the fresh guest..."
            in dlg.pod_log_view.toPlainText()
        )
        assert dict(dlg._phase_started_at) == started_before
    finally:
        page.deleteLater()


def test_unknown_stage_keeps_header_but_updates_detail_and_log() -> None:
    page = _fresh_install_page()
    dlg = page.progress
    try:
        # Given: a header set by a known pre-stage first.
        page.on_setup_progress("recreate", "Stopping pod...")
        header_before = dlg.header.text()
        started_before = dict(dlg._phase_started_at)

        # When: a stage with no mapping and no human header arrives.
        page.on_setup_progress("mystery_stage", "something happened")

        # Then: the header is unchanged, but the detail + log still update, and
        #       the checklist does not advance.
        assert dlg.header.text() == header_before
        assert dlg.sub_detail.text() == "something happened"
        assert "[mystery_stage] something happened" in dlg.pod_log_view.toPlainText()
        assert dict(dlg._phase_started_at) == started_before
    finally:
        page.deleteLater()


def test_mapped_stage_still_advances_checklist_after_a_pre_stage() -> None:
    page = _fresh_install_page()
    dlg = page.progress
    try:
        # Given: a recreate pre-stage that sets the human header and must not
        #        consume a checklist row (begin() already seeded row 0, so row
        #        1 is still untouched).
        page.on_setup_progress("recreate", "Stopping pod...")
        assert dlg.header.text() == "Preparing Windows"
        assert 1 not in dlg._phase_started_at

        # When: a mapped provisioning stage that routes to row 1 arrives.
        page.on_setup_progress(_MAPPED_LATER_STAGE, "Guest agent settling...")

        # Then: the header flips back from the human pre-stage form to the
        #       phase form, and row 1 advances (mapped routing survives).
        assert 1 in dlg._phase_started_at
        assert dlg._active_phase_idx == 1
        assert "Phase 2 /" in dlg.header.text()
    finally:
        page.deleteLater()
