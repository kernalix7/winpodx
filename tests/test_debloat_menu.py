# SPDX-License-Identifier: MIT
"""Tests for the text-mode debloat picker (#247 phase 4)."""

from __future__ import annotations

import pytest

from winpodx.cli.debloat_menu import run_menu
from winpodx.core.debloat import load_catalog


@pytest.fixture
def catalog():
    return load_catalog()


def _scripted_input(lines: list[str]):
    """Return an ``input_fn`` stub that yields ``lines`` in order."""
    it = iter(lines)

    def _read(_prompt: str) -> str:
        return next(it)

    return _read


def _capture_print():
    """Return ``(out_fn, lines)`` where ``lines`` collects every call."""
    captured: list[str] = []

    def _emit(line: str) -> None:
        captured.append(line)

    return _emit, captured


class TestRunMenuBasics:
    def test_quit_returns_none(self, catalog):
        out, _ = _capture_print()
        result = run_menu(catalog, input_fn=_scripted_input(["q"]), print_fn=out)
        assert result is None

    def test_apply_with_default_normal_preset(self, catalog):
        out, _ = _capture_print()
        result = run_menu(catalog, input_fn=_scripted_input(["a"]), print_fn=out)
        assert set(result) == set(catalog.items_for_preset("normal"))

    def test_eof_quits_gracefully(self, catalog):
        """EOFError from input_fn (e.g. piped /dev/null) -> None."""

        def _eof(_prompt):
            raise EOFError

        out, _ = _capture_print()
        result = run_menu(catalog, input_fn=_eof, print_fn=out)
        assert result is None

    def test_blank_input_is_ignored(self, catalog):
        out, _ = _capture_print()
        result = run_menu(catalog, input_fn=_scripted_input(["", "", "q"]), print_fn=out)
        assert result is None


class TestPresetCommands:
    def test_switch_preset_reseeds_selection(self, catalog):
        out, _ = _capture_print()
        result = run_menu(
            catalog,
            input_fn=_scripted_input(["p performance", "a"]),
            print_fn=out,
        )
        # Menu returns items in catalog declaration order, not preset
        # order -- compare as sets so the test doesn't break when the
        # preset's TOML ordering diverges from catalog declaration.
        assert set(result) == set(catalog.items_for_preset("performance"))

    def test_unknown_preset_keeps_state(self, catalog):
        out, lines = _capture_print()
        result = run_menu(
            catalog,
            input_fn=_scripted_input(["p bogus", "a"]),
            print_fn=out,
        )
        # Initial normal selection survives the bad command.
        assert set(result) == set(catalog.items_for_preset("normal"))
        joined = "\n".join(lines)
        assert "unknown preset" in joined

    def test_p_alone_lists_presets(self, catalog):
        out, lines = _capture_print()
        run_menu(catalog, input_fn=_scripted_input(["p", "q"]), print_fn=out)
        joined = "\n".join(lines)
        for name in catalog.preset_names:
            assert name in joined


class TestItemToggle:
    def test_toggle_by_number(self, catalog):
        """Toggle one off then apply -> returned list reflects the change."""
        out, _ = _capture_print()
        # Initial preset 'normal' = telemetry + ads (positions 1 and 2 in
        # catalog declaration order). Toggle '1' off -> only ads remains.
        result = run_menu(
            catalog,
            input_fn=_scripted_input(["1", "a"]),
            print_fn=out,
        )
        assert "telemetry" not in result
        assert "ads" in result

    def test_toggle_by_name(self, catalog):
        out, _ = _capture_print()
        # Start with normal (telemetry+ads), add transparency by name.
        result = run_menu(
            catalog,
            input_fn=_scripted_input(["transparency", "a"]),
            print_fn=out,
        )
        assert "transparency" in result
        assert "telemetry" in result  # was in normal seed

    def test_out_of_range_number_keeps_state(self, catalog):
        out, lines = _capture_print()
        result = run_menu(
            catalog,
            input_fn=_scripted_input(["999", "a"]),
            print_fn=out,
        )
        assert set(result) == set(catalog.items_for_preset("normal"))
        joined = "\n".join(lines)
        assert "out of range" in joined

    def test_unknown_name_keeps_state(self, catalog):
        out, lines = _capture_print()
        result = run_menu(
            catalog,
            input_fn=_scripted_input(["nonexistent_item", "a"]),
            print_fn=out,
        )
        assert set(result) == set(catalog.items_for_preset("normal"))
        joined = "\n".join(lines)
        assert "unrecognised" in joined


class TestApplyGuard:
    def test_apply_with_empty_selection_does_not_return(self, catalog):
        """Pressing 'a' with no items selected should re-prompt rather
        than fire an empty payload."""
        out, lines = _capture_print()
        # Untick both items in the normal seed, then try to apply,
        # then quit.
        result = run_menu(
            catalog,
            input_fn=_scripted_input(["1", "2", "a", "q"]),
            print_fn=out,
        )
        assert result is None
        joined = "\n".join(lines)
        assert "no items selected" in joined


class TestRenderOutput:
    def test_initial_render_shows_all_items(self, catalog):
        out, lines = _capture_print()
        run_menu(catalog, input_fn=_scripted_input(["q"]), print_fn=out)
        joined = "\n".join(lines)
        for name in catalog.items:
            assert name in joined

    def test_help_command_lists_commands(self, catalog):
        out, lines = _capture_print()
        run_menu(catalog, input_fn=_scripted_input(["h", "q"]), print_fn=out)
        joined = "\n".join(lines)
        assert "apply" in joined
        assert "quit" in joined
