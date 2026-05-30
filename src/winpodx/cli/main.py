# SPDX-License-Identifier: MIT
"""Main CLI entry point for winpodx, zero external dependencies."""

from __future__ import annotations

import argparse
import sys

from winpodx import __version__
from winpodx.core.i18n import tr


def cli(argv: list[str] | None = None) -> None:
    """winpodx CLI entry point."""
    from winpodx.utils.logging import setup_logging

    setup_logging()

    # Resolve the UI language (cfg.ui.language, default auto -> host locale)
    # before any user-facing text is emitted. Best-effort; never blocks.
    from winpodx.core.i18n import init_from_config

    init_from_config()

    # v0.2.1: pick up any pending setup steps from a partial install.sh
    # run BEFORE we route into the user's command. Skip on the
    # uninstall path so a half-installed system can still be torn
    # down cleanly. Skip on `--version` / no-arg help so quick
    # introspection isn't blocked behind a network probe.
    _maybe_resume_pending(argv)

    parser = argparse.ArgumentParser(
        prog="winpodx",
        description="Windows app integration for Linux desktop",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=_format_version_string(),
    )

    sub = parser.add_subparsers(dest="command")

    # --- app ---
    app_parser = sub.add_parser("app", help="Manage Windows applications")
    app_sub = app_parser.add_subparsers(dest="app_command")

    app_sub.add_parser("list", help="List available apps")

    refresh_p = app_sub.add_parser("refresh", help="Discover apps installed on the Windows pod")
    refresh_p.add_argument(
        "--json",
        action="store_true",
        help="Print discovered apps as JSON to stdout (human text to stderr)",
    )
    refresh_p.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Discovery timeout in seconds (default: 30)",
    )

    run_p = app_sub.add_parser("run", help="Run a Windows application")
    run_p.add_argument("name", help="App name or 'desktop'")
    run_p.add_argument("file", nargs="?", help="File to open")
    run_p.add_argument("--wait", action="store_true", help="Wait for app to exit")
    run_p.add_argument(
        "--extra-args",
        default="",
        metavar="ARGS",
        help=(
            "Extra FreeRDP flags appended to this launch only. Merged AFTER "
            "the global cfg.rdp.extra_flags so per-launch overrides win. "
            "Whitelisted flags only — see _BARE_FLAGS / _SIMPLE_VALUE_FLAGS "
            "in core/rdp.py. Useful for debugging codec issues, e.g. "
            '`--extra-args="/gfx:RFX"` to force RemoteFX and skip H.264 '
            "negotiation when the system FreeRDP build has experimental VAAPI "
            "(cachyos as of 2026-05-06 — RemoteApp dies at post_connect)."
        ),
    )

    inst_p = app_sub.add_parser("install", help="Install app into desktop")
    inst_p.add_argument("name", help="App name to install")
    inst_p.add_argument("--mime", action="store_true", help="Register MIME types")

    app_sub.add_parser("install-all", help="Install all apps into desktop")

    rm_p = app_sub.add_parser("remove", help="Remove app from desktop")
    rm_p.add_argument("name", help="App name to remove")

    app_sub.add_parser("sessions", help="Show active sessions")

    kill_p = app_sub.add_parser("kill", help="Kill an active session")
    kill_p.add_argument("name", help="Session app name to kill")

    # --- pod ---
    pod_parser = sub.add_parser("pod", help="Manage Windows pod")
    pod_sub = pod_parser.add_subparsers(dest="pod_command")

    start_p = pod_sub.add_parser("start", help="Start the pod")
    start_p.add_argument("--wait", action="store_true", help="Wait for pod to become ready")
    start_p.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Wait timeout in seconds (1-3600)",
    )
    start_p.add_argument(
        "--tuning",
        choices=["auto", "performance", "safe", "off", "manual"],
        default=None,
        help=(
            "One-shot override of cfg.pod.tuning_profile for this invocation. "
            "Does not persist to winpodx.toml. `performance` = auto + force CPU "
            "pinning + no-balloon regardless of host idle headroom. Useful for "
            "A/B-testing a profile without committing to it. See `winpodx info` "
            "for what each profile would resolve to on this host."
        ),
    )

    pod_sub.add_parser("stop", help="Stop the pod")
    pod_sub.add_parser("status", help="Show pod status")
    pod_sub.add_parser("restart", help="Restart the pod")
    recreate_p = pod_sub.add_parser(
        "recreate",
        help=(
            "Regenerate compose.yaml from current config + destroy and "
            "re-create the container (Windows disk preserved). Use after "
            "editing first-boot env knobs (language, region, keyboard, "
            "timezone, edition, backend) so the new values reach dockur. "
            "Note: dockur honors language / region / keyboard / edition "
            "only on the *initial* Windows install; recreating with these "
            "changed but the disk preserved will not re-run Sysprep. Pass "
            "--wipe-storage to also destroy the Windows disk and trigger "
            "a fresh install (~10 minutes)."
        ),
    )
    recreate_p.add_argument(
        "--wipe-storage",
        action="store_true",
        help=(
            "Also destroy the Windows storage volume / bind-mount so dockur "
            "re-runs the full Windows install. Required for language / "
            "edition changes to actually reach the guest. ~10 minute cost."
        ),
    )
    pod_sub.add_parser(
        "apply-fixes",
        help=(
            "Apply Windows-side runtime fixes (RDP timeouts, NIC power-save, "
            "TermService recovery, MaxSessions) to the existing pod. "
            "Idempotent — safe to run any time."
        ),
    )
    sync_p = pod_sub.add_parser(
        "sync-password",
        help=(
            "Re-sync the Windows guest's account password to the value in "
            "winpodx config. Use when password rotation has drifted (cfg "
            "and Windows disagree). Prompts for the last-known-working "
            "password to authenticate one final time."
        ),
    )
    sync_p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Read the recovery password from $WINPODX_RECOVERY_PASSWORD env var.",
    )
    multi_p = pod_sub.add_parser(
        "multi-session",
        help=(
            "Toggle the bundled rdprrap multi-session RDP patch. "
            "{on|off|status} — enables/disables independent RemoteApp "
            "sessions. Requires rdprrap-conf to be present in the guest "
            "(installed by OEM bundle since v0.1.6)."
        ),
    )
    multi_p.add_argument(
        "action",
        choices=("on", "off", "status"),
        help="on = enable multi-session, off = disable, status = report current state",
    )
    wait_p = pod_sub.add_parser(
        "wait-ready",
        help=(
            "Wait until the Windows VM has finished first-boot setup and "
            "the FreeRDP RemoteApp channel is responsive. Used by install.sh "
            "and useful after a cold `pod start`."
        ),
    )
    wait_p.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Maximum seconds to wait (default 600 = 10 minutes).",
    )
    wait_p.add_argument(
        "--logs",
        action="store_true",
        help="Tail container logs while waiting so the user sees Windows boot progress.",
    )
    wait_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help=(
            "With --logs, print raw container output. Without it (default), the "
            "Windows ISO download collapses to a clean progress line and UEFI boot "
            "noise is hidden."
        ),
    )

    from winpodx.cli.pod_install_resume import add_subcommand as add_install_resume
    from winpodx.cli.pod_install_status import add_subcommand as add_install_status

    add_install_status(pod_sub)
    add_install_resume(pod_sub)

    pod_sub.add_parser(
        "recover-oem",
        help=(
            "Re-stage C:\\OEM in the Windows guest when dockur's automatic "
            "first-boot OEM copy failed (#287). Tars /oem inside the "
            "container, starts an HTTP server, and prints the noVNC "
            "PowerShell commands the user must paste to download + run "
            "install.bat manually. podman/docker backends only."
        ),
    )

    grow_p = pod_sub.add_parser(
        "grow-disk",
        help=(
            "Grow the Windows virtual disk and extend C: to fill it (#318). "
            "Bumps disk_size (capped at disk_max_size), recreates the "
            "container so dockur grows the image, then extends C:. Windows "
            "data is preserved. podman/docker backends only."
        ),
    )
    grow_p.add_argument(
        "size",
        nargs="?",
        default=None,
        metavar="SIZE",
        help=(
            "Absolute target size (e.g. 128G). Omit to add the auto-grow "
            "increment (default 32G) to the current size."
        ),
    )
    grow_p.add_argument(
        "--increment",
        default=None,
        metavar="SIZE",
        help="Amount to add to the current size instead of the configured default (e.g. 64G).",
    )
    grow_p.add_argument(
        "--extend-only",
        action="store_true",
        help=(
            "Skip the resize; only extend C: into existing unallocated space "
            "(finishes a grow whose guest wasn't responsive yet)."
        ),
    )
    grow_p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )

    pod_sub.add_parser(
        "disk-usage",
        help="Show the Windows C: drive size / free / used%% and auto-grow status (#318).",
    )

    sync_p = pod_sub.add_parser(
        "sync-guest",
        help=(
            "Push refreshed guest artifacts (agent.ps1, urlacl, rdprrap/shim, "
            "registry fixes) into the running guest after a host upgrade, "
            "instead of a wipe-reinstall. Runs automatically on pod start when "
            "the guest is older than the host. podman/docker only."
        ),
    )
    sync_p.add_argument(
        "--force",
        action="store_true",
        help="Re-sync even when the guest version stamp already matches the host.",
    )

    # --- config ---
    cfg_parser = sub.add_parser("config", help="Manage configuration")
    cfg_sub = cfg_parser.add_subparsers(dest="config_command")

    cfg_sub.add_parser("show", help="Show current config")

    set_p = cfg_sub.add_parser("set", help="Set a config value")
    set_p.add_argument("key", help="e.g. rdp.user, pod.backend")
    set_p.add_argument(
        "value",
        nargs="?",
        default=None,
        help=(
            "New value to set. Omit when passing --auto. Reserved sentinel "
            "values: explicit empty string ('') stores the empty default."
        ),
    )
    set_p.add_argument(
        "--auto",
        action="store_true",
        help=(
            "Use the host-detected value for this key instead of supplying "
            "one positionally. Currently supported keys: pod.timezone (host "
            "IANA zone from timedatectl / /etc/localtime / /etc/timezone, "
            "translated to a Windows TZ ID via the CLDR table). Other keys "
            "will gain auto-detect in follow-up phases of #254."
        ),
    )

    cfg_sub.add_parser("import", help="Import winapps.conf")

    # --- setup ---
    setup_p = sub.add_parser("setup", help="Run setup wizard")
    setup_p.add_argument("--backend", choices=["podman", "docker", "libvirt", "manual"])
    setup_p.add_argument(
        "--freerdp-source",
        choices=["auto", "native", "flatpak"],
        default=None,
        help=(
            "Which FreeRDP client the launcher prefers: auto (Flatpak when "
            "present, else native), native, or flatpak. Stored in "
            "cfg.rdp.freerdp_source."
        ),
    )
    setup_p.add_argument(
        "--multimon",
        choices=["span", "off", "multimon"],
        default=None,
        help=(
            "Multi-monitor RAIL strategy: span (default — size the session "
            "desktop to the host monitor bounding box so a window dragged to "
            "another monitor keeps input), off (single-monitor desktop; use "
            "for non-rectangular layouts), or multimon (full MonitorDefArray; "
            "breaks RAIL input — diagnosis only). Stored in cfg.rdp.multimon."
        ),
    )
    # Curated edition list pulled from winpodx.core.config.WIN_VERSION_LABELS
    # so the help text stays in sync with the validator and the GUI dropdown.
    from winpodx.core.config import known_win_version_codes

    _curated_editions = " | ".join(known_win_version_codes())
    setup_p.add_argument(
        "--win-version",
        metavar="EDITION",
        help=(
            "Windows edition to install (passed to dockur via VERSION env "
            f"var). Curated set: {_curated_editions}. Other values pass "
            "through to dockur with a warning — see ARCHITECTURE.md for "
            "the custom-ISO workaround. Only takes effect on fresh installs "
            "(no existing winpodx.toml); for existing installs use the GUI "
            "Settings → Windows Edition picker or edit winpodx.toml."
        ),
    )
    # --non-interactive: pre-#255 used to be the way to skip prompts.
    # As of #255, ``winpodx setup`` is non-interactive by default, so
    # this flag is a deprecated alias kept for back-compat with
    # install.sh, packaging scripts, and CI callers. The new opposite
    # is ``--customize``.
    setup_p.add_argument(
        "--non-interactive",
        action="store_true",
        help="DEPRECATED: setup is non-interactive by default since #255.",
    )
    setup_p.add_argument(
        "--customize",
        action="store_true",
        help=(
            "Wizard mode: walk every customizable knob (specs, edition, "
            "backend, locale, timezone, tuning profile, debloat preset, "
            "anti-detection) with host-detected defaults pre-filled. "
            "Press Enter to accept any value. Default is non-interactive "
            "auto setup; use this flag to opt into the wizard."
        ),
    )
    # --create-only was removed in 0.6.0 (item B). The post-create
    # provisioning chain (wait-ready → apply-fixes → discovery →
    # reverse-open) is now the single `winpodx provision` command, so
    # install.sh runs `winpodx setup ... && winpodx provision --verbose`
    # instead of carrying its own bash copy. A standalone `winpodx setup`
    # always finishes the full flow.
    setup_p.add_argument(
        "--update-image",
        action="store_true",
        help=(
            "Pull the latest dockur/windows from docker.io, resolve its "
            "digest, and pin cfg.pod.image to it. The next `winpodx pod "
            "start` will recreate the container so the new image takes "
            "effect (volume preserved — ~30 s, no ISO redownload). "
            "Without this flag, the bundled DOCKUR_IMAGE_PIN stays in "
            "place across upgrades."
        ),
    )
    setup_p.add_argument(
        "--migrate-storage",
        action="store_true",
        help=(
            "Move the Windows VM disk image from the legacy "
            "`winpodx-data` named volume to a per-user bind mount "
            "(~/.local/share/winpodx/storage by default), applying "
            "`chattr +C` automatically on btrfs so the raw disk image "
            "bypasses Copy-on-Write fragmentation. The Windows install "
            "is preserved (rsync copy, no Sysprep redo, no ISO "
            "redownload). Cost: ~5-10 min on NVMe + 2× volume size in "
            "free space during the copy. Existing pods that were "
            "created before this option existed need this once."
        ),
    )
    setup_p.add_argument(
        "--migrate-storage-target",
        metavar="PATH",
        help=(
            "Override the bind-mount destination for `--migrate-storage` "
            "(absolute path; ~ expansion supported). Default: "
            "~/.local/share/winpodx/storage."
        ),
    )
    setup_p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompts (for migrate-storage etc).",
    )

    # --- other commands ---
    sub.add_parser("gui", help="Launch graphical interface (requires PySide6)")
    sub.add_parser("tray", help="Launch system tray icon")
    sub.add_parser("info", help="Show system information")
    autostart_p = sub.add_parser(
        "autostart",
        help=(
            "Toggle starting the Windows pod on login (opt-in, off by default). "
            "`on` installs the tray autostart entry + sets auto_start so the pod "
            "comes up at login; `off` disables it; `status` reports state."
        ),
    )
    autostart_p.add_argument(
        "action",
        nargs="?",
        choices=("on", "off", "status"),
        default="status",
        help="on / off / status (default: status)",
    )

    language_p = sub.add_parser(
        "language",
        help=(
            "Show or set the winpodx UI language (tray / GUI / CLI text). "
            "No arg = show current. 'auto' = follow the host locale ($LANG)."
        ),
    )
    language_p.add_argument(
        "code",
        nargs="?",
        choices=("auto", "en", "ko", "zh", "ja", "de", "fr", "it"),
        default=None,
        help="auto / en / ko / zh / ja / de / fr / it (omit to show current)",
    )
    check_p = sub.add_parser(
        "check",
        help="Run all health probes (pod, RDP, agent, password age, disk, …)",
    )
    check_p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of the human-readable report",
    )
    sub.add_parser("cleanup", help="Remove Office lock files")
    sub.add_parser("timesync", help="Force Windows time sync")
    debloat_p = sub.add_parser(
        "debloat",
        help="Run Windows debloat. Default = normal preset (telemetry + ads).",
    )
    debloat_p.add_argument(
        "--list",
        action="store_true",
        help="Print the item catalog + preset definitions and exit.",
    )
    debloat_p.add_argument(
        "--preset",
        choices=["normal", "full", "performance", "speed"],
        default=None,
        help=(
            "Run a curated preset. normal = telemetry + ads (current default); "
            "full = + onedrive / web_search / widgets / scheduled_tasks; "
            "performance = + sysmain / startup_programs / visual_effects; "
            "speed = + search_indexing / transparency."
        ),
    )
    debloat_p.add_argument(
        "--items",
        default=None,
        help=(
            "Comma-separated list of debloat item names (see --list). "
            "Mutually exclusive with --preset; explicit list wins."
        ),
    )
    debloat_p.add_argument(
        "--undo",
        action="store_true",
        help=(
            "Run each selected item's undo script instead of its apply "
            "script. Items without an undo path (e.g. onedrive, "
            "startup_programs) are rejected with a clear error. "
            "Combine with --items <list> for targeted revert; combine "
            "with --preset <name> to undo a whole preset."
        ),
    )
    debloat_p.add_argument(
        "--menu",
        action="store_true",
        help=(
            "Open the interactive text-mode picker. Lists every catalog "
            "item with current selection state; commands: <N> toggle, "
            "'p <preset>' switch, 'a' apply, 'q' quit, 'h' help. "
            "Use this on SSH / TTY-only installs where the Qt GUI "
            "picker is unavailable."
        ),
    )

    sub.add_parser("rotate-password", help="Rotate Windows RDP password")

    doctor_p = sub.add_parser(
        "doctor",
        help=(
            "Diagnose common winpodx state issues (orphan container, stale "
            "config, missing deps, half-installed state, broken autostart). "
            "Read-only by default -- prints per-check findings + suggested "
            "next command; --fix runs idempotent auto-remediation. Exits "
            "non-zero on FAIL findings."
        ),
    )
    doctor_p.add_argument(
        "--json",
        action="store_true",
        help=(
            "Machine-readable output: emit the Finding list as a JSON array "
            "(each element has severity, title, detail, suggestion, fix_id keys)."
        ),
    )
    doctor_p.add_argument(
        "--quick",
        action="store_true",
        help=(
            "Skip the slow probes (container health / guest exec) and run "
            "only the cheap local checks: freerdp, kvm, backend-on-PATH, "
            "config-state, pending-setup, autostart, initialized-flag. "
            "Completes in < 1 s on most hosts."
        ),
    )
    doctor_p.add_argument(
        "--fix",
        action="store_true",
        help=(
            "Auto-remediate findings that have a known fixer (idempotent; "
            "no-op when already healthy), then re-probe and report fixed / "
            "still failing. Implies the slow probes."
        ),
    )

    host_p = sub.add_parser(
        "setup-host",
        help=(
            "Host-side setup wizard. Detects + (with --apply) fixes via "
            "pkexec the host bits the AppImage cannot bundle: kvm group "
            "membership, /etc/subuid + /etc/subgid entries for rootless "
            "podman, kvm module persistence. Safe on already-set-up "
            "hosts (no-op if everything's in place)."
        ),
    )
    host_p.add_argument(
        "--status",
        action="store_true",
        help="Print the host state report and exit. No mutation.",
    )
    host_p.add_argument(
        "--apply",
        action="store_true",
        help="Apply via pkexec without prompting for confirmation.",
    )

    unsub = sub.add_parser(
        "uninstall",
        help=(
            "Remove winpodx user state. Default: stop container, kill tray/GUI/listener, "
            "remove desktop entries/icons/data dir/autostart, keep config + container disk. "
            "--purge also removes container, podman volume, storage path, and config dir."
        ),
    )
    unsub.add_argument(
        "--purge",
        action="store_true",
        help=(
            "Full wipe: container stop+rm, podman volume rm, storage bind-mount contents "
            "rm, config dir rm. Matches 'apt purge' semantics."
        ),
    )
    unsub.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive prompts. Used by scripts / CI.",
    )

    power_p = sub.add_parser("power", help="Manage pod power state")
    power_p.add_argument("--suspend", action="store_true", help="Suspend (pause) the pod")
    power_p.add_argument("--resume", action="store_true", help="Resume the pod")

    migrate_p = sub.add_parser(
        "migrate",
        help="Post-upgrade wizard — show release notes and populate discovered apps",
    )
    migrate_p.add_argument(
        "--no-refresh",
        action="store_true",
        help="Skip the app-discovery prompt (still updates the version marker)",
    )
    migrate_p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable all prompts (for automation / CI)",
    )
    migrate_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help=(
            "Stream raw container logs during the wait-ready stage instead of "
            "the clean self-erasing line. Matches `winpodx provision --verbose`."
        ),
    )

    # --- provision (post-create provisioning chain, 0.6.0 item B) ---
    # The single source of truth for wait-ready → agent-settle → apply-fixes
    # → discovery → reverse-open. Replaces install.sh's ~140 lines of bash,
    # setup_cmd's _run_full_provision, and (via the helper) feeds migrate /
    # pending.resume. Run with no flags it reproduces the install.sh defaults
    # so an AppImage's first run can just `winpodx provision`.
    provision_p = sub.add_parser(
        "provision",
        help=(
            "Finish provisioning a created pod: wait for Windows first-boot, "
            "apply runtime fixes, discover apps, set up reverse-open. Run with "
            "no flags it reproduces install.sh's post-create behaviour."
        ),
    )
    provision_p.add_argument(
        "--wait-timeout",
        type=int,
        default=3600,
        help="Seconds to wait for the Windows guest to become responsive (default 3600).",
    )
    provision_p.add_argument(
        "--require-agent",
        action="store_true",
        help=(
            "Hard-gate on the in-guest agent /health and fail (don't fall "
            "back to FreeRDP) if it never answers. Off by default."
        ),
    )
    provision_p.add_argument(
        "--no-discovery",
        action="store_true",
        help="Skip the app-discovery stage (on by default).",
    )
    provision_p.add_argument(
        "--no-reverse-open",
        action="store_true",
        help=("Skip reverse-open setup (on by default when cfg.reverse_open.enabled)."),
    )
    provision_p.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Discovery retry attempts with exponential backoff (default 2).",
    )
    provision_p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Stream raw per-stage progress to stderr.",
    )

    # --- guest (guest-side operations, 0.6.0 item G) ---
    # Canonical home for operations that run inside the Windows guest.
    # The old `pod <x>` forms keep working via deprecation aliases.
    guest_parser = sub.add_parser("guest", help="Guest-side operations (apply-fixes, sync, …)")
    guest_sub = guest_parser.add_subparsers(dest="guest_command")

    guest_sub.add_parser(
        "apply-fixes",
        help=(
            "Apply Windows-side runtime fixes (RDP timeouts, NIC power-save, "
            "TermService recovery, MaxSessions) to the existing pod. "
            "Idempotent — safe to run any time."
        ),
    )
    guest_sync_p = guest_sub.add_parser(
        "sync",
        help=(
            "Push refreshed guest artifacts (agent.ps1, urlacl, rdprrap/shim, "
            "registry fixes) into the running guest after a host upgrade, "
            "instead of a wipe-reinstall. Runs automatically on pod start when "
            "the guest is older than the host. podman/docker only."
        ),
    )
    guest_sync_p.add_argument(
        "--force",
        action="store_true",
        help="Re-sync even when the guest version stamp already matches the host.",
    )
    guest_syncpw_p = guest_sub.add_parser(
        "sync-password",
        help=(
            "Re-sync the Windows guest's account password to the value in "
            "winpodx config. Use when password rotation has drifted."
        ),
    )
    guest_syncpw_p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Read the recovery password from $WINPODX_RECOVERY_PASSWORD env var.",
    )
    guest_multi_p = guest_sub.add_parser(
        "multi-session",
        help=(
            "Toggle the bundled rdprrap multi-session RDP patch. "
            "{on|off|status} — enables/disables independent RemoteApp sessions."
        ),
    )
    guest_multi_p.add_argument(
        "action",
        choices=("on", "off", "status"),
        help="on = enable multi-session, off = disable, status = report current state",
    )
    guest_sub.add_parser(
        "recover-oem",
        help=(
            "Re-stage C:\\OEM in the Windows guest when dockur's automatic "
            "first-boot OEM copy failed. podman/docker backends only."
        ),
    )

    # --- install (install + storage state, 0.6.0 item G) ---
    # Canonical home for install progress and disk operations.
    # Distinct from `winpodx app install` which stays under `app`.
    # The old `pod <x>` forms keep working via deprecation aliases.
    install_group_parser = sub.add_parser(
        "install",
        help="Install progress and storage management (status, resume, grow-disk, disk-usage)",
    )
    install_group_sub = install_group_parser.add_subparsers(dest="install_command")

    from winpodx.cli.pod_install_resume import add_subcommand as _add_inst_resume
    from winpodx.cli.pod_install_status import add_subcommand as _add_inst_status

    _add_inst_status(install_group_sub, name="status")
    _add_inst_resume(install_group_sub, name="resume")

    install_grow_p = install_group_sub.add_parser(
        "grow-disk",
        help=(
            "Grow the Windows virtual disk and extend C: to fill it. podman/docker backends only."
        ),
    )
    install_grow_p.add_argument(
        "size",
        nargs="?",
        default=None,
        metavar="SIZE",
        help=(
            "Absolute target size (e.g. 128G). Omit to add the auto-grow "
            "increment (default 32G) to the current size."
        ),
    )
    install_grow_p.add_argument(
        "--increment",
        default=None,
        metavar="SIZE",
        help="Amount to add to the current size instead of the configured default.",
    )
    install_grow_p.add_argument(
        "--extend-only",
        action="store_true",
        help="Skip the resize; only extend C: into existing unallocated space.",
    )
    install_grow_p.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    install_group_sub.add_parser(
        "disk-usage",
        help="Show the Windows C: drive size / free / used%% and auto-grow status.",
    )

    # --- host-open (reverse file associations, #48) ---
    from winpodx.cli.host_open import add_subcommand as add_host_open

    add_host_open(sub)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    # First-run prompt (#255): fire before tray spawn / dispatch when
    # the user hasn't run setup yet. Skipped for introspection /
    # config / uninstall / gui / tray commands and for non-TTY stdin.
    # When the prompt runs and the user picks setup, control returns
    # here so the original command proceeds against a now-initialized
    # pod.
    try:
        from winpodx.cli.first_run import maybe_run_first_run_prompt

        maybe_run_first_run_prompt(args.command)
    except Exception:  # noqa: BLE001 -- never block the CLI on the prompt itself
        pass

    # Best-effort: ensure the tray subprocess is up before dispatching
    # any CLI command. The tray is the only driver for the UNRESPONSIVE
    # auto-recovery flow, so even users who only ever touch winpodx via
    # the CLI get the same idle-stall recovery the GUI users get.
    # ``setup`` / ``gui`` / ``tray`` skip this:
    #   * ``setup`` runs during install before the tray is wanted;
    #   * ``gui`` spawns the tray itself from run_gui();
    #   * ``tray`` IS the tray -- spawning a second copy is what
    #     tray_spawn's pgrep + tray.py's flock are guarding against, but
    #     short-circuiting here is cheaper than relying on those.
    if args.command not in ("setup", "gui", "tray"):
        try:
            from winpodx.desktop.tray_spawn import maybe_spawn_tray

            maybe_spawn_tray()
        except Exception:  # noqa: BLE001 — best-effort, never crash CLI
            pass

    _dispatch(args)


