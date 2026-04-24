"""CLI handlers for configuration management."""

from __future__ import annotations

import argparse
import sys


def handle_config(args: argparse.Namespace) -> None:
    """Route config subcommands."""
    cmd = args.config_command
    if cmd == "show":
        _show()
    elif cmd == "set":
        _set(args.key, args.value)
    elif cmd == "import":
        _import_config()
    else:
        print("Usage: winpodx config {show|set|import}")
        sys.exit(1)


def _show() -> None:
    from winpodx.core.config import Config, check_session_budget

    cfg = Config.load()
    print("[rdp]")
    print(f"  user     = {cfg.rdp.user}")
    print(f"  password = {'***' if cfg.rdp.password else '(not set)'}")
    print(f"  askpass  = {cfg.rdp.askpass or '(not set)'}")
    print(f"  domain   = {cfg.rdp.domain or '(not set)'}")
    print(f"  ip       = {cfg.rdp.ip}")
    print(f"  port     = {cfg.rdp.port}")
    print(f"  scale    = {cfg.rdp.scale}")
    print(f"  dpi      = {cfg.rdp.dpi or 'auto'}")
    print()
    print("[pod]")
    print(f"  backend       = {cfg.pod.backend}")
    print(f"  vm_name       = {cfg.pod.vm_name}")
    print(f"  cpu_cores     = {cfg.pod.cpu_cores}")
    print(f"  ram_gb        = {cfg.pod.ram_gb}")
    print(f"  max_sessions  = {cfg.pod.max_sessions}")
    print(f"  auto_start    = {cfg.pod.auto_start}")
    print(f"  idle_timeout  = {cfg.pod.idle_timeout}")

    warning = check_session_budget(cfg)
    if warning:
        print()
        print(f"WARNING: {warning}", file=sys.stderr)


def _set(key: str, value: str) -> None:
    from winpodx.core.config import Config, check_session_budget

    cfg = Config.load()

    section, _, field = key.partition(".")
    if not field:
        print("Key format: section.field (e.g., rdp.user, pod.backend)")
        sys.exit(1)

    target = getattr(cfg, section, None)
    if target is None or not hasattr(target, field):
        print(f"Unknown config key: {key}")
        sys.exit(1)

    current = getattr(target, field)
    if isinstance(current, bool):
        coerced: str | int | bool = value.lower() in ("true", "1", "yes")
    elif isinstance(current, int):
        try:
            coerced = int(value)
        except ValueError:
            print(f"Invalid integer value: {value}")
            sys.exit(1)
    else:
        coerced = value

    setattr(target, field, coerced)
    # Re-run dataclass __post_init__ so clamps (e.g. max_sessions [1,50])
    # apply to the value we just set before save + before the budget
    # check sees it.
    target.__post_init__()
    coerced = getattr(target, field)
    cfg.save()
    print(f"Set {key} = {coerced}")

    # Budget warning only fires when over-subscribed — default config
    # stays quiet. Applies whenever max_sessions or ram_gb changes.
    if section == "pod" and field in ("max_sessions", "ram_gb"):
        warning = check_session_budget(cfg)
        if warning:
            print(f"WARNING: {warning}", file=sys.stderr)


def _import_config() -> None:
    from winpodx.core.config import Config
    from winpodx.utils.compat import import_winapps_config

    cfg = import_winapps_config()
    if cfg is None:
        print("No winapps.conf found.")
        return

    cfg.save()
    print(f"Imported winapps config to {Config.path()}")
