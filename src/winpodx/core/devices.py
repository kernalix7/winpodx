# SPDX-License-Identifier: MIT
"""Host <-> guest device passthrough (#286).

winpodx's default backend is dockur/windows, i.e. QEMU/KVM running inside a
Podman/Docker container. This module is the structured layer over
``cfg.pod.devices`` (a flat ``"<type>|<id>|<label>"`` string list — see
``core/config.py``) that the CLI (`winpodx device`) and the GUI Devices tab
build on:

* :class:`DeviceConfig` — the in-memory model, with parse/format helpers that
  round-trip the persisted string form.
* host enumeration — :func:`list_host_usb` (``lsusb``) and
  :func:`list_host_pci` (``lspci`` + ``/sys`` IOMMU groups).
* a safety classifier — :func:`classify_safety`. USB is low-risk; PCI VFIO is
  high-risk (unbinding a GPU / disk controller / active NIC from its host
  driver can take the host down), so risky PCI requires an explicit force.
* QEMU arg builders — :func:`qemu_device_args` / :func:`host_device_nodes`,
  consumed by ``core/pod/compose.py`` so an assigned device survives a
  container recreate.
* a QMP client — :class:`QmpClient` + :func:`live_attach` / :func:`live_detach`
  so a USB device can be hot-plugged into a *running* guest without a recreate
  (PCI always needs a recreate).
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# Mirrors core/config.py's validators — kept in sync, not imported, to avoid a
# config <-> devices import cycle (config validates the persisted strings; this
# module owns the rich model).
_USB_ID_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")
_PCI_ID_RE = re.compile(r"^(?:[0-9a-f]{4}:)?[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")

# PCI class-code prefixes (high byte) used by the safety classifier.
_PCI_CLASS_STORAGE = "01"  # mass-storage controller — may back the boot disk
_PCI_CLASS_NETWORK = "02"  # network controller — may be the active uplink
_PCI_CLASS_DISPLAY = "03"  # display / VGA — usually the primary GPU


@dataclass
class DeviceConfig:
    """One host device assigned to the guest."""

    dtype: str  # "usb" | "pci"
    did: str  # USB "VID:PID" | PCI "[domain:]bus:slot.func"
    label: str = ""

    def to_entry(self) -> str:
        """Serialise to the ``"<type>|<id>|<label>"`` persisted form."""
        return f"{self.dtype}|{self.did}|{self.label}"

    @property
    def key(self) -> str:
        """Stable identity (type + address), ignoring the cosmetic label."""
        return f"{self.dtype}:{self.did}"


def parse_entry(entry: str) -> DeviceConfig | None:
    """Parse a persisted ``"<type>|<id>|<label>"`` string into a
    :class:`DeviceConfig`, or ``None`` if it is malformed."""
    if not isinstance(entry, str):
        return None
    parts = entry.split("|", 2)
    if len(parts) != 3:
        return None
    dtype, did, label = parts[0].strip().lower(), parts[1].strip().lower(), parts[2].strip()
    if dtype == "usb" and _USB_ID_RE.match(did):
        return DeviceConfig("usb", did, label)
    if dtype == "pci" and _PCI_ID_RE.match(did):
        return DeviceConfig("pci", did, label)
    return None


def parse_entries(entries: list[str]) -> list[DeviceConfig]:
    """Parse a ``cfg.pod.devices`` list, dropping malformed entries."""
    out: list[DeviceConfig] = []
    for e in entries:
        dc = parse_entry(e)
        if dc is not None:
            out.append(dc)
    return out


# --------------------------------------------------------------------------
# Host enumeration
# --------------------------------------------------------------------------


@dataclass
class HostDevice:
    """A device discovered on the host (candidate for passthrough)."""

    dtype: str  # "usb" | "pci"
    did: str  # USB "VID:PID" | PCI address
    label: str = ""
    pci_class: str = ""  # PCI high-byte class code (e.g. "03"), PCI only
    iommu_group: str | None = None  # PCI only
    bus: str = ""  # USB bus number (3-digit), USB only
    addr: str = ""  # USB device number (3-digit), USB only

    def to_device_config(self) -> DeviceConfig:
        return DeviceConfig(self.dtype, self.did, self.label)


_LSUSB_RE = re.compile(
    r"^Bus\s+(?P<bus>\d{3})\s+Device\s+(?P<addr>\d{3}):\s+"
    r"ID\s+(?P<vid>[0-9a-fA-F]{4}):(?P<pid>[0-9a-fA-F]{4})\s*(?P<name>.*)$"
)


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    """Run *cmd*, returning stdout (empty string on any failure)."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout if res.returncode == 0 else ""