def _emit_deprecation(old: str, new: str) -> None:
    """Print a one-line deprecation notice to stderr.

    Both the old and new names are full command paths, e.g.
    ``"pod apply-fixes"`` and ``"guest apply-fixes"``.
    The notice is the only output side-effect; the caller is responsible
    for invoking the new handler afterwards.
    """
    print(
        f"[deprecated] 'winpodx {old}' will be removed in 0.7.0; use 'winpodx {new}'",
        file=sys.stderr,
    )


def _dispatch(args: argparse.Namespace) -> None:
    """Route parsed args to the appropriate handler."""
    cmd = args.command

    if cmd == "app":
        from winpodx.cli.app import handle_app

        handle_app(args)
    elif cmd == "pod":
        from winpodx.cli.pod import handle_pod

        handle_pod(args)
    elif cmd == "config":
        from winpodx.cli.config_cmd import handle_config

        handle_config(args)
    elif cmd == "setup":
        from winpodx.cli.setup_cmd import handle_setup

        handle_setup(args)
    elif cmd == "rotate-password":
        from winpodx.cli.setup_cmd import handle_rotate_password

        handle_rotate_password(args)
    elif cmd == "gui":
        try:
            from winpodx.gui.main_window import run_gui

            run_gui()
        except ImportError:
            print(tr("PySide6 required. Install with your package manager or: pip install PySide6"))
    elif cmd == "tray":
        from winpodx.desktop.tray import run_tray

        run_tray()
    elif cmd == "autostart":
        _cmd_autostart(getattr(args, "action", "status"))
    elif cmd == "language":
        _cmd_language(getattr(args, "code", None))
    elif cmd == "guest":
        from winpodx.cli.guest import handle_guest

        handle_guest(args)
    elif cmd == "install":
        from winpodx.cli.install_cmd import handle_install_group

        handle_install_group(args)
    elif cmd == "info":
        _emit_deprecation("info", "doctor")
        _cmd_info()
    elif cmd == "check":
        _emit_deprecation("check", "doctor")
        sys.exit(_cmd_check(args))
    elif cmd == "cleanup":
        _cmd_cleanup()
    elif cmd == "timesync":
        _cmd_timesync()
    elif cmd == "debloat":
        _cmd_debloat(args)
    elif cmd == "uninstall":
        _cmd_uninstall(args)
    elif cmd == "doctor":
        from winpodx.cli.doctor import handle_doctor

        handle_doctor(args)
    elif cmd == "setup-host":
        from winpodx.setup_wizard.__main__ import main as setup_host_main

        wizard_argv: list[str] = []
        if getattr(args, "status", False):
            wizard_argv.append("--status")
        if getattr(args, "apply", False):
            wizard_argv.append("--apply")
        sys.exit(setup_host_main(wizard_argv))
    elif cmd == "power":
        _cmd_power(args)
    elif cmd == "migrate":
        from winpodx.cli.migrate import run_migrate

        sys.exit(run_migrate(args))
    elif cmd == "provision":
        sys.exit(_cmd_provision(args))
    elif cmd == "host-open":
        from winpodx.cli.host_open import handle as handle_host_open

        sys.exit(handle_host_open(args))


