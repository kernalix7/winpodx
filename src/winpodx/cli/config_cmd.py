# SPDX-License-Identifier: MIT
"""CLI handlers for configuration management."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from winpodx.core.i18n import tr

# pod.* keys baked into compose.yaml that a plain recreate (container rm+create,
# Windows disk preserved) is enough to apply — we do it automatically.
_RECREATE_ON_CHANGE = frozenset(
    {
        "disguise_level",
        "tuning_profile",
        "cpu_cores",
        "ram_gb",
        "disk_size",
        "vnc_port",
        "image",
    }
)
# Keys that only take effect on a *fresh* Windows install (first-boot unattend),
# so a plain recreate won't reach the guest — needs --wipe-storage. We never
# auto-wipe (destructive); just point the user at it.
_WIPE_ON_CHANGE = frozenset({"win_version"})


def _apply_compose_change(cfg: Any) -> None:
    """Regenerate compose for *cfg* and apply it to the pod right away (#246).

    Mirrors the GUI's Save behaviour and the migrate auto-recreate: regenerate
    compose.yaml, and if the pod is running, recreate it now (stop+start;
    container rm+create, Windows storage volume preserved, ~30s). If it's
    stopped, the regenerated compose lands on the next ``pod start``. Best-effort
    — never raises, so a failed apply can't lose the saved config value.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return
    try:
        from winpodx.core.compose import generate_compose

        generate_compose(cfg)
    except Exception as e:  # noqa: BLE001
        print(tr("  warning: could not regenerate compose ({error})").format(error=e))
        return
    try:
        from winpodx.core.pod import PodState, pod_status, start_pod, stop_pod

        running = pod_status(cfg).state == PodState.RUNNING
    except Exception:  # noqa: BLE001
        running = False
    if not running:
        print(tr("Saved — applies on the next 'winpodx pod start'."))
        return
    print(tr("Applying to the running container (recreate ~30s, storage preserved)..."))
    try:
        stop_pod(cfg)
        start_pod(cfg)
    except Exception as e:  # noqa: BLE001
        print(
            tr("  recreate failed ({error}); run 'winpodx pod recreate' to apply.").format(error=e)
        )


def handle_config(args: argparse.Namespace) -> None:
    """Route config subcommands."""
    cmd = args.config_command
    if cmd == "show":
        _show()
    elif cmd == "set":
        _set(args.key, args.value, auto=getattr(args, "auto", False))
    elif cmd == "import":
        _import_config()
    else:
        print(tr("Usage: winpodx config {show|set|import}"))
        sys.exit(1)


def _show() -> None:
    from winpodx.core.config import Config, check_session_budget

    cfg = Config.load()
    print(tr("[rdp]"))
    print(tr("  user     = {user}").format(user=cfg.rdp.user))
    print(tr("  password = {pw}").format(pw="***" if cfg.rdp.password else tr("(not set)")))
    print(tr("  askpass  = {askpass}").format(askpass=cfg.rdp.askpass or tr("(not set)")))
    print(tr("  domain   = {domain}").format(domain=cfg.rdp.domain or tr("(not set)")))
    print(tr("  ip       = {ip}").format(ip=cfg.rdp.ip))
    print(tr("  port     = {port}").format(port=cfg.rdp.port))
    print(tr("  scale    = {scale}").format(scale=cfg.rdp.scale))
    print(tr("  dpi      = {dpi}").format(dpi=cfg.rdp.dpi or tr("auto")))
    print()
    print(tr("[pod]"))
    print(tr("  backend       = {backend}").format(backend=cfg.pod.backend))
    print(tr("  vm_name       = {vm_name}").format(vm_name=cfg.pod.vm_name))
    print(tr("  cpu_cores     = {cpu_cores}").format(cpu_cores=cfg.pod.cpu_cores))
    print(tr("  ram_gb        = {ram_gb}").format(ram_gb=cfg.pod.ram_gb))
    print(tr("  max_sessions  = {max_sessions}").format(max_sessions=cfg.pod.max_sessions))
    print(tr("  auto_start    = {auto_start}").format(auto_start=cfg.pod.auto_start))
    print(tr("  idle_timeout  = {idle_timeout}").format(idle_timeout=cfg.pod.idle_timeout))

    print(tr("[desktop]"))
    print(
        tr("  mime_associations = {v}  (file types in 'Open with')").format(
            v=cfg.desktop.mime_associations
        )
    )

    warning = check_session_budget(cfg)
    if warning:
        print()
        print(tr("WARNING: {warning}").format(warning=warning), file=sys.stderr)


def _set(key: str, value: str | None, *, auto: bool = False) -> None:
    from winpodx.core.config import Config, check_session_budget

    cfg = Config.load()

    section, _, field = key.partition(".")
    if not field:
        print(tr("Key format: section.field (e.g., rdp.user, pod.backend)"))
        sys.exit(1)

    target = getattr(cfg, section, None)
    if target is None or not hasattr(target, field):
        print(tr("Unknown config key: {key}").format(key=key))
        sys.exit(1)

    if auto:
        if value is not None:
            print(tr("--auto and a positional value are mutually exclusive."))
            sys.exit(1)
        resolved = _resolve_auto_value(section, field)
        if resolved is None:
            print(
                tr(
                    "--auto not yet supported for {key}. Currently supported: "
                    "pod.timezone. Other keys will gain auto-detect in follow-up "
                    "phases of #254 -- pass a value explicitly for now."
                ).format(key=key)
            )
            sys.exit(1)
        value = resolved

    if value is None:
        print(tr("Missing value. Either pass a positional value or --auto."))
        sys.exit(1)

    current = getattr(target, field)
    if isinstance(current, bool):
        coerced: str | int | bool = value.lower() in ("true", "1", "yes")
    elif isinstance(current, int):
        try:
            coerced = int(value)
        except ValueError:
            print(tr("Invalid integer value: {value}").format(value=value))
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
    print(tr("Set {key} = {value}").format(key=key, value=coerced))

    # Budget warning only fires when over-subscribed — default config
    # stays quiet. Applies whenever max_sessions or ram_gb changes.
    if section == "pod" and field in ("max_sessions", "ram_gb"):
        warning = check_session_budget(cfg)
        if warning:
            print(tr("WARNING: {warning}").format(warning=warning), file=sys.stderr)

    # Apply compose-affecting changes immediately (no manual recreate needed),
    # matching the GUI's Save behaviour.
    if not auto and section == "pod" and field in _RECREATE_ON_CHANGE:
        from winpodx.core.config import disguise_changes_devices

        if field == "disguise_level" and disguise_changes_devices(current, coerced):
            # Device-changing switch (#246): a plain recreate would regenerate
            # compose for hardware the installed guest can't boot (0x7B). Do NOT
            # auto-apply — require an explicit wipe + reinstall.
            print(
                tr(
                    "WARNING: '{level}' switches the guest to emulated hardware "
                    "(disk -> SATA, network -> e1000, GPU -> std). The existing "
                    "Windows install CANNOT boot on it, so applying this DESTROYS "
                    "the Windows disk and reinstalls from scratch. Run 'winpodx pod "
                    "recreate --wipe-storage' to apply -- this PERMANENTLY DELETES "
                    "all apps and data in the VM."
                ).format(level=coerced)
            )
        else:
            _apply_compose_change(cfg)
    elif not auto and section == "pod" and field in _WIPE_ON_CHANGE:
        print(
            tr(
                "Note: the Windows edition only changes on a fresh install — run "
                "'winpodx pod recreate --wipe-storage' to apply (this wipes the guest)."
            )
        )


def _resolve_auto_value(section: str, field: str) -> str | None:
    """Return the host-detected value for ``<section>.<field>``, or None.

    Currently wired (#254 phase 2):

    * ``pod.timezone`` -- IANA zone from
      :func:`winpodx.utils.locale.detect_timezone`. We store the IANA
      name verbatim rather than translating to a Windows TZ ID, so the
      TOML stays human-readable and the compose generator can re-resolve
      to the matching Windows ID via the CLDR table at run time.

    Returns ``None`` for keys that don't have a detection helper yet --
    the caller surfaces a clear error and exits non-zero.
    """
    if section == "pod" and field == "timezone":
        from winpodx.utils.locale import detect_timezone

        return detect_timezone()
    return None


def _import_config() -> None:
    from winpodx.core.config import Config
    from winpodx.utils.compat import import_winapps_config

    cfg = import_winapps_config()
    if cfg is None:
        print(tr("No winapps.conf found."))
        return

    cfg.save()
    print(tr("Imported winapps config to {path}").format(path=Config.path()))
