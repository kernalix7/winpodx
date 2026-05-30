# SPDX-License-Identifier: MIT
"""CLI handlers for host<->guest device passthrough (`winpodx device`, #286).

Subcommands:

* ``list``   — enumerate host USB + PCI devices, marking which are assigned to
               the guest and flagging the risky ones (PCI).
* ``status`` — show the currently assigned devices and the guest's run state.
* ``attach`` — assign a host device to the guest. USB attaches live over QMP
               when the guest is running (no restart); PCI needs a recreate and
               is gated behind ``--force`` because VFIO unbinds it from the host
               driver. Persisted to ``cfg.pod.devices``.
* ``detach`` — release a device (live USB unplug + un-persist; PCI un-persist +
               recreate).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from winpodx.core import devices as D
from winpodx.core.config import Config


def handle_device(args: argparse.Namespace) -> None:
    cmd = getattr(args, "device_command", None)
    if cmd == "list":
        _list(args)
    elif cmd == "status":
        _status(args)
    elif cmd == "attach":
        _attach(args)
    elif cmd == "detach":
        _detach(args)
    else:
        print("usage: winpodx device {list|status|attach|detach}", file=sys.stderr)
        sys.exit(2)


def _assigned_keys(cfg: Config) -> set[str]:
    return {d.key for d in D.parse_entries(cfg.pod.devices)}


def _guest_running(cfg: Config) -> bool:
    try:
        from winpodx.core.pod.backend import get_backend

        return bool(get_backend(cfg).is_running())
    except Exception:
        return False


def _enumerate_host() -> list[D.HostDevice]:
    return D.list_host_usb() + D.list_host_pci()


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


def _list(args: argparse.Namespace) -> None:
    cfg = Config.load()
    assigned = _assigned_keys(cfg)
    hosts = _enumerate_host()

    if getattr(args, "json", False):
        rows = []
        for h in hosts:
            safety = D.classify_safety(h)
            rows.append(
                {
                    "type": h.dtype,
                    "id": h.did,
                    "label": h.label,
                    "assigned": h.to_device_config().key in assigned,
                    "safe": safety.safe,
                    "reasons": safety.reasons,
                    "iommu_group": h.iommu_group,
                }
            )
        print(json.dumps(rows, indent=2))
        return

    if not hosts:
        print("No host devices found (is lsusb / lspci installed?).")
        return

    print(f"{'':2} {'TYPE':4}  {'ID':15}  {'SAFE':4}  LABEL")
    for h in hosts:
        dc = h.to_device_config()
        safety = D.classify_safety(h)
        mark = "*" if dc.key in assigned else " "
        safe = "yes" if safety.safe else "RISK"
        print(f"{mark:2} {h.dtype:4}  {h.did:15}  {safe:4}  {h.label[:48]}")
    print(
        "\n  * = assigned to guest.  RISK = PCI passthrough"
        " (needs --force; see `device list --json`)."
    )


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def _status(args: argparse.Namespace) -> None:
    cfg = Config.load()
    devs = D.parse_entries(cfg.pod.devices)
    running = _guest_running(cfg)

    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "guest_running": running,
                    "devices": [{"type": d.dtype, "id": d.did, "label": d.label} for d in devs],
                },
                indent=2,
            )
        )
        return

    print(f"Guest running: {'yes' if running else 'no'}")
    if not devs:
        print("No devices assigned. Use `winpodx device attach <id>`.")
        return
    print("Assigned devices:")
    for d in devs:
        print(f"  {d.dtype:4}  {d.did:15}  {d.label}")


# --------------------------------------------------------------------------
# attach / detach
# --------------------------------------------------------------------------


def _resolve(did: str, type_hint: str | None) -> tuple[D.DeviceConfig, D.HostDevice | None]:
    """Resolve an id (+ optional --type) to a DeviceConfig, preferring a live
    host match (carries label + safety) and falling back to format inference."""
    did = did.strip().lower()
    for h in _enumerate_host():
        if h.did == did and (type_hint is None or h.dtype == type_hint):
            return h.to_device_config(), h
    # Not currently plugged in / not enumerable — infer type from the id shape.
    dtype = type_hint or ("pci" if "." in did else "usb")
    return D.DeviceConfig(dtype, did), None


def _attach(args: argparse.Namespace) -> None:
    cfg = Config.load()
    dc, host = _resolve(args.id, getattr(args, "type", None))
    dc_parsed = D.parse_entry(dc.to_entry())
    if dc_parsed is None:
        print(f"Invalid device id: {args.id!r} (USB VID:PID or PCI address).", file=sys.stderr)
        sys.exit(2)
    dc = dc_parsed
    if getattr(args, "label", None):
        dc.label = args.label
    elif host is not None:
        dc.label = host.label

    if dc.key in _assigned_keys(cfg):
        print(f"{dc.dtype} {dc.did} is already assigned.")
        return

    # Safety gate — PCI (and any device classified risky) needs --force.
    safety = (
        D.classify_safety(host)
        if host
        else D.classify_safety(D.HostDevice(dtype=dc.dtype, did=dc.did))
    )
    if not safety.safe and not getattr(args, "force", False):
        print(f"Refusing to attach {dc.dtype} {dc.did} without --force:", file=sys.stderr)
        for r in safety.reasons:
            print(f"  - {r}", file=sys.stderr)
        print("Re-run with --force if you understand the risk.", file=sys.stderr)
        sys.exit(1)

    # Persist first so the assignment survives a recreate / restart.
    cfg.pod.devices = list(cfg.pod.devices) + [dc.to_entry()]
    cfg.pod.__post_init__()
    cfg.save()
    print(f"Assigned {dc.dtype} {dc.did}" + (f" ({dc.label})" if dc.label else "") + ".")

    if dc.dtype == "usb":
        _apply_usb_attach(cfg, dc)
    else:
        _apply_pci_change(cfg, "attach")


def _detach(args: argparse.Namespace) -> None:
    cfg = Config.load()
    dc, _host = _resolve(args.id, getattr(args, "type", None))
    if dc.key not in _assigned_keys(cfg):
        print(f"{dc.dtype} {dc.did} is not assigned.")
        return

    # Un-persist (match by key, ignore label).
    cfg.pod.devices = [
        e for e in cfg.pod.devices if (p := D.parse_entry(e)) is None or p.key != dc.key
    ]
    cfg.pod.__post_init__()
    cfg.save()
    print(f"Released {dc.dtype} {dc.did}.")

    if dc.dtype == "usb":
        _apply_usb_detach(cfg, dc)
    else:
        _apply_pci_change(cfg, "detach")


def _live_unavailable_reason(cfg: Config, sock: str) -> str | None:
    """Return why live QMP isn't available (for diagnostics), or None if it is."""
    if not getattr(cfg.pod, "usb_live", True):
        return "usb_live is disabled in config (USB applies on `pod recreate`)"
    if not _guest_running(cfg):
        return "the guest isn't running"
    if not os.path.exists(sock):
        return (
            f"the QMP socket isn't there yet ({sock}) — the running guest predates "
            "live-USB support; run `winpodx pod recreate` once to enable it"
        )
    return None


