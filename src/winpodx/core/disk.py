# SPDX-License-Identifier: MIT
"""Windows guest disk auto-grow + manual grow (#318).

The Windows system volume sits on dockur's virtual disk, whose ceiling is
``cfg.pod.disk_size``. dockur grows the underlying image when ``disk_size``
increases and the container is recreated, but it never extends the guest's
C: partition for an *existing* install -- the extra space lands as
unallocated. This module closes that gap:

- :func:`get_guest_disk_usage` probes C: size / free via the agent ``/exec``
  (PowerShell ``Get-Volume``), no agent change required.
- :func:`grow_disk` bumps ``disk_size`` (bounded by the effective cap --
  the optional ``disk_max_size`` and/or host free space minus a reserve),
  recreates the container so dockur grows the image, waits for the guest,
  then extends C: to fill via ``Resize-Partition``.
- :func:`maybe_autogrow` is the daemon hook: when ``disk_autogrow`` is on,
  the pod is idle, and used space crosses ``disk_autogrow_threshold_pct``,
  it grows just enough to restore ``disk_autogrow_target_free_pct`` free
  (in whole ``disk_autogrow_increment`` steps), never past the cap.

dockur's virtual disk is sparse, so raising the ceiling doesn't consume
host space up front -- the host fills only as Windows writes -- and the
host reserve keeps the ceiling within what the host can actually back, so
a later guest fill can't ENOSPC the host. dockur has no online resize, so
each grow recreates the container (a quick guest reboot); auto-grow runs
only while idle for that reason.

All guest-side work goes through ``windows_exec.run_in_windows`` (agent
``/exec`` with FreeRDP fallback) -- there is no new agent endpoint.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from winpodx.core.config import Config

log = logging.getLogger(__name__)

# dockur size shape: <integer><unit?> where unit is K/M/G/T (default bytes
# per dockur, but winpodx always emits an explicit unit). Mirrors
# ``config._DISK_SIZE_RE`` but captures the parts for arithmetic.
_SIZE_RE = re.compile(r"^\s*([1-9][0-9]{0,4})\s*([KMGTkmgt]?)\s*$")
_UNIT_BYTES = {
    "": 1,
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
}


class DiskError(Exception):
    """Raised when a grow operation can't proceed (validation / lifecycle)."""


