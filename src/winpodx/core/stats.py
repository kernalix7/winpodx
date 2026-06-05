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
import os
import re
import subprocess
import threading
import time
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


def _resolve_container_name(cli: str, base: str, env: dict) -> str | None:
    """Return the actual running container name for ``base``.

    podman-compose can override the explicit ``container_name`` with the
    project-prefixed form (``winpodx_<base>_1``), so a bare ``stats <base>``
    misses it. ``ps --filter name=`` substring-matches, so it finds the real
    name. Fast + best-effort; None if nothing matches or the probe fails.
    """
    try:
        result = subprocess.run(
            [cli, "ps", "--filter", f"name={base}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    names = [n for n in result.stdout.split() if n]
    return names[0] if names else None


def _podman_stats_cpu_ram(cfg: Config) -> tuple[float | None, float | None, float | None]:
    """Probe live CPU%/RAM via ``<cli> stats --no-stream --format json``.

    Resolves the real (possibly compose-prefixed) container name first, runs
    under ``host_env`` so an AppImage build uses the host's podman + libs (not
    the bundled ones), and times out fast. Returns ``(cpu_pct, ram_used_gb,
    ram_pct)``; any field ``None`` when the probe fails or can't be parsed.
    Never raises. (Rootless cgroup v2 routinely leaves CPU%/MEM as ``--`` here —
    that's a podman limitation; :func:`_host_cpu_pct` prefers the direct cgroup
    read and only falls back here.)
    """
    cli = _stats_cli(cfg)
    if cli is None:
        return None, None, None

    from winpodx.backend._hostenv import host_env

    env = host_env()
    base = cfg.pod.container_name
    container = _resolve_container_name(cli, base, env) or base
    try:
        result = subprocess.run(
            [cli, "stats", "--no-stream", "--format", "json", container],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=env,
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


# CPU% needs two reads of the cgroup's cumulative CPU time; cache the last one.
# Single-threaded per snapshot (the dashboard ticks one at a time), so a plain
# dict is enough — no lock.
_CPU_SAMPLE: dict[str, float | None] = {"mono": None, "usage_usec": None}


def _container_pid(cli: str, cfg: Config, env: dict) -> int | None:
    """Host-side init PID of the pod container, or ``None``. Never raises."""
    base = cfg.pod.container_name
    container = _resolve_container_name(cli, base, env) or base
    try:
        result = subprocess.run(
            [cli, "inspect", "--format", "{{.State.Pid}}", container],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("container pid probe failed: %s", e)
        return None
    if result.returncode != 0:
        return None
    try:
        pid = int(result.stdout.strip())
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _cgroup_dir_for_pid(pid: int) -> str | None:
    """Resolve a PID's cgroup v2 directory under ``/sys/fs/cgroup``.

    Reads the unified (``0::``) line of ``/proc/<pid>/cgroup``. Returns ``None``
    on cgroup v1, an unreadable proc entry, or a path that doesn't exist.
    """
    try:
        with open(f"/proc/{pid}/cgroup", encoding="ascii") as fh:
            rel = None
            for line in fh:
                if line.startswith("0::"):
                    rel = line.rstrip("\n").split("::", 1)[1]
                    break
    except OSError as e:
        log.debug("read /proc/%s/cgroup failed: %s", pid, e)
        return None
    if not rel:
        return None  # cgroup v1 (no unified hierarchy)
    cgdir = "/sys/fs/cgroup" + rel
    return cgdir if os.path.isdir(cgdir) else None


def _cgroup_memory_bytes(cgdir: str) -> int | None:
    """Bytes of memory used by the container, or ``None``. Never raises.

    Prefers the cgroup's own ``memory.current`` (accurate, present when the
    memory controller is delegated). Rootless setups frequently delegate only
    ``cpu``/``pids`` to the user slice — so ``memory.current`` is absent even
    though ``cpu.stat`` reads fine — in which case fall back to summing the
    ``VmRSS`` of every process in the cgroup (``cgroup.procs`` is always
    readable, and per-process RSS is kernel-accounted regardless of which
    controllers are on). For the dockur backend the QEMU process dominates, so
    its resident guest RAM is captured.
    """
    try:
        with open(f"{cgdir}/memory.current", encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        pass  # memory controller not delegated (common rootless) -- sum RSS

    try:
        with open(f"{cgdir}/cgroup.procs", encoding="ascii") as fh:
            pids = [int(line) for line in fh if line.strip()]
    except (OSError, ValueError) as e:
        log.debug("cgroup.procs read failed: %s", e)
        return None

    total = 0
    found = False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/status", encoding="ascii") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1]) * 1024  # kB -> bytes
                        found = True
                        break
        except (OSError, ValueError):
            continue  # process exited mid-scan, or no VmRSS (kernel thread)
    return total if found else None


def _cgroup_cpu_ram(cfg: Config) -> tuple[float | None, float | None, float | None]:
    """Read live CPU%/RAM straight from the container's cgroup v2 files.

    The rootless-reliable fallback for when ``podman stats`` blanks CPU/MEM.
    RAM comes from :func:`_cgroup_memory_bytes` (``memory.current``, or summed
    process RSS when the memory controller isn't delegated); CPU% from the delta
    of ``cpu.stat``'s cumulative ``usage_usec`` between calls (so the *first*
    call after start yields CPU ``None`` — no prior sample to diff against).
    Returns ``(cpu_pct, ram_used_gb, ram_pct)``; any field ``None`` on failure.
    Never raises.
    """
    cli = _stats_cli(cfg)
    if cli is None:
        return None, None, None

    from winpodx.backend._hostenv import host_env

    env = host_env()
    pid = _container_pid(cli, cfg, env)
    if pid is None:
        return None, None, None
    cgdir = _cgroup_dir_for_pid(pid)
    if cgdir is None:
        return None, None, None

    ram_used_gb: float | None = None
    ram_pct: float | None = None
    mem_bytes = _cgroup_memory_bytes(cgdir)
    if mem_bytes is not None:
        ram_used_gb = mem_bytes / _BYTES_IN_GB
        cap_bytes = int(cfg.pod.ram_gb) * _BYTES_IN_GB
        if cap_bytes > 0:
            ram_pct = max(0.0, min(100.0, mem_bytes / cap_bytes * 100.0))

    cpu_pct: float | None = None
    try:
        usage_usec: int | None = None
        with open(f"{cgdir}/cpu.stat", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("usage_usec"):
                    usage_usec = int(line.split()[1])
                    break
        now = time.monotonic()
        prev_mono = _CPU_SAMPLE["mono"]
        prev_usage = _CPU_SAMPLE["usage_usec"]
        if usage_usec is not None and prev_usage is not None and prev_mono is not None:
            dt = now - prev_mono
            if dt > 0:
                cores = max(1, int(cfg.pod.cpu_cores))
                busy_sec = (usage_usec - prev_usage) / 1_000_000.0
                cpu_pct = max(0.0, min(100.0, busy_sec / dt / cores * 100.0))
        if usage_usec is not None:
            _CPU_SAMPLE["mono"] = now
            _CPU_SAMPLE["usage_usec"] = usage_usec
    except (OSError, ValueError) as e:
        log.debug("cgroup cpu.stat read failed: %s", e)

    return cpu_pct, ram_used_gb, ram_pct


def _host_cpu_pct(cfg: Config) -> float | None:
    """Host-measured CPU% for the pod container (the QEMU process for dockur).

    Reads the cgroup ``cpu.stat`` delta first -- an instant local file read, so
    the dashboard doesn't pay the ~seconds ``podman stats`` sampling cost on
    every tick -- and only falls back to ``podman stats`` when the cgroup CPU
    isn't available. RAM is deliberately *not* read here: for a VM the host sees
    ~all the guest RAM resident (~100%), so RAM comes from the guest agent
    instead (:func:`_guest_resources`). Never raises.

    Note: the cgroup CPU needs two samples to produce a delta, so the very first
    tick returns the ``podman stats`` value (or ``None``); subsequent ticks use
    the instant cgroup read.
    """
    cpu, _ram_used, _ram_pct = _cgroup_cpu_ram(cfg)
    if cpu is not None:
        return cpu
    cpu_stats, _u, _p = _podman_stats_cpu_ram(cfg)
    return cpu_stats


def _guest_resources(
    cfg: Config,
) -> tuple[tuple[float | None, float | None, float | None], tuple[float | None, float | None]]:
    """Guest C: disk + physical RAM in one agent call.

    Returns ``((disk_total_gb, disk_used_gb, disk_pct), (ram_used_gb, ram_pct))``;
    each field ``None`` on failure. RAM is the guest's *own* counter (Windows
    physical RAM in use), which is the only meaningful figure for a VM -- the
    host-side cgroup/RSS reads sit near 100%. Agent-only (never flashes a
    FreeRDP window); short timeout so it fails fast. Never raises.
    """
    none_disk = (None, None, None)
    none_ram: tuple[float | None, float | None] = (None, None)
    try:
        from winpodx.core.disk import get_guest_resources

        gr = get_guest_resources(cfg, timeout=6)
    except Exception as e:  # noqa: BLE001 -- never let a probe break the snapshot
        log.debug("guest resources probe failed: %s", e)
        return none_disk, none_ram
    if gr is None:
        return none_disk, none_ram

    disk = none_disk
    if gr.disk is not None:
        try:
            disk = (
                gr.disk.total_bytes / _BYTES_IN_GB,
                gr.disk.used_bytes / _BYTES_IN_GB,
                gr.disk.used_pct,
            )
        except (AttributeError, TypeError, ZeroDivisionError) as e:
            log.debug("guest disk math failed: %s", e)

    ram = none_ram
    if gr.ram_used_bytes is not None and gr.ram_total_bytes:
        used_gb = gr.ram_used_bytes / _BYTES_IN_GB
        pct = max(0.0, min(100.0, gr.ram_used_bytes / gr.ram_total_bytes * 100.0))
        ram = (used_gb, pct)

    return disk, ram


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


def pod_resource_snapshot(
    cfg: Config,
    *,
    pod_state: str | None = None,
    with_disk: bool = True,
) -> ResourceSnapshot:
    """Build a best-effort :class:`ResourceSnapshot` for the dashboard.

    Always returns a snapshot -- never raises. Configured caps come from
    ``cfg.pod``. When the pod is running, two probes run **concurrently** under
    a hard time cap so a slow guest can't stall the dashboard: host CPU% (fast,
    every tick) and the guest disk+RAM agent call. RAM comes from *inside*
    Windows (the host sees ~100% for a VM), so it shares the guest round-trip
    with disk.

    ``pod_state`` lets the caller pass the already-known state (the GUI keeps
    it fresh on its own timer) so we skip a redundant, slow re-probe.
    ``with_disk=False`` skips the guest agent call entirely (the caller polls
    disk+RAM on a slower cadence and caches them); CPU still updates every tick.
    """
    state = pod_state if pod_state is not None else _pod_state(cfg)

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

    if state == "running":
        box: dict[str, object] = {}

        def _cpu() -> None:
            try:
                box["cpu"] = _host_cpu_pct(cfg)
            except Exception:  # noqa: BLE001 -- best-effort probe
                box["cpu"] = None

        def _guest() -> None:
            # Disk + guest RAM share one agent round-trip; only on the slow
            # cadence (with_disk). CPU stays host-side + fast every tick.
            try:
                box["guest"] = _guest_resources(cfg) if with_disk else None
            except Exception:  # noqa: BLE001 -- best-effort probe
                box["guest"] = None

        workers = [
            threading.Thread(target=_cpu, daemon=True),
            threading.Thread(target=_guest, daemon=True),
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=8)  # hard cap regardless of any single probe hanging

        cpu_val = box.get("cpu")
        cpu_pct = cpu_val if isinstance(cpu_val, (int, float)) else None
        guest = box.get("guest")
        if guest is not None:
            (disk_total_gb, disk_used_gb, disk_pct), (ram_used_gb, ram_pct) = guest

    return ResourceSnapshot(
        pod_state=state,
        cpu_cores=cpu_cores,
        cpu_pct=cpu_pct,
        ram_gb=ram_gb,
        ram_used_gb=ram_used_gb,
        ram_pct=ram_pct,
        disk_total_gb=disk_total_gb,
        disk_used_gb=disk_used_gb,
        disk_pct=disk_pct,
    )
