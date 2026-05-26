# SPDX-License-Identifier: MIT
"""``winpodx host-open`` — manage Linux apps registered with the Windows guest.

This is the CLI front of the reverse-open feature (#48). Subcommands:

- ``refresh`` — rescan the host's installed ``.desktop`` entries,
  filter by allow / denylist, convert their icons to ``.ico``, and
  stage an ``apps.json`` manifest under
  ``~/.local/share/winpodx/reverse-open/``. The actual push to the
  guest happens in Phase 2b's listener+sync; this Phase 2a command
  stops at staging so the host-side scan is testable in isolation.
- ``list`` — print the discovered apps (or the staged manifest if
  ``--cached``). Plain-text or ``--json``.
- ``status`` — show the feature toggle, allowlist/denylist counts, and
  last sync timestamp. ``--json`` available.
- ``enable`` / ``disable`` — flip ``cfg.reverse_open.enabled``. The
  command persists via :meth:`Config.save` and prints a single line of
  confirmation. No daemon is spawned in Phase 2a; that's Phase 2b.
- ``add`` / ``remove`` — manage the slug allowlist or denylist in
  ``winpodx.toml``. ``--allow`` / ``--deny`` selects which list;
  default is allow.

The CLI deliberately does not require a running pod or an installed
agent — Phase 2a is host-only. Subcommands that would touch the guest
(``push``, ``unregister-all``, etc.) land in Phase 2b alongside the
listener.

See ``docs/design/REVERSE_OPEN_DESIGN.md`` §"CLI: winpodx host-open"
for the full subcommand surface this implements.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from winpodx.core.config import Config
from winpodx.core.i18n import tr
from winpodx.reverse_open.discovery import LinuxApp, discover_apps
from winpodx.reverse_open.icons import convert_to_ico, resolve_icon
from winpodx.reverse_open.lifecycle import (
    DaemonPaths,
    ListenerStartFailed,
    is_listener_running,
    reload_apps_db,
    start_listener,
    stop_listener,
)
from winpodx.reverse_open.listener import ListenerConfig
from winpodx.utils.paths import data_dir

_SLUG_VALIDATE = re.compile(r"^[a-z0-9-]+$")


def _reverse_open_dir() -> Path:
    """Return the on-disk staging area for reverse-open artefacts.

    ``~/.local/share/winpodx/reverse-open/`` — created on demand by the
    refresh subcommand, intentionally NOT created on import so that
    listing / status on a host that has never run ``refresh`` is a
    cheap no-op.
    """
    return data_dir() / "reverse-open"


def _icons_dir() -> Path:
    """Sub-directory under :func:`_reverse_open_dir` for generated ``.ico``."""
    return _reverse_open_dir() / "icons"


def _apps_json() -> Path:
    """Path to the staged ``apps.json`` manifest written by ``refresh``."""
    return _reverse_open_dir() / "apps.json"


def _incoming_dir() -> Path:
    """Daemon's watched directory for guest-written request files."""
    return _reverse_open_dir() / "incoming"


def _seen_uuids_path() -> Path:
    """Path to the persistent replay-defence ring buffer."""
    return _reverse_open_dir() / "seen-uuids.json"


def _share_roots(cfg: Config) -> dict[str, Path]:
    """Map FreeRDP drive aliases → POSIX paths the daemon will spawn from.

    Built from the same flags ``core/rdp.py`` passes to xfreerdp3:

    - ``+home-drive`` makes ``\\\\tsclient\\home`` map to ``$HOME``.
    - ``/drive:media,<path>`` makes ``\\\\tsclient\\media`` map to the
      detected media base (when a USB stick / external mount is
      present at session-start time).

    Phase 2a stages the manifest under ``$HOME``, so listing
    ``home`` is the only mapping that has to be present for the
    feature to work; ``media`` is opportunistic.

    The listener feeds this dict to :func:`safe_open_unc`; any
    guest-supplied UNC path that doesn't resolve under one of these
    roots is rejected at the open boundary.
    """
    from winpodx.core.rdp import _find_media_base  # local import — heavy

    roots: dict[str, Path] = {"home": Path.home()}
    try:
        media = _find_media_base()
    except Exception:  # noqa: BLE001 — never let a probe failure block startup
        media = None
    if media:
        roots["media"] = Path(media)
    return roots


