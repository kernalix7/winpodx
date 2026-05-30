# SPDX-License-Identifier: MIT
"""Parse-and-grep guard for install.sh's post-create provisioning (0.6.0 item B).

After the provision-unify cleanup, install.sh's post-`winpodx setup`
provisioning is the single ``winpodx provision`` command — NOT a bash copy
of the wait-ready → /health poll → 6× discovery retry → host-open chain.
These tests fail loudly if a regression reintroduces an inline bash copy,
which is exactly the duplication the unification killed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"


def _strip_comments(text: str) -> str:
    """Drop comment lines so prose explaining the unification doesn't trip the
    grep guards. A comment line is one whose first non-space char is ``#``
    (the shebang counts as a comment too). Inline trailing comments are rare
    in install.sh and not load-bearing for these assertions, so a line-level
    strip is sufficient and avoids mangling quoted ``#`` inside strings."""
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


@pytest.fixture(scope="module")
def script() -> str:
    assert _INSTALL_SH.is_file(), f"install.sh not found at {_INSTALL_SH}"
    return _strip_comments(_INSTALL_SH.read_text(encoding="utf-8"))


def test_invokes_winpodx_provision(script: str) -> None:
    # The post-create step runs a single winpodx command chosen by the
    # fresh/upgrade branch (PROVISION_CMD array), invoked through the symlink.
    assert "PROVISION_CMD=(provision --require-agent)" in script
    assert '"$SYMLINK" "${PROVISION_CMD[@]}"' in script


def test_no_inline_health_curl_poll(script: str) -> None:
    # The 30× `curl .../health` settle poll moved into finish_provisioning.
    # (install.sh still uses curl elsewhere — e.g. the GitHub release check —
    #  so we assert the absence of the agent /health endpoint specifically,
    #  not curl in general.)
    assert "/health" not in script
    assert "127.0.0.1:8765" not in script
    assert "8765/health" not in script


def test_no_inline_six_times_discovery_retry(script: str) -> None:
    # The 6× `app refresh` retry loop is gone from bash.
    assert "for attempt in 1 2 3 4 5 6" not in script
    assert '"$SYMLINK" app refresh' not in script


def test_no_inline_host_open_listener_start(script: str) -> None:
    # Reverse-open listener start + refresh moved into finish_provisioning.
    assert "host-open start-listener" not in script
    assert "host-open refresh" not in script


def test_fresh_uses_provision_require_agent(script: str) -> None:
    # Fresh install (no prior config): provision with the agent-first gate
    # (#271) so discovery/apply defer instead of racing FreeRDP into
    # install.bat's autologon session.
    assert "PROVISION_CMD=(provision --require-agent)" in script


def test_upgrade_uses_migrate(script: str) -> None:
    # Upgrade (prior config existed): migrate FIRST syncs the refreshed guest
    # scripts (guest_sync) + pins the image, THEN runs the apply -> discovery
    # -> reverse-open chain. A blind `provision`-for-both (the first item-B
    # cut) left upgraded guests on stale agent.ps1 / OEM scripts.
    assert "PROVISION_CMD=(migrate --non-interactive)" in script


def test_provision_step_branches_on_fresh_vs_upgrade(script: str) -> None:
    # The two flows are gated on the pre-setup IS_FRESH_INSTALL snapshot.
    assert 'if [ "$IS_FRESH_INSTALL" = "1" ]; then' in script


def test_create_only_flag_is_gone(script: str) -> None:
    # setup --create-only was removed; install.sh must not pass it.
    assert "--create-only" not in script


def test_setup_skips_its_own_provision_tail(script: str) -> None:
    # install.sh runs the chain once via the explicit `winpodx provision`,
    # so it tells `winpodx setup` to skip its own full-provision tail.
    assert "WINPODX_NO_PROVISION=1" in script


def test_provision_call_disarms_err_trap(script: str) -> None:
    # bash fires the ERR trap on a failing *pipeline* even under `set +e`, so
    # the provision call must explicitly `trap - ERR` before it and re-arm
    # `trap rollback_and_exit_err ERR` after — otherwise a deferred (exit 5)
    # provision rolls back the whole fresh install before the rc handling runs.
    assert "trap - ERR" in script
    assert "trap rollback_and_exit_err ERR" in script
    # Re-arm must come back after the disarm (ordering sanity).
    assert script.index("trap - ERR") < script.rindex("trap rollback_and_exit_err ERR")


def test_deferred_provision_is_not_a_rollback(script: str) -> None:
    # Exit 4 (wait-ready ran long) / 5 (agent-first discovery deferred) are
    # NOT failures: Windows is downloaded + booted, so they record pending and
    # keep the install instead of rolling back ~15 min of ISO download.
    assert '[ "$PROVISION_RC" -eq 4 ] || [ "$PROVISION_RC" -eq 5 ]' in script
    # The deferred branch points the user at the recovery command.
    assert "winpodx app refresh" in script


