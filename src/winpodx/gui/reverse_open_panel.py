# SPDX-License-Identifier: MIT
"""Settings-page panel for the reverse-open feature (#48).

Split out of :mod:`main_window` so the daemon-status logic + the
allow/deny list mutations are testable without instantiating the
full main window. The :class:`ReverseOpenPanel` widget itself still
requires Qt — the unit tests cover the pure-Python helpers (status
dict builder, slug-validation, list mutation operations).

Phase 2d intentionally keeps the panel thin: it surfaces the
existing CLI affordances (``enable`` / ``disable`` / ``refresh`` /
``start-listener`` / ``stop-listener`` / ``add`` / ``remove``) as
buttons + list widgets. The save flow merges into the existing
Settings page's "Save Settings" button so the user has one
top-level commit action.

The panel module imports Qt lazily — importing this module without
PySide6 installed must NOT crash, since the host_open CLI tests
import the same module indirectly via the Settings-page wiring.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from winpodx.core.i18n import tr
from winpodx.reverse_open.config import _SLUG_RE
from winpodx.reverse_open.lifecycle import is_listener_running

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from winpodx.core.config import Config

log = logging.getLogger(__name__)


# ----- pure-python helpers (Qt-free) ------------------------------------------


@dataclass
class PanelStatus:
    """Snapshot of what the panel needs to render at a given moment.

    Built fresh whenever the user clicks "Refresh status" or saves
    the page. Bound to a dict for the optional ``--json`` debug
    output (``winpodx host-open daemon-status`` already covers this
    on the CLI side; the dict is a convenience for GUI logging).
    """

    enabled: bool
    daemon_running: bool
    daemon_pid: int | None
    cached_app_count: int | None
    cached_generated_at: str | None
    allowlist: list[str]
    denylist: list[str]


def build_panel_status(cfg: Config, cached_manifest: dict[str, Any] | None) -> PanelStatus:
    """Assemble a :class:`PanelStatus` from config + cached manifest.

    ``cached_manifest`` is the dict read off ``apps.json`` (the
    structure the CLI ``host-open status --json`` would emit under
    ``cache.app_count`` / ``cache.generated_at``); pass ``None`` if
    the manifest doesn't exist yet.
    """
    pid = is_listener_running()
    cached_count: int | None = None
    cached_at: str | None = None
    if cached_manifest:
        apps = cached_manifest.get("apps")
        if isinstance(apps, list):
            cached_count = len(apps)
        gen = cached_manifest.get("generated_at")
        if isinstance(gen, str) and gen:
            cached_at = gen
    return PanelStatus(
        enabled=bool(cfg.reverse_open.enabled),
        daemon_running=pid is not None,
        daemon_pid=pid,
        cached_app_count=cached_count,
        cached_generated_at=cached_at,
        allowlist=list(cfg.reverse_open.allowlist),
        denylist=list(cfg.reverse_open.denylist),
    )


def validate_slug(text: str) -> tuple[bool, str]:
    """Return ``(ok, normalised_or_error)`` for a user-typed slug.

    The GUI's input dialogs accept any string; we apply the same
    lower-kebab grammar the CLI's ``add`` / ``remove`` subcommands
    use, so a slug round-trips between GUI and CLI without surprises.
    """
    candidate = text.strip().lower()
    if not candidate:
        return False, "slug is empty"
    if not _SLUG_RE.fullmatch(candidate):
        return False, f"slug {candidate!r} must match /^[a-z0-9-]+$/"
    return True, candidate


def add_slug(
    current: list[str],
    other: list[str],
    slug: str,
) -> tuple[bool, list[str], list[str], str]:
    """Add ``slug`` to ``current``, removing it from ``other`` if present.

    Mirrors the CLI's "add to one list wipes presence from the other"
    rule. Returns ``(changed, new_current, new_other, message)``.
    ``changed=False`` means the slug was already in ``current``
    (caller surfaces that as a soft warning rather than an error).
    """
    if slug in current:
        return False, list(current), list(other), f"already present: {slug}"
    new_current = sorted([*current, slug])
    new_other = [s for s in other if s != slug]
    return True, new_current, new_other, f"added {slug}"


def remove_slug(current: list[str], slug: str) -> tuple[bool, list[str], str]:
    """Remove ``slug`` from ``current``. Mirrors the CLI ``remove`` subcommand."""
    if slug not in current:
        return False, list(current), f"not present: {slug}"
    return True, [s for s in current if s != slug], f"removed {slug}"


def format_status_line(status: PanelStatus) -> str:
    """Single-line human-readable summary used in the panel header."""
    if status.daemon_running:
        daemon = f"Daemon running (pid {status.daemon_pid})"
    else:
        daemon = "Daemon stopped"
    cache_bit = (
        f"{status.cached_app_count} apps cached"
        if status.cached_app_count is not None
        else "no manifest yet"
    )
    state = "enabled" if status.enabled else "disabled"
    return f"{state} — {daemon} — {cache_bit}"


# ----- Qt widget (lazy) -------------------------------------------------------


def build_panel(cfg: Config, parent: QWidget | None = None) -> QWidget:
    """Build the reverse-open Settings card. Requires PySide6.

    The returned widget is a self-contained card the caller drops
    into the Settings page's column layout (next to RDP / Pod
    cards). Any persistence of toggles / allow/deny edits happens
    against the in-memory ``cfg`` — the parent Settings page is
    responsible for calling ``cfg.save()`` when the user clicks
    "Save Settings".
    """
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtWidgets import (
        QFrame,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    from winpodx.cli.host_open import (
        _apps_json,
        _cmd_refresh,
        _cmd_start_listener,
        _cmd_stop_listener,
    )
    from winpodx.gui import theme
    from winpodx.gui._main_window_secondary_style import make_ghost_button
    from winpodx.gui._toggle_switch import ToggleSwitch
    from winpodx.gui._widget_helpers import make_settings_card, mark_fluid_wrap
    from winpodx.gui.icons import load_icon
    from winpodx.gui.theme import (
        BTN_GHOST,
        BTN_PRIMARY,
        BTN_SECONDARY,
        CONTROL_HEIGHT_W11,
        FONT_SUBHEAD,
        SPACE_S,
        SPACE_XS,
        C,
    )

    card = QFrame(parent)
    card.setObjectName("settingsSection")
    card.setFrameShape(QFrame.Shape.NoFrame)
    card.setStyleSheet(
        theme.SETTINGS_SECTION
        + theme.CHECKBOX
        + theme.LIST_WIDGET
        + f"QLabel {{ color: {C.TEXT}; font-size: 13px; background: transparent; }}"
    )
    layout = QVBoxLayout(card)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE_S)

    title = QLabel(tr("▦  Reverse File Associations"))
    title.setText(title.text().removeprefix("▦  "))
    title.setObjectName("settingsGroupHeading")
    title.setStyleSheet(
        f"color: {C.TEXT}; font-size: {FONT_SUBHEAD}px; font-weight: 600; background: transparent;"
    )
    title_row = QHBoxLayout()
    title_row.setContentsMargins(0, 0, 0, 0)
    title_row.setSpacing(SPACE_S)
    title_icon = QLabel()
    title_icon.setFixedSize(18, 18)
    title_icon.setPixmap(load_icon("reverse-associations", C.BLUE, 18).pixmap(18, 18))
    title_icon.setStyleSheet("background: transparent;")
    title_row.addWidget(title_icon)
    title_row.addWidget(title)
    title_row.addStretch()
    layout.addLayout(title_row)

    sub = QLabel(tr("Linux apps appear in the Windows guest's right-click ‘Open with…’ menu."))
    sub.setObjectName("settingsGroupCaption")
    sub.setWordWrap(True)
    mark_fluid_wrap(sub)
    sub.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 11px;")
    layout.addWidget(sub)

    enable_box = ToggleSwitch()
    enable_box.setChecked(bool(cfg.reverse_open.enabled))
    layout.addWidget(
        make_settings_card(
            "reverse-associations",
            tr("Enable reverse-open"),
            action=enable_box,
            object_name="reverseOpenEnableRow",
        )
    )

    status_label = QLabel("")
    status_label.setWordWrap(True)
    mark_fluid_wrap(status_label)
    status_label.setStyleSheet(
        f"background: {C.MANTLE}; color: {C.SUBTEXT1}; border-radius: 8px; padding: 8px 10px;"
    )
    layout.addWidget(status_label)

    # --- action buttons ------------------------------------------------------
    buttons_row = QHBoxLayout()
    btn_refresh = QPushButton(tr("Refresh && sync"))
    btn_start = QPushButton(tr("Start daemon"))
    btn_stop = QPushButton(tr("Stop daemon"))
    btn_status = QPushButton(tr("Refresh status"))
    btn_refresh.setStyleSheet(BTN_PRIMARY)
    for b in (btn_start, btn_stop, btn_status):
        b.setStyleSheet(BTN_GHOST)
    for b in (btn_refresh, btn_start, btn_stop, btn_status):
        b.setMinimumHeight(CONTROL_HEIGHT_W11)
        buttons_row.addWidget(b)
    buttons_row.addStretch()
    layout.addLayout(buttons_row)

    # --- allow / deny lists --------------------------------------------------
    lists_hint = QLabel(tr("Allowlist = only these apps are offered; Denylist = these are hidden."))
    lists_hint.setWordWrap(True)
    mark_fluid_wrap(lists_hint)
    lists_hint.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 11px;")
    layout.addWidget(lists_hint)

    lists_grid = QVBoxLayout()
    lists_grid.setSpacing(SPACE_S)
    allow_label = QLabel(tr("Allowlist (empty = all discovered)"))
    deny_label = QLabel(tr("Denylist (apps to hide)"))
    allow_label.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 12px; font-weight: 500;")
    deny_label.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 12px; font-weight: 500;")
    allow_host = QWidget()
    allow_box = QVBoxLayout(allow_host)
    allow_box.setContentsMargins(0, 0, 0, 0)
    allow_box.setSpacing(SPACE_XS)
    deny_host = QWidget()
    deny_box = QVBoxLayout(deny_host)
    deny_box.setContentsMargins(0, 0, 0, 0)
    deny_box.setSpacing(SPACE_XS)
    lists_grid.addWidget(allow_label)
    lists_grid.addWidget(allow_host)

    allow_btns = QHBoxLayout()
    allow_btns.setSpacing(SPACE_S)
    btn_allow_add = QPushButton(tr("+ Add"))
    btn_allow_add.setText(btn_allow_add.text().removeprefix("+ "))
    btn_allow_add.setIcon(load_icon("plus", C.TEXT, 16))
    btn_allow_add.setIconSize(QSize(16, 16))
    btn_allow_add.setStyleSheet(BTN_SECONDARY)
    btn_allow_add.setMinimumHeight(CONTROL_HEIGHT_W11)
    allow_btns.addWidget(btn_allow_add)
    allow_btns.addStretch()

    deny_btns = QHBoxLayout()
    deny_btns.setSpacing(SPACE_S)
    btn_deny_add = QPushButton(tr("+ Add"))
    btn_deny_add.setText(btn_deny_add.text().removeprefix("+ "))
    btn_deny_add.setIcon(load_icon("plus", C.TEXT, 16))
    btn_deny_add.setIconSize(QSize(16, 16))
    btn_deny_add.setStyleSheet(BTN_SECONDARY)
    btn_deny_add.setMinimumHeight(CONTROL_HEIGHT_W11)
    deny_btns.addWidget(btn_deny_add)
    deny_btns.addStretch()

    lists_grid.addLayout(allow_btns)
    lists_grid.addWidget(deny_label)
    lists_grid.addWidget(deny_host)
    lists_grid.addLayout(deny_btns)
    layout.addLayout(lists_grid)

    # --- behaviour wiring ----------------------------------------------------

    def _read_cached_manifest() -> dict[str, Any] | None:
        try:
            import json

            return json.loads(_apps_json().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _refresh_status_label() -> None:
        status = build_panel_status(cfg, _read_cached_manifest())
        status_label.setText(format_status_line(status))

    def _on_enable(state: int) -> None:
        enabled = state == Qt.CheckState.Checked.value or bool(state)
        cfg.reverse_open.enabled = enabled
        cfg.save()
        # Make the checkbox live (#425): enabling starts the listener now,
        # disabling stops it -- so users don't have to also click "Start
        # daemon". Best-effort + QUIET: starting needs the guest up, so when
        # it's down we just persist the flag (the listener comes up on the
        # next pod bringup) rather than popping the _run_cli error modal for
        # that expected case.
        from types import SimpleNamespace

        handler = _cmd_start_listener if enabled else _cmd_stop_listener
        try:
            handler(SimpleNamespace(json=False))
        except Exception:  # noqa: BLE001 — status label reflects the outcome
            log.debug(
                "reverse-open %s on toggle failed (guest may be down)",
                "start" if enabled else "stop",
                exc_info=True,
            )
        _refresh_status_label()

    def _slugs_of(box: QVBoxLayout) -> list[str]:
        out: list[str] = []
        for i in range(box.count()):
            widget = box.itemAt(i).widget()
            if widget is not None:
                out.append(widget.title_label.text())
        return out

    def _sync_lists_to_cfg() -> None:
        cfg.reverse_open.allowlist = _slugs_of(allow_box)
        cfg.reverse_open.denylist = _slugs_of(deny_box)

    def _clear_box(box: QVBoxLayout) -> None:
        while box.count():
            item = box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _slug_row(slug: str, box: QVBoxLayout) -> QFrame:
        btn = make_ghost_button(icon="close")
        btn.setAccessibleName(tr("− Remove"))
        btn.clicked.connect(lambda _=False, s=slug, b=box: _remove_from(b, s))
        return make_settings_card(
            "list",
            slug,
            action=btn,
            compact=True,
            object_name="reverseOpenSlugRow",
        )

    def _fill_box(box: QVBoxLayout, slugs: list[str]) -> None:
        _clear_box(box)
        for slug in slugs:
            box.addWidget(_slug_row(slug, box))

    def _remove_from(box: QVBoxLayout, slug: str) -> None:
        current = _slugs_of(box)
        changed, remaining, _msg = remove_slug(current, slug)
        if changed:
            _fill_box(box, remaining)
            _sync_lists_to_cfg()

    def _prompt_slug(prefix: str) -> str | None:
        text, ok = QInputDialog.getText(card, prefix, tr("Slug:"))
        if not ok:
            return None
        valid, value_or_err = validate_slug(text)
        if not valid:
            # validate_slug() returns a regex-dump for the CLI round-trip;
            # show the user a friendly, example-led message instead.
            QMessageBox.warning(
                card,
                tr("Invalid slug"),
                tr("Use lowercase letters, numbers and dashes (e.g. my-app)."),
            )
            return None
        return value_or_err

    def _add_list(target: QVBoxLayout, other: QVBoxLayout, label: str) -> None:
        slug = _prompt_slug(tr("Add to {list}").format(list=label))
        if not slug:
            return
        changed, new_target, new_other, msg = add_slug(_slugs_of(target), _slugs_of(other), slug)
        if not changed:
            QMessageBox.information(card, label, msg)
            return
        _fill_box(target, new_target)
        _fill_box(other, new_other)
        _sync_lists_to_cfg()

    def _run_cli(handler, **kwargs) -> None:
        """Bridge a host_open CLI handler to the GUI thread.

        The handlers all write to ``sys.stdout`` / ``sys.stderr`` via
        ``print()`` — we don't intercept their output here; the user
        sees the result reflected in the next status refresh and any
        modal we raise. The CLI bodies are fast enough (≤ 100 ms each)
        that we don't bother offloading to a worker thread for v1.
        """
        from types import SimpleNamespace

        args = SimpleNamespace(**kwargs)
        try:
            handler(args)
        except Exception as exc:  # noqa: BLE001
            log.exception("host-open CLI handler raised")
            QMessageBox.warning(card, tr("reverse-open"), str(exc))

    def _on_refresh_sync() -> None:
        """Refresh + sync the reverse-open handlers off the GUI thread.

        The CLI ``_cmd_refresh`` re-discovers guest apps and re-syncs the
        Windows-side handlers, which can take 30s+ — running it inline
        freezes the UI. Read the lists on the GUI thread first, then do the
        work on a daemon worker while a :class:`BusyDialog` spins, and
        marshal completion back to the GUI thread.
        """
        from PySide6.QtCore import QTimer

        from winpodx.gui._widget_helpers import BusyDialog

        _sync_lists_to_cfg()
        dlg = BusyDialog(
            card.window(),
            tr("Reverse-open"),
            tr("Refreshing reverse-open handlers…"),
            eta_hint=tr("This can take 30 seconds or more."),
        )
        error: list[Exception] = []

        def _work() -> None:
            from types import SimpleNamespace

            try:
                _cmd_refresh(SimpleNamespace(json=False, skip_icons=False, include_nodisplay=False))
            except Exception as exc:  # noqa: BLE001 — surfaced on the GUI thread
                log.exception("host-open refresh raised")
                error.append(exc)
            finally:
                dlg.finish()

        # Start the worker only once dlg.exec()'s nested event loop is running,
        # so the queued accept() from dlg.finish() always lands on a live dialog
        # (mirrors MaintenanceMixin._run_busy_op).
        QTimer.singleShot(0, lambda: threading.Thread(target=_work, daemon=True).start())
        dlg.exec()
        if error:
            QMessageBox.warning(card, tr("reverse-open"), str(error[0]))
        _refresh_status_label()

    btn_refresh.clicked.connect(_on_refresh_sync)
    btn_start.clicked.connect(
        lambda: (_run_cli(_cmd_start_listener, json=False), _refresh_status_label())
    )
    btn_stop.clicked.connect(
        lambda: (_run_cli(_cmd_stop_listener, json=False), _refresh_status_label())
    )
    btn_status.clicked.connect(_refresh_status_label)
    btn_allow_add.clicked.connect(lambda: _add_list(allow_box, deny_box, tr("allowlist")))
    btn_deny_add.clicked.connect(lambda: _add_list(deny_box, allow_box, tr("denylist")))
    enable_box.stateChanged.connect(_on_enable)

    _fill_box(allow_box, list(cfg.reverse_open.allowlist))
    _fill_box(deny_box, list(cfg.reverse_open.denylist))
    _refresh_status_label()

    return card
