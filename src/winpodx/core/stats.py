# SPDX-License-Identifier: MIT
"""Best-effort resource snapshot for the GUI/tray dashboard.

Bundles the three numbers a user wants to see at a glance -- pod state,
live CPU/RAM against the configured cap, and Windows guest disk usage --
into a single :class:`ResourceSnapshot`. Everything here is *best-effort*:
each external probe (pod-state query, ``podman stats``, guest disk probe)
is wrapped so a failing or slow backend yields ``None`` for that field
rather than raising. The configured caps (``cpu_cores`` / ``ram_gb``) are
read straight from ``cfg.pod`` and are always populated.

Live CPU/RAM come from ``<cli> stats --no-stream --format json`` where
``<cli>`` honours ``cfg.pod.backend`` (podman by default, docker when
configured); ``manual`` backends have no container to probe so the live
fields stay ``None``. Disk usage reuses
:func:`winpodx.core.disk.get_guest_disk_usage`. When the pod isn't
running the live fields are all ``None`` -- there's nothing to measure.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass

from winpodx.core.config import Config

log = logging.getLogger(__name__)

_BYTES_IN_GB = 1024**3

# ``MemUsage`` looks like ``"1.5GiB / 16GiB"`` (podman) or ``"1.5GiB / 16GiB"``
# (docker). Grab the first quantity (the used side); the second is the limit,
# which we already have from cfg.pod.ram_gb.
_MEM_USAGE_RE = re.compile(
    r"(?P<num>[0-9]+(?:\.[0-9]+)?)\s*(?P<unit>[KMGTP]?i?B)",
    re.IGNORECASE,
)

# Binary (1024-based) and decimal (1000-based) multipliers. podman/docker emit
# IEC units (KiB/MiB/GiB) by default but some builds print SI units (kB/MB/GB);
# accept both so parsing is robust across CLI versions.
_UNIT_TO_BYTES = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
    "pb": 1000**5,
    "pib": 1024**5,
}


@dataclass
class ResourceSnapshot:
    """A point-in-time view of pod resource usage for the dashboard.

    Configured caps (``cpu_cores`` / ``ram_gb``) are always set. Live
    fields are ``None`` when the pod isn't running or the relevant probe
    failed -- callers render those as "--" rather than zero.
    """

    pod_state: str  # running | paused | stopped | checking | unknown
    cpu_cores: int  # configured cap from cfg.pod.cpu_cores
    cpu_pct: float | None  # live %, None if unavailable
    ram_gb: int  # configured cap from cfg.pod.ram_gb
    ram_used_gb: float | None
    ram_pct: float | None
    disk_total_gb: float | None
    disk_used_gb: float | None
    disk_pct: float | None


def _parse_mem_bytes(token: str) -> float | None:
    """Parse a single ``stats`` memory quantity (e.g. ``"1.5GiB"``) to bytes."""
    m = _MEM_USAGE_RE.search(token or "")
    if not m:
        return None
    factor = _UNIT_TO_BYTES.get(m.group("unit").lower())
    if factor is None:
        return None
    try:
        return float(m.group("num")) * factor
    except (TypeError, ValueError):
        return None


def _parse_cpu_pct(value: object) -> float | None:
    """Parse a ``stats`` ``CPUPerc`` field (``"12.34%"`` or a number) to float."""
    if value is None:
        return None
    try:
        text = str(value).strip().rstrip("%").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_mem_pct(value: object) -> float | None:
    """Parse a ``stats`` ``MemPerc`` field (``"9.5%"`` or a number) to float."""
    return _parse_cpu_pct(value)


def _stats_cli(cfg: Config) -> str | None:
    """Return the container CLI to probe, or None for backends without one."""
    backend = cfg.pod.backend
    if backend == "docker":
        return "docker"
    if backend == "podman":
        return "podman"
    # manual (RDP) or anything else has no container to probe.
    return None


def _live_cpu_ram(cfg: Config) -> tuple[float | None, float | None, float | None]:
    """Probe live CPU%/RAM via ``<cli> stats --no-stream --format json``.

    Returns ``(cpu_pct, ram_used_gb, ram_pct)``, with any field ``None``
    when the probe fails or the value can't be parsed. Never raises.
    """
    cli = _stats_cli(cfg)
    if cli is None:
        return None, None, None

    container = cfg.pod.container_name
    try:
        result = subprocess.run(
            [cli, "stats", "--no-stream", "--format", "json", container],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("stats probe (%s) failed to run: %s", cli, e)
        return None, None, None

    if result.returncode != 0:
        log.debug("stats probe rc=%s stderr=%s", result.returncode, result.stderr.strip())
        return None, None, None

    try:
        data = json.loads(result.stdout.strip() or "null")
    except (ValueError, TypeError) as e:
        log.debug("stats probe unparseable %r: %s", result.stdout, e)
        return None, None, None

    # podman emits a JSON array of per-container objects; docker emits one
    # JSON object per line (a single object when one container is named).
    row: object = None
    if isinstance(data, list):
        row = data[0] if data else None
    elif isinstance(data, dict):
        row = data
    if not isinstance(row, dict):
        return None, None, None

    cpu_pct = _parse_cpu_pct(row.get("CPUPerc") or row.get("CPU"))
    ram_pct = _parse_mem_pct(row.get("MemPerc") or row.get("Mem"))

    ram_used_gb: float | None = None
    mem_usage = row.get("MemUsage") or row.get("MemoryUsage")
    if isinstance(mem_usage, str):
        # "1.5GiB / 16GiB" -> take the used side (before the slash).
        used_token = mem_usage.split("/", 1)[0]
        used_bytes = _parse_mem_bytes(used_token)
        if used_bytes is not None:
            ram_used_gb = used_bytes / _BYTES_IN_GB

    return cpu_pct, ram_used_gb, ram_pct


def _guest_disk(cfg: Config) -> tuple[float | None, float | None, float | None]:
    """Probe Windows guest C: usage. Returns ``(total_gb, used_gb, pct)``.

    Reuses :func:`winpodx.core.disk.get_guest_disk_usage`; any failure
    yields all-``None``. Never raises.
    """
    try:
        from winpodx.core.disk import get_guest_disk_usage

        usage = get_guest_disk_usage(cfg)
    except Exception as e:  # noqa: BLE001 -- never let a probe break the snapshot
        log.debug("guest disk probe failed: %s", e)
        return None, None, None

    if usage is None:
        return None, None, None

    try:
        total_gb = usage.total_bytes / _BYTES_IN_GB
        used_gb = usage.used_bytes / _BYTES_IN_GB
        pct = usage.used_pct
    except (AttributeError, TypeError, ZeroDivisionError) as e:
        log.debug("guest disk usage math failed: %s", e)
        return None, None, None

    return total_gb, used_gb, pct


def _pod_state(cfg: Config) -> str:
    """Query the pod state via the canonical pod-state source.

    Returns one of ``running`` / ``paused`` / ``stopped`` / ``checking`` /
    ``unknown``. ``checking`` covers the transient STARTING/UNRESPONSIVE
    states; ERROR or any probe failure maps to ``unknown``. Never raises.
    """
    try:
        from winpodx.core.pod.backend import PodState, pod_status

        state = pod_status(cfg).state
    except Exception as e:  # noqa: BLE001 -- best-effort; degrade to unknown
        log.debug("pod state probe failed: %s", e)
        return "unknown"

    if state == PodState.RUNNING:
        return "running"
    if state == PodState.PAUSED:
        return "paused"
    if state == PodState.STOPPED:
        return "stopped"
    if state in (PodState.STARTING, PodState.UNRESPONSIVE):
        return "checking"
    return "unknown"


def pod_resource_snapshot(cfg: Config) -> ResourceSnapshot:
    """Build a best-effort :class:`ResourceSnapshot` for the dashboard.

    Always returns a snapshot -- never raises. Configured caps come from
    ``cfg.pod``; live CPU/RAM and guest disk are probed only when the pod
    is running (and the backend has a container to query), with every
    external call wrapped so a failure leaves that field ``None``.
    """
    pod_state = _pod_state(cfg)

    try:
        cpu_cores = int(cfg.pod.cpu_cores)
    except (AttributeError, TypeError, ValueError):
        cpu_cores = 0
    try:
        ram_gb = int(cfg.pod.ram_gb)
    except (AttributeError, TypeError, ValueError):
        ram_gb = 0

    cpu_pct: float | None = None
    ram_used_gb: float | None = None
    ram_pct: float | None = None
    disk_total_gb: float | None = None
    disk_used_gb: float | None = None
    disk_pct: float | None = None

    if pod_state == "running":
        cpu_pct, ram_used_gb, ram_pct = _live_cpu_ram(cfg)
        disk_total_gb, disk_used_gb, disk_pct = _guest_disk(cfg)

    return ResourceSnapshot(
        pod_state=pod_state,
        cpu_cores=cpu_cores,
        cpu_pct=cpu_pct,
        ram_gb=ram_gb,
        ram_used_gb=ram_used_gb,
        ram_pct=ram_pct,
        disk_total_gb=disk_total_gb,
        disk_used_gb=disk_used_gb,
        disk_pct=disk_pct,
    )