def _apply_usb_attach(cfg: Config, dc: D.DeviceConfig) -> None:
    """Hot-plug a USB device into the running guest over QMP (no restart).

    Prints exactly what happened so a failure is visible rather than silent.
    """
    sock = D.host_qmp_socket_path()
    reason = _live_unavailable_reason(cfg, sock)
    if reason is not None:
        print(f"  Persisted; live attach skipped because {reason}.")
        return
    print(f"  Hot-plugging via QMP ({sock}) …")
    try:
        D.live_attach(sock, dc)
        print("  Hot-plugged into the running guest (live, no restart).")
    except D.QmpError as e:
        print(f"  Live attach FAILED: {e}")
        print("  (Persisted; it will apply on the next `pod recreate`. Check `podman logs`.)")


def _apply_usb_detach(cfg: Config, dc: D.DeviceConfig) -> None:
    sock = D.host_qmp_socket_path()
    reason = _live_unavailable_reason(cfg, sock)
    if reason is not None:
        print(f"  Un-persisted; live detach skipped because {reason}.")
        return
    print(f"  Unplugging via QMP ({sock}) …")
    try:
        D.live_detach(sock, dc)
        print("  Unplugged from the running guest (live).")
    except D.QmpError as e:
        print(f"  Live detach FAILED: {e}")
        print("  (Un-persisted; it will apply on the next `pod recreate`.)")


def _apply_pci_change(cfg: Config, action: str) -> None:
    """PCI passthrough can't be hot-plugged — it needs a container recreate."""
    if not _guest_running(cfg):
        print("  Applies on next `pod start`.")
        return
    print(f"  PCI {action} needs a guest restart. Run `winpodx pod recreate` to apply it.")
