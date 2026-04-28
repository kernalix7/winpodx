"""Host CPU + RAM detection and VM tier presets (v0.2.1).

Reads `/proc/meminfo` for total RAM and `os.cpu_count()` for CPU
threads, then maps to one of three tiers — low / mid / high — each
with sensible CPU + RAM values for the dockur/windows VM. Used by
``setup_cmd`` to pre-fill defaults during interactive setup and by
the GUI Settings page to surface a "Recommended for your machine"
hint.

Tier policy (deliberate, not auto-derived):

    Host RAM      Host CPU      Tier   VM CPU   VM RAM
    >=32 GB       >=12 thr      high     8       12 GB
    16-32 GB       6-12 thr     mid      4        6 GB
    <16 GB         <6 thr       low      2        4 GB

Both axes must clear the threshold to land in mid/high — a 64 GB
machine with 4 cores still gets "low" since CPU is the bottleneck.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class HostSpecs:
    cpu_threads: int
    ram_gb: int


@dataclass
class TierPreset:
    name: str  # "low" | "mid" | "high"
    label: str  # human-friendly Korean label
    cpu_cores: int
    ram_gb: int


_TIER_LOW = TierPreset(name="low", label="하 (Low)", cpu_cores=2, ram_gb=4)
_TIER_MID = TierPreset(name="mid", label="중 (Mid)", cpu_cores=4, ram_gb=6)
_TIER_HIGH = TierPreset(name="high", label="상 (High)", cpu_cores=8, ram_gb=12)


def detect_host_specs() -> HostSpecs:
    """Return CPU thread count and total RAM (GB, integer floor) of the host.

    Falls back to (1, 4) on parse error so callers always get a usable
    object — better to recommend conservatively than crash the wizard.
    """
    cpu = os.cpu_count() or 1
    ram_gb = _read_proc_meminfo_total_gb()
    return HostSpecs(cpu_threads=cpu, ram_gb=ram_gb)


def _read_proc_meminfo_total_gb() -> int:
    meminfo = Path("/proc/meminfo")
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                # `MemTotal:        8167456 kB`
                kb = int(line.split()[1])
                return max(1, kb // 1024 // 1024)
    except (OSError, ValueError, IndexError) as e:
        log.debug("could not read /proc/meminfo: %s", e)
    return 4  # safe default


def recommend_tier(specs: HostSpecs) -> TierPreset:
    """Pick a VM preset based on host CPU + RAM.

    Both axes must clear the threshold for the higher tier — a host
    with lots of RAM but few CPU threads (or vice versa) gets the
    lower tier so we don't over-allocate the constrained resource.
    """
    if specs.ram_gb >= 32 and specs.cpu_threads >= 12:
        return _TIER_HIGH
    if specs.ram_gb >= 16 and specs.cpu_threads >= 6:
        return _TIER_MID
    return _TIER_LOW


def all_tiers() -> list[TierPreset]:
    """Three presets in low -> high order. Useful for GUI dropdowns."""
    return [_TIER_LOW, _TIER_MID, _TIER_HIGH]