def _listener_config(cfg: Config) -> ListenerConfig:
    """Default daemon tuning — wired through host_open + tests can override."""
    return ListenerConfig(
        incoming_dir=_incoming_dir(),
        share_roots=_share_roots(cfg),
    )


def _filter_apps(apps: list[LinuxApp], cfg: Config) -> tuple[list[LinuxApp], list[tuple[str, str]]]:
    """Apply the allowlist + denylist filters from :class:`ReverseOpenConfig`.

    Returns (kept, skipped) where ``skipped`` is a list of
    ``(slug, reason)`` pairs so the caller can surface to the user
    which apps were dropped and why. The allowlist semantics match
    the design doc: an empty allowlist means "all discovered apps"
    (still subject to denylist); a non-empty allowlist means "only
    these slugs".
    """
    allow = frozenset(cfg.reverse_open.allowlist)
    deny = frozenset(cfg.reverse_open.denylist)
    kept: list[LinuxApp] = []
    skipped: list[tuple[str, str]] = []
    for app in apps:
        if app.slug in deny:
            skipped.append((app.slug, "denylist"))
            continue
        if allow and app.slug not in allow:
            skipped.append((app.slug, "not-in-allowlist"))
            continue
        kept.append(app)
    return kept, skipped


def _app_to_dict(app: LinuxApp) -> dict[str, Any]:
    """Serialise a :class:`LinuxApp` to a JSON-compatible dict.

    The manifest format must match the schema documented in
    ``REVERSE_OPEN_DESIGN.md`` §"File schema (guest ← host)". The
    biggest gotcha is :attr:`LinuxApp.desktop_file` is a :class:`Path`;
    we serialise it as a string. Phase 2b's sync layer will reuse this
    dict shape when pushing to the guest.
    """
    d = asdict(app)
    d["desktop_file"] = str(app.desktop_file)
    return d