def parse_lsusb(output: str) -> list[HostDevice]:
    """Parse ``lsusb`` output into USB :class:`HostDevice` entries.

    Root hubs (``ID 1d6b:*`` Linux Foundation) are skipped — they are not
    pluggable peripherals and passing one through is never what a user wants.
    """
    devices: list[HostDevice] = []
    seen: set[str] = set()
    for line in output.splitlines():
        m = _LSUSB_RE.match(line.strip())
        if not m:
            continue
        vid, pid = m["vid"].lower(), m["pid"].lower()
        if vid == "1d6b":  # Linux Foundation root hub
            continue
        did = f"{vid}:{pid}"
        if did in seen:
            # Same VID:PID on multiple ports — keep the first; the QMP
            # attach can disambiguate by bus/addr at use time.
            continue
        seen.add(did)
        devices.append(
            HostDevice(
                dtype="usb",
                did=did,
                label=(m["name"] or "").strip(),
                bus=m["bus"],
                addr=m["addr"],
            )
        )
    return devices


def list_host_usb() -> list[HostDevice]:
    """Enumerate host USB devices via ``lsusb`` (empty list if unavailable)."""
    return parse_lsusb(_run(["lsusb"]))


_LSPCI_RE = re.compile(
    r"^(?P<addr>(?:[0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])\s+"
    r"\"?(?P<cls>[0-9a-fA-F]{4})\"?\s+\"?(?P<rest>.*)$"
)


def parse_lspci(output: str, iommu_lookup=None) -> list[HostDevice]:
    """Parse ``lspci -Dmm -n`` (machine-readable, numeric) into PCI
    :class:`HostDevice` entries. *iommu_lookup* maps a PCI address to its
    IOMMU group (injected for testability)."""
    devices: list[HostDevice] = []
    for line in output.splitlines():
        m = _LSPCI_RE.match(line.strip())
        if not m:
            continue
        addr = m["addr"].lower()
        cls4 = m["cls"].lower()
        pci_class = cls4[:2]  # high byte = broad class
        group = iommu_lookup(addr) if iommu_lookup else None
        devices.append(
            HostDevice(
                dtype="pci",
                did=addr,
                label=m["rest"].strip().strip('"'),
                pci_class=pci_class,
                iommu_group=group,
            )
        )
    return devices


def _iommu_group_for(addr: str) -> str | None:
    """Return the IOMMU group number for a PCI address, or ``None``."""
    # /sys/bus/pci/devices wants the full domain-qualified address.
    full = addr if addr.count(":") == 2 else f"0000:{addr}"
    try:
        import os

        link = f"/sys/bus/pci/devices/{full}/iommu_group"
        if os.path.islink(link):
            return os.path.basename(os.readlink(link))
    except OSError:
        pass
    return None


def list_host_pci() -> list[HostDevice]:
    """Enumerate host PCI devices via ``lspci`` (empty list if unavailable)."""
    return parse_lspci(_run(["lspci", "-Dmm", "-n"]), iommu_lookup=_iommu_group_for)


# --------------------------------------------------------------------------
# Safety classifier
# --------------------------------------------------------------------------


@dataclass
class Safety:
    """Risk assessment for passing a host device to the guest."""

    safe: bool
    reasons: list[str] = field(default_factory=list)