def _cmd_provision(args: argparse.Namespace) -> int:
    """Drive ``core.provisioner.finish_provisioning`` from the CLI (item B).

    Flags map 1:1 onto the helper parameters. Run with no flags it
    reproduces install.sh's post-create defaults (wait 3600s, soft agent
    settle, discovery on with 6× retry, reverse-open on when enabled), so
    an AppImage first run can simply ``winpodx provision`` for the
    install.sh UX without re-implementing it (item I AppImage parity).
    """
    from winpodx.core.config import Config
    from winpodx.core.provisioner import (
        ProvisionAgentUnavailable,
        finish_provisioning,
    )

    cfg = Config.load()

    if cfg.pod.backend not in ("podman", "docker"):
        print(
            tr("provision not supported for backend {backend} (podman/docker only).").format(
                backend=repr(cfg.pod.backend)
            )
        )
        return 2

    verbose = bool(getattr(args, "verbose", False))

    def _on_progress(stage: str, detail: str) -> None:
        # Always surface stage transitions; --verbose just keeps the raw
        # detail lines flowing without buffering.
        print(f"  [{stage}] {detail}", file=sys.stderr, flush=verbose)

    def _rich_wait(_cfg: Config, timeout: int) -> bool:
        # Route the wait-ready stage through the log-streaming wait so a fresh
        # install shows the live download/boot progress + the wget-ETA dynamic
        # deadline extension for slow links (#126) — not a silent multi-minute
        # hang. Injected here so core/provisioner stays free of any cli import.
        # `_wait_ready` returns on ready and sys.exit(2/3) on a hard failure;
        # map that exit to False so finish_provisioning records "timeout"
        # instead of the process dying mid-chain.
        from winpodx.cli.pod import _wait_ready

        try:
            _wait_ready(timeout, show_logs=True, verbose=verbose)
            return True
        except SystemExit as exc:
            return exc.code in (0, None)

    with_reverse_open = not bool(getattr(args, "no_reverse_open", False))
    with_discovery = not bool(getattr(args, "no_discovery", False))

    try:
        results = finish_provisioning(
            cfg,
            wait_timeout=int(getattr(args, "wait_timeout", 3600)),
            require_agent=bool(getattr(args, "require_agent", False)),
            with_reverse_open=with_reverse_open,
            with_discovery=with_discovery,
            retries=int(getattr(args, "retries", 2)),
            on_progress=_on_progress,
            wait_fn=_rich_wait,
        )
    except ProvisionAgentUnavailable as e:
        # Agent-first deferral (Stage 2 settle OR discovery): exit 5 so
        # install.sh maps it to the pending machinery (#271 deferred behaviour).
        print(tr("provision deferred: {error}").format(error=e), file=sys.stderr)
        return 5

    if results.get("wait_ready") == "timeout":
        print(
            tr(
                "provision: Windows guest did not become responsive in time. "
                "Re-run `winpodx provision` once `winpodx pod status` reports the "
                "pod is fully up."
            ),
            file=sys.stderr,
        )
        return 4

    print(tr("Provisioning complete."))
    for stage, status in results.items():
        if isinstance(status, dict):
            # apply_fixes carries a {helper: status_str} map; render it as a
            # compact "N/N fixes OK" when every helper succeeded, else a joined
            # "k: v, k: v" string (same form as provisioner.py:515) so no raw
            # Python dict repr leaks into user output.
            total = len(status)
            ok_count = sum(1 for v in status.values() if v == "ok")
            if total and ok_count == total:
                rendered = f"{ok_count}/{total} fixes OK"
            else:
                rendered = ", ".join(f"{k}: {v}" for k, v in status.items())
            print(f"  {stage}: {rendered}")
        else:
            print(f"  {stage}: {status}")
    return 0


