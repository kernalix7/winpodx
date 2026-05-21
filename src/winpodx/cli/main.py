# SPDX-License-Identifier: MIT
"""Main CLI entry point for winpodx, zero external dependencies."""

from __future__ import annotations

import argparse
import sys

from winpodx import __version__


def cli(argv: list[str] | None = None) -> None:
    """winpodx CLI entry point."""
    from winpodx.utils.logging import setup_logging

    setup_logging()

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
    parser.add_argument("--version", action="version", version=f"winpodx {__version__}")

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

    from winpodx.cli.pod_install_resume import add_subcommand as add_install_resume
    from winpodx.cli.pod_install_status import add_subcommand as add_install_status

    add_install_status(pod_sub)
    add_install_resume(pod_sub)

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
        "--win-version",
        metavar="EDITION",
        help=(
            "Windows edition to install (passed to dockur via VERSION env "
            "var). Curated set: 11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 "
            "| tiny10 | 2025 | 2022 | 2019 | 2016. Other values pass "
            "through to dockur with a warning — see ARCHITECTURE.md for "
            "the custom-ISO workaround. Only takes effect on fresh installs "
            "(no existing winpodx.toml); for existing installs use the GUI "
            "Settings → Windows Edition picker or edit winpodx.toml."
        ),
    )
    setup_p.add_argument("--non-interactive", action="store_true")
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

    sub.add_parser("rotate-password", help="Rotate Windows RDP password")

    unsub = sub.add_parser("uninstall", help="Remove winpodx files (keeps container)")
    unsub.add_argument("--purge", action="store_true", help="Also remove config")

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

    # --- host-open (reverse file associations, #48) ---
    from winpodx.cli.host_open import add_subcommand as add_host_open

    add_host_open(sub)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

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
            print("PySide6 required. Install with your package manager or: pip install PySide6")
    elif cmd == "tray":
        from winpodx.desktop.tray import run_tray

        run_tray()
    elif cmd == "info":
        _cmd_info()
    elif cmd == "check":
        sys.exit(_cmd_check(args))
    elif cmd == "cleanup":
        _cmd_cleanup()
    elif cmd == "timesync":
        _cmd_timesync()
    elif cmd == "debloat":
        _cmd_debloat(args)
    elif cmd == "uninstall":
        _cmd_uninstall(args)
    elif cmd == "power":
        _cmd_power(args)
    elif cmd == "migrate":
        from winpodx.cli.migrate import run_migrate

        sys.exit(run_migrate(args))
    elif cmd == "host-open":
        from winpodx.cli.host_open import handle as handle_host_open

        sys.exit(handle_host_open(args))


def _cmd_info() -> None:
    from winpodx.core.config import Config
    from winpodx.core.info import gather_info

    print("=== winpodx system info ===\n")

    cfg = Config.load()
    info = gather_info(cfg)

    sys_ = info["system"]
    print("[System]")
    print(f"  winpodx:        {sys_['winpodx']}")
    print(f"  OEM bundle:     {sys_['oem_bundle']}")
    print(f"  rdprrap:        {sys_['rdprrap']}")
    print(f"  Distro:         {sys_['distro']}")
    print(f"  Kernel:         {sys_['kernel']}")
    print()

    disp = info["display"]
    print("[Display]")
    print(f"  Session type:       {disp['session_type']}")
    print(f"  Desktop env:        {disp['desktop_environment']}")
    print(f"  Wayland FreeRDP:    {disp['wayland_freerdp']}")
    print(f"  Raw scale factor:   {disp['raw_scale']}")
    print(f"  RDP scale:          {disp['rdp_scale']}")
    print()

    print("[Dependencies]")
    for name, dep in info["dependencies"].items():
        status = "OK" if dep["found"] == "true" else "MISSING"
        path_info = f" ({dep['path']})" if dep["path"] else ""
        print(f"  {name:<15} [{status}]{path_info}")
    print()

    pod = info["pod"]
    print("[Pod]")
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
    print("[Config]")
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
        print(f"WARNING: {warning}", file=sys.stderr)

    print()
    print("[Tuning]")
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
        print("=== winpodx check ===\n")
        for p in probes:
            glyph = _CHECK_GLYPHS.get(p.status, p.status.upper())
            print(f"  [{glyph}] {p.name:<18} {p.detail}  ({p.duration_ms}ms)")
        print()
        print(f"Overall: {checks.overall(probes).upper()}")

    return 0 if all(p.status != "fail" for p in probes) else 1


