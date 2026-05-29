# SPDX-License-Identifier: MIT
"""CLI handlers for Windows app management."""

from __future__ import annotations

import argparse
import sys

from winpodx.core.app import _SAFE_NAME_RE
from winpodx.core.i18n import tr


def _validate_app_name(name: str) -> None:
    """Validate app name format or exit with error."""
    if not name or not _SAFE_NAME_RE.match(name):
        print(tr("Invalid app name: {name}").format(name=repr(name)), file=sys.stderr)
        sys.exit(1)


def handle_app(args: argparse.Namespace) -> None:
    """Route app subcommands."""
    cmd = args.app_command
    if cmd == "list":
        _list_apps()
    elif cmd == "refresh":
        _refresh_apps(getattr(args, "json", False), getattr(args, "timeout", 30))
    elif cmd == "run":
        _run_app(
            args.name,
            args.file,
            args.wait,
            extra_args=getattr(args, "extra_args", "") or "",
        )
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
        print(tr("Usage: winpodx app {list|refresh|run|install|install-all|remove|sessions|kill}"))
        sys.exit(1)


def _list_apps() -> None:
    from winpodx.core.app import list_available_apps

    apps = list_available_apps()
    if not apps:
        print(tr("No applications found. Run 'winpodx setup' first."))
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
        # v0.4.0 (post-rc1): install.sh's post-install refresh sets
        # WINPODX_REQUIRE_AGENT=1 so FreeRDP fallback is suppressed
        # while install.bat may still be in-flight (FreeRDP would kick
        # install.bat's autologon session). Exit code 5 signals
        # "deferred — re-run when agent comes up"; install.sh maps this
        # to mark_pending discovery instead of treating it as a hard
        # failure.
        "agent_unavailable": 5,
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
            print(tr("Pod is not running. Run 'winpodx pod start --wait' first."), file=sys.stderr)
        elif kind == "unsupported_backend":
            print(
                tr("Discovery requires podman or docker backend. Check 'winpodx config show'."),
                file=sys.stderr,
            )
        elif kind == "agent_unavailable":
            print(
                tr(
                    "Guest agent not up yet — refresh deferred. "
                    "Re-run `winpodx app refresh` once the pod finishes first-boot."
                ),
                file=sys.stderr,
            )
        else:
            print(tr("ERROR: {error}").format(error=exc), file=sys.stderr)
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
            print(tr("Discovered {count} app(s):").format(count=len(apps)))
            for a in apps:
                print(f"  {a.name:<20} {a.full_name}")
        else:
            print(tr("No apps discovered."))


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
    registered_names: list[str] = []
    for slug in discovered_slugs:
        info = available.get(slug)
        if info is None:
            continue
        try:
            install_desktop_entry(info)
            installed += 1
            registered_names.append(info.full_name or info.name)
        except Exception as e:  # noqa: BLE001
            print(
                tr("  warning: could not install desktop entry for {slug}: {error}").format(
                    slug=slug, error=e
                ),
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
                    tr("  warning: could not remove stale entry {slug}: {error}").format(
                        slug=slug, error=e
                    ),
                    file=sys.stderr,
                )

    if installed or removed:
        try:
            update_icon_cache()
        except Exception as e:  # noqa: BLE001
            print(
                tr("  warning: icon cache refresh failed: {error}").format(error=e), file=sys.stderr
            )
    if installed:
        print(
            tr("  Registered {count} app(s) in your desktop menu.").format(count=installed),
            file=sys.stderr,
        )
        # List the registered app names so the install / provision flow shows
        # WHICH apps landed, not just a count (kernalix7: "discovery: 58 apps
        # never lists app NAMES"). Mirrors `winpodx app refresh`'s listing.
        for name in sorted(registered_names):
            print(f"    {name}", file=sys.stderr)
    # Explain the discovery-vs-registered gap: discovered slugs with no bundled
    # AppInfo profile can't be registered, so the discovery count and the
    # "Registered N" count legitimately differ. Surface the difference instead
    # of leaving the user to wonder where the missing apps went.
    unmatched = sorted(discovered_slugs - available_slugs)
    if unmatched:
        preview = ", ".join(unmatched[:10]) + ("…" if len(unmatched) > 10 else "")
        print(
            tr(
                "  Note: {count} discovered app(s) had no bundled profile and "
                "were not added to the menu: {names}"
            ).format(count=len(unmatched), names=preview),
            file=sys.stderr,
        )
    if removed:
        suffix = "y" if removed == 1 else "ies"
        print(
            tr("  Removed {count} stale desktop entr{suffix}.").format(
                count=removed, suffix=suffix
            ),
            file=sys.stderr,
        )


