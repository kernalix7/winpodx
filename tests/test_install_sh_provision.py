# SPDX-License-Identifier: MIT
"""Parse-and-grep guard for install.sh's post-create provisioning (0.6.0 item B).

After the provision-unify cleanup, install.sh's post-`winpodx setup`
provisioning is the single ``winpodx provision`` command — NOT a bash copy
of the wait-ready → /health poll → 6× discovery retry → host-open chain.
These tests fail loudly if a regression reintroduces an inline bash copy,
which is exactly the duplication the unification killed.
"""

from __future__ import annotations

import os
import subprocess
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


def test_installs_podman_compose_with_podman(script: str) -> None:
    # #580/#503: winpodx hard-requires the standalone podman-compose, which the
    # podman package doesn't pull in. install.sh must detect it and add it to
    # the install set so pod creation doesn't fail with "compose command not
    # found".
    assert "PODMAN_COMPOSE_PRESENT" in script
    assert 'MISSING+=("podman-compose")' in script


def test_atomic_rpm_path_honours_explicit_source_override(script: str) -> None:
    # #548: on Atomic (rpm-ostree) install.sh defaults to the OBS RPM, but an
    # explicit --main/--ref/--source/--image-tar must win and fall through to
    # the git/venv flow (the OBS RPM only ships tagged releases). The gate must
    # therefore require all three override vars to be empty before taking the
    # rpm-ostree path.
    lines = script.splitlines()
    # The gate opens with `if command -v rpm-ostree ... \` and continues the
    # compound `&& [ -z ... ]` condition on the next line.
    gate = next(
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("if command -v rpm-ostree")),
        None,
    )
    assert gate is not None, "rpm-ostree gate must open the Atomic install path"
    cond = " ".join(lines[gate : gate + 2])
    assert '-z "$WINPODX_REF"' in cond
    assert '-z "$WINPODX_SOURCE"' in cond
    assert '-z "$WINPODX_IMAGE_TAR"' in cond


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


@pytest.mark.parametrize("exit_path", ["failure", "interrupt"])
@pytest.mark.parametrize("compose_owns_oem", [False, True])
def test_failed_setup_preserves_oem_owner_on_later_rollback(
    tmp_path: Path, exit_path: str, compose_owns_oem: bool
) -> None:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    rollback = text[
        text.index("cleanup_install_marker() {") : text.index("# Map generic dependency")
    ]
    setup = text[text.index("# --- Run setup ---") : text.index("# NOTE: --win-iso staging")]
    home = tmp_path / "home"
    install_dir = home / ".local/bin/winpodx-app"
    launcher = home / ".local/bin/winpodx-run"
    symlink = home / ".local/bin/winpodx"
    compose = home / ".config/winpodx/compose.yaml"
    container_started = tmp_path / "container-started"
    (install_dir / "config/oem").mkdir(parents=True)
    (install_dir / "config/oem/install.bat").write_text("OEM script")
    launcher.write_text("existing launcher")
    symlink.write_text("existing command")
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$COMPOSE_OWNS_OEM" = 1 ]; then\n'
        '    mkdir -p "$(dirname "$COMPOSE_PATH")"\n'
        '    printf \'      - %s/config/oem:/oem:Z\\n\' "$INSTALL_DIR" > "$COMPOSE_PATH"\n'
        '    touch "$CONTAINER_STARTED"\n'
        "fi\n"
        "exit 17\n"
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        HOME=str(home),
        INSTALL_DIR=str(install_dir),
        COMPOSE_PATH=str(compose),
        COMPOSE_OWNS_OEM=str(int(compose_owns_oem)),
        CONTAINER_STARTED=str(container_started),
    )
    harness = f"""set -euo pipefail
CONFIG_HOME="$HOME/.config"
VENV_PY="{fake_python}"
LAUNCHER="$HOME/.local/bin/winpodx-run"
SYMLINK="$HOME/.local/bin/winpodx"
VENV_DIR="$INSTALL_DIR/.venv"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
METAINFO_DIR="$HOME/.local/share/metainfo"
WINPODX_INSTALL_MARKER="$CONFIG_HOME/winpodx/.install_in_progress"
IS_FRESH_INSTALL=1
ROLLBACK_ARMED=1
SWAP_IN_PROGRESS=0
UPGRADE_SWAP_DONE=0
SYMLINK_BACKED_UP=0
SYMLINK_BACKUP=""
SETUP_OK=1
WINPODX_BACKEND=podman
WINPODX_WIN_VERSION=""
WINPODX_STORAGE_DIR=""
WINPODX_WIN_ISO=""
WINPODX_FREERDP_SOURCE=auto
WINPODX_MANUAL=0
log() {{ :; }}
warn() {{ :; }}
err() {{ :; }}
{rollback}
{setup}
{("false" if exit_path == "failure" else "cleanup_and_exit_int")}
"""
    result = subprocess.run(["bash", "-c", harness], env=env, capture_output=True, text=True)
    assert result.returncode == (1 if exit_path == "failure" else 130), result.stderr
    assert container_started.exists() is compose_owns_oem
    assert install_dir.exists() is compose_owns_oem, (
        "rollback deleted the install tree backing /oem"
        if compose_owns_oem
        else "rollback failed to clean a pre-ownership install"
    )
    assert launcher.exists() is compose_owns_oem
    assert symlink.exists() is compose_owns_oem
    if compose_owns_oem:
        assert launcher.read_text() == "existing launcher"
        assert symlink.read_text() == "existing command"