def _cmd_cleanup() -> None:
    from winpodx.core.daemon import cleanup_lock_files

    removed = cleanup_lock_files()
    if removed:
        for f in removed:
            print(f"  Removed: {f}")
        print(f"\n{len(removed)} lock files cleaned up.")
    else:
        print("No lock files found.")


def _cmd_timesync() -> None:
    from winpodx.core.config import Config
    from winpodx.core.daemon import sync_windows_time

    cfg = Config.load()
    if sync_windows_time(cfg):
        print("Windows time synchronized.")
    else:
        print("Time sync failed. Is the pod running?")


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
        print(f"Debloat catalog error: {e}")
        return

    if getattr(args, "list", False):
        print(format_catalog_listing(catalog))
        return

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
        print(f"Debloat selection error: {e}")
        return

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print("Debloat only supported for Podman/Docker backends.")
        return

    undo = getattr(args, "undo", False)
    try:
        payload = (
            build_undo_script(catalog, selection) if undo else build_run_script(catalog, selection)
        )
    except DebloatCatalogError as e:
        print(f"Debloat payload build error: {e}")
        return

    verb = "undo" if undo else "apply"
    description = f"debloat-{verb} (" + ",".join(selection) + ")"
    print(f"Running debloat {verb} ({len(selection)} item(s); may take a minute)...")
    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    try:
        result = run_via_transport(cfg, payload, description=description, timeout=300)
    except WindowsExecError as e:
        print(f"Debloat channel failure: {e}")
        return

    if result.rc == 0:
        if result.stdout.strip():
            print(result.stdout.rstrip())
        print(f"Debloat {verb} complete.")
    else:
        print(
            f"Debloat {verb} failed (rc={result.rc}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def _cmd_power(args: argparse.Namespace) -> None:
    from winpodx.core.config import Config
    from winpodx.core.daemon import is_pod_paused, resume_pod, suspend_pod

    cfg = Config.load()

    if args.suspend:
        if suspend_pod(cfg):
            print("Pod suspended (paused). CPU freed, memory retained.")
        else:
            print("Failed to suspend pod.")
    elif args.resume:
        if resume_pod(cfg):
            print("Pod resumed.")
        else:
            print("Failed to resume pod.")
    else:
        paused = is_pod_paused(cfg)
        print(f"Pod power state: {'suspended' if paused else 'active'}")


def _cmd_uninstall(args: argparse.Namespace) -> None:
    import shutil

    from winpodx.utils.paths import applications_dir, config_dir, data_dir, icons_dir, runtime_dir

    purge = args.purge
    removed = 0

    app_dir = applications_dir()
    desktop_files = list(app_dir.glob("winpodx-*.desktop"))
    if desktop_files:
        for f in desktop_files:
            f.unlink()
        print(f"  Removed {len(desktop_files)} desktop entries")
        removed += len(desktop_files)

    icon_base = icons_dir()
    if icon_base.exists():
        for icon in icon_base.rglob("winpodx-*"):
            icon.unlink()
            removed += 1
        print(f"  Removed icons from {icon_base}")

    dd = data_dir()
    if dd.exists():
        shutil.rmtree(dd)
        print(f"  Removed {dd}")
        removed += 1

    rd = runtime_dir()
    if rd.exists():
        shutil.rmtree(rd)
        removed += 1

    cd = config_dir()
    if cd.exists():
        if purge:
            shutil.rmtree(cd)
            print(f"  Removed {cd}")
            removed += 1
        else:
            print(f"  Config preserved at {cd} (use --purge to remove)")

    from winpodx.core.config import Config as _Config

    _container = _Config.load().pod.container_name
    print(f"\nUninstall complete ({removed} items removed).")
    print(f"Container '{_container}' was NOT removed.")
    print(f"To remove it: podman stop {_container} && podman rm {_container}")


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
