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
    that's a podman limitation; :func:`_live_cpu_ram` then falls back to reading
    the cgroup directly.)
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


def _live_cpu_ram(cfg: Config) -> tuple[float | None, float | None, float | None]:
    """Live CPU%/RAM for the pod, with a cgroup-direct fallback.

    Tries ``<cli> stats`` first (cheap, works on most setups). Rootless podman
    very often reports CPU% (and sometimes MEM) as ``--`` there, so any field it
    leaves ``None`` is back-filled from the container's cgroup v2 files —
    ``memory.current`` for RAM and ``cpu.stat``'s ``usage_usec`` delta for CPU%.
    Returns ``(cpu_pct, ram_used_gb, ram_pct)``; never raises.
    """
    cpu_pct, ram_used_gb, ram_pct = _podman_stats_cpu_ram(cfg)
    if cpu_pct is not None and ram_pct is not None:
        return cpu_pct, ram_used_gb, ram_pct

    c_cpu, c_used, c_pct = _cgroup_cpu_ram(cfg)
    if cpu_pct is None:
        cpu_pct = c_cpu
    if ram_pct is None:
        ram_pct = c_pct
        if ram_used_gb is None:
            ram_used_gb = c_used
    return cpu_pct, ram_used_gb, ram_pct


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


def _cgroup_cpu_ram(cfg: Config) -> tuple[float | None, float | None, float | None]:
    """Read live CPU%/RAM straight from the container's cgroup v2 files.

    The rootless-reliable fallback for when ``podman stats`` blanks CPU/MEM.
    RAM comes from ``memory.current`` (one read); CPU% from the delta of
    ``cpu.stat``'s cumulative ``usage_usec`` between calls (so the *first* call
    after start yields CPU ``None`` — there's no prior sample to diff against).
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
    try:
        with open(f"{cgdir}/memory.current", encoding="ascii") as fh:
            mem_bytes = int(fh.read().strip())
        ram_used_gb = mem_bytes / _BYTES_IN_GB
        cap_bytes = int(cfg.pod.ram_gb) * _BYTES_IN_GB
        if cap_bytes > 0:
            ram_pct = max(0.0, min(100.0, mem_bytes / cap_bytes * 100.0))
    except (OSError, ValueError) as e:
        log.debug("cgroup memory.current read failed: %s", e)

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


def _guest_disk(cfg: Config) -> tuple[float | None, float | None, float | None]:
    """Probe Windows guest C: usage. Returns ``(total_gb, used_gb, pct)``.

    Reuses :func:`winpodx.core.disk.get_guest_disk_usage`; any failure
    yields all-``None``. Never raises.
    """
    try:
        from winpodx.core.disk import get_guest_disk_usage

        # agent_only: a passive dashboard poll must NOT fall back to a FreeRDP
        # RemoteApp PowerShell (it flashes a visible window in the guest every
        # run). If the agent isn't reachable (e.g. guest at the login screen),
        # this returns None and the disk bar just shows its cached/"n/a" value.
        # Short timeout so it fails fast rather than hanging the dashboard.
        usage = get_guest_disk_usage(cfg, timeout=6, agent_only=True)
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


def pod_resource_snapshot(
    cfg: Config,
    *,
    pod_state: str | None = None,
    with_disk: bool = True,
) -> ResourceSnapshot:
    """Build a best-effort :class:`ResourceSnapshot` for the dashboard.

    Always returns a snapshot -- never raises. Configured caps come from
    ``cfg.pod``. Live CPU/RAM and guest disk are probed only when the pod is
    running, and they run **concurrently** with a hard time cap so a slow
    guest (e.g. sitting at the login screen) can't stall the dashboard.

    ``pod_state`` lets the caller pass the already-known state (the GUI keeps
    it fresh on its own timer) so we skip a redundant, slow re-probe.
    ``with_disk=False`` skips the expensive guest probe entirely (the caller
    polls disk on a slower cadence and caches it).
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
        box: dict[str, tuple] = {}

        def _cr() -> None:
            try:
                box["cr"] = _live_cpu_ram(cfg)
            except Exception:  # noqa: BLE001 -- best-effort probe
                box["cr"] = (None, None, None)

        def _dk() -> None:
            try:
                box["dk"] = _guest_disk(cfg) if with_disk else (None, None, None)
            except Exception:  # noqa: BLE001 -- best-effort probe
                box["dk"] = (None, None, None)

        workers = [
            threading.Thread(target=_cr, daemon=True),
            threading.Thread(target=_dk, daemon=True),
        ]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=8)  # hard cap regardless of any single probe hanging

        cpu_pct, ram_used_gb, ram_pct = box.get("cr", (None, None, None))
        disk_total_gb, disk_used_gb, disk_pct = box.get("dk", (None, None, None))

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
