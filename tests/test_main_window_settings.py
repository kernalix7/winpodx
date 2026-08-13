# SPDX-License-Identifier: MIT
"""Tests for the Settings / header-chrome / navigation mixins.

``WinpodxWindow`` composes ~13 ``*Mixin`` classes; instantiating the whole
window drags in the pod poller, the log tails, the discovery worker and a
live QMainWindow. None of that is needed to exercise the mixins themselves:
each one is a plain class whose only contract is a handful of ``self.*``
attributes. So every test here mixes ONE mixin into a bare harness object
(a ``QWidget`` when the mixin parents real widgets/shortcuts to ``self``)
and stubs exactly the attributes that mixin reads.

Covers:
  - HeaderMixin: sidebar nav rows, pod chip + start/stop wiring, warning
    banner, info bar counts, bottom log ticker.
  - NavigationMixin: page switching (dashboard/tools timer lifecycle, info
    auto-refresh), Alt+N / Ctrl+F shortcuts, first-launch checks, the
    first-run setup prompt and the Quick Start dialog.
  - SettingsPageMixin: full page build off a Config, every widget's value
    round-tripping back into Config via ``_save_settings``, per-field
    validation/clamping, the recreate/wipe prompts, the disguise-level
    revert, the locale combo storage contract and the budget warning.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from winpodx.core.config import Config  # noqa: E402
from winpodx.gui._main_window_header import HeaderMixin  # noqa: E402
from winpodx.gui._main_window_nav import NavigationMixin  # noqa: E402
from winpodx.gui._main_window_settings import SettingsPageMixin  # noqa: E402

# ----- shared helpers ----------------------------------------------------


def _ensure_qapp() -> QApplication:
    """Return a QApplication, creating one if needed."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeSignal:
    """Minimal stand-in for a Qt Signal that records emits."""

    def __init__(self) -> None:
        self.emissions: list[tuple] = []

    def emit(self, *args) -> None:
        self.emissions.append(args)


