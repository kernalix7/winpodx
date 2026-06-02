# SPDX-License-Identifier: MIT
"""First-run setup prompt (#255).

Fires on the first ``winpodx <cmd>`` invocation when ``cfg.pod.initialized``
is False (or no config exists). Surfaces a three-way prompt:

    [Y]es     -- run ``winpodx setup`` (auto, host-detected defaults)
    [C]ustom  -- run ``winpodx setup --customize`` (wizard)
    [n]o      -- exit without changes

After the user picks Y or C, the requested setup mode runs in-process,
flips ``cfg.pod.initialized`` to True, and control returns to the
caller so the original command (e.g. ``winpodx app run desktop``)
proceeds with a fully-provisioned guest.

Skip-list: commands that introspect state without needing a configured
pod are never gated on first-run (the user can't do anything about
the prompt without first running one of those commands).
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from winpodx.core.i18n import tr

# Commands that bypass the first-run prompt -- introspection, help,
# meta, and the setup / uninstall commands that mutate config state
# themselves. ``info`` is on the list because users hitting a broken
# install commonly run it first to diagnose, and we don't want the
# prompt eating that signal.
_SKIP_COMMANDS = frozenset(
    {
        "version",
        "help",
        "info",
        "setup",
        "uninstall",
        "config",
        "doctor",
        "device",  # device passthrough management — reads/writes config directly
        "gui",  # GUI has its own first-run modal
        "tray",  # tray spawns from GUI; no prompt path
    }
)

PromptFn = Callable[[str], str]
OutFn = Callable[[str], None]


def _default_input(prompt: str) -> str:
    return input(prompt)


def _default_print(line: str) -> None:
    print(line)


def should_prompt(command: str | None) -> bool:
    """Return True if first-run prompt should fire for ``command``.

    False for: introspection / meta / commands that already handle
    config state themselves / non-TTY stdin (so a piped script never
    blocks). True otherwise.
    """
    if command is None:
        return False
    if command.lstrip("-") in _SKIP_COMMANDS:
        return False
    if not sys.stdin.isatty():
        return False
    return True


def maybe_run_first_run_prompt(
    command: str | None,
    *,
    input_fn: PromptFn = _default_input,
    print_fn: OutFn = _default_print,
) -> bool:
    """Run the first-run prompt if appropriate. Returns True if setup
    was run (caller should re-load config), False otherwise.

    Skipped silently when:
      * ``command`` is in the skip-list
      * stdin is not a TTY (piped / CI)
      * config already exists AND ``cfg.pod.initialized`` is True
      * config loading raises (broken install -- let the caller's own
        error path surface it instead of bolting a prompt on top)
    """
    if not should_prompt(command):
        return False

    try:
        from winpodx.core.config import Config

        cfg = Config.load()
    except Exception:  # noqa: BLE001 -- broken-config path is the caller's problem
        return False

    if cfg.pod.initialized:
        return False

    print_fn("")
    print_fn(tr("WinPodX has not been set up yet on this account."))
    print_fn("")
    print_fn(tr("Run setup now?"))
    print_fn(tr("  [Y]es     -- auto setup (host-detected defaults, no prompts)"))
    print_fn(tr("  [C]ustom  -- wizard (pick every knob)"))
    print_fn(tr("  [n]o      -- skip; you can run `winpodx setup` later"))
    print_fn("")

    try:
        answer = input_fn(tr("Choice [Y/c/n]: ")).strip().lower()
    except EOFError:
        return False

    if answer in ("", "y", "yes"):
        mode = "auto"
    elif answer in ("c", "custom", "wizard"):
        mode = "customize"
    elif answer in ("n", "no"):
        print_fn(tr("Skipped. Run `winpodx setup` when you're ready."))
        return False
    else:
        print_fn(
            tr(
                "Unrecognised choice {answer}; skipping. Run `winpodx setup` to set up later."
            ).format(answer=repr(answer))
        )
        return False

    return _run_setup(mode)


def _run_setup(mode: str) -> bool:
    """Invoke ``winpodx setup`` (auto or --customize) in-process.

    Returns True on success. Failures are surfaced via the setup
    command's own output; the caller treats False as "config still
    not initialized".
    """
    import argparse

    from winpodx.cli.setup_cmd import handle_setup

    args = argparse.Namespace(
        backend=None,
        win_version=None,
        update_image=False,
        migrate_storage=False,
        migrate_storage_target=None,
        non_interactive=(mode == "auto"),
        customize=(mode == "customize"),
    )
    try:
        handle_setup(args)
    except SystemExit as e:
        return e.code in (None, 0)
    except Exception:  # noqa: BLE001
        return False
    return True