def _now_iso() -> str:
    """UTC ISO-8601 timestamp with seconds precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _print_human_list(apps: list[LinuxApp], stream) -> None:
    """Render the app list in a human-readable column layout."""
    if not apps:
        print(tr("(no apps)"), file=stream)
        return
    slug_w = max(len(a.slug) for a in apps)
    name_w = max(len(a.name) for a in apps)
    for app in apps:
        marker = "*" if app.is_default_for else " "
        mimes = ", ".join(app.mime_types[:3])
        if len(app.mime_types) > 3:
            mimes += f", +{len(app.mime_types) - 3}"
        print(
            f"  {marker} {app.slug:<{slug_w}}  {app.name:<{name_w}}  {mimes}",
            file=stream,
        )


# ----- refresh ----------------------------------------------------------------


def _cmd_refresh(args: argparse.Namespace) -> int:
    cfg = Config.load()
    apps = discover_apps(include_nodisplay=args.include_nodisplay)
    kept, skipped = _filter_apps(apps, cfg)

    icons_dir = _icons_dir()
    icons_dir.mkdir(parents=True, exist_ok=True)
    icon_results: dict[str, bool] = {}
    if not args.skip_icons:
        for app in kept:
            src = resolve_icon(app.icon_name) if app.icon_name else None
            dst = icons_dir / f"{app.slug}.ico"
            if src is None:
                # Generate a placeholder so Windows always has SOMETHING
                # to display; the loop continues regardless.
                ok = convert_to_ico(Path(""), dst)
            else:
                ok = convert_to_ico(src, dst)
            icon_results[app.slug] = ok

    apps_json_path = _apps_json()
    apps_json_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "generated_at": _now_iso(),
        "host": {
            "xdg_current_desktop": _xdg_desktop_label(),
        },
        "apps": [_app_to_dict(a) for a in kept],
    }
    tmp = apps_json_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(apps_json_path)

    # If a daemon is running, signal it to re-load the manifest so
    # the fresh app list takes effect without a restart. SIGHUP is
    # the canonical "reload your config" signal; lifecycle.py wires
    # it up to AppsDatabase.load().
    daemon_reloaded = reload_apps_db()

    # Push to the guest if the agent is reachable. The sync runs the
    # guest-side register-apps.ps1 which writes the Windows registry
    # entries that surface our slugs in Explorer's "Open with…" menu.
    # Agent unreachable is normal during pre-install / pod-down states
    # — we record the failure shape in the summary and the user can
    # re-run refresh once the pod is up.
    sync_pushed_apps: int | None = None
    sync_pushed_icons: int | None = None
    sync_error: str | None = None
    if cfg.reverse_open.enabled:
        try:
            from winpodx.reverse_open.sync import SyncError, sync_to_guest

            result = sync_to_guest(cfg, _reverse_open_dir())
            sync_pushed_apps = result.pushed_apps
            sync_pushed_icons = result.pushed_icons
            cfg.reverse_open.last_synced_at = _now_iso()
            cfg.save()
        except SyncError as exc:
            sync_error = f"guest register failed: {exc}"
        except Exception as exc:  # noqa: BLE001 — surface to user, don't crash
            sync_error = f"agent unreachable or error: {exc.__class__.__name__}"

    if args.json:
        out = {
            "discovered": len(apps),
            "kept": len(kept),
            "skipped": [{"slug": s, "reason": r} for s, r in skipped],
            "icons_real": sum(1 for ok in icon_results.values() if ok),
            "icons_placeholder": sum(1 for ok in icon_results.values() if not ok),
            "apps_json": str(apps_json_path),
            "daemon_reloaded": daemon_reloaded,
            "sync_pushed_apps": sync_pushed_apps,
            "sync_pushed_icons": sync_pushed_icons,
            "sync_error": sync_error,
        }
        json.dump(out, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(
            tr("Discovered {count} apps; staged {kept} after filters.").format(
                count=len(apps), kept=len(kept)
            )
        )
        if skipped:
            print(
                tr("  Skipped: {count} ({summary})").format(
                    count=len(skipped), summary=_skip_summary(skipped)
                )
            )
        if not args.skip_icons:
            real = sum(1 for ok in icon_results.values() if ok)
            ph = sum(1 for ok in icon_results.values() if not ok)
            print(
                tr("  Icons: {real} resolved, {placeholder} placeholder.").format(
                    real=real, placeholder=ph
                )
            )
        print(f"  Manifest: {apps_json_path}")
        if daemon_reloaded:
            print(tr("  Daemon: SIGHUP sent; new manifest loaded."))
        if sync_pushed_apps is not None:
            print(
                tr("  Guest sync: pushed {apps} app(s) + {icons} icon(s) → registered.").format(
                    apps=sync_pushed_apps, icons=sync_pushed_icons
                )
            )
        elif sync_error is not None:
            print(tr("  Guest sync: skipped ({error})").format(error=sync_error))
        elif not cfg.reverse_open.enabled:
            print(
                tr("  Note: reverse-open is disabled; run `winpodx host-open enable` to activate.")
            )
    return 0


def _skip_summary(skipped: list[tuple[str, str]]) -> str:
    counts: dict[str, int] = {}
    for _, reason in skipped:
        counts[reason] = counts.get(reason, 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))


def _xdg_desktop_label() -> str:
    import os

    return os.environ.get("XDG_CURRENT_DESKTOP", "") or ""


# ----- list -------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> int:
    cfg = Config.load()
    if args.cached:
        manifest_path = _apps_json()
        if not manifest_path.is_file():
            if args.json:
                json.dump({"apps": [], "source": "cache", "exists": False}, sys.stdout)
                sys.stdout.write("\n")
            else:
                print(
                    tr("(no cached manifest — run `winpodx host-open refresh` first)"),
                    file=sys.stderr,
                )
            return 1
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if args.json:
            json.dump(
                {"source": "cache", "exists": True, "manifest": manifest},
                sys.stdout,
                indent=2,
                sort_keys=True,
            )
            sys.stdout.write("\n")
        else:
            apps_data = manifest.get("apps", [])
            print(f"Cached manifest: {manifest_path} ({len(apps_data)} apps)")
            for entry in apps_data:
                marker = "*" if entry.get("is_default_for") else " "
                print(f"  {marker} {entry['slug']}  {entry['name']}")
        return 0

    apps = discover_apps(include_nodisplay=args.include_nodisplay)
    kept, _ = _filter_apps(apps, cfg)
    if args.json:
        json.dump(
            {
                "source": "scan",
                "apps": [_app_to_dict(a) for a in kept],
            },
            sys.stdout,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
    else:
        print(f"Discovered {len(apps)}; after filters: {len(kept)}")
        _print_human_list(kept, sys.stdout)
    return 0


# ----- status -----------------------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> int:
    cfg = Config.load()
    manifest_path = _apps_json()
    cached_count: int | None = None
    cached_generated_at: str | None = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            cached_count = len(manifest.get("apps", []))
            cached_generated_at = manifest.get("generated_at")
        except (OSError, json.JSONDecodeError):
            cached_count = None

    payload = {
        "enabled": cfg.reverse_open.enabled,
        "allowlist": list(cfg.reverse_open.allowlist),
        "denylist": list(cfg.reverse_open.denylist),
        "last_synced_at": cfg.reverse_open.last_synced_at,
        "cache": {
            "path": str(manifest_path),
            "exists": manifest_path.is_file(),
            "app_count": cached_count,
            "generated_at": cached_generated_at,
        },
    }

    if args.json:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        state = tr("enabled") if cfg.reverse_open.enabled else tr("disabled")
        print(tr("reverse-open: {state}").format(state=state))
        print(tr("  allowlist: {count} slug(s)").format(count=len(cfg.reverse_open.allowlist)))
        print(tr("  denylist:  {count} slug(s)").format(count=len(cfg.reverse_open.denylist)))
        last = cfg.reverse_open.last_synced_at or tr("(never)")
        print(tr("  last sync: {last}").format(last=last))
        if cached_count is not None:
            print(
                tr("  cache:     {count} app(s), generated {generated_at}").format(
                    count=cached_count, generated_at=cached_generated_at
                )
            )
        else:
            print(tr("  cache:     (none — run `winpodx host-open refresh`)"))
    return 0


# ----- enable / disable -------------------------------------------------------


def _cmd_enable(args: argparse.Namespace) -> int:
    cfg = Config.load()
    if cfg.reverse_open.enabled:
        print(tr("reverse-open: already enabled"))
        return 0
    cfg.reverse_open.enabled = True
    cfg.save()
    print(tr("reverse-open: enabled"))
    print(
        tr(
            "  Run `winpodx host-open refresh` to stage the host app list. "
            "The guest push happens once the listener daemon ships in Phase 2b."
        )
    )
    return 0


def _cmd_disable(args: argparse.Namespace) -> int:
    cfg = Config.load()
    if not cfg.reverse_open.enabled:
        print(tr("reverse-open: already disabled"))
        return 0
    cfg.reverse_open.enabled = False
    cfg.save()
    print(tr("reverse-open: disabled"))
    return 0


# ----- add / remove -----------------------------------------------------------


def _validate_slug(slug: str) -> str | None:
    if not _SLUG_VALIDATE.fullmatch(slug):
        return f"slug {slug!r} is not a valid lower-kebab identifier (expected /^[a-z0-9-]+$/)"
    return None


def _cmd_add(args: argparse.Namespace) -> int:
    cfg = Config.load()
    target = "denylist" if args.deny else "allowlist"
    other = "allowlist" if args.deny else "denylist"
    target_list = getattr(cfg.reverse_open, target)
    other_list = getattr(cfg.reverse_open, other)

    err = _validate_slug(args.slug)
    if err is not None:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if args.slug in target_list:
        print(tr("{target}: {slug} already present").format(target=target, slug=args.slug))
        return 0

    # If the slug is on the opposite list, remove it there first so the
    # add doesn't produce a contradiction. Mirror the design doc's
    # "explicit add to one list wipes presence from the other".
    if args.slug in other_list:
        other_list.remove(args.slug)
    target_list.append(args.slug)
    target_list.sort()
    cfg.save()
    print(tr("{target}: added {slug}").format(target=target, slug=args.slug))
    if args.slug in cfg.reverse_open.denylist and target == "denylist":
        # Mention the DANGEROUS_DEFAULTS fold — if the user added a
        # safe-looking slug to denylist, no comment needed; if they
        # tried to add one of the canonical-dangerous slugs to allowlist
        # we already removed it from denylist above, so the fold in
        # __post_init__ may re-add it on next load. Surface this so the
        # user isn't surprised.
        pass
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    cfg = Config.load()
    target = "denylist" if args.deny else "allowlist"
    target_list = getattr(cfg.reverse_open, target)

    err = _validate_slug(args.slug)
    if err is not None:
        print(f"error: {err}", file=sys.stderr)
        return 2

    if args.slug not in target_list:
        print(tr("{target}: {slug} not present").format(target=target, slug=args.slug))
        return 0

    target_list.remove(args.slug)
    cfg.save()
    print(tr("{target}: removed {slug}").format(target=target, slug=args.slug))
    return 0


# ----- daemon lifecycle subcommands -------------------------------------------


def _cmd_start_listener(args: argparse.Namespace) -> int:
    cfg = Config.load()
    listener_cfg = _listener_config(cfg)
    listener_cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
    try:
        listener_cfg.incoming_dir.chmod(0o700)
    except OSError as exc:
        print(f"error: cannot tighten incoming dir permissions: {exc}", file=sys.stderr)
        return 1
    apps_path = _apps_json()
    seen_path = _seen_uuids_path()
    try:
        pid = start_listener(listener_cfg, apps_path, seen_path)
    except ListenerStartFailed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        json.dump({"pid": pid, "incoming_dir": str(listener_cfg.incoming_dir)}, sys.stdout)
        sys.stdout.write("\n")
    else:
        print(tr("reverse-open listener: pid {pid}").format(pid=pid))
        print(f"  watching: {listener_cfg.incoming_dir}")
    return 0


def _cmd_stop_listener(args: argparse.Namespace) -> int:
    sent = stop_listener()
    if args.json:
        json.dump({"stopped": sent}, sys.stdout)
        sys.stdout.write("\n")
    else:
        msg = (
            tr("reverse-open listener: stopped")
            if sent
            else tr("reverse-open listener: not running")
        )
        print(msg)
    return 0


def _cmd_unregister_guest(args: argparse.Namespace) -> int:
    """Run guest-side unregister-apps.ps1 via the agent.

    Used by ``uninstall.sh`` to scrub the per-app .cmd files, Start
    Menu shortcuts, and registry entries from the Windows guest BEFORE
    the container is torn down. Idempotent (the script no-ops if there
    are no winpodx-* entries to remove).
    """
    cfg = Config.load()
    try:
        from winpodx.reverse_open.sync import SyncError, unregister_on_guest

        result = unregister_on_guest(cfg)
    except SyncError as exc:
        if args.json:
            json.dump({"ok": False, "error": str(exc)}, sys.stdout)
            sys.stdout.write("\n")
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        if args.json:
            json.dump(
                {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"},
                sys.stdout,
            )
            sys.stdout.write("\n")
        else:
            print(f"  agent unreachable or error: {exc.__class__.__name__}", file=sys.stderr)
        return 1
    if args.json:
        json.dump(
            {"ok": True, "stdout": result.stdout, "stderr": result.stderr},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        print(tr("reverse-open: guest registry scrubbed"))
        if result.stdout.strip():
            print(f"  {result.stdout.strip()}")
    return 0


def _cmd_daemon_status(args: argparse.Namespace) -> int:
    pid = is_listener_running()
    paths = DaemonPaths.default()
    if args.json:
        json.dump(
            {
                "running": pid is not None,
                "pid": pid,
                "pid_file": str(paths.pid_file),
                "log_file": str(paths.log_file),
            },
            sys.stdout,
            indent=2,
            sort_keys=True,
        )
        sys.stdout.write("\n")
    else:
        if pid is None:
            print(tr("reverse-open listener: not running"))
        else:
            print(tr("reverse-open listener: running (pid {pid})").format(pid=pid))
        print(f"  pid file: {paths.pid_file}")
        print(f"  log file: {paths.log_file}")
    return 0


# ----- parser wiring ----------------------------------------------------------


def add_subcommand(top_subparsers: argparse._SubParsersAction) -> None:
    """Register ``host-open`` and all its sub-subcommands on the top parser."""
    p = top_subparsers.add_parser(
        "host-open",
        help="Manage Linux apps registered as Windows 'Open with…' handlers",
    )
    sub = p.add_subparsers(dest="host_open_command")

    refresh = sub.add_parser(
        "refresh",
        help="Scan the host for .desktop apps and stage the manifest",
    )
    refresh.add_argument("--json", action="store_true", help="Machine-readable output")
    refresh.add_argument(
        "--skip-icons",
        action="store_true",
        help="Skip ICO conversion (faster; use when icons haven't changed)",
    )
    refresh.add_argument(
        "--include-nodisplay",
        action="store_true",
        help="Include entries marked NoDisplay=true (protocol handlers etc.)",
    )

    lst = sub.add_parser("list", help="Print discovered or cached apps")
    lst.add_argument("--json", action="store_true", help="Machine-readable output")
    lst.add_argument(
        "--cached",
        action="store_true",
        help="Read the staged manifest instead of running a fresh scan",
    )
    lst.add_argument(
        "--include-nodisplay",
        action="store_true",
        help="Include entries marked NoDisplay=true (live-scan mode only)",
    )

    st = sub.add_parser("status", help="Show enabled state, allowlist counts, cache")
    st.add_argument("--json", action="store_true", help="Machine-readable output")

    sub.add_parser("enable", help="Enable reverse-open in winpodx.toml")
    sub.add_parser("disable", help="Disable reverse-open in winpodx.toml")

    add = sub.add_parser("add", help="Add a slug to the allowlist (or denylist with --deny)")
    add.add_argument("slug")
    add.add_argument("--deny", action="store_true", help="Add to denylist instead")

    rm = sub.add_parser("remove", help="Remove a slug from the allowlist (or denylist with --deny)")
    rm.add_argument("slug")
    rm.add_argument("--deny", action="store_true", help="Remove from denylist instead")

    start = sub.add_parser("start-listener", help="Start the reverse-open daemon")
    start.add_argument("--json", action="store_true", help="Machine-readable output")
    stop = sub.add_parser("stop-listener", help="Stop the reverse-open daemon")
    stop.add_argument("--json", action="store_true", help="Machine-readable output")
    dstat = sub.add_parser("daemon-status", help="Show whether the daemon is running")
    dstat.add_argument("--json", action="store_true", help="Machine-readable output")
    ug = sub.add_parser(
        "unregister-guest",
        help="Run unregister-apps.ps1 on the guest to scrub Windows-side artifacts",
    )
    ug.add_argument("--json", action="store_true", help="Machine-readable output")


def handle(args: argparse.Namespace) -> int:
    """Dispatch ``host-open`` sub-subcommand to its handler."""
    cmd = getattr(args, "host_open_command", None)
    if not cmd:
        print(
            tr(
                "host-open: missing subcommand. "
                "Try `winpodx host-open status` or `winpodx host-open --help`."
            ),
            file=sys.stderr,
        )
        return 2
    handlers = {
        "refresh": _cmd_refresh,
        "list": _cmd_list,
        "status": _cmd_status,
        "enable": _cmd_enable,
        "disable": _cmd_disable,
        "add": _cmd_add,
        "remove": _cmd_remove,
        "start-listener": _cmd_start_listener,
        "stop-listener": _cmd_stop_listener,
        "daemon-status": _cmd_daemon_status,
        "unregister-guest": _cmd_unregister_guest,
    }
    handler = handlers.get(cmd)
    if handler is None:
        print(f"host-open: unknown subcommand {cmd!r}", file=sys.stderr)
        return 2
    return handler(args)
