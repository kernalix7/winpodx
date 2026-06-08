# SPDX-License-Identifier: MIT
"""Regression: _filter_apps must not recurse when its own rebuild re-enters it.

Discover -> _reload_apps -> _filter_apps rebuilds app_list_layout, which adds
word-wrapped empty-state labels into a setWidgetResizable QScrollArea and forces
a synchronous heightForWidth layout pass. On Wayland that pass re-entered
_filter_apps mid-rebuild (window resizeEvent -> _reflow_library, and the
pod-status -> _filter_apps refresh), and Qt's QBoxLayout::heightForWidth then
recursed without bound -> SIGSEGV (the whole GUI window "just closed").

The fix is a re-entrancy guard that coalesces a nested call into a single
trailing rebuild. These tests pin that behavior without a live Qt event loop.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from winpodx.gui._main_window_library import LibraryPageMixin  # noqa: E402


class _Label:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:  # noqa: N802 - Qt signature
        self.text = text


class _Harness(LibraryPageMixin):
    """Minimal host exposing only what _filter_apps reads, with a
    _populate_app_view that re-enters _filter_apps once (the resize/pod-status
    race) so the guard is exercised."""

    def __init__(self) -> None:
        self._active_category = ""
        self.apps: list = []
        self.app_count_label = _Label()
        self.populate_calls: list[int] = []
        self._reenter_with: str | None = None

    # --- helpers _filter_apps calls; all no-ops except _populate_app_view ---
    def _refresh_commands(self, q: str) -> None:
        pass

    def _visible_apps(self) -> list:
        return []

    def _refresh_launcher_sections(self, filtered: list) -> None:
        pass

    def _populate_app_view(self, apps: list) -> None:
        self.populate_calls.append(len(apps))
        # Simulate Qt's synchronous layout pass re-entering _filter_apps once,
        # mid-rebuild, with a different query (e.g. a resize-driven reflow).
        if self._reenter_with is not None:
            text, self._reenter_with = self._reenter_with, None
            self._filter_apps(text)


def test_reentrant_filter_does_not_recurse_and_coalesces():
    h = _Harness()
    h._reenter_with = "zzz"
    # Without the guard this recurses through the synchronous re-entry forever.
    h._filter_apps("")
    # Outer rebuild + exactly one coalesced trailing rebuild (text differed).
    assert h.populate_calls == [0, 0]
    # Guard is released so later top-level calls still work.
    assert getattr(h, "_filtering", False) is False


def test_reentrant_filter_with_same_text_skips_redundant_rebuild():
    h = _Harness()
    h._reenter_with = ""  # nested call carries the SAME query as the outer call
    h._filter_apps("")
    # Same text -> the trailing rebuild is skipped (no redundant work).
    assert h.populate_calls == [0]
    assert getattr(h, "_filtering", False) is False


def test_non_reentrant_filter_runs_once():
    h = _Harness()  # _reenter_with stays None -> no nested call
    h._filter_apps("")
    assert h.populate_calls == [0]
    assert getattr(h, "_filtering", False) is False
