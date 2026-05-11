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
from winpodx.reverse_open.discovery import LinuxApp, discover_apps
from winpodx.reverse_open.icons import convert_to_ico, resolve_icon
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
        print("(no apps)", file=stream)
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

    if args.json:
        out = {
            "discovered": len(apps),
            "kept": len(kept),
            "skipped": [{"slug": s, "reason": r} for s, r in skipped],
            "icons_real": sum(1 for ok in icon_results.values() if ok),
            "icons_placeholder": sum(1 for ok in icon_results.values() if not ok),
            "apps_json": str(apps_json_path),
        }
        json.dump(out, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(f"Discovered {len(apps)} apps; staged {len(kept)} after filters.")
        if skipped:
            print(f"  Skipped: {len(skipped)} ({_skip_summary(skipped)})")
        if not args.skip_icons:
            real = sum(1 for ok in icon_results.values() if ok)
            ph = sum(1 for ok in icon_results.values() if not ok)
            print(f"  Icons: {real} resolved, {ph} placeholder.")
        print(f"  Manifest: {apps_json_path}")
        if not cfg.reverse_open.enabled:
            print("  Note: reverse-open is disabled; run `winpodx host-open enable` to activate.")
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
                    "(no cached manifest — run `winpodx host-open refresh` first)",
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
        state = "enabled" if cfg.reverse_open.enabled else "disabled"
        print(f"reverse-open: {state}")
        print(f"  allowlist: {len(cfg.reverse_open.allowlist)} slug(s)")
        print(f"  denylist:  {len(cfg.reverse_open.denylist)} slug(s)")
        last = cfg.reverse_open.last_synced_at or "(never)"
        print(f"  last sync: {last}")
        if cached_count is not None:
            print(f"  cache:     {cached_count} app(s), generated {cached_generated_at}")
        else:
            print("  cache:     (none — run `winpodx host-open refresh`)")
    return 0


# ----- enable / disable -------------------------------------------------------


def _cmd_enable(args: argparse.Namespace) -> int:
    cfg = Config.load()
    if cfg.reverse_open.enabled:
        print("reverse-open: already enabled")
        return 0
    cfg.reverse_open.enabled = True
    cfg.save()
    print("reverse-open: enabled")
    print(
        "  Run `winpodx host-open refresh` to stage the host app list. "
        "The guest push happens once the listener daemon ships in Phase 2b."
    )
    return 0


def _cmd_disable(args: argparse.Namespace) -> int:
    cfg = Config.load()
    if not cfg.reverse_open.enabled:
        print("reverse-open: already disabled")
        return 0
    cfg.reverse_open.enabled = False
    cfg.save()
    print("reverse-open: disabled")
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
        print(f"{target}: {args.slug} already present")
        return 0

    # If the slug is on the opposite list, remove it there first so the
    # add doesn't produce a contradiction. Mirror the design doc's
    # "explicit add to one list wipes presence from the other".
    if args.slug in other_list:
        other_list.remove(args.slug)
    target_list.append(args.slug)
    target_list.sort()
    cfg.save()
    print(f"{target}: added {args.slug}")
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
        print(f"{target}: {args.slug} not present")
        return 0

    target_list.remove(args.slug)
    cfg.save()
    print(f"{target}: removed {args.slug}")
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


def handle(args: argparse.Namespace) -> int:
    """Dispatch ``host-open`` sub-subcommand to its handler."""
    cmd = getattr(args, "host_open_command", None)
    if not cmd:
        print(
            "host-open: missing subcommand. "
            "Try `winpodx host-open status` or `winpodx host-open --help`.",
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
    }
    handler = handlers.get(cmd)
    if handler is None:
        print(f"host-open: unknown subcommand {cmd!r}", file=sys.stderr)
        return 2
    return handler(args)