def test_mode_prompt_reads_from_tty_so_curl_bash_can_choose(script: str) -> None:
    # Under `curl ... | bash`, stdin is the script pipe, so the R/A/C/N mode
    # menu (and Custom sub-prompts) must read from the controlling terminal
    # /dev/tty — otherwise the menu is unreachable via the canonical install
    # path and silently defaults to Recommended.
    assert "true </dev/tty" in script  # interactivity also detected via /dev/tty
    assert "TTY_DEV" in script
    # Every interactive prompt reads from $TTY_DEV, not bare stdin.
    for bare in (
        "read -r mode_answer\n",
        "read -r be_answer\n",
        "read -r gui_answer\n",
        "read -r answer\n",
    ):
        assert bare not in script, f"prompt still reads bare stdin: {bare!r}"
    assert script.count('read -r mode_answer < "$TTY_DEV"') == 1


def test_custom_mode_picks_freerdp_source(script: str) -> None:
    # Custom mode lets the user pick the FreeRDP client source (native/flatpak/
    # auto), not just the backend + GUI.
    assert "WINPODX_FREERDP_SOURCE" in script
    assert '[ "$INSTALL_MODE" = "c" ]' in script
    # The resolved source is handed to setup so the launcher honours it.
    assert "--freerdp-source" in script


def test_freerdp_flatpak_not_installed_redundantly(script: str) -> None:
    # When a FreeRDP client (native or Flatpak) is already present, no client is
    # pulled. With NO client present, auto installs the NATIVE package (native
    # is preferred); only an explicit `--freerdp-source flatpak` installs the
    # Flatpak (INSTALL_FREERDP_FLATPAK).
    assert "FREERDP_FLATPAK_PRESENT" in script
    assert "FREERDP_NATIVE_PRESENT" in script
    assert "INSTALL_FREERDP_FLATPAK" in script
    assert "com.freerdp.FreeRDP" in script


def test_prints_install_plan_before_acting(script: str) -> None:
    # After the mode + dependency sources are resolved, install.sh prints a
    # plan summary (mode / backend / FreeRDP action / GUI / packages / VM)
    # before installing anything, so the run is transparent.
    assert "install plan" in script
    # The plan must be emitted before the package-install loop runs.
    assert script.index("install plan") < script.index("if [ ${#MISSING[@]} -gt 0 ]; then")


def test_upgrade_skips_the_mode_prompt(script: str) -> None:
    # On an upgrade / re-run (a config already exists) the R/A/C/N mode prompt
    # is pointless — install.sh reuses the existing config and runs migrate,
    # so the prompt is gated on IS_FRESH_INSTALL.
    assert '[ "$IS_FRESH_INSTALL" != "1" ]' in script
    # The mode-prompt heredoc must come after that upgrade gate.
    assert script.index('[ "$IS_FRESH_INSTALL" != "1" ]') < script.index("Install mode?")


def test_too_old_podman_guard_blocks_before_install(script: str) -> None:
    # #271 ask 3: when the resolved backend is podman but podman is < 4 (Ubuntu
    # 22.04 ships 3.4), Recommended mode / explicit --backend podman would
    # otherwise proceed and fail at provisioning AFTER installing packages. A
    # guard refuses to blindly continue — and it must run BEFORE any package
    # install so an abort leaves the system unmodified.
    guard = '[ "$WINPODX_ALLOW_OLD_PODMAN" != "1" ]'
    assert guard in script
    # Gated on the podman-too-old condition + the podman backend.
    assert '[ "$WINPODX_BACKEND" = "podman" ] && [ "$PODMAN_TOO_OLD" = true ]' in script
    # The guard sits before the install-plan / package-install phase.
    assert script.index(guard) < script.index("install plan")


def test_too_old_podman_guard_has_override(script: str) -> None:
    # An out-of-band podman upgrade can make the probe stale, so the guard is
    # bypassable via env var and a matching flag.
    assert "WINPODX_ALLOW_OLD_PODMAN" in script
    assert "--allow-old-podman" in script


def test_too_old_podman_noninteractive_exits_clean(script: str) -> None:
    # Non-interactive runs can't prompt, so the guard exits cleanly with
    # guidance rather than silently switching backend or proceeding.
    assert "aborted before modifying the system" in script.lower()


def test_no_inline_chain_steps_survive(script: str) -> None:
    """After `winpodx setup`, the post-create chain is driven by ONE winpodx
    command (provision on fresh, migrate on upgrade, both via PROVISION_CMD).
    The individual chain commands the old ~140-line bash copy invoked
    directly must NOT survive as separate `"$SYMLINK" <cmd>` calls."""
    forbidden = (
        '"$SYMLINK" pod wait-ready',
        '"$SYMLINK" app refresh',
        '"$SYMLINK" host-open',
    )
    for cmd in forbidden:
        assert cmd not in script, f"inline bash chain step still present: {cmd!r}"