def test_successful_setup_disarms_fresh_install_rollback(script: str) -> None:
    setup_start = script.index('if WINPODX_NO_PROVISION=1 "$VENV_PY"')
    desktop_integration = script.index('mkdir -p "$DESKTOP_DIR" "$ICON_DIR"')
    setup_region = script[setup_start:desktop_integration]

    failure_branch = setup_region.index('if [ "$SETUP_OK" -eq 0 ]; then')
    success_branch = setup_region.index("\n    else\n", failure_branch)
    rollback_disarm = setup_region.index("ROLLBACK_ARMED=0", failure_branch)
    branch_end = setup_region.index("\n    fi", success_branch)

    assert success_branch < rollback_disarm < branch_end


def test_missing_appstream_metainfo_is_non_fatal(script: str) -> None:
    source = "$INSTALL_DIR/data/org.winpodx.WinPodX.metainfo.xml"
    guard = f'if [ -f "{source}" ]; then'
    copy = f'cp "{source}"'

    assert guard in script
    assert script.index(guard) < script.index(copy)


# --- #810: dockur ejects the install ISO after the first full shutdown -------


def test_successful_fresh_install_cold_restarts_to_remove_install_media(script: str) -> None:
    command = 'WINPODX_NO_TRAY_SPAWN=1 "$SYMLINK" pod restart'
    gate = '[ "$IS_FRESH_INSTALL" = "1" ] && [ "$SETUP_OK" -eq 1 ]'
    assert gate in script
    assert command in script
    assert "Finalizing Windows installation" in script

    # The restart belongs only to PROVISION_RC=0's final else branch. Exit 4/5
    # remains deferred, and every other non-zero exit remains a warning path.
    success_branch = script.index('else\n        rm -f "$PENDING_FILE"')
    branch_end = script.index('\n    fi\n    rm -f "$PROVISION_OUT"', success_branch)
    restart = script.index(command)
    assert success_branch < restart < branch_end


def test_final_install_restart_is_best_effort(script: str) -> None:
    command = 'WINPODX_NO_TRAY_SPAWN=1 "$SYMLINK" pod restart'
    assert f"if ! {command}; then" in script
    assert "final stop/start did not complete" in script
    assert r"Run \`winpodx pod restart\` once" in script


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


# --- #789: the two multi-minute stretches that used to be silent -------------


def test_pyside6_download_is_announced(script: str) -> None:
    """A ~100 MB wheel under `pip --quiet` looks like a hang, and people
    Ctrl-C out of it — which is how half-installed states get created."""
    active = _strip_comments(script)
    assert "PySide6, ~100 MB" in active


def test_setup_output_is_teed_not_swallowed(script: str) -> None:
    """Redirecting setup straight to a file hid the container image pull.
    It must reach the terminal as well as the capture file."""
    active = _strip_comments(script)

    assert 'tee "$SETUP_OUT"' in active
    # The old form sent everything to the file and showed nothing.
    assert '-m winpodx setup "${SETUP_ARGS[@]}" >"$SETUP_OUT" 2>&1' not in active


def test_setup_failure_detection_survives_the_pipe(script: str) -> None:
    """Piping through tee would report tee's status instead of setup's without
    pipefail, silently turning a failed setup into a successful install."""
    assert "set -euo pipefail" in script

    active = _strip_comments(script)
    assert "SETUP_OK=0" in active
    assert 'tail -n 20 "$SETUP_OUT"' in active