def classify_safety(dev: HostDevice) -> Safety:
    """Classify a host device's passthrough risk.

    USB is low-risk (hot-pluggable, the host keeps its other devices). PCI
    VFIO is high-risk: binding a device to ``vfio-pci`` unbinds it from its
    host driver, and the whole IOMMU group moves together — so a GPU, the
    boot-disk controller, or the active NIC can take the host down. Risky PCI
    is flagged ``safe=False`` so the CLI/GUI gate it behind an explicit force.
    """
    if dev.dtype == "usb":
        return Safety(True, [])

    reasons: list[str] = []
    if dev.pci_class == _PCI_CLASS_DISPLAY:
        reasons.append(
            "display/VGA controller — likely the primary GPU; unbinding it can blank the host"
        )
    elif dev.pci_class == _PCI_CLASS_STORAGE:
        reasons.append("storage controller — may back the host's boot/root disk")
    elif dev.pci_class == _PCI_CLASS_NETWORK:
        reasons.append("network controller — may be the host's active uplink")
    else:
        reasons.append(
            "PCI passthrough requires binding to vfio-pci (unbinds from the host driver)"
        )
    if dev.iommu_group is not None:
        reasons.append(
            f"IOMMU group {dev.iommu_group}: every device in the group is passed through together"
        )
    # All PCI passthrough is treated as needing confirmation.
    return Safety(False, reasons)


# --------------------------------------------------------------------------
# QEMU arg builders (consumed by core/pod/compose.py)
# --------------------------------------------------------------------------


def qemu_device_args(devices: list[DeviceConfig]) -> list[str]:
    """Build the QEMU ``-device`` argument tokens for *devices*.

    USB -> ``-device usb-host,vendorid=0x..,productid=0x..`` (matched by
    VID:PID so it survives re-plugging to a different port). PCI ->
    ``-device vfio-pci,host=<addr>``.
    """
    args: list[str] = []
    for d in devices:
        if d.dtype == "usb":
            vid, pid = d.did.split(":")
            args += ["-device", f"usb-host,vendorid=0x{vid},productid=0x{pid}"]
        elif d.dtype == "pci":
            args += ["-device", f"vfio-pci,host={d.did}"]
    return args


def host_device_nodes(devices: list[DeviceConfig]) -> list[str]:
    """Host ``/dev`` nodes the container needs for *devices*.

    PCI VFIO needs ``/dev/vfio/vfio`` (the VFIO container device) — the
    per-group node is added at attach time by the platform. USB live-attach
    needs the whole ``/dev/bus/usb`` tree exposed so a device plugged in
    *after* container creation is still reachable for a QMP ``device_add``.
    """
    nodes: list[str] = []
    if any(d.dtype == "usb" for d in devices):
        nodes.append("/dev/bus/usb")
    if any(d.dtype == "pci" for d in devices):
        nodes.append("/dev/vfio/vfio")
    return nodes


# --------------------------------------------------------------------------
# Live USB hot-plug via dockur's built-in QEMU monitor (#286)
# --------------------------------------------------------------------------
#
# dockur runs QEMU with `-monitor telnet:localhost:7100` — an HMP (human
# monitor) on the container's loopback — and a `-device qemu-xhci,id=xhci`
# USB-3 controller. We reach the monitor with
# `<backend> exec <container> bash -c '<talk to 127.0.0.1:7100 via /dev/tcp>'`:
# the dockur image ships bash, `exec` runs as root, and 127.0.0.1:7100 is the
# container's loopback where the monitor listens. `device_add usb-host,...` /
# `device_del` hot-plug a host USB device into the running guest with no
# restart.
#
# This deliberately reuses dockur's existing monitor instead of a custom
# `-qmp` unix socket bind-mount: that approach crash-looped Windows boot
# because dockur's QEMU could not create the socket in the host bind-mount
# ("Failed to bind socket ... Permission denied"). No bind-mount here.

DOCKUR_MONITOR_PORT = 7100  # dockur's `-monitor telnet:localhost:7100`
DOCKUR_USB_BUS = "xhci.0"  # bus of dockur's `-device qemu-xhci,id=xhci`