@dataclass
class DiskUsage:
    """Snapshot of the guest C: volume."""

    total_bytes: int
    free_bytes: int

    @property
    def used_bytes(self) -> int:
        return max(0, self.total_bytes - self.free_bytes)

    @property
    def used_pct(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return 100.0 * self.used_bytes / self.total_bytes


@dataclass
class GrowResult:
    old_size: str
    new_size: str
    partition_extended: bool
    note: str = ""


def parse_size(value: str) -> int:
    """Parse a dockur size string (e.g. ``"64G"``) to bytes.

    Raises :class:`DiskError` on anything that isn't a valid size shape.
    """
    m = _SIZE_RE.match(value or "")
    if not m:
        raise DiskError(f"invalid disk size {value!r}")
    n, unit = m.group(1), m.group(2).upper()
    return int(n) * _UNIT_BYTES[unit]


def format_size(num_bytes: int) -> str:
    """Format bytes back to the largest whole dockur unit (e.g. ``"96G"``).

    Rounds *up* to the next whole unit so a grow never produces a target
    smaller than requested. Falls back to the next unit down only when the
    value isn't a whole multiple of the larger one.
    """
    if num_bytes <= 0:
        raise DiskError(f"non-positive size {num_bytes!r}")
    for unit in ("T", "G", "M", "K"):
        factor = _UNIT_BYTES[unit]
        if num_bytes >= factor and num_bytes % factor == 0:
            return f"{num_bytes // factor}{unit}"
    # Not a whole multiple of any unit -- round up to the next whole GiB
    # (disk_size never needs finer granularity than that in practice).
    gib = (num_bytes + _UNIT_BYTES["G"] - 1) // _UNIT_BYTES["G"]
    return f"{gib}G"


# Auto-grow must never consume the last of the host disk -- keep a reserve
# free so the host stays healthy. Whichever is larger: a flat floor or a
# fraction of the host filesystem.
_HOST_RESERVE_FLOOR = 10 * (1024**3)  # 10 GiB
_HOST_RESERVE_FRACTION = 0.10  # or 10% of the host filesystem


def _host_free_and_total(cfg: Config) -> tuple[int, int] | None:
    """(free, total) bytes of the filesystem backing the Windows disk image.

    Returns None when the storage path can't be resolved (named-volume mode
    on an unknown root) -- callers treat None as "can't verify host capacity".
    """
    sp = cfg.pod.storage_path
    if not sp:
        return None
    try:
        path = Path(sp).expanduser()
        # Walk up to the nearest existing parent so statvfs succeeds even
        # before the storage dir is materialised.
        while not path.exists() and path != path.parent:
            path = path.parent
        usage = shutil.disk_usage(path)
        return usage.free, usage.total
    except OSError as e:  # noqa: BLE001
        log.debug("host free-space probe failed for %r: %s", sp, e)
        return None


def effective_max_bytes(cfg: Config, current_bytes: int) -> int | None:
    """Largest disk size a grow may target, in bytes.

    The bound is the *minimum* of:
      * the explicit ``disk_max_size`` cap, if the user set one; and
      * what the host can actually back -- ``current + (host_free - reserve)``
        so auto-grow can't fill the host disk.

    Returns None when neither bound applies (no explicit cap and the host
    capacity can't be probed) -- i.e. unbounded, host-trusted.
    """
    caps: list[int] = []
    if cfg.pod.disk_max_size:
        try:
            caps.append(parse_size(cfg.pod.disk_max_size))
        except DiskError:
            pass
    ht = _host_free_and_total(cfg)
    if ht is not None:
        free, total = ht
        reserve = max(_HOST_RESERVE_FLOOR, int(total * _HOST_RESERVE_FRACTION))
        headroom = free - reserve
        # Even with no headroom the disk can't shrink below its current size.
        caps.append(current_bytes + max(0, headroom))
    if not caps:
        return None
    return min(caps)


# PowerShell: emit C: total + free as JSON. ``Get-Volume`` is present on
# every supported Windows edition; SizeRemaining is free bytes.
_USAGE_PS = (
    "$v = Get-Volume -DriveLetter C -ErrorAction Stop; "
    "[Console]::Out.Write((ConvertTo-Json -Compress "
    "@{ total = [int64]$v.Size; free = [int64]$v.SizeRemaining }))"
)

# PowerShell: extend C: into the new space at the end of the disk.
#
# The simple case (`SizeMax > current`) means the free space is directly
# after C: -- just resize. But dockur's Windows layout puts a small WinRE
# **Recovery partition immediately after C:**, so the space a grow adds lands
# *after* that recovery partition and C: can't reach it: SizeMax == current
# and a naive resize reports "already-max" while the disk has unallocated
# tail space (exactly what @drjwhitty hit -- disk 96G, C: 64G, 32G free
# stranded behind the recovery partition).
#
# When that's the case, detach WinRE, delete the blocking recovery partition,
# extend C: to fill, and re-enable WinRE (it falls back to C:\Windows when no
# dedicated partition exists -- fine for a VM). Guarded so it only ever
# removes a partition typed Recovery that sits at/after C:'s end.
_EXTEND_PS = r"""
$ErrorActionPreference = 'Stop'
$c = Get-Partition -DriveLetter C
$max = (Get-PartitionSupportedSize -DriveLetter C).SizeMax
if ($max -gt $c.Size) {
    Resize-Partition -DriveLetter C -Size $max
    Write-Output 'extended'
} else {
    $disk = Get-Disk -Number $c.DiskNumber
    $tailFree = $disk.Size - ($c.Offset + $c.Size)
    $cEnd = $c.Offset + $c.Size
    $rec = Get-Partition -DiskNumber $c.DiskNumber |
        Where-Object { $_.Type -eq 'Recovery' -and $_.Offset -ge $cEnd } |
        Sort-Object Offset | Select-Object -First 1
    if ($rec -and $tailFree -gt 0) {
        try { reagentc /disable | Out-Null } catch {}
        $dn = $rec.DiskNumber; $pn = $rec.PartitionNumber
        Remove-Partition -DiskNumber $dn -PartitionNumber $pn -Confirm:$false
        $max2 = (Get-PartitionSupportedSize -DriveLetter C).SizeMax
        Resize-Partition -DriveLetter C -Size $max2
        try { reagentc /enable | Out-Null } catch {}
        Write-Output 'extended-after-recovery-removal'
    } else {
        Write-Output 'already-max'
    }
}
""".strip()


def get_guest_disk_usage(
    cfg: Config, *, timeout: int = 30, agent_only: bool = False
) -> DiskUsage | None:
    """Probe the guest C: volume. Returns None when the guest is unreachable
    or the output can't be parsed (callers treat None as "skip this round").

    Default runs through ``run_via_transport`` (HTTP agent first, FreeRDP
    RemoteApp fallback). ``agent_only=True`` restricts the probe to the agent
    and returns None if it isn't reachable — used by the GUI dashboard's
    passive poll so a background metric never falls back to a FreeRDP RemoteApp
    PowerShell, which flashes a visible window in the guest on every run.
    """
    if agent_only:
        try:
            from winpodx.core.transport import dispatch

            transport = dispatch(cfg)
        except Exception as e:  # noqa: BLE001 — agent optional; degrade to None
            log.debug("disk-usage agent dispatch failed: %s", e)
            return None
        if transport is None or getattr(transport, "name", None) != "agent":
            return None
        try:
            res = transport.exec(_USAGE_PS, timeout=timeout, description="disk-usage")
        except Exception as e:  # noqa: BLE001 — never flash / never raise here
            log.debug("disk-usage agent probe failed: %s", e)
            return None
        rc, stdout = res.rc, res.stdout
    else:
        from winpodx.core.windows_exec import WindowsExecError, run_via_transport

        try:
            result = run_via_transport(cfg, _USAGE_PS, timeout=timeout, description="disk-usage")
        except WindowsExecError as e:
            log.debug("disk-usage probe exec failed: %s", e)
            return None
        rc, stdout = result.rc, result.stdout

    if rc != 0:
        log.debug("disk-usage probe rc=%s", rc)
        return None
    try:
        data = json.loads(stdout.strip())
        total = int(data["total"])
        free = int(data["free"])
    except (ValueError, KeyError, TypeError) as e:
        log.debug("disk-usage probe unparseable %r: %s", stdout, e)
        return None
    if total <= 0:
        return None
    return DiskUsage(total_bytes=total, free_bytes=free)


@dataclass
class GuestResources:
    """Guest-internal disk + physical RAM, fetched in one agent call."""

    disk: DiskUsage | None
    ram_used_bytes: int | None
    ram_total_bytes: int | None


# PowerShell: C: volume + physical RAM as one JSON blob. Win32_OperatingSystem
# reports memory in KiB, so x1024 -> bytes. RAM is read from *inside* Windows
# on purpose: for the dockur (QEMU) backend the host sees ~all the guest RAM
# resident (qemu maps it), so host cgroup / RSS reads sit near 100% and are
# meaningless -- only the guest's own counter reflects actual usage.
_RESOURCES_PS = (
    "$v = Get-Volume -DriveLetter C -ErrorAction Stop; "
    "$os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop; "
    "[Console]::Out.Write((ConvertTo-Json -Compress @{ "
    "total = [int64]$v.Size; free = [int64]$v.SizeRemaining; "
    "ramTotal = [int64]$os.TotalVisibleMemorySize * 1024; "
    "ramFree = [int64]$os.FreePhysicalMemory * 1024 }))"
)


def get_guest_resources(cfg: Config, *, timeout: int = 6) -> GuestResources | None:
    """Guest C: usage + physical RAM in a single agent ``/exec`` call.

    Agent-only by design: this is the GUI dashboard's passive poll, so it must
    never fall back to a FreeRDP RemoteApp PowerShell (which flashes a visible
    window in the guest every run). Returns ``None`` when the agent isn't
    reachable (e.g. guest at the login screen) or the output can't be parsed --
    callers treat ``None`` as "skip this round". Never raises.
    """
    try:
        from winpodx.core.transport import dispatch

        transport = dispatch(cfg)
    except Exception as e:  # noqa: BLE001 -- agent optional; degrade to None
        log.debug("guest-resources dispatch failed: %s", e)
        return None
    if transport is None or getattr(transport, "name", None) != "agent":
        return None
    try:
        res = transport.exec(_RESOURCES_PS, timeout=timeout, description="resources")
    except Exception as e:  # noqa: BLE001 -- never flash / never raise here
        log.debug("guest-resources probe failed: %s", e)
        return None
    if res.rc != 0:
        log.debug("guest-resources probe rc=%s", res.rc)
        return None
    try:
        data = json.loads(res.stdout.strip())
        total = int(data["total"])
        free = int(data["free"])
        ram_total = int(data["ramTotal"])
        ram_free = int(data["ramFree"])
    except (ValueError, KeyError, TypeError) as e:
        log.debug("guest-resources unparseable %r: %s", res.stdout, e)
        return None

    disk = DiskUsage(total_bytes=total, free_bytes=free) if total > 0 else None
    if ram_total > 0:
        ram_used = max(0, ram_total - ram_free)
        ram_total_out: int | None = ram_total
    else:
        ram_used = None
        ram_total_out = None
    return GuestResources(disk=disk, ram_used_bytes=ram_used, ram_total_bytes=ram_total_out)


def extend_guest_system_volume(cfg: Config, *, timeout: int = 120) -> bool:
    """Extend C: to fill the (now larger) virtual disk. Returns True on
    success or when already at max; False when the guest call fails."""
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(
            cfg, _EXTEND_PS, timeout=timeout, description="extend-system-volume"
        )
    except WindowsExecError as e:
        log.warning("partition extend exec failed: %s", e)
        return False
    if not result.ok:
        log.warning("partition extend rc=%s stderr=%s", result.rc, result.stderr)
        return False
    log.info("partition extend: %s", result.stdout.strip() or "ok")
    return True


def compute_grow_target(
    cfg: Config,
    *,
    target_size: str | None = None,
    increment: str | None = None,
) -> str:
    """Resolve the new disk size for a grow, enforcing the effective cap.

    ``target_size`` wins if given (absolute); otherwise current + ``increment``
    (default ``cfg.pod.disk_autogrow_increment``). Raises :class:`DiskError`
    if the result wouldn't grow the disk or exceeds the effective maximum
    (explicit ``disk_max_size`` and/or host free space minus reserve).
    """
    current = parse_size(cfg.pod.disk_size)
    if target_size is not None:
        new = parse_size(target_size)
    else:
        inc = parse_size(increment or cfg.pod.disk_autogrow_increment)
        new = current + inc
    if new <= current:
        raise DiskError(f"target {format_size(new)} is not larger than current {cfg.pod.disk_size}")
    eff_max = effective_max_bytes(cfg, current)
    if eff_max is not None and new > eff_max:
        if eff_max <= current:
            raise DiskError("not enough host free space to grow (would breach the host reserve)")
        limit_reason = (
            f"disk_max_size {cfg.pod.disk_max_size}"
            if cfg.pod.disk_max_size
            else "host free space minus reserve"
        )
        raise DiskError(
            f"target {format_size(new)} exceeds the limit {format_size(eff_max)} ({limit_reason})"
        )
    return format_size(new)


def compute_autogrow_target(cfg: Config, usage: DiskUsage) -> str | None:
    """Size an auto-grow to restore the configured free-space headroom.

    Rather than a flat step, grow just enough that ``used`` drops to
    ``(100 - disk_autogrow_target_free_pct)%`` of the new disk -- rounded up
    to whole ``disk_autogrow_increment`` units, floored at one increment, and
    clamped to :func:`effective_max_bytes`. Returns the new size string, or
    None when the disk can't grow by even one increment (at the cap / no host
    room) so the caller skips this round.
    """
    current = parse_size(cfg.pod.disk_size)
    inc = parse_size(cfg.pod.disk_autogrow_increment)
    used = usage.used_bytes

    # Disk size that lands ``used`` at the target utilisation:
    #   used / new_total <= (100 - target_free)/100  =>  new_total >= used / frac
    used_frac_target = (100 - cfg.pod.disk_autogrow_target_free_pct) / 100.0
    needed_total = int(used / used_frac_target) + 1 if used_frac_target > 0 else current

    # Grow in whole increments, at least one.
    delta = max(inc, needed_total - current)
    steps = (delta + inc - 1) // inc
    new = current + steps * inc

    eff_max = effective_max_bytes(cfg, current)
    if eff_max is not None:
        if eff_max < current + inc:
            return None  # can't fit even one increment
        if new > eff_max:
            # Clamp down to the largest whole-increment size that fits.
            fit_steps = (eff_max - current) // inc
            new = current + fit_steps * inc
    if new <= current:
        return None
    return format_size(new)


def grow_disk(
    cfg: Config,
    *,
    target_size: str | None = None,
    increment: str | None = None,
    extend_partition: bool = True,
    wait_timeout: int = 600,
) -> GrowResult:
    """Grow the Windows virtual disk and extend C: to fill it.

    Stops the pod, bumps ``disk_size`` (capped), regenerates compose so
    dockur grows the image on the next boot, restarts, waits for the guest,
    then runs the partition extend. Raises :class:`DiskError` on validation
    or lifecycle failure; the config is only persisted once the new size is
    validated, and is rolled back if the container won't come back up.
    """
    from winpodx.core.pod import PodState, start_pod, stop_pod
    from winpodx.core.pod.compose import generate_compose
    from winpodx.core.provisioner import wait_for_windows_responsive

    if cfg.pod.backend not in ("podman", "docker"):
        raise DiskError(f"grow-disk only supports podman/docker, not {cfg.pod.backend!r}")

    old_size = cfg.pod.disk_size
    # compute_grow_target enforces the effective cap (explicit disk_max_size
    # and/or host free space minus the reserve), so no separate host guard.
    new_size = compute_grow_target(cfg, target_size=target_size, increment=increment)

    log.info("grow-disk: %s -> %s", old_size, new_size)
    stop_pod(cfg)

    cfg.pod.disk_size = new_size
    cfg.save()
    try:
        generate_compose(cfg)
    except Exception as e:  # noqa: BLE001 -- roll back size on compose failure
        cfg.pod.disk_size = old_size
        cfg.save()
        raise DiskError(f"failed to regenerate compose: {e}") from e

    status = start_pod(cfg)
    if status.state not in (PodState.RUNNING, PodState.STARTING):
        cfg.pod.disk_size = old_size
        cfg.save()
        generate_compose(cfg)
        raise DiskError(
            f"container did not come back up after grow (state={status.state}); "
            "rolled disk_size back"
        )

    extended = False
    note = ""
    if extend_partition:
        if wait_for_windows_responsive(cfg, timeout=wait_timeout):
            extended = extend_guest_system_volume(cfg)
            if not extended:
                note = (
                    "disk image grown but C: not extended -- run "
                    "`winpodx pod grow-disk --extend-only` once the guest is up, "
                    "or extend C: manually in Disk Management."
                )
        else:
            note = (
                "disk image grown but guest didn't become responsive in time; "
                "C: not extended yet (retry with `winpodx pod grow-disk --extend-only`)."
            )

    return GrowResult(
        old_size=old_size,
        new_size=new_size,
        partition_extended=extended,
        note=note,
    )


def maybe_autogrow(cfg: Config) -> bool:
    """Daemon hook: grow the disk if it's filling up and the pod is idle.

    Returns True when a grow was performed. Caller (idle monitor) invokes
    this only when there are no active sessions, so a grow never interrupts
    a live RemoteApp session. The grow is sized to restore the configured
    free-space headroom (not a flat step) and bounded by the effective cap.
    """
    if not cfg.pod.disk_autogrow:
        return False

    usage = get_guest_disk_usage(cfg)
    if usage is None:
        return False
    if usage.used_pct < cfg.pod.disk_autogrow_threshold_pct:
        return False

    target = compute_autogrow_target(cfg, usage)
    if target is None:
        log.info(
            "auto-grow wanted (C: %.1f%% used) but can't grow further (at cap / no host room)",
            usage.used_pct,
        )
        return False

    log.info(
        "auto-grow trigger: C: %.1f%% used (>= %d%%), growing %s -> %s to restore ~%d%% free",
        usage.used_pct,
        cfg.pod.disk_autogrow_threshold_pct,
        cfg.pod.disk_size,
        target,
        cfg.pod.disk_autogrow_target_free_pct,
    )
    try:
        grow_disk(cfg, target_size=target)
    except DiskError as e:
        log.warning("auto-grow skipped: %s", e)
        return False
    return True
