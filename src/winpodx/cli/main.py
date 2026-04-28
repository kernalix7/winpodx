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

    # --- config ---
    cfg_parser = sub.add_parser("config", help="Manage configuration")
    cfg_sub = cfg_parser.add_subparsers(dest="config_command")

    cfg_sub.add_parser("show", help="Show current config")

    set_p = cfg_sub.add_parser("set", help="Set a config value")
    set_p.add_argument("key", help="e.g. rdp.user, pod.backend")
    set_p.add_argument("value", help="New value to set")

    cfg_sub.add_parser("import", help="Import winapps.conf")

    # --- setup ---
    setup_p = sub.add_parser("setup", help="Run setup wizard")
    setup_p.add_argument("--backend", choices=["podman", "docker", "libvirt", "manual"])
    setup_p.add_argument("--non-interactive", action="store_true")

    # --- other commands ---
    sub.add_parser("gui", help="Launch graphical interface (requires PySide6)")
    sub.add_parser("tray", help="Launch system tray icon")
    sub.add_parser("info", help="Show system information")
    sub.add_parser("cleanup", help="Remove Office lock files")
    sub.add_parser("timesync", help="Force Windows time sync")
    sub.add_parser("debloat", help="Run Windows debloat script")

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

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

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
    elif cmd == "cleanup":
        _cmd_cleanup()
    elif cmd == "timesync":
        _cmd_timesync()
    elif cmd == "debloat":
        _cmd_debloat()
    elif cmd == "uninstall":
        _cmd_uninstall(args)
    elif cmd == "power":
        _cmd_power(args)
    elif cmd == "migrate":
        from winpodx.cli.migrate import run_migrate

        sys.exit(run_migrate(args))


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


def _cmd_debloat() -> None:
    """Run debloat.ps1 inside the Windows VM via FreeRDP RemoteApp.

    v0.1.9.5: was on the broken `podman cp + podman exec` path which
    couldn't reach the Windows VM. Now reads the script body locally
    and pipes it through ``windows_exec.run_in_windows``.
    """
    from pathlib import Path

    from winpodx.core.config import Config
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print("Debloat only supported for Podman/Docker backends.")
        return

    candidates = [
        Path(__file__).parent.parent.parent.parent / "scripts" / "windows" / "debloat.ps1",
        Path.home() / ".local" / "bin" / "winpodx-app" / "scripts" / "windows" / "debloat.ps1",
    ]
    script = next((p for p in candidates if p.exists()), None)
    if script is None:
        print(f"Debloat script not found in any of: {[str(p) for p in candidates]}")
        return

    try:
        payload = script.read_text(encoding="utf-8")
    except OSError as e:
        print(f"Cannot read debloat script {script}: {e}")
        return

    print("Running debloat (this may take a minute)...")
    try:
        result = run_in_windows(cfg, payload, description="debloat", timeout=180)
    except WindowsExecError as e:
        print(f"Debloat channel failure: {e}")
        return

    if result.rc == 0:
        if result.stdout.strip():
            print(result.stdout.rstrip())
        print("Debloat complete.")
    else:
        print(f"Debloat failed (rc={result.rc}): {result.stderr.strip() or result.stdout.strip()}")


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
    skip_first = args[0].lstrip("-") in {"version", "help", "uninstall", "config", "info"}
    if skip_first:
        return
    try:
        from winpodx.utils.pending import has_pending, resume

        if has_pending():
            resume()
    except Exception:  # noqa: BLE001 — never block the user's command
        pass