# Treat these substrings in a monitor reply as a failed command. The telnet
# monitor echoes the command back (plus VT100 noise), and `device_add` /
# `device_del` print nothing on success, so a clean reply has none of these.
_HMP_ERROR_RE = re.compile(r"error|not found|could not|failed|no such|unable", re.IGNORECASE)


class HmpError(RuntimeError):
    """A QEMU monitor command failed or the monitor was unreachable."""


def usb_qom_id(dev: DeviceConfig) -> str:
    """Stable QOM id for a hot-plugged USB device (used by add + del)."""
    return "winpodx-usb-" + dev.did.replace(":", "")


def hmp_command(backend: str, container: str, command: str, *, timeout: float = 8.0) -> str:
    """Send one HMP command to dockur's QEMU monitor and return the raw reply.

    Runs ``<backend> exec <container> bash -c '...'`` that opens the
    container's ``127.0.0.1:DOCKUR_MONITOR_PORT`` via bash ``/dev/tcp``, writes
    *command*, and reads the response. Raises :class:`HmpError` if the monitor
    is unreachable. The reply is noisy (telnet echo + VT100), so callers use
    :func:`_hmp_raise_on_error` rather than parsing it exactly.
    """
    import shlex

    script = (
        f"exec 3<>/dev/tcp/127.0.0.1/{DOCKUR_MONITOR_PORT} || exit 7; "
        f"printf '%s\\n' {shlex.quote(command)} >&3; "
        "sleep 0.4; (timeout 1 cat <&3 || true)"
    )
    try:
        from winpodx.backend._hostenv import host_env

        env = host_env()
    except Exception:  # noqa: BLE001 -- host_env is best-effort
        env = None
    try:
        res = subprocess.run(
            [backend, "exec", container, "bash", "-c", script],
            capture_output=True,
            text=True,
            errors="replace",  # the telnet monitor greeting starts with 0xff IAC bytes
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise HmpError(f"monitor exec failed: {e}") from e
    if res.returncode != 0 and not res.stdout.strip():
        raise HmpError(
            f"QEMU monitor unreachable (rc={res.returncode}): {res.stderr.strip() or 'no output'}"
        )
    return res.stdout


def _hmp_raise_on_error(reply: str, action: str, dev: DeviceConfig) -> None:
    if _HMP_ERROR_RE.search(reply):
        snippet = " ".join(reply.split())[-200:]
        raise HmpError(f"{action} {dev.did} failed: {snippet}")


def live_attach(backend: str, container: str, dev: DeviceConfig) -> None:
    """Live-attach a USB device to the running guest.

    Delegates to the **usbredir** path (``core.usbredir``): a host
    ``usbredirect`` feeds a QEMU ``usb-redir`` socket channel. We do NOT use
    ``device_add usb-host`` — QEMU's in-container libusb is frozen at boot (the
    container's netns/userns block USB hotplug), so a runtime ``usb-host`` only
    yields an empty stub. The usbredir grab runs host-side where libusb hotplug
    works, so this is truly live. Raises :class:`HmpError` on failure. PCI is
    not supported live — pass it via a recreate.
    """
    if dev.dtype != "usb":
        raise HmpError(f"live attach only supports USB devices, not {dev.dtype!r}")
    from winpodx.core import usbredir

    log.info("usbredir live-attach usb %s", dev.did)
    usbredir.attach(backend, container, dev)
    log.info("usbredir live-attach usb %s ok", dev.did)


def live_detach(backend: str, container: str, dev: DeviceConfig) -> None:
    """Tear down the live usbredir session for a USB device (idempotent)."""
    if dev.dtype != "usb":
        raise HmpError(f"live detach only supports USB devices, not {dev.dtype!r}")
    from winpodx.core import usbredir

    log.info("usbredir live-detach usb %s", dev.did)
    usbredir.detach(backend, container, dev)
    log.info("usbredir live-detach usb %s ok", dev.did)