def _cmd_language(code: str | None) -> None:
    """Show or set the winpodx UI language."""
    from winpodx.core.config import Config
    from winpodx.core.i18n import current_language, resolve_language, set_language

    cfg = Config.load()
    if code is None:
        configured = cfg.ui.language
        resolved = resolve_language(configured)
        print(
            f"UI language: {configured}"
            + (f" (resolved: {resolved})" if configured == "auto" else "")
        )
        print("Available: auto, en, ko, zh, ja, de, fr, it")
        print("Set with: winpodx language <code>")
        return
    cfg.ui.language = code
    cfg.save()
    set_language(code)
    print(f"UI language set to {code} (resolved: {current_language()}).")
    print("Applies to new winpodx processes (restart the tray / GUI to see it).")


def _cmd_autostart(action: str) -> None:
    """on/off/status for login pod auto-start (opt-in)."""
    from winpodx.desktop.autostart import (
        is_autostart_enabled,
        is_tray_autostart_enabled,
        set_autostart,
    )

    if action == "on":
        set_autostart(True)
        print(tr("Autostart ON: the Windows pod will start when you log in."))
        print(tr("  (tray autostart entry installed + auto_start enabled)"))
    elif action == "off":
        set_autostart(False)
        print(tr("Autostart OFF: the pod will not start on login."))
    else:  # status
        on = is_autostart_enabled()
        print(tr("Login pod auto-start: {state}").format(state="ON" if on else "OFF"))
        tray_state = tr("present") if is_tray_autostart_enabled() else tr("absent")
        print(tr("  tray autostart entry: {state}").format(state=tray_state))
        if not on:
            print(tr("  enable with: winpodx autostart on"))


