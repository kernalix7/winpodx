"""Main CLI entry point for winpodx — zero external dependencies."""

from __future__ import annotations

import argparse

from winpodx import __version__


def cli(argv: list[str] | None = None) -> None:
    """winpodx — Windows app integration for Linux desktop."""
    from winpodx.utils.logging import setup_logging

    setup_logging()

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


def _cmd_info() -> None:
    from winpodx.core.config import Config
    from winpodx.display.detector import display_info
    from winpodx.display.scaling import detect_raw_scale, detect_scale_factor
    from winpodx.utils.deps import check_all

    print("=== winpodx system info ===\n")

    di = display_info()
    print("[Display]")
    print(f"  Session type:       {di['session_type']}")
    print(f"  Desktop env:        {di['desktop_environment']}")
    print(f"  Wayland FreeRDP:    {di['wayland_freerdp']}")
    print(f"  Raw scale factor:   {detect_raw_scale():.2f}")
    print(f"  RDP scale:          {detect_scale_factor()}%")
    print()

    print("[Dependencies]")
    deps = check_all()
    for name, dep in deps.items():
        status = "OK" if dep.found else "MISSING"
        path_info = f" ({dep.path})" if dep.path else ""
        print(f"  {name:<15} [{status}]{path_info}")
    print()

    cfg = Config.load()
    print("[Config]")
    print(f"  Path:     {Config.path()}")
    print(f"  Backend:  {cfg.pod.backend}")
    print(f"  IP:       {cfg.rdp.ip}:{cfg.rdp.port}")
    print(f"  User:     {cfg.rdp.user}")
    print(f"  Scale:    {cfg.rdp.scale}%")
    print(f"  Idle:     {cfg.pod.idle_timeout}s")


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
    import subprocess
    from pathlib import Path

    from winpodx.core.config import Config

    cfg = Config.load()
    if cfg.pod.backend not in ("podman", "docker"):
        print("Debloat only supported for Podman/Docker backends.")
        return

    script = Path(__file__).parent.parent.parent.parent / "scripts" / "windows" / "debloat.ps1"
    if not script.exists():
        print(f"Debloat script not found: {script}")
        return

    runtime = "podman" if cfg.pod.backend == "podman" else "docker"
    container = "winpodx-windows"

    print("Copying debloat script to Windows...")
    try:
        subprocess.run([runtime, "cp", str(script), f"{container}:C:/debloat.ps1"], check=True)
    except subprocess.CalledProcessError:
        print("Failed to copy script. Is the pod running? Try: winpodx pod start")
        return
    except FileNotFoundError:
        print(f"'{runtime}' not found. Is {cfg.pod.backend} installed?")
        return

    print("Running debloat (this may take a minute)...")
    try:
        result = subprocess.run(
            [
                runtime,
                "exec",
                container,
                "powershell",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                "C:\\debloat.ps1",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("Debloat timed out after 120 seconds.")
        return

    if result.returncode == 0:
        print(result.stdout)
        print("Debloat complete.")
    else:
        print(f"Debloat failed: {result.stderr}")


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

    # Desktop entries
    app_dir = applications_dir()
    desktop_files = list(app_dir.glob("winpodx-*.desktop"))
    if desktop_files:
        for f in desktop_files:
            f.unlink()
        print(f"  Removed {len(desktop_files)} desktop entries")
        removed += len(desktop_files)

    # Icons
    icon_base = icons_dir()
    if icon_base.exists():
        for icon in icon_base.rglob("winpodx-*"):
            icon.unlink()
            removed += 1
        print(f"  Removed icons from {icon_base}")

    # App definitions
    dd = data_dir()
    if dd.exists():
        shutil.rmtree(dd)
        print(f"  Removed {dd}")
        removed += 1

    # Runtime PID files
    rd = runtime_dir()
    if rd.exists():
        shutil.rmtree(rd)
        removed += 1

    # Config (only with --purge)
    cd = config_dir()
    if cd.exists():
        if purge:
            shutil.rmtree(cd)
            print(f"  Removed {cd}")
            removed += 1
        else:
            print(f"  Config preserved at {cd} (use --purge to remove)")

    print(f"\nUninstall complete ({removed} items removed).")
    print("Container 'winpodx-windows' was NOT removed.")
    print("To remove it: podman stop winpodx-windows && podman rm winpodx-windows")
