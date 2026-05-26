# SPDX-License-Identifier: MIT
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


_TIER_LOW = TierPreset(name="low", label="Low", cpu_cores=2, ram_gb=4)
_TIER_MID = TierPreset(name="mid", label="Mid", cpu_cores=4, ram_gb=6)
_TIER_HIGH = TierPreset(name="high", label="High", cpu_cores=8, ram_gb=12)


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


# ---------------------------------------------------------------------------
# Tuning capability detection — issue #215.
#
# Standard Windows-on-KVM tuning checklist contains items that are safe to
# enable only when the host meets a precondition (`+invtsc` needs invariant
# TSC; io_uring needs Linux >= 5.6; hugepages need the operator to pre-set
# `vm.nr_hugepages`). Auto-detect once at compose time so users don't have
# to know any of this; expose the detection result so they can see what was
# applied and override if needed via `cfg.pod.tuning_profile`.
# ---------------------------------------------------------------------------


@dataclass
class TuningCapability:
    """Host-side facts that gate optional perf tunings.

    All fields are best-effort: detection failure is treated as "feature
    absent" so we degrade safely to the dockur baseline.
    """

    invtsc: bool
    io_uring: bool
    hugepages_enabled: bool
    dedicated_host: bool
    kernel_version: tuple[int, int] | None
    cpu_vendor: str  # "intel" | "amd" | "arm" | "unknown"
    # #245: nested-KVM expose. True when /sys/module/kvm_intel/parameters/nested
    # or /sys/module/kvm_amd/parameters/nested reports Y (or the boot
    # cmdline equivalent). Gates the +vmx/+svm CPU feature pass-through
    # and the hv-evmcs (Intel) optimisation.
    nested_kvm: bool = False


@dataclass
class TuningProfile:
    """Resolved set of tunings for a given (capability, user-pref) pair."""

    name: str  # "auto" | "safe" | "off" | "manual"
    apply_invtsc: bool
    apply_io_uring: bool
    apply_hugepages: bool
    apply_cpu_pinning: bool
    apply_platform_tick: bool
    apply_no_balloon: bool
    # #245: extended Windows-on-KVM tuning set.
    #   apply_hv_enlightenments -- emit the hv-* CPU sub-options that tell
    #     Windows it's running under a paravirtualised hypervisor (relaxed,
    #     vapic, vpindex, runtime, synic, reset, frequencies,
    #     reenlightenment, tlbflush, ipi, spinlocks=0x1fff, stimer,
    #     stimer-direct) + the -no-hpet QEMU machine arg. Always safe on
    #     Windows guests; significant scheduling / timer wins.
    #   apply_virtio_rng -- expose virtio-rng-pci backed by /dev/urandom
    #     so Windows entropy pool fills quickly on first boot (avoids
    #     CryptoAPI / TLS handshake stalls).
    #   apply_evmcs -- Intel-only nested-VMCS optimisation; no-op overhead
    #     when guest isn't running nested VMs but speeds them up when it
    #     is.
    #   apply_nested_virt -- expose +vmx (Intel) / +svm (AMD) CPU feature
    #     so Windows guest can host Hyper-V / WSL2 / Docker Desktop.
    apply_hv_enlightenments: bool = False
    apply_virtio_rng: bool = False
    apply_evmcs: bool = False
    apply_nested_virt: bool = False


_PROFILE_OFF = TuningProfile(
    name="off",
    apply_invtsc=False,
    apply_io_uring=False,
    apply_hugepages=False,
    apply_cpu_pinning=False,
    apply_platform_tick=False,
    apply_no_balloon=False,
    apply_hv_enlightenments=False,
    apply_virtio_rng=False,
    apply_evmcs=False,
    apply_nested_virt=False,
)


def _read_cpuinfo_flags() -> set[str]:
    try:
        text = Path("/proc/cpuinfo").read_text()
    except OSError as e:
        log.debug("could not read /proc/cpuinfo: %s", e)
        return set()
    for line in text.splitlines():
        if line.startswith("flags") or line.startswith("Features"):
            _, _, rest = line.partition(":")
            return {tok.strip() for tok in rest.split() if tok.strip()}
    return set()