def _cmd_info() -> None:
    from winpodx.core.config import Config
    from winpodx.core.info import gather_info

    print(tr("=== winpodx system info ===\n"))

    cfg = Config.load()
    info = gather_info(cfg)

    sys_ = info["system"]
    print(tr("[System]"))
    print(f"  winpodx:        {sys_['winpodx']}")
    print(f"  Install:        {sys_.get('install_source', '(unknown)')}")
    print(f"  OEM bundle:     {sys_['oem_bundle']}")
    print(f"  rdprrap:        {sys_['rdprrap']}")
    print(f"  Distro:         {sys_['distro']}")
    print(f"  Kernel:         {sys_['kernel']}")
    print()

    disp = info["display"]
    print(tr("[Display]"))
    print(f"  Session type:       {disp['session_type']}")
    print(f"  Desktop env:        {disp['desktop_environment']}")
    print(f"  Wayland FreeRDP:    {disp['wayland_freerdp']}")
    print(f"  Raw scale factor:   {disp['raw_scale']}")
    print(f"  RDP scale:          {disp['rdp_scale']}")
    print()

    print(tr("[Dependencies]"))
    for name, dep in info["dependencies"].items():
        status = "OK" if dep["found"] == "true" else "MISSING"
        path_info = f" ({dep['path']})" if dep["path"] else ""
        print(f"  {name:<15} [{status}]{path_info}")
    print()

    pod = info["pod"]
    print(tr("[Pod]"))
    print(f"  State:              {pod['state']}")
    if pod["uptime"]:
        print(f"  Started at:         {pod['uptime']}")
    print(
        f"  RDP {pod['rdp_port']:<5}        "
        f"{'reachable' if pod['rdp_reachable'] else 'unreachable'}"
    )
    print(
        f"  VNC {pod['vnc_port']:<5}        "
        f"{'reachable' if pod['vnc_reachable'] else 'unreachable'}"
    )
    print(f"  Active sessions:    {pod['active_sessions']}")
    print()

    conf = info["config"]
    print(tr("[Config]"))
    print(f"  Path:          {conf['path']}")
    print(f"  Backend:       {conf['backend']}")
    print(f"  IP:            {conf['ip']}:{conf['port']}")
    print(f"  User:          {conf['user']}")
    print(f"  Scale:         {conf['scale']}%")
    print(f"  Idle:          {conf['idle_timeout']}s")
    print(f"  Max sessions:  {conf['max_sessions']}")
    print(f"  RAM (GB):      {conf['ram_gb']}")

    warning = conf.get("budget_warning") or ""
    if warning:
        print()
        print(tr("WARNING: {warning}").format(warning=warning), file=sys.stderr)

    print()
    print(tr("[Tuning]"))
    from winpodx.utils.specs import (
        detect_tuning_capability,
        format_tuning_summary,
        recommend_tuning_profile,
    )

    cap = detect_tuning_capability(vm_cpu_cores=cfg.pod.cpu_cores, vm_ram_gb=cfg.pod.ram_gb)
    profile = recommend_tuning_profile(cap, user_pref=cfg.pod.tuning_profile)
    print(format_tuning_summary(cap, profile))