class FakeTimer:
    """Records start()/stop() so timer lifecycle is observable."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def start(self) -> None:
        self.events.append("start")

    def stop(self) -> None:
        self.events.append("stop")


def _make_cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.cpu_cores = 4
    cfg.pod.ram_gb = 8
    return cfg


@pytest.fixture(autouse=True)
def _keep_english_ui():
    """A test that flips the UI language must not leak into its neighbours."""
    import winpodx.core.i18n as i18n_mod

    lang, catalog = i18n_mod._lang, dict(i18n_mod._catalog)
    yield
    i18n_mod._lang = lang
    i18n_mod._catalog = catalog


# ----- HeaderMixin -------------------------------------------------------


class HeaderHarness(HeaderMixin):
    """Bare host exposing only what HeaderMixin reads."""

    def __init__(self, cfg: Config, apps: list) -> None:
        self.cfg = cfg
        self.apps = apps
        self.switched: list[int] = []
        self.started = 0
        self.stopped = 0

    def _switch_page(self, index: int) -> None:
        self.switched.append(index)

    def _on_start_pod(self) -> None:
        self.started += 1

    def _on_stop_pod(self) -> None:
        self.stopped += 1


def test_sidebar_builds_eight_checkable_rows_with_dashboard_preselected() -> None:
    _ensure_qapp()
    host = HeaderHarness(_make_cfg(), apps=[])
    bar = host._build_sidebar()

    assert len(host.nav_buttons) == 8
    assert all(b.isCheckable() for b in host.nav_buttons)
    # Only the Dashboard row starts checked.
    assert [i for i, b in enumerate(host.nav_buttons) if b.isChecked()] == [0]
    # Each row is parented into the sidebar (never a stray top-level widget).
    assert all(b.parentWidget() is bar for b in host.nav_buttons)
    assert bar.width() == 200


def test_sidebar_rows_and_logo_route_to_their_page_index() -> None:
    _ensure_qapp()
    host = HeaderHarness(_make_cfg(), apps=[])
    bar = host._build_sidebar()

    host.nav_buttons[3].click()
    host.nav_buttons[7].click()
    assert host.switched == [3, 7]

    logo = bar.findChild(QPushButton, "logoHomeButton")
    assert logo is not None
    logo.click()
    assert host.switched == [3, 7, 0]


def test_top_strip_pod_controls_call_start_and_stop() -> None:
    _ensure_qapp()
    host = HeaderHarness(_make_cfg(), apps=[])
    host._strip = host._build_top_strip()

    assert host.pod_dot.size().width() == 10
    assert host.agent_dot.text() == "A"
    assert host.rdp_dot.text() == "R"
    assert host.pod_label.text()

    host.btn_start.click()
    host.btn_start.click()
    host.btn_stop.click()
    assert (host.started, host.stopped) == (2, 1)


def test_status_banner_starts_visible_and_its_button_starts_the_pod() -> None:
    _ensure_qapp()
    host = HeaderHarness(_make_cfg(), apps=[])
    banner = host._build_status_banner()

    assert not banner.isHidden()
    assert host.banner_text.text()
    assert host.banner_btn.text()
    host.banner_btn.click()
    assert host.started == 1


def test_info_bar_reports_app_count_backend_and_resources() -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.pod.backend = "docker"
    cfg.pod.cpu_cores = 6
    cfg.pod.ram_gb = 12
    host = HeaderHarness(cfg, apps=[object(), object(), object()])
    bar = host._build_info_bar()

    assert "3" in host.info_label.text()
    texts = [w.text() for w in bar.findChildren(QLabel)]
    assert "docker" in texts
    assert "6 CPU · 12 GB" in texts
    # The pod address label starts empty; _on_pod_status fills it later.
    assert host.info_pod_addr.text() == ""


def test_log_bar_exposes_two_empty_ticker_lines() -> None:
    _ensure_qapp()
    host = HeaderHarness(_make_cfg(), apps=[])
    bar = host._build_log_bar()

    assert bar.height() == 38
    assert host.log_bar_line1.text() == ""
    assert host.log_bar_line2.text() == ""


# ----- NavigationMixin ---------------------------------------------------


class NavHarness(NavigationMixin, QWidget):
    """QWidget host — NavigationMixin parents QShortcuts / dialogs to self."""

    def __init__(self, cfg: Config, *, apps=None, extras: bool = True) -> None:
        QWidget.__init__(self)
        self.cfg = cfg
        self.apps = [] if apps is None else apps
        self.log_signal = FakeSignal()
        self.info_started = 0
        self.info_stopped = 0
        self.refreshed_apps = 0

        self.pages = QStackedWidget(self)
        for _ in range(8):
            self.pages.addWidget(QWidget())
        self.nav_buttons = []
        for _ in range(8):
            btn = QPushButton(self)
            btn.setCheckable(True)
            self.nav_buttons.append(btn)
        self.search_box = QLineEdit(self)

        if extras:
            self._dashboard_timer = FakeTimer()
            self._sessions_timer = FakeTimer()
            self.workspace_calls = 0
            self.dashboard_calls = 0
            self.running_strip_calls = 0
            self.sessions_panel_calls: list[bool] = []
            self._populate_workspace = self._record_workspace
            self._refresh_dashboard = self._record_dashboard
            self._refresh_running_strip = self._record_running_strip
            self._refresh_sessions_panel = self._record_sessions_panel

    def _record_workspace(self) -> None:
        self.workspace_calls += 1

    def _record_dashboard(self) -> None:
        self.dashboard_calls += 1

    def _record_running_strip(self) -> None:
        self.running_strip_calls += 1

    def _record_sessions_panel(self, force: bool = False) -> None:
        self.sessions_panel_calls.append(force)

    def _start_info_auto_refresh(self) -> None:
        self.info_started += 1

    def _stop_info_auto_refresh(self) -> None:
        self.info_stopped += 1

    def _on_refresh_apps(self) -> None:
        self.refreshed_apps += 1


def test_switch_page_moves_the_stack_and_checks_only_that_nav_row() -> None:
    _ensure_qapp()
    host = NavHarness(_make_cfg())
    host._switch_page(4)

    assert host.pages.currentIndex() == 4
    assert [i for i, b in enumerate(host.nav_buttons) if b.isChecked()] == [4]

    host._switch_page(6)
    assert host.pages.currentIndex() == 6
    assert [i for i, b in enumerate(host.nav_buttons) if b.isChecked()] == [6]


def test_switch_page_runs_dashboard_polling_only_on_page_zero() -> None:
    _ensure_qapp()
    host = NavHarness(_make_cfg())

    host._switch_page(0)
    assert (host.workspace_calls, host.dashboard_calls) == (1, 1)
    assert host._dashboard_timer.events == ["start"]

    host._switch_page(2)
    assert host._dashboard_timer.events == ["start", "stop"]
    # Leaving the page must not re-run the (podman stats / disk) probes.
    assert (host.workspace_calls, host.dashboard_calls) == (1, 1)


def test_switch_page_refreshes_running_strip_on_all_apps_page_only() -> None:
    _ensure_qapp()
    host = NavHarness(_make_cfg())

    host._switch_page(1)
    assert host.running_strip_calls == 1
    host._switch_page(2)
    host._switch_page(1)
    assert host.running_strip_calls == 2


def test_switch_page_toggles_info_auto_refresh_on_the_info_page() -> None:
    _ensure_qapp()
    host = NavHarness(_make_cfg())

    host._switch_page(5)
    assert (host.info_started, host.info_stopped) == (1, 0)
    host._switch_page(0)
    assert (host.info_started, host.info_stopped) == (1, 1)


def test_switch_page_forces_a_session_scan_when_entering_tools() -> None:
    _ensure_qapp()
    host = NavHarness(_make_cfg())

    host._switch_page(3)
    assert host.sessions_panel_calls == [True]
    assert host._sessions_timer.events == ["start"]

    host._switch_page(0)
    assert host._sessions_timer.events == ["start", "stop"]


def test_switch_page_survives_a_host_without_the_optional_panels() -> None:
    _ensure_qapp()
    host = NavHarness(_make_cfg(), extras=False)

    for idx in (0, 1, 3, 5):
        host._switch_page(idx)
    assert host.pages.currentIndex() == 5
    assert (host.info_started, host.info_stopped) == (1, 3)


def test_install_shortcuts_registers_alt_keys_once_and_they_switch_pages() -> None:
    _ensure_qapp()
    from PySide6.QtGui import QShortcut

    host = NavHarness(_make_cfg())
    host._install_shortcuts()
    first = host.findChildren(QShortcut)
    # 8 Alt+N rows + Ctrl+F.
    assert len(first) == 9

    # Idempotent — a second call must not stack duplicates.
    host._install_shortcuts()
    assert len(host.findChildren(QShortcut)) == 9

    alt3 = [s for s in first if s.key().toString() == "Alt+3"]
    assert alt3, [s.key().toString() for s in first]
    alt3[0].activated.emit()
    assert host.pages.currentIndex() == 2


def test_find_shortcut_jumps_to_all_apps_and_focuses_the_search_box() -> None:
    _ensure_qapp()
    from PySide6.QtGui import QKeySequence, QShortcut

    host = NavHarness(_make_cfg())
    host.search_box.setText("notepad")
    host._switch_page(5)
    host._install_shortcuts()

    find_seq = QKeySequence(QKeySequence.StandardKey.Find)
    find = [s for s in host.findChildren(QShortcut) if s.key() == find_seq]
    assert find
    find[0].activated.emit()

    assert host.pages.currentIndex() == 1
    assert host.search_box.selectedText() == "notepad"


class _FakeQTimer:
    """Captures QTimer.singleShot(ms, fn) instead of arming a real timer."""

    calls: list[tuple] = []

    @staticmethod
    def singleShot(msec, fn) -> None:
        _FakeQTimer.calls.append((msec, fn))


@pytest.fixture()
def fake_single_shot(monkeypatch: pytest.MonkeyPatch):
    _FakeQTimer.calls = []
    monkeypatch.setattr("winpodx.gui._main_window_nav.QTimer", _FakeQTimer)
    return _FakeQTimer.calls


def test_first_launch_checks_defer_to_the_setup_prompt_when_uninitialized(
    monkeypatch: pytest.MonkeyPatch, fake_single_shot
) -> None:
    _ensure_qapp()
    monkeypatch.setattr("winpodx.utils.pending.has_pending", lambda: False)
    cfg = _make_cfg()
    cfg.pod.initialized = False
    host = NavHarness(cfg)

    host._maybe_run_first_launch_checks()

    assert host._shortcuts_installed is True
    assert [fn for _ms, fn in fake_single_shot] == [host._show_first_run_setup_prompt]


def test_first_launch_checks_show_quick_start_only_without_apps_or_marker(
    monkeypatch: pytest.MonkeyPatch, fake_single_shot
) -> None:
    _ensure_qapp()
    monkeypatch.setattr("winpodx.utils.pending.has_pending", lambda: False)
    cfg = _make_cfg()
    cfg.pod.initialized = True
    host = NavHarness(cfg)

    host._maybe_run_first_launch_checks()
    assert [fn for _ms, fn in fake_single_shot] == [host._show_quick_start]

    # Already-registered apps suppress the welcome dialog.
    fake_single_shot.clear()
    host2 = NavHarness(cfg, apps=[object()])
    host2._maybe_run_first_launch_checks()
    assert fake_single_shot == []


class _InlineThread:
    """Runs the worker body on the calling thread, so the test stays
    deterministic and no daemon thread outlives the QWidget harness."""

    def __init__(self, target=None, daemon=False, **kwargs) -> None:
        self._target = target

    def start(self) -> None:
        self._target()


class _InlineThreading:
    Thread = _InlineThread


@pytest.fixture()
def inline_worker_threads(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("winpodx.gui._main_window_nav.threading", _InlineThreading)


def test_first_launch_checks_resume_pending_setup_in_the_background(
    monkeypatch: pytest.MonkeyPatch, fake_single_shot, inline_worker_threads
) -> None:
    _ensure_qapp()
    monkeypatch.setattr("winpodx.utils.pending.has_pending", lambda: True)
    resumed: list[bool] = []

    def _resume(printer=None):
        if printer is not None:
            printer("[resume] step 1")
        resumed.append(True)

    monkeypatch.setattr("winpodx.utils.pending.resume", _resume)
    monkeypatch.setattr("winpodx.gui._main_window_nav.list_available_apps", lambda: ["a", "b"])

    cfg = _make_cfg()
    cfg.pod.initialized = True
    host = NavHarness(cfg)
    host._maybe_run_first_launch_checks()

    assert resumed == [True]
    assert host.apps == ["a", "b"]
    messages = [args[0] for args in host.log_signal.emissions]
    assert "[resume] step 1" in messages
    assert any("app list refreshed" in m for m in messages)


@pytest.fixture()
def click_dialog_button(monkeypatch: pytest.MonkeyPatch):
    """Make every QMessageBox.exec() return immediately, picking the Nth
    ``addButton`` call. Qt reorders ``buttons()`` by platform button role, so
    the insertion order is recorded here to keep the pick unambiguous."""
    chosen = {"index": 0}
    added: dict = {}
    real_add = QMessageBox.addButton

    def _pick(index: int) -> None:
        chosen["index"] = index

    def _add(self, *args):
        btn = real_add(self, *args)
        added.setdefault(id(self), []).append(btn)
        return btn

    monkeypatch.setattr(QMessageBox, "addButton", _add)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: added[id(self)][chosen["index"]],
    )
    return _pick


def test_first_run_setup_prompt_skip_button_does_nothing(
    click_dialog_button, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    host = NavHarness(_make_cfg())
    ran: list[str] = []
    monkeypatch.setattr(NavHarness, "_run_first_run_setup", lambda self, mode: ran.append(mode))

    click_dialog_button(2)  # Auto / Customize / Skip
    host._show_first_run_setup_prompt()
    assert ran == []


@pytest.mark.parametrize(("index", "mode"), [(0, "auto"), (1, "customize")])
def test_first_run_setup_prompt_routes_auto_and_customize(
    click_dialog_button, monkeypatch: pytest.MonkeyPatch, index: int, mode: str
) -> None:
    _ensure_qapp()
    host = NavHarness(_make_cfg())
    ran: list[str] = []
    monkeypatch.setattr(NavHarness, "_run_first_run_setup", lambda self, m: ran.append(m))

    click_dialog_button(index)
    host._show_first_run_setup_prompt()
    assert ran == [mode]


def test_run_first_run_setup_invokes_handle_setup_and_reloads_config(
    monkeypatch: pytest.MonkeyPatch, fake_single_shot, inline_worker_threads
) -> None:
    _ensure_qapp()
    seen: list = []
    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", lambda args: seen.append(args))
    reloaded = _make_cfg()
    reloaded.rdp.user = "reloaded-user"
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: reloaded))

    host = NavHarness(_make_cfg())
    host._run_first_run_setup("customize")

    assert len(seen) == 1
    assert seen[0].customize is True
    assert seen[0].non_interactive is False
    assert host.cfg is reloaded
    assert any("Setup complete" in args[0] for args in host.log_signal.emissions)
    # The ready toast is marshalled onto the GUI thread, never called inline.
    assert [ms for ms, _fn in fake_single_shot] == [0]


def test_run_first_run_setup_logs_a_failure_without_raising(
    monkeypatch: pytest.MonkeyPatch, fake_single_shot, inline_worker_threads
) -> None:
    _ensure_qapp()

    def _boom(args):
        raise RuntimeError("no podman")

    monkeypatch.setattr("winpodx.cli.setup_cmd.handle_setup", _boom)
    host = NavHarness(_make_cfg())
    host._run_first_run_setup("auto")

    assert any("Setup failed: no podman" in args[0] for args in host.log_signal.emissions)
    assert fake_single_shot == []


def _stub_first_run_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "winpodx.core.deps_quickcheck.collect_first_run_checks",
        lambda cfg: {
            "backend": "ok",
            "freerdp": "ok",
            "pod_state": "running",
            "rdp_port": "open",
            "apps_count": 0,
        },
    )
    monkeypatch.setattr("winpodx.utils.pending.has_pending", lambda: False)


def test_quick_start_writes_the_welcomed_marker_and_opens_settings(
    monkeypatch: pytest.MonkeyPatch, click_dialog_button
) -> None:
    _ensure_qapp()
    _stub_first_run_checks(monkeypatch)
    cfg = _make_cfg()
    host = NavHarness(cfg)
    marker = cfg.path().parent / ".welcomed"
    marker.parent.mkdir(parents=True, exist_ok=True)
    assert not marker.exists()

    click_dialog_button(0)  # Open Settings
    host._show_quick_start()

    assert marker.exists()
    assert host.pages.currentIndex() == 2


def test_quick_start_announces_pending_setup_steps(
    monkeypatch: pytest.MonkeyPatch, click_dialog_button
) -> None:
    _ensure_qapp()
    _stub_first_run_checks(monkeypatch)
    monkeypatch.setattr("winpodx.utils.pending.has_pending", lambda: True)
    host = NavHarness(_make_cfg())
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "setText", lambda self, text: shown.append(text))

    click_dialog_button(2)  # Close
    host._show_quick_start()

    assert shown
    assert "Pending setup steps detected" in shown[0]
    assert host.pages.currentIndex() == 0
    assert host.refreshed_apps == 0


def test_quick_start_refresh_button_triggers_an_app_rescan(
    monkeypatch: pytest.MonkeyPatch, click_dialog_button
) -> None:
    _ensure_qapp()
    _stub_first_run_checks(monkeypatch)
    host = NavHarness(_make_cfg())

    click_dialog_button(1)  # Refresh Apps
    host._show_quick_start()
    assert host.refreshed_apps == 1
    assert host.pages.currentIndex() == 0


# ----- SettingsPageMixin: pure-logic helpers -----------------------------


class BudgetHarness(SettingsPageMixin):
    """Only what _update_budget_warning touches."""

    def __init__(self, ram: str, sessions: str, *, with_summary: bool = True) -> None:
        self.input_ram = QLineEdit(ram)
        self.input_max_sessions = QLineEdit(sessions)
        self.budget_warning_label = QLabel("")
        self.budget_warning_label.setVisible(False)
        if with_summary:
            self.budget_summary_label = QLabel("")


def test_budget_summary_reports_the_session_math_and_stays_quiet_when_it_fits() -> None:
    _ensure_qapp()
    host = BudgetHarness(ram="16", sessions="4")
    host._update_budget_warning()

    summary = host.budget_summary_label.text()
    assert "4" in summary and "16" in summary
    assert host.budget_warning_label.isVisible() is False


def test_budget_warning_fires_when_max_sessions_oversubscribes_ram() -> None:
    _ensure_qapp()
    host = BudgetHarness(ram="2", sessions="50")
    host._update_budget_warning()

    assert host.budget_warning_label.isVisible() is True
    assert "WARNING" in host.budget_warning_label.text()


def test_budget_inputs_are_clamped_into_the_supported_range() -> None:
    _ensure_qapp()
    # 999 sessions clamps to 50, 0 GB RAM clamps to 1 before the estimate.
    host = BudgetHarness(ram="0", sessions="999")
    host._update_budget_warning()

    summary = host.budget_summary_label.text()
    assert "50" in summary
    assert "999" not in summary
    assert "1 GB" in summary


def test_budget_warning_hides_itself_on_non_numeric_input() -> None:
    _ensure_qapp()
    host = BudgetHarness(ram="2", sessions="50")
    host._update_budget_warning()
    assert host.budget_warning_label.isVisible() is True

    host.input_ram.setText("abc")
    host._update_budget_warning()
    assert host.budget_warning_label.isVisible() is False
    assert host.budget_summary_label.text() == ""


def test_budget_warning_tolerates_a_host_without_the_summary_label() -> None:
    _ensure_qapp()
    host = BudgetHarness(ram="2", sessions="50", with_summary=False)
    host._update_budget_warning()
    assert host.budget_warning_label.isVisible() is True


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("▣  RDP Connection", ("rdp", "RDP Connection")),
        ("▨  Hardware", ("hardware", "Hardware")),
        ("🌐  Localization", ("globe", "Localization")),
        ("Windows Update", (None, "Windows Update")),
    ],
)
def test_settings_title_glyph_maps_to_an_in_house_icon(title: str, expected: tuple) -> None:
    assert SettingsPageMixin._split_settings_title_icon(title) == expected


def _locale_options() -> list:
    return [("Korean", "Korean"), ("German", "German")]


def test_locale_combo_prepends_auto_storing_the_empty_sentinel() -> None:
    _ensure_qapp()
    host = SettingsPageMixin()
    combo = host._build_locale_combo(
        cfg_value="", options=_locale_options(), empty_label="Auto (English)"
    )

    assert combo.count() == 3
    assert combo.itemData(0) == ""
    assert combo.currentIndex() == 0
    assert combo.currentData() == ""
    assert combo.itemText(0) == "Auto (English)"


def test_locale_combo_selects_the_curated_row_matching_config() -> None:
    _ensure_qapp()
    host = SettingsPageMixin()
    combo = host._build_locale_combo(
        cfg_value="German", options=_locale_options(), empty_label="Auto"
    )

    assert combo.currentData() == "German"
    assert combo.currentIndex() == 2
    assert combo.count() == 3


def test_locale_combo_round_trips_a_hand_edited_toml_value_as_custom() -> None:
    _ensure_qapp()
    host = SettingsPageMixin()
    combo = host._build_locale_combo(
        cfg_value="Klingon", options=_locale_options(), empty_label="Auto"
    )

    assert combo.count() == 4
    assert combo.currentIndex() == 3
    assert combo.currentData() == "Klingon"
    assert combo.currentText() == "Klingon (custom)"


# ----- SettingsPageMixin: full page build --------------------------------


class SettingsHarness(SettingsPageMixin, QWidget):
    """QWidget host — the save handler parents QMessageBox to self."""

    def __init__(self, cfg: Config) -> None:
        QWidget.__init__(self)
        self.cfg = cfg
        self.info_label = QLabel("", self)
        self.pages = QStackedWidget(self)
        self.pages.addWidget(QWidget())
        self.update_status_refreshes = 0
        self.enable_updates_calls = 0
        self.disable_updates_calls = 0
        self.bringup_calls: list[dict] = []

    def _refresh_update_status(self) -> None:
        self.update_status_refreshes += 1

    def _on_enable_updates(self) -> None:
        self.enable_updates_calls += 1

    def _on_disable_updates(self) -> None:
        self.disable_updates_calls += 1

    def _run_full_bring_up(self, **kwargs) -> None:
        self.bringup_calls.append(kwargs)


@pytest.fixture()
def hermetic_settings(monkeypatch: pytest.MonkeyPatch):
    """Keep the Settings page off the host: no timedatectl, no tuning probe,
    no autostart .desktop writes, no config file reads/writes."""
    monkeypatch.setattr("winpodx.utils.locale.detect_timezone", lambda: "Europe/Berlin")

    def _no_tuning(*, vm_cpu_cores, vm_ram_gb):
        raise RuntimeError("probe disabled in tests")

    monkeypatch.setattr("winpodx.utils.specs.detect_tuning_capability", _no_tuning)

    autostart_state = {"enabled": False, "writes": []}
    monkeypatch.setattr(
        "winpodx.desktop.autostart.is_autostart_enabled",
        lambda: autostart_state["enabled"],
    )
    monkeypatch.setattr(
        "winpodx.desktop.autostart.set_autostart",
        lambda on: autostart_state["writes"].append(bool(on)),
    )
    monkeypatch.setattr("winpodx.reverse_open.lifecycle.is_listener_running", lambda: None)

    # A modal that nobody stubbed would block the suite forever; make it a
    # loud failure instead. Tests that expect a prompt re-patch these.
    def _unexpected(*args, **kwargs):
        raise AssertionError("unexpected modal QMessageBox in a headless test")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_unexpected))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(_unexpected))

    saved: list[Config] = []
    stored = _make_cfg()
    stored.desktop.mime_associations = True
    stored.desktop.full_app_scan = False
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: stored))
    monkeypatch.setattr(Config, "save", lambda self: saved.append(self))
    return {"autostart": autostart_state, "saved": saved, "stored": stored}


def _build_page(cfg: Config) -> SettingsHarness:
    host = SettingsHarness(cfg)
    host._page = host._build_settings_page()
    return host


def test_settings_page_seeds_every_rdp_field_from_config(hermetic_settings) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.rdp.user = "WPX-User"
    cfg.rdp.ip = "10.0.0.5"
    cfg.rdp.port = 3391
    cfg.rdp.scale = 140
    cfg.rdp.dpi = 150
    cfg.rdp.password_max_age = 14
    cfg.rdp.extra_flags = "/gfx:RFX"
    host = _build_page(cfg)

    assert host.input_user.text() == "WPX-User"
    assert host.input_ip.text() == "10.0.0.5"
    assert host.input_port.text() == "3391"
    assert host.input_scale.currentData() == 140
    assert host.input_dpi.currentData() == 150
    assert host.input_pw_max_age.currentData() == 14
    assert host.input_extra_flags.text() == "/gfx:RFX"


def test_settings_page_seeds_every_hardware_field_from_config(hermetic_settings) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.pod.backend = "docker"
    cfg.pod.cpu_cores = 6
    cfg.pod.ram_gb = 12
    cfg.pod.idle_timeout = 900
    cfg.pod.idle_action = "stop"
    cfg.pod.max_sessions = 7
    host = _build_page(cfg)

    assert host.input_backend.currentText() == "docker"
    assert host.input_cpu.text() == "6"
    assert host.input_ram.text() == "12"
    assert host.input_idle.text() == "900"
    assert host.input_idle_action.currentData() == "stop"
    assert host.input_max_sessions.text() == "7"
    # The budget summary renders at build time from those two fields.
    assert "7" in host.budget_summary_label.text()


def test_settings_page_exposes_out_of_list_toml_values_as_custom_rows(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    monkeypatch.setattr(
        "winpodx.utils.specs.detect_tuning_capability",
        lambda *, vm_cpu_cores, vm_ram_gb: ("cap", vm_cpu_cores, vm_ram_gb),
    )
    monkeypatch.setattr(
        "winpodx.utils.specs.recommend_tuning_profile",
        lambda cap, *, user_pref: ("profile", user_pref),
    )
    monkeypatch.setattr(
        "winpodx.utils.specs.format_tuning_summary",
        lambda cap, profile: f"summary for {cap[1]}c/{cap[2]}g {profile[1]}",
    )

    cfg = _make_cfg()
    cfg.rdp.dpi = 133
    cfg.rdp.password_max_age = 45
    cfg.pod.win_version = "win12-preview"
    host = _build_page(cfg)

    assert host.input_dpi.currentData() == 133
    assert host.input_dpi.currentText() == "133%"
    assert host.input_pw_max_age.currentData() == 45
    assert host.input_pw_max_age.currentText() == "45 days"
    assert host.input_win_version.currentData() == "win12-preview"
    assert host.input_win_version.currentText() == "win12-preview (custom)"
    assert host.tuning_summary_label.text() == "summary for 4c/8g auto"


def test_settings_card_without_a_glyph_prefix_renders_a_bare_header() -> None:
    _ensure_qapp()
    host = SettingsPageMixin()
    field = QLineEdit("value")
    card = host._settings_card("Plain Title", "subtitle", [("Label", field)])

    headers = [w.text() for w in card.findChildren(QLabel)]
    assert "Plain Title" in headers
    assert "subtitle" in headers
    assert "Label" in headers
    assert field.parentWidget() is card


def test_settings_page_offers_off_balanced_max_for_the_disguise_level(hermetic_settings) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.pod.disguise_level = "max"
    host = _build_page(cfg)

    values = [
        host.input_disguise_level.itemData(i) for i in range(host.input_disguise_level.count())
    ]
    assert values == ["off", "balanced", "max"]
    assert host.input_disguise_level.currentData() == "max"


def test_settings_page_marks_an_unknown_tuning_profile_as_unknown(hermetic_settings) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.pod.tuning_profile = "performance"
    host = _build_page(cfg)
    assert host.input_tuning_profile.currentData() == "performance"

    cfg2 = _make_cfg()
    cfg2.pod.tuning_profile = "warp-speed"
    host2 = _build_page(cfg2)
    assert host2.input_tuning_profile.currentData() == "warp-speed"
    assert host2.input_tuning_profile.currentText().endswith("(unknown)")
    # detect_tuning_capability blew up — the card still renders a fallback.
    assert host2.tuning_summary_label.text()


def test_settings_page_falls_back_to_auto_for_an_unknown_ui_language(hermetic_settings) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.ui.language = "ko"
    host = _build_page(cfg)
    assert host.input_ui_language.currentData() == "ko"

    cfg2 = _make_cfg()
    cfg2.ui.language = "tlh"
    host2 = _build_page(cfg2)
    assert host2.input_ui_language.currentData() == "auto"


def test_ui_language_selector_persists_the_pick_immediately(hermetic_settings) -> None:
    _ensure_qapp()
    applied: list[str] = []
    host = _build_page(_make_cfg())

    import winpodx.core.i18n as i18n_mod

    original = i18n_mod.set_language
    i18n_mod.set_language = lambda code: applied.append(code)
    try:
        idx = host.input_ui_language.findData("ja")
        assert idx >= 0
        host.input_ui_language.setCurrentIndex(idx)
    finally:
        i18n_mod.set_language = original

    assert applied == ["ja"]
    assert host.cfg.ui.language == "ja"
    assert hermetic_settings["stored"].ui.language == "ja"
    assert hermetic_settings["stored"] in hermetic_settings["saved"]


def test_autostart_checkbox_reflects_and_writes_the_desktop_entry(hermetic_settings) -> None:
    _ensure_qapp()
    host = _build_page(_make_cfg())

    assert host.checkbox_autostart_tray.isChecked() is False
    host.checkbox_autostart_tray.setChecked(True)
    host.checkbox_autostart_tray.setChecked(False)
    assert hermetic_settings["autostart"]["writes"] == [True, False]


def test_file_association_and_full_scan_toggles_persist_to_config(hermetic_settings) -> None:
    _ensure_qapp()
    stored = hermetic_settings["stored"]
    host = _build_page(_make_cfg())

    assert host.checkbox_mime_assoc.isChecked() is True
    assert host.checkbox_full_app_scan.isChecked() is False

    host.checkbox_mime_assoc.setChecked(False)
    assert stored.desktop.mime_associations is False

    host.checkbox_full_app_scan.setChecked(True)
    assert stored.desktop.full_app_scan is True
    assert len(hermetic_settings["saved"]) == 2


def test_immediate_toggles_survive_an_unreadable_config_and_a_failed_write(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()

    def _unreadable(cls):
        raise OSError("config is gone")

    def _no_autostart(on):
        raise OSError("read-only ~/.config")

    monkeypatch.setattr(Config, "load", classmethod(_unreadable))
    monkeypatch.setattr("winpodx.desktop.autostart.set_autostart", _no_autostart)
    host = _build_page(_make_cfg())

    # Unreadable config falls back to the shipped defaults, not a crash.
    assert host.checkbox_mime_assoc.isChecked() is True
    assert host.checkbox_full_app_scan.isChecked() is False

    host.checkbox_autostart_tray.setChecked(True)
    host.checkbox_mime_assoc.setChecked(False)
    host.checkbox_full_app_scan.setChecked(True)
    host.input_ui_language.setCurrentIndex(host.input_ui_language.findData("de"))

    assert host.checkbox_autostart_tray.isChecked() is True
    assert host.checkbox_full_app_scan.isChecked() is True
    # The failed persist must not have mutated the in-memory language either.
    assert host.cfg.ui.language == "auto"


def test_ui_language_selector_ignores_a_cleared_selection(hermetic_settings) -> None:
    _ensure_qapp()
    host = _build_page(_make_cfg())
    hermetic_settings["saved"].clear()

    host.input_ui_language.setCurrentIndex(-1)

    assert host.cfg.ui.language == "auto"
    assert hermetic_settings["saved"] == []


def test_settings_page_probes_the_windows_update_state_once_on_build(hermetic_settings) -> None:
    _ensure_qapp()
    host = _build_page(_make_cfg())

    assert host.update_status_refreshes == 1
    assert host._btn_retry_updates.isVisible() is False
    host._btn_enable_updates.click()
    host._btn_disable_updates.click()
    host._btn_retry_updates.click()
    assert (host.enable_updates_calls, host.disable_updates_calls) == (1, 1)
    assert host.update_status_refreshes == 2


def test_settings_page_keeps_rendering_when_the_reverse_open_panel_fails(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()

    def _boom(cfg, parent=None):
        raise RuntimeError("panel exploded")

    monkeypatch.setattr("winpodx.gui.reverse_open_panel.build_panel", _boom)
    host = _build_page(_make_cfg())
    # The page still built past the failed panel.
    assert host.checkbox_autostart_tray is not None
    assert host.budget_warning_label is not None


def test_reflow_stacks_the_top_cards_when_the_page_is_too_narrow(hermetic_settings) -> None:
    _ensure_qapp()
    from PySide6.QtWidgets import QBoxLayout

    host = _build_page(_make_cfg())

    host.pages.setFixedWidth(200)
    host._reflow_settings()
    assert host._settings_cols.direction() == QBoxLayout.Direction.TopToBottom

    host.pages.setFixedWidth(4000)
    host._reflow_settings()
    assert host._settings_cols.direction() == QBoxLayout.Direction.LeftToRight


def test_reflow_is_a_no_op_before_the_page_exists() -> None:
    _ensure_qapp()
    host = SettingsPageMixin()
    host._reflow_settings()  # no _settings_cols / pages yet — must not raise


# ----- SettingsPageMixin: save round-trip --------------------------------


def _no_recreate(monkeypatch: pytest.MonkeyPatch, old: Config) -> None:
    """Config.load() returns ``old`` so dirty-checks compare against it."""
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: old))


def test_save_settings_rejects_non_numeric_fields_without_touching_config(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    host = _build_page(cfg)
    warned: list[tuple] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a) or 0))

    host.input_cpu.setText("four")
    host.input_user.setText("changed")
    hermetic_settings["saved"].clear()
    host._save_settings()

    assert len(warned) == 1
    assert cfg.rdp.user != "changed"
    assert hermetic_settings["saved"] == []


def test_save_settings_writes_every_form_field_into_config(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    host = _build_page(cfg)
    old = _make_cfg()
    old.rdp.user = "WPX-User"
    _no_recreate(monkeypatch, old)

    host.input_user.setText("WPX-User")
    host.input_ip.setText("127.0.0.2")
    host.input_extra_flags.setText("  /gfx:RFX  ")
    host.input_dpi.setCurrentIndex(host.input_dpi.findData(200))
    host.input_pw_max_age.setCurrentIndex(host.input_pw_max_age.findData(30))
    host.input_scale.setCurrentIndex(host.input_scale.findData(180))
    host.input_idle.setText("1200")
    host.input_idle_action.setCurrentIndex(host.input_idle_action.findData("stop"))
    host.input_max_sessions.setText("12")
    hermetic_settings["saved"].clear()

    host._save_settings()

    assert cfg.rdp.user == "WPX-User"
    assert cfg.rdp.ip == "127.0.0.2"
    assert cfg.rdp.extra_flags == "/gfx:RFX"
    assert cfg.rdp.dpi == 200
    assert cfg.rdp.password_max_age == 30
    assert cfg.rdp.scale == 180
    assert cfg.pod.idle_timeout == 1200
    assert cfg.pod.idle_action == "stop"
    assert cfg.pod.max_sessions == 12
    assert hermetic_settings["saved"] == [cfg]
    assert host.info_label.text()
    assert host.bringup_calls == []


def test_save_settings_clamps_max_sessions_before_persisting(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    host = _build_page(cfg)
    _no_recreate(monkeypatch, cfg)

    host.input_max_sessions.setText("999")
    host._save_settings()
    assert cfg.pod.max_sessions == 50

    host.input_max_sessions.setText("0")
    host._save_settings()
    assert cfg.pod.max_sessions == 1


def test_save_settings_treats_empty_numeric_fields_as_their_defaults(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.rdp.port = 3390
    cfg.pod.ram_gb = 4
    host = _build_page(cfg)
    old = _make_cfg()
    old.rdp.port = 3390
    old.pod.ram_gb = 4
    _no_recreate(monkeypatch, old)

    host.input_port.setText("")
    host.input_cpu.setText("")
    host.input_ram.setText("")
    host.input_idle.setText("")
    host._save_settings()

    assert cfg.rdp.port == 3390
    assert cfg.pod.cpu_cores == 4
    assert cfg.pod.ram_gb == 4
    assert cfg.pod.idle_timeout == 0


def test_save_settings_prompts_for_a_recreate_when_cpu_changes(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    host = _build_page(cfg)
    old = _make_cfg()
    old.pod.cpu_cores = 4
    _no_recreate(monkeypatch, old)

    asked: list[tuple] = []

    def _question(parent, title, text, *a, **k):
        asked.append((title, text))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))

    host.input_cpu.setText("8")
    host._save_settings()

    assert len(asked) == 1
    assert "wipe" not in asked[0][1].lower()
    assert host.bringup_calls == [
        {"recreate": True, "wipe_storage": False, "build_disguise": False}
    ]
    assert cfg.pod.cpu_cores == 8


def test_save_settings_declined_recreate_still_persists_the_new_values(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    host = _build_page(cfg)
    old = _make_cfg()
    _no_recreate(monkeypatch, old)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )

    host.input_ram.setText("16")
    host._save_settings()

    assert cfg.pod.ram_gb == 16
    assert host.bringup_calls == []
    assert host.info_label.text()


def test_save_settings_asks_for_a_wipe_when_the_install_locale_changes(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    host = _build_page(cfg)
    old = _make_cfg()
    old.pod.language = ""
    _no_recreate(monkeypatch, old)

    prompts: list[str] = []
    defaults: list = []

    def _question(parent, title, text, buttons=None, default=None):
        prompts.append(text)
        defaults.append(default)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))

    host.input_language.setCurrentIndex(host.input_language.findData("Korean"))
    host._save_settings()

    assert len(prompts) == 1
    assert "reinstall" in prompts[0].lower()
    # Destructive prompt defaults to No so a stray Enter can't wipe Windows.
    assert defaults[0] == QMessageBox.StandardButton.No
    assert host.bringup_calls == [{"recreate": True, "wipe_storage": True, "build_disguise": False}]
    assert cfg.pod.language == "Korean"


def test_save_settings_reverts_a_declined_device_changing_disguise_switch(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.pod.disguise_level = "balanced"
    host = _build_page(cfg)
    old = _make_cfg()
    old.pod.disguise_level = "balanced"
    _no_recreate(monkeypatch, old)

    prompts: list[str] = []

    def _question(parent, title, text, buttons=None, default=None):
        prompts.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    monkeypatch.setattr("winpodx.cli.disguise.disguise_image_present", lambda cfg: True)

    host.input_disguise_level.setCurrentIndex(host.input_disguise_level.findData("max"))
    host._save_settings()

    assert "WIPE WARNING" in prompts[0]
    assert host.bringup_calls == []
    # Declining must not leave "max" persisted — the guest couldn't boot.
    assert cfg.pod.disguise_level == "balanced"
    assert host.input_disguise_level.currentData() == "balanced"


def test_save_settings_builds_the_patched_qemu_image_for_a_fresh_hardened_switch(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    cfg.pod.disguise_level = "off"
    host = _build_page(cfg)
    old = _make_cfg()
    old.pod.disguise_level = "off"
    _no_recreate(monkeypatch, old)

    prompts: list[str] = []

    def _question(parent, title, text, buttons=None, default=None):
        prompts.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))
    monkeypatch.setattr("winpodx.cli.disguise.disguise_image_present", lambda cfg: False)

    host.input_disguise_level.setCurrentIndex(host.input_disguise_level.findData("max"))
    host._save_settings()

    assert "patched-QEMU" in prompts[0]
    assert host.bringup_calls == [{"recreate": True, "wipe_storage": True, "build_disguise": True}]
    assert cfg.pod.disguise_level == "max"
    assert cfg.pod.disguise_image == ""


def test_save_settings_repoints_disguise_image_when_it_is_already_built(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    from winpodx.cli.disguise import _DISGUISE_TAG

    cfg = _make_cfg()
    cfg.pod.disguise_level = "max"
    cfg.pod.disguise_image = ""
    host = _build_page(cfg)
    old = _make_cfg()
    old.pod.disguise_level = "max"
    _no_recreate(monkeypatch, old)
    monkeypatch.setattr("winpodx.cli.disguise.disguise_image_present", lambda cfg: True)

    host._save_settings()

    assert cfg.pod.disguise_image == _DISGUISE_TAG
    assert host.bringup_calls == []


def test_save_settings_skips_the_recreate_prompt_on_the_manual_backend(
    hermetic_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ensure_qapp()
    cfg = _make_cfg()
    host = _build_page(cfg)
    old = _make_cfg()
    _no_recreate(monkeypatch, old)

    def _never(*a, **k):
        raise AssertionError("manual backend must not prompt for a recreate")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_never))

    host.input_backend.setCurrentText("manual")
    host.input_cpu.setText("12")
    host._save_settings()

    assert cfg.pod.backend == "manual"
    assert cfg.pod.cpu_cores == 12
    assert host.bringup_calls == []