def _run_app(name: str, file: str | None, wait: bool, *, extra_args: str = "") -> None:
    from winpodx.core.provisioner import ProvisionError, ensure_ready
    from winpodx.core.rdp import launch_app, launch_desktop
    from winpodx.desktop.notify import notify_error

    try:
        cfg = ensure_ready()
    except (ProvisionError, RuntimeError) as e:
        notify_error(str(e))
        print(tr("Setup error: {error}").format(error=e), file=sys.stderr)
        sys.exit(1)

    if name == "desktop":
        try:
            session = launch_desktop(cfg, extra_args=extra_args)
            print(
                tr("Launching Windows desktop... (stderr log: {log})").format(
                    log=session.stderr_log
                )
            )
            if wait and session.process:
                session.process.wait()
        except RuntimeError as e:
            notify_error(str(e))
            print(tr("Launch failed: {error}").format(error=e), file=sys.stderr)
            sys.exit(1)
        return

    from winpodx.core.app import find_app

    app_info = find_app(name)
    if not app_info:
        print(
            tr("Unknown app: {name}. Run 'winpodx app list' to see available apps.").format(
                name=name
            )
        )
        sys.exit(1)

    try:
        session = launch_app(
            cfg,
            app_info.executable,
            file,
            launch_uri=app_info.launch_uri or None,
            wm_class_hint=app_info.wm_class_hint or None,
            default_args=app_info.args or None,
            extra_args=extra_args,
        )
        print(
            tr("Launching {app_name}... (stderr log: {log})").format(
                app_name=app_info.full_name, log=session.stderr_log
            )
        )

        if wait and session.process:
            session.process.wait()
            session.pid_file.unlink(missing_ok=True)
    except RuntimeError as e:
        notify_error(str(e))
        print(tr("Launch failed: {error}").format(error=e), file=sys.stderr)
        sys.exit(1)


def _install_app(name: str, mime: bool) -> None:
    from winpodx.core.app import find_app
    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.mime import register_mime_types

    app_info = find_app(name)
    if not app_info:
        print(tr("Unknown app: {name}").format(name=name))
        sys.exit(1)

    path = install_desktop_entry(app_info)
    print(tr("Installed {app_name} → {path}").format(app_name=app_info.full_name, path=path))

    if mime and app_info.mime_types:
        register_mime_types(app_info)
        print(tr("Registered MIME types: {types}").format(types=", ".join(app_info.mime_types)))


def _install_all() -> None:
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.icons import update_icon_cache

    apps = list_available_apps()
    if not apps:
        print(tr("No apps available."))
        return

    for a in apps:
        install_desktop_entry(a)
        print(tr("  Installed {app_name}").format(app_name=a.full_name))

    update_icon_cache()
    print(tr("\n{count} apps installed into desktop environment.").format(count=len(apps)))


def _remove_app(name: str) -> None:
    _validate_app_name(name)
    from winpodx.desktop.entry import remove_desktop_entry

    remove_desktop_entry(name)
    print(tr("Removed {name} from desktop environment.").format(name=name))


def _show_sessions() -> None:
    from winpodx.core.process import list_active_sessions

    active = list_active_sessions()
    if not active:
        print(tr("No active sessions."))
        return

    print(f"{'App':<20} {'PID':<10} {'Status':<10}")
    print("-" * 40)
    for s in active:
        status_str = tr("alive") if s.is_alive else tr("dead")
        print(f"{s.app_name:<20} {s.pid:<10} {status_str:<10}")


def _kill_session(name: str) -> None:
    _validate_app_name(name)
    from winpodx.core.process import kill_session

    if kill_session(name):
        print(tr("Killed session: {name}").format(name=name))
    else:
        print(tr("No active session found: {name}").format(name=name))
