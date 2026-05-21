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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from winpodx.reverse_open.config import _SLUG_RE
from winpodx.reverse_open.lifecycle import is_listener_running

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from winpodx.core.config import Config

logger = logging.getLogger(__name__)


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
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QCheckBox,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
    )

    from winpodx.cli.host_open import (
        _apps_json,
        _cmd_refresh,
        _cmd_start_listener,
        _cmd_stop_listener,
    )

    card = QFrame(parent)
    card.setObjectName("settingsSection")
    card.setFrameShape(QFrame.Shape.NoFrame)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(8)

    title = QLabel("▦  Reverse File Associations")
    title.setStyleSheet("font-size: 16px; font-weight: bold;")
    layout.addWidget(title)

    sub = QLabel("Linux apps appear in the Windows guest's right-click ‘Open with…’ menu.")
    sub.setWordWrap(True)
    layout.addWidget(sub)

    enable_box = QCheckBox("Enable reverse-open")
    enable_box.setChecked(bool(cfg.reverse_open.enabled))
    layout.addWidget(enable_box)

    status_label = QLabel("")
    status_label.setWordWrap(True)
    layout.addWidget(status_label)

    # --- action buttons ------------------------------------------------------
    buttons_row = QHBoxLayout()
    btn_refresh = QPushButton("Refresh && sync")
    btn_start = QPushButton("Start daemon")
    btn_stop = QPushButton("Stop daemon")
    btn_status = QPushButton("Refresh status")
    for b in (btn_refresh, btn_start, btn_stop, btn_status):
        buttons_row.addWidget(b)
    buttons_row.addStretch()
    layout.addLayout(buttons_row)

    # --- allow / deny lists --------------------------------------------------
    lists_grid = QGridLayout()
    allow_label = QLabel("Allowlist (empty = all discovered)")
    deny_label = QLabel("Denylist")
    allow_list = QListWidget()
    deny_list = QListWidget()
    for slug in cfg.reverse_open.allowlist:
        QListWidgetItem(slug, allow_list)
    for slug in cfg.reverse_open.denylist:
        QListWidgetItem(slug, deny_list)
    lists_grid.addWidget(allow_label, 0, 0)
    lists_grid.addWidget(deny_label, 0, 1)
    lists_grid.addWidget(allow_list, 1, 0)
    lists_grid.addWidget(deny_list, 1, 1)

    allow_btns = QHBoxLayout()
    btn_allow_add = QPushButton("+ Add")
    btn_allow_rm = QPushButton("− Remove")
    allow_btns.addWidget(btn_allow_add)
    allow_btns.addWidget(btn_allow_rm)
    allow_btns.addStretch()

    deny_btns = QHBoxLayout()
    btn_deny_add = QPushButton("+ Add")
    btn_deny_rm = QPushButton("− Remove")
    deny_btns.addWidget(btn_deny_add)
    deny_btns.addWidget(btn_deny_rm)
    deny_btns.addStretch()

    lists_grid.addLayout(allow_btns, 2, 0)
    lists_grid.addLayout(deny_btns, 2, 1)
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
        cfg.reverse_open.enabled = state == Qt.CheckState.Checked.value or bool(state)
        _refresh_status_label()

    def _sync_lists_to_cfg() -> None:
        cfg.reverse_open.allowlist = [allow_list.item(i).text() for i in range(allow_list.count())]
        cfg.reverse_open.denylist = [deny_list.item(i).text() for i in range(deny_list.count())]

    def _prompt_slug(prefix: str) -> str | None:
        text, ok = QInputDialog.getText(card, prefix, "Slug:")
        if not ok:
            return None
        valid, value_or_err = validate_slug(text)
        if not valid:
            QMessageBox.warning(card, "Invalid slug", value_or_err)
            return None
        return value_or_err

    def _add_list(target: QListWidget, other: QListWidget, label: str) -> None:
        slug = _prompt_slug(f"Add to {label}")
        if not slug:
            return
        current_target = [target.item(i).text() for i in range(target.count())]
        current_other = [other.item(i).text() for i in range(other.count())]
        changed, new_target, new_other, msg = add_slug(current_target, current_other, slug)
        if not changed:
            QMessageBox.information(card, label, msg)
            return
        target.clear()
        for s in new_target:
            QListWidgetItem(s, target)
        other.clear()
        for s in new_other:
            QListWidgetItem(s, other)
        _sync_lists_to_cfg()

    def _remove_list(target: QListWidget) -> None:
        item = target.currentItem()
        if item is None:
            QMessageBox.information(card, "Remove", "Select a slug first.")
            return
        target.takeItem(target.row(item))
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
            logger.exception("host-open CLI handler raised")
            QMessageBox.warning(card, "reverse-open", str(exc))

    btn_refresh.clicked.connect(
        lambda: (
            _sync_lists_to_cfg(),
            _run_cli(
                _cmd_refresh,
                json=False,
                skip_icons=False,
                include_nodisplay=False,
            ),
            _refresh_status_label(),
        )
    )
    btn_start.clicked.connect(
        lambda: (_run_cli(_cmd_start_listener, json=False), _refresh_status_label())
    )
    btn_stop.clicked.connect(
        lambda: (_run_cli(_cmd_stop_listener, json=False), _refresh_status_label())
    )
    btn_status.clicked.connect(_refresh_status_label)
    btn_allow_add.clicked.connect(lambda: _add_list(allow_list, deny_list, "allowlist"))
    btn_allow_rm.clicked.connect(lambda: _remove_list(allow_list))
    btn_deny_add.clicked.connect(lambda: _add_list(deny_list, allow_list, "denylist"))
    btn_deny_rm.clicked.connect(lambda: _remove_list(deny_list))
    enable_box.stateChanged.connect(_on_enable)

    _refresh_status_label()

    return card
