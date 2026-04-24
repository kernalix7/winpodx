"""CLI handlers for Windows app management."""

from __future__ import annotations

import argparse
import sys

from winpodx.core.app import _SAFE_NAME_RE


def _validate_app_name(name: str) -> None:
    """Validate app name format or exit with error."""
    if not name or not _SAFE_NAME_RE.match(name):
        print(f"Invalid app name: {name!r}", file=sys.stderr)
        sys.exit(1)


def handle_app(args: argparse.Namespace) -> None:
    """Route app subcommands."""
    cmd = args.app_command
    if cmd == "list":
        _list_apps()
    elif cmd == "refresh":
        _refresh_apps(getattr(args, "json", False), getattr(args, "timeout", 30))
    elif cmd == "run":
        _run_app(args.name, args.file, args.wait)
    elif cmd == "install":
        _install_app(args.name, getattr(args, "mime", False))
    elif cmd == "install-all":
        _install_all()
    elif cmd == "remove":
        _remove_app(args.name)
    elif cmd == "sessions":
        _show_sessions()
    elif cmd == "kill":
        _kill_session(args.name)
    else:
        print("Usage: winpodx app {list|refresh|run|install|install-all|remove|sessions|kill}")
        sys.exit(1)


def _list_apps() -> None:
    from winpodx.core.app import list_available_apps

    apps = list_available_apps()
    if not apps:
        print("No applications found. Run 'winpodx setup' first.")
        return

    print(f"{'Name':<20} {'Full Name':<30} {'Categories':<20}")
    print("-" * 70)
    for a in apps:
        cats = ", ".join(a.categories[:2]) if a.categories else ""
        print(f"{a.name:<20} {a.full_name:<30} {cats:<20}")


def _refresh_apps(as_json: bool, timeout: int) -> None:
    try:
        from winpodx.core.discovery import DiscoveryError, discover_apps, persist_discovered
    except ImportError:
        msg = "ERROR: core discovery module not available (winpodx may need updating)"
        print(msg, file=sys.stderr)
        sys.exit(1)

    _KIND_TO_CODE = {
        "pod_not_running": 2,
        "script_failed": 3,
        "bad_json": 3,
        "truncated": 3,
        "timeout": 4,
    }

    try:
        apps = discover_apps(timeout=timeout)
    except DiscoveryError as exc:
        kind = getattr(exc, "kind", "")
        code = _KIND_TO_CODE.get(kind, 3)
        if kind == "pod_not_running":
            print("Pod is not running. Run 'winpodx pod start --wait' first.", file=sys.stderr)
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(code)

    persist_discovered(apps)

    if as_json:
        import json

        data = [
            {
                "name": a.name,
                "slug": a.slug,
                "executable": a.executable,
                "launch_uri": a.launch_uri,
                "source": a.source,
                "icon_path": a.icon_path,
                "full_name": a.full_name,
            }
            for a in apps
        ]
        print(json.dumps(data))
        print(f"OK: discovered {len(apps)} app(s)", file=sys.stderr)
    else:
        if apps:
            print(f"Discovered {len(apps)} app(s):")
            for a in apps:
                print(f"  {a.name:<20} {a.full_name}")
        else:
            print("No apps discovered.")


def _run_app(name: str, file: str | None, wait: bool) -> None:
    from winpodx.core.provisioner import ProvisionError, ensure_ready
    from winpodx.core.rdp import launch_app, launch_desktop
    from winpodx.desktop.notify import notify_error

    try:
        cfg = ensure_ready()
    except (ProvisionError, RuntimeError) as e:
        notify_error(str(e))
        print(f"Setup error: {e}", file=sys.stderr)
        sys.exit(1)

    if name == "desktop":
        print("Launching Windows desktop...")
        session = launch_desktop(cfg)
        if wait and session.process:
            session.process.wait()
        return

    from winpodx.core.app import find_app

    app_info = find_app(name)
    if not app_info:
        print(f"Unknown app: {name}. Run 'winpodx app list' to see available apps.")
        sys.exit(1)

    try:
        session = launch_app(cfg, app_info.executable, file)

        if wait and session.process:
            session.process.wait()
            session.pid_file.unlink(missing_ok=True)
    except RuntimeError as e:
        notify_error(str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _install_app(name: str, mime: bool) -> None:
    from winpodx.core.app import find_app
    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.mime import register_mime_types

    app_info = find_app(name)
    if not app_info:
        print(f"Unknown app: {name}")
        sys.exit(1)

    path = install_desktop_entry(app_info)
    print(f"Installed {app_info.full_name} → {path}")

    if mime and app_info.mime_types:
        register_mime_types(app_info)
        print(f"Registered MIME types: {', '.join(app_info.mime_types)}")


def _install_all() -> None:
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.icons import update_icon_cache

    apps = list_available_apps()
    if not apps:
        print("No apps available.")
        return

    for a in apps:
        install_desktop_entry(a)
        print(f"  Installed {a.full_name}")

    update_icon_cache()
    print(f"\n{len(apps)} apps installed into desktop environment.")


def _remove_app(name: str) -> None:
    _validate_app_name(name)
    from winpodx.desktop.entry import remove_desktop_entry

    remove_desktop_entry(name)
    print(f"Removed {name} from desktop environment.")


def _show_sessions() -> None:
    from winpodx.core.process import list_active_sessions

    active = list_active_sessions()
    if not active:
        print("No active sessions.")
        return

    print(f"{'App':<20} {'PID':<10} {'Status':<10}")
    print("-" * 40)
    for s in active:
        print(f"{s.app_name:<20} {s.pid:<10} {'alive' if s.is_alive else 'dead':<10}")


def _kill_session(name: str) -> None:
    _validate_app_name(name)
    from winpodx.core.process import kill_session

    if kill_session(name):
        print(f"Killed session: {name}")
    else:
        print(f"No active session found: {name}")