_CHECK_GLYPHS: dict[str, str] = {
    "ok": "OK  ",
    "warn": "WARN",
    "fail": "FAIL",
    "skip": "SKIP",
}


def _cmd_check(args: argparse.Namespace) -> int:
    """Run every health probe and print a report.

    Exit code: 0 if no probe is ``fail``; 1 otherwise. ``warn`` is
    informational and does not change the exit code (so a CI smoke can
    still pass with a low-disk warning).
    """
    from winpodx.core import checks
    from winpodx.core.config import Config

    cfg = Config.load()
    probes = checks.run_all(cfg)

    if getattr(args, "json", False):
        import json

        out = {
            "overall": checks.overall(probes),
            "probes": [
                {
                    "name": p.name,
                    "status": p.status,
                    "detail": p.detail,
                    "duration_ms": p.duration_ms,
                }
                for p in probes
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        print(tr("=== winpodx check ===\n"))
        for p in probes:
            glyph = _CHECK_GLYPHS.get(p.status, p.status.upper())
            print(f"  [{glyph}] {p.name:<18} {p.detail}  ({p.duration_ms}ms)")
        print()
        print(tr("Overall: {overall}").format(overall=checks.overall(probes).upper()))

    return 0 if all(p.status != "fail" for p in probes) else 1


def _cmd_cleanup() -> None:
    from winpodx.core.daemon import cleanup_lock_files

    removed = cleanup_lock_files()
    if removed:
        for f in removed:
            print(f"  Removed: {f}")
        print(tr("\n{count} lock files cleaned up.").format(count=len(removed)))
    else:
        print(tr("No lock files found."))


def _cmd_timesync() -> None:
    from winpodx.core.config import Config
    from winpodx.core.daemon import sync_windows_time

    cfg = Config.load()
    if sync_windows_time(cfg):
        print(tr("Windows time synchronized."))
    else:
        print(tr("Time sync failed. Is the pod running?"))


def _cmd_debloat(args: argparse.Namespace) -> None:
    """Run debloat against the Windows VM (#247 phase 1).

    Switches on three argparse args:

      * ``--list``  -- print the catalog + presets and exit (no guest
                        traffic).
      * ``--preset`` / ``--items`` -- pick what to run. Resolution rules
                        live in ``winpodx.core.debloat.resolve_selection``;
                        defaults to the ``normal`` preset (telemetry +
                        ads) when both are absent for back-compat with
                        the pre-#247 ``winpodx debloat`` invocation.

    The selected items are concatenated into a single PowerShell
    payload by ``build_run_script`` and sent through the existing
    ``run_via_transport`` channel.
    """
    from winpodx.core.config import Config
    from winpodx.core.debloat import (
        DebloatCatalogError,
        build_run_script,
        build_undo_script,
        format_catalog_listing,
        load_catalog,
        resolve_selection,
    )

    try:
        catalog = load_catalog()
    except DebloatCatalogError as e:
        print(tr("Debloat catalog error: {error}").format(error=e))
        return

    if getattr(args, "list", False):
        print(format_catalog_listing(catalog))
        return

    if getattr(args, "menu", False):
        from winpodx.cli.debloat_menu import run_menu

        initial = getattr(args, "preset", None) or "normal"
        menu_selection = run_menu(catalog, initial_preset=initial)
        if menu_selection is None:
            # User quit.
            return
        selection = menu_selection
    else:
        raw_items = getattr(args, "items", None)
        items_list = (
            [name.strip() for name in raw_items.split(",") if name.strip()] if raw_items else None
        )
        try:
            selection = resolve_selection(
                catalog,
                preset=getattr(args, "preset", None),
                items=items_list,
            )
        except DebloatCatalogError as e:
            print(tr("Debloat selection error: {error}").format(error=e))
            return

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print(tr("Debloat only supported for Podman/Docker backends."))
        return

    undo = getattr(args, "undo", False)
    try:
        payload = (
            build_undo_script(catalog, selection) if undo else build_run_script(catalog, selection)
        )
    except DebloatCatalogError as e:
        print(tr("Debloat payload build error: {error}").format(error=e))
        return

    verb = "undo" if undo else "apply"
    description = f"debloat-{verb} (" + ",".join(selection) + ")"
    print(
        tr("Running debloat {verb} ({count} item(s); may take a minute)...").format(
            verb=verb, count=len(selection)
        )
    )
    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    try:
        result = run_via_transport(cfg, payload, description=description, timeout=300)
    except WindowsExecError as e:
        print(tr("Debloat channel failure: {error}").format(error=e))
        return

    if result.rc == 0:
        if result.stdout.strip():
            print(result.stdout.rstrip())
        print(tr("Debloat {verb} complete.").format(verb=verb))
    else:
        print(
            tr("Debloat {verb} failed (rc={rc}): {detail}").format(
                verb=verb, rc=result.rc, detail=result.stderr.strip() or result.stdout.strip()
            )
        )


def _cmd_power(args: argparse.Namespace) -> None:
    from winpodx.core.config import Config
    from winpodx.core.daemon import is_pod_paused, resume_pod, suspend_pod

    cfg = Config.load()

    if args.suspend:
        if suspend_pod(cfg):
            print(tr("Pod suspended (paused). CPU freed, memory retained."))
        else:
            print(tr("Failed to suspend pod."))
    elif args.resume:
        if resume_pod(cfg):
            print(tr("Pod resumed."))
        else:
            print(tr("Failed to resume pod."))
    else:
        paused = is_pod_paused(cfg)
        pod_state = tr("suspended") if paused else tr("active")
        print(tr("Pod power state: {state}").format(state=pod_state))


def _cmd_uninstall(args: argparse.Namespace) -> None:
    """Hand off to the canonical bash uninstaller (#255)."""
    from winpodx.cli.uninstall import handle_uninstall

    handle_uninstall(args)


def _format_version_string() -> str:
    """Render the ``winpodx --version`` line with install-source suffix.

    Example: ``winpodx 0.5.8 (installed via apt)``. Detection is
    best-effort; on any failure the suffix is omitted so the string
    falls back to plain ``winpodx <version>``.
    """
    try:
        from winpodx.utils.install_source import detect

        source = detect()
        return f"winpodx {__version__} ({source.label})"
    except Exception:  # noqa: BLE001
        return f"winpodx {__version__}"


def _maybe_resume_pending(argv: list[str] | None) -> None:
    """v0.2.1: detect a partial install (`.pending_setup` marker present)
    and resume the missing steps before the user's command runs.

    Skipped when the user is invoking `uninstall` / `--version` / `--help`
    so basic introspection and recovery aren't blocked behind a network
    probe. Best-effort; never raises.
    """
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        return
    # gui / tray have their own threaded resume in `_maybe_run_first_launch_checks`
    # — running it synchronously here would block the launcher for up to 5 min
    # while the user stares at no window. Skip-list them.
    skip_first = args[0].lstrip("-") in {
        "version",
        "help",
        "uninstall",
        "config",
        "info",
        "gui",
        "tray",
    }
    if skip_first:
        return
    try:
        from winpodx.utils.pending import has_pending, resume

        if has_pending():
            resume()
    except Exception:  # noqa: BLE001 — never block the user's command
        pass