def _read_cpu_vendor() -> str:
    try:
        text = Path("/proc/cpuinfo").read_text()
    except OSError:
        return "unknown"
    for line in text.splitlines():
        if line.startswith("vendor_id"):
            _, _, rest = line.partition(":")
            v = rest.strip().lower()
            if "intel" in v:
                return "intel"
            if "amd" in v:
                return "amd"
            return v or "unknown"
        if line.startswith("CPU implementer"):
            return "arm"
    return "unknown"


def _read_kernel_version() -> tuple[int, int] | None:
    try:
        release = os.uname().release
    except OSError:
        return None
    head = release.split("-", 1)[0]
    parts = head.split(".")
    try:
        return (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return None


def _read_hugepages_total() -> int:
    """Read `HugePages_Total` from /proc/meminfo. Returns 0 on failure."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("HugePages_Total:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError) as e:
        log.debug("could not read HugePages_Total: %s", e)
    return 0


def _read_nested_kvm() -> bool:
    """Return True when the host kernel exposes nested-KVM.

    Probes ``/sys/module/kvm_intel/parameters/nested`` and the AMD
    equivalent. The kernel renders the value as ``Y`` / ``N`` (newer
    kernels) or ``1`` / ``0`` (older). Either signal is accepted; any
    other content or read error reports "feature absent" so callers
    safely skip the +vmx/+svm + hv-evmcs pass-through.

    Only one module is loaded at a time per host (vendor-specific), so a
    successful read from either path is sufficient.
    """
    for module in ("kvm_intel", "kvm_amd"):
        try:
            val = Path(f"/sys/module/{module}/parameters/nested").read_text().strip()
        except OSError:
            continue
        if val and val[0] in ("Y", "y", "1"):
            return True
    return False


def _idle_cpu_count() -> int:
    """Best-effort estimate of CPUs currently idle.

    We avoid sampling /proc/stat (slow, needs two reads); use
    `os.getloadavg()` as a rough proxy. load1 < cpu_count means there's
    headroom. Returns max(0, cpu_count - ceil(load1)).
    """
    cpu = os.cpu_count() or 1
    try:
        load1, _, _ = os.getloadavg()
    except OSError:
        return cpu
    import math

    used = math.ceil(load1)
    return max(0, cpu - used)


def detect_tuning_capability(*, vm_cpu_cores: int, vm_ram_gb: int) -> TuningCapability:
    """Probe the host for everything `recommend_tuning_profile` needs.

    `vm_cpu_cores` / `vm_ram_gb` are the VM's allocation; we use them to
    decide whether the host can comfortably afford dedicated resources
    (`dedicated_host`). Other fields are pure host-side facts.
    """
    flags = _read_cpuinfo_flags()
    invtsc = ("constant_tsc" in flags) and ("nonstop_tsc" in flags)
    kernel = _read_kernel_version()
    io_uring = kernel is not None and (kernel[0], kernel[1]) >= (5, 6)
    hugepages = _read_hugepages_total() > 0

    host = detect_host_specs()
    idle_cpu = _idle_cpu_count()
    # "Dedicated" = at least twice the VM's allocation currently free on
    # both axes. Factor-of-two cushion avoids pinning a VM on a shared
    # workstation where another tool spikes briefly.
    dedicated = idle_cpu >= vm_cpu_cores * 2 and host.ram_gb >= vm_ram_gb * 2

    return TuningCapability(
        invtsc=invtsc,
        io_uring=io_uring,
        hugepages_enabled=hugepages,
        dedicated_host=dedicated,
        kernel_version=kernel,
        cpu_vendor=_read_cpu_vendor(),
        nested_kvm=_read_nested_kvm(),
    )


def recommend_tuning_profile(cap: TuningCapability, *, user_pref: str = "auto") -> TuningProfile:
    """Resolve a TuningProfile from host capability and user preference.

    `user_pref` is `cfg.pod.tuning_profile`:
      * `"off"` — everything off, dockur defaults only.
      * `"safe"` — only Tier-1 tunings that don't require host setup
        (currently: `+invtsc`, `platform_tick`).
      * `"auto"` (default) — apply everything the host can support.
        Soft-gated knobs (CPU pinning, no-balloon) check
        ``cap.dedicated_host`` so we don't starve other workloads on a
        shared workstation.
      * `"performance"` — same shape as ``auto`` but the soft-gates are
        bypassed. CPU pinning + no-balloon flip on regardless of
        ``cap.dedicated_host``. The user is telling us "treat this box
        as dedicated to winpodx -- minimise guest latency at the cost
        of other host workloads". Hard-gated knobs (``+invtsc``,
        ``io_uring`` -- the ones QEMU would reject or the kernel would
        crash on if applied unsupported) still respect capability
        detection.
      * `"manual"` — return `safe` shape; callers are expected to override
        individual flags from `cfg.pod.tuning_*` keys instead. Helper
        stays pure (no Config read) so it's easy to test.
    """
    if user_pref == "off":
        return _PROFILE_OFF

    is_x86 = cap.cpu_vendor in ("intel", "amd")
    # "performance" forces soft-gated knobs on; "auto" defers to detection.
    treat_as_dedicated = (user_pref == "performance") or cap.dedicated_host

    if user_pref in ("safe", "manual"):
        return TuningProfile(
            name=user_pref,
            apply_invtsc=cap.invtsc and is_x86,
            apply_io_uring=False,
            apply_hugepages=False,
            apply_cpu_pinning=False,
            # Always safe on a winpodx-owned guest; reversible via bcdedit
            # /deletevalue. Keep on under "safe" so users who explicitly
            # request the conservative profile still get the no-cost
            # timer win.
            apply_platform_tick=True,
            apply_no_balloon=False,
            # #245: hv-* + virtio-rng are Windows-guest-safe + no host
            # setup needed -- include in "safe" so the conservative
            # profile still gets the scheduling / entropy wins. evmcs +
            # nested-virt need explicit host-side nested KVM module
            # option => excluded from "safe" by definition.
            apply_hv_enlightenments=is_x86,
            apply_virtio_rng=True,
            apply_evmcs=False,
            apply_nested_virt=False,
        )

    # auto + performance share the same code path; only the
    # treat_as_dedicated flag differs (auto respects detection,
    # performance forces it).
    return TuningProfile(
        name="performance" if user_pref == "performance" else "auto",
        apply_invtsc=cap.invtsc and is_x86,
        apply_io_uring=cap.io_uring,
        apply_hugepages=cap.hugepages_enabled,
        apply_cpu_pinning=treat_as_dedicated,
        apply_platform_tick=True,
        apply_no_balloon=treat_as_dedicated,
        # #245: hv-* + virtio-rng always on under auto when host is x86.
        # evmcs (Intel-only) + nested-virt (Intel/AMD) gated on detected
        # nested-KVM module option -- a no-op for users who haven't
        # opted in.
        apply_hv_enlightenments=is_x86,
        apply_virtio_rng=True,
        apply_evmcs=cap.nested_kvm and cap.cpu_vendor == "intel",
        apply_nested_virt=cap.nested_kvm and is_x86,
    )


def format_tuning_summary(cap: TuningCapability, profile: TuningProfile) -> str:
    """Render a human-readable summary for `winpodx info` / setup."""
    kv = ".".join(str(x) for x in cap.kernel_version) if cap.kernel_version else "?"

    def yn(b: bool) -> str:
        return "yes" if b else "no"

    lines = [
        f"  invtsc:        {yn(cap.invtsc):<4}  ({cap.cpu_vendor})",
        f"  io_uring:      {yn(cap.io_uring):<4}  (kernel {kv}, need >= 5.6)",
        f"  hugepages:     {yn(cap.hugepages_enabled):<4}  (sysctl vm.nr_hugepages)",
        f"  dedicated:     {yn(cap.dedicated_host):<4}",
        f"  nested_kvm:    {yn(cap.nested_kvm):<4}  (/sys/module/kvm_*/parameters/nested)",
        "",
        f"  Profile: {profile.name}",
        f"    +invtsc:        {yn(profile.apply_invtsc)}",
        f"    io_uring aio:   {yn(profile.apply_io_uring)}",
        f"    hugepages:      {yn(profile.apply_hugepages)}",
        f"    CPU pinning:    {yn(profile.apply_cpu_pinning)}",
        f"    platform_tick:  {yn(profile.apply_platform_tick)}",
        f"    no balloon:     {yn(profile.apply_no_balloon)}",
        f"    hv-* + no-hpet: {yn(profile.apply_hv_enlightenments)}",
        f"    virtio-rng:     {yn(profile.apply_virtio_rng)}",
        f"    nested virt:    {yn(profile.apply_nested_virt)}",
        f"    hv-evmcs:       {yn(profile.apply_evmcs)}",
    ]
    return "\n".join(lines)
