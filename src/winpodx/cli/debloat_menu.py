# SPDX-License-Identifier: MIT
"""Text-mode debloat picker (#247 phase 4).

Headless-friendly alternative to the Qt picker for users on SSH /
TTY-only installs. Pure ``input()`` + ``print()`` -- no curses
dependency, no display server, no terminal capability sniffing -- so
the same code path works inside an interactive shell, a recovery
console, or a CI integration test with mocked stdin.

Loop shape:

    while True:
        render(state)
        cmd = prompt()
        process(cmd, state)
        if cmd in ("a", "q"):
            return

Commands accepted by the prompt (case-insensitive, whitespace-stripped):

    <N>           Toggle item number N (1-based).
    p <name>      Switch to preset <name> (re-seeds checkboxes).
    p             List presets.
    a             Apply current selection (return ``selection`` list).
    q             Quit without applying (return ``None``).
    h, ?          Print help.

This module is import-safe: ``run_menu`` is the only public entry
point and it accepts an injectable ``input_fn`` so the CLI handler
can pass ``builtins.input`` while tests pass a ``StringIO``-backed
stub.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from winpodx.core.debloat import DebloatCatalog
from winpodx.core.i18n import tr

PromptFn = Callable[[str], str]
OutFn = Callable[[str], None]


def _default_print(line: str) -> None:
    print(line)


def _default_input(prompt: str) -> str:
    return input(prompt)


def run_menu(
    catalog: DebloatCatalog,
    *,
    initial_preset: str = "normal",
    input_fn: PromptFn = _default_input,
    print_fn: OutFn = _default_print,
) -> list[str] | None:
    """Run the interactive text-mode picker. Returns selection or ``None``.

    Args:
        catalog: The debloat catalog to drive the UI from.
        initial_preset: Preset name to seed the initial selection with.
            Falls back to "normal" silently if unknown -- matches the
            GUI dialog's behaviour.
        input_fn: Callable used to read a line of user input. Defaults
            to the builtin ``input``. Tests pass a stub.
        print_fn: Callable used to emit one line of output. Defaults
            to ``print``. Tests pass a list-appending stub.

    Returns:
        The ordered list of selected item names if the user applied,
        or ``None`` if the user quit.
    """
    item_order = list(catalog.items.keys())
    if initial_preset not in catalog.presets:
        initial_preset = "normal" if "normal" in catalog.presets else next(iter(catalog.presets))
    selection: set[str] = set(catalog.items_for_preset(initial_preset))
    current_preset: str | None = initial_preset

    print_fn("")
    print_fn(tr("=== WinPodX debloat (menu) ==="))
    print_fn(tr("h / ? for help. Press <a> to apply, <q> to quit."))
    print_fn("")

    while True:
        _render(catalog, item_order, selection, current_preset, print_fn)
        try:
            raw = input_fn("> ").strip()
        except EOFError:
            # Treat EOF (piped /dev/null, test stub exhaustion) as quit
            # rather than crashing on an empty input.
            return None
        if not raw:
            continue
        cmd = raw.lower()

        if cmd in ("q", "quit", "exit"):
            return None
        if cmd in ("a", "apply"):
            if not selection:
                print_fn(tr("(no items selected; press 'q' to quit instead)"))
                continue
            return [name for name in item_order if name in selection]
        if cmd in ("h", "?", "help"):
            _print_help(catalog, print_fn)
            continue

        if cmd.startswith("p"):
            # 'p' alone -> list presets. 'p <name>' -> switch.
            rest = raw[1:].strip().lower()
            if not rest:
                print_fn(tr("Presets: {presets}").format(presets=", ".join(catalog.preset_names)))
                continue
            if rest not in catalog.presets:
                print_fn(
                    tr("(unknown preset {preset}; available: {available})").format(
                        preset=repr(rest), available=", ".join(catalog.preset_names)
                    )
                )
                continue
            selection = set(catalog.items_for_preset(rest))
            current_preset = rest
            continue

        # Item number.
        if cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(item_order):
                name = item_order[idx]
                if name in selection:
                    selection.discard(name)
                else:
                    selection.add(name)
                current_preset = None  # user-edited -> custom
                continue
            print_fn(
                tr("(item {item} out of range 1-{max_item})").format(
                    item=repr(cmd), max_item=len(item_order)
                )
            )
            continue

        # Item name.
        if cmd in catalog.items:
            if cmd in selection:
                selection.discard(cmd)
            else:
                selection.add(cmd)
            current_preset = None
            continue

        print_fn(tr("(unrecognised command {cmd}; type 'h' for help)").format(cmd=repr(raw)))


def _render(
    catalog: DebloatCatalog,
    item_order: list[str],
    selection: set[str],
    current_preset: str | None,
    print_fn: OutFn,
) -> None:
    """Render the current state of the picker."""
    width = max((len(name) for name in item_order), default=0)
    print_fn(f"Preset: {current_preset or 'custom'}")
    print_fn(f"Selected: {len(selection)} of {len(item_order)}")
    print_fn("")
    for i, name in enumerate(item_order, start=1):
        item = catalog.items[name]
        mark = "x" if name in selection else " "
        one_way = " (one-way)" if not item.is_reversible else ""
        print_fn(f"  {i:>2}. [{mark}] [{item.risk:<6}] {name:<{width}}  {item.label}{one_way}")
    print_fn("")


def _print_help(catalog: DebloatCatalog, print_fn: OutFn) -> None:
    print_fn("")
    print_fn(tr("Commands:"))
    print_fn(tr("  <N>           Toggle item number N (1-based)"))
    print_fn(tr("  <name>        Toggle item by name (e.g. 'telemetry')"))
    print_fn(tr("  p             List presets"))
    print_fn(tr("  p <name>      Switch to preset <name>"))
    print_fn(f"                ({', '.join(catalog.preset_names)})")
    print_fn(tr("  a, apply      Apply current selection and exit"))
    print_fn(tr("  q, quit       Quit without applying"))
    print_fn(tr("  h, ?, help    Show this help"))
    print_fn("")


def _isatty_or_force() -> bool:
    """Whether stdin is a TTY (or env-overridden for testing)."""
    return sys.stdin.isatty()
