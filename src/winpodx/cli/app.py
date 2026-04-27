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

    from winpodx.core.config import Config

    _KIND_TO_CODE = {
        "pod_not_running": 2,
        "unsupported_backend": 2,
        "script_missing": 3,
        "script_failed": 3,
        "bad_json": 3,
        "truncated": 3,
        "timeout": 4,
    }

    cfg = Config.load()

    # v0.2.0: stream per-source progress lines to stderr so the user sees
    # "Scanning Registry App Paths..." etc instead of a silent multi-second
    # pause. JSON output (when --json) stays clean — progress goes to stderr.
    def _on_progress(msg: str) -> None:
        print(f"  ... {msg}", file=sys.stderr, flush=True)

    try:
        apps = discover_apps(cfg, timeout=timeout, progress_callback=_on_progress)
    except DiscoveryError as exc:
        kind = getattr(exc, "kind", "")
        code = _KIND_TO_CODE.get(kind, 3)
        if kind == "pod_not_running":
            print("Pod is not running. Run 'winpodx pod start --wait' first.", file=sys.stderr)
        elif kind == "unsupported_backend":
            print(
                "Discovery requires podman or docker backend. Check 'winpodx config show'.",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(code)

    persist_discovered(apps)

    # v0.2.0.8: auto-register .desktop entries so discovered apps land in
    # the DE menu without a separate `winpodx app install-all` step.
    # Previous releases persisted app.toml + icons but never created the
    # XDG entries, so users saw "Discovered N apps" then no apps in the
    # menu. Best-effort — failures are logged but don't abort refresh.
    if apps:
        _register_desktop_entries(apps)

    if as_json:
        import json

        data = [
            {
                "name": a.name,
                "slug": a.slug,
                "full_name": a.full_name,
                "executable": a.executable,
                "args": a.args,
                "source": a.source,
                "launch_uri": a.launch_uri,
                "wm_class_hint": a.wm_class_hint,
                "icon_path": a.icon_path,
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


def _register_desktop_entries(discovered) -> None:
    """v0.2.0.8: install .desktop + icon-cache entries for freshly-discovered
    apps so they appear in the user's DE menu without a separate
    ``winpodx app install-all`` step.

    v0.2.0.9: bidirectional sync — also remove stale `winpodx-*.desktop`
    entries that no longer correspond to any app on disk. Without this,
    apps uninstalled from the Windows guest stayed in the user's DE
    menu indefinitely (kernalix7 reported on 2026-04-27: "예전에
    만들었다고 해서 없어졌는데 계속 남아있으면 안돼"). User-authored
    entries (under ~/.local/share/winpodx/apps/) are preserved.
    """
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry, remove_desktop_entry
    from winpodx.desktop.icons import update_icon_cache
    from winpodx.utils.paths import applications_dir

    discovered_slugs = {d.slug or d.name for d in discovered}
    available = {a.name: a for a in list_available_apps()}
    available_slugs = set(available)
    installed = 0
    for slug in discovered_slugs:
        info = available.get(slug)
        if info is None:
            continue
        try:
            install_desktop_entry(info)
            installed += 1
        except Exception as e:  # noqa: BLE001
            print(
                f"  warning: could not install desktop entry for {slug}: {e}",
                file=sys.stderr,
            )

    # v0.2.0.9: prune any winpodx-*.desktop file whose slug is not in the
    # current AppInfo set (covers both vanished discoveries and old
    # bundled / removed entries).
    removed = 0
    apps_dir = applications_dir()
    if apps_dir.exists():
        for entry in apps_dir.glob("winpodx-*.desktop"):
            stem = entry.stem  # winpodx-<slug>
            if not stem.startswith("winpodx-"):
                continue
            slug = stem[len("winpodx-") :]
            # Don't touch the GUI launcher itself or any winpodx-internal entry.
            if slug in {"", "gui", "launcher"}:
                continue
            if slug in available_slugs:
                continue
            try:
                remove_desktop_entry(slug)
                removed += 1
            except Exception as e:  # noqa: BLE001
                print(
                    f"  warning: could not remove stale entry {slug}: {e}",
                    file=sys.stderr,
                )

    if installed or removed:
        try:
            update_icon_cache()
        except Exception as e:  # noqa: BLE001
            print(f"  warning: icon cache refresh failed: {e}", file=sys.stderr)
    if installed:
        print(f"  Registered {installed} app(s) in your desktop menu.", file=sys.stderr)
    if removed:
        suffix = "y" if removed == 1 else "ies"
        print(f"  Removed {removed} stale desktop entr{suffix}.", file=sys.stderr)


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
