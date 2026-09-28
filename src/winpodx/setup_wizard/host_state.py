# SPDX-License-Identifier: MIT
"""Read-only detection of host-side state that pkexec apply will fix."""

from __future__ import annotations

import grp
import os
import pwd
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from winpodx.core.config import Config
    from winpodx.utils.deps import DepCheck


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    """A blocking prerequisite and its actionable remedy."""

    key: str
    detail: str
    fixable: bool


@dataclass(frozen=True, slots=True)
class PreflightReport:
    failures: tuple[PreflightIssue, ...]

    @property
    def ready(self) -> bool:
        return not self.failures

    def message(self) -> str:
        return "Host preflight failed:\n" + "\n".join(
            f"- {'Fixable' if issue.fixable else 'Manual action'}: {issue.detail}"
            for issue in self.failures
        )


@dataclass(frozen=True, slots=True)
class PreflightFacts:
    host: HostState
    cpu_virtualization: bool
    ram_gb: int
    disk_free_gb: float
    disk_required_gb: float
    iso_required_gb: float
    backend: str
    deps: dict[str, DepCheck]
    compose_available: bool
    rootless: bool
    vm_ram_gb: int = 0


def assess_preflight(facts: PreflightFacts) -> PreflightReport:
    """Assess independent host requirements together, without changing the host."""
    issues: list[PreflightIssue] = []

    def add(key: str, detail: str, fixable: bool = False) -> None:
        issues.append(PreflightIssue(key, detail, fixable))

    if facts.backend in ("podman", "docker"):
        if not facts.cpu_virtualization:
            add("cpu_virtualization", "Enable CPU virtualization (VT-x / AMD-V) in firmware.")
        if not facts.host.dev_kvm_present:
            add("dev_kvm_present", "Load the KVM module; check virtualization in firmware.")
        elif not facts.host.dev_kvm_readable:
            add(
                "dev_kvm_readable",
                "Grant this user read/write access to /dev/kvm (kvm group or udev rules).",
                facts.host.kvm_group_exists and not facts.host.in_kvm_group,
            )
        minimum_ram = max(8, facts.vm_ram_gb + 2)
        if facts.ram_gb < minimum_ram:
            add(
                "ram",
                f"At least {minimum_ram} GiB host RAM is required (found {facts.ram_gb} GiB).",
            )
        needed = facts.disk_required_gb + facts.iso_required_gb
        if facts.disk_free_gb < needed:
            add(
                "disk",
                f"Free {needed:g} GiB on Windows storage (available {facts.disk_free_gb:g} GiB).",
            )
        if facts.backend == "podman" and facts.rootless:
            if not facts.host.subuid_configured:
                add("subuid_configured", "Configure /etc/subuid for rootless Podman.", True)
            if not facts.host.subgid_configured:
                add("subgid_configured", "Configure /etc/subgid for rootless Podman.", True)
        backend = facts.deps.get(facts.backend)
        if backend is None or not backend.found:
            add("backend", f"Install the {facts.backend} container backend.")
        elif backend.daemon_reachable is False:
            add("backend", backend.note or f"{facts.backend} is not usable; check its daemon.")
        if not facts.compose_available:
            add("compose", f"Install a usable {facts.backend} compose provider.")
    if not facts.deps["freerdp"].found:
        add("freerdp", "Install FreeRDP 3+ before running Windows apps.")
    return PreflightReport(tuple(issues))


def _cpu_virtualization_available() -> bool:
    if os.uname().machine in ("aarch64", "arm64"):
        return Path("/dev/kvm").exists()
    try:
        text = Path("/proc/cpuinfo").read_text()
    except OSError:
        return False
    return any(flag in text.split() for flag in ("vmx", "svm"))


def _storage_free_gb(path: Path) -> float:
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free / 1024**3


def inspect_preflight(
    cfg: Config,
    *,
    storage_path: Path | None = None,
    iso_path: str | None = None,
    deps: dict[str, DepCheck] | None = None,
) -> PreflightReport:
    """Read-only probe for setup and a restored Windows guest's first boot."""
    from winpodx.core.storage_migration import (
        default_target_path,
        get_volume_mountpoint,
        resolve_named_volume,
    )
    from winpodx.utils.deps import check_all, check_backend_daemon, check_compose_provider
    from winpodx.utils.specs import detect_host_specs

    checks = deps if deps is not None else check_all()
    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return assess_preflight(
            PreflightFacts(detect_host_state(), True, 8, 100, 0, 0, backend, checks, True, False)
        )
    dep = checks.get(backend)
    if dep is not None and dep.found and dep.daemon_reachable is None:
        reachable, hint = check_backend_daemon(backend, timeout=3)
        dep = type(dep)(dep.name, dep.found, dep.path, hint or dep.note, reachable)
        checks = {**checks, backend: dep}

    extra: list[PreflightIssue] = []
    volume: str | None = None
    target = Path(cfg.pod.storage_path).expanduser() if cfg.pod.storage_path else None
    if target is None:
        volume = resolve_named_volume(backend) if dep is not None and dep.found else None
        mount = get_volume_mountpoint(backend, volume) if volume is not None else None
        if volume is not None and mount is None:
            extra.append(
                PreflightIssue(
                    "disk", f"Cannot inspect {backend} storage volume {volume} filesystem.", False
                )
            )
        target = Path(mount) if mount is not None else storage_path or default_target_path()
    iso_gb = 0.0
    if iso_path:
        source = Path(iso_path).expanduser()
        try:
            if volume is None and source.resolve() != (target / "custom.iso").resolve():
                iso_gb = source.stat().st_size / 1024**3
            with source.open("rb"):
                pass
        except OSError:
            extra.append(
                PreflightIssue("iso", f"Local ISO is missing or unreadable: {source}", False)
            )
    free_gb = float("inf")
    if not any(issue.key == "disk" for issue in extra):
        try:
            free_gb = _storage_free_gb(target)
        except OSError:
            extra.append(
                PreflightIssue("disk", f"Cannot inspect storage filesystem: {target}", False)
            )
    compose = check_compose_provider(backend)
    existing = cfg.pod.initialized or volume is not None
    disk_gb = 8 if existing else _disk_size_gb(cfg.pod.disk_size)
    download_gb = 0 if iso_path or existing else 8
    rootless = False
    if backend == "podman" and dep is not None and dep.found and dep.daemon_reachable is not False:
        from winpodx.backend.podman import is_rootless_podman

        rootless = is_rootless_podman()
    facts = PreflightFacts(
        detect_host_state(),
        _cpu_virtualization_available(),
        detect_host_specs().ram_gb,
        free_gb,
        disk_gb,
        iso_gb + download_gb,
        backend,
        checks,
        compose,
        rootless,
        vm_ram_gb=cfg.pod.ram_gb,
    )
    return PreflightReport(assess_preflight(facts).failures + tuple(extra))


def _disk_size_gb(size: str) -> float:
    number, suffix = int(size[:-1]), size[-1].upper()
    return number * {"M": 1 / 1024, "G": 1, "T": 1024}[suffix]


def require_preflight(
    cfg: Config,
    *,
    storage_path: Path | None = None,
    iso_path: str | None = None,
    deps: dict[str, DepCheck] | None = None,
) -> None:
    """Block setup before it writes storage or starts a pod, listing all failures."""
    report = inspect_preflight(cfg, storage_path=storage_path, iso_path=iso_path, deps=deps)
    if not report.ready:
        raise RuntimeError(report.message())


@dataclass
class HostState:
    """Snapshot of the host bits the setup wizard cares about."""

    in_kvm_group: bool
    """True iff the current user is a member of the ``kvm`` group."""

    kvm_group_exists: bool
    """True iff a ``kvm`` group exists on the host."""

    dev_kvm_present: bool
    """True iff ``/dev/kvm`` exists (host kernel exposes KVM)."""

    dev_kvm_readable: bool
    """True iff the current user can ``os.access(/dev/kvm, R_OK|W_OK)``."""

    subuid_configured: bool
    """True iff ``/etc/subuid`` has an entry for the current user."""

    subgid_configured: bool
    """True iff ``/etc/subgid`` has an entry for the current user."""

    kvm_module_persistent: bool
    """True iff ``/etc/modules-load.d/`` has any file naming kvm_intel /
    kvm_amd, so the module loads at boot without a manual modprobe."""

    @property
    def kvm_access_ok(self) -> bool:
        """Whether this user can actually open ``/dev/kvm`` right now.

        ``kvm`` group membership is ONE mechanism for granting this, not the
        requirement itself. Distros that ship ``/dev/kvm`` world-accessible via
        a 0666 udev rule grant it with no group at all, so treating membership
        as its own requirement fails a check the user cannot act on -- and a
        ``usermod`` plus re-login there would change nothing.
        """
        return self.dev_kvm_present and self.dev_kvm_readable

    @property
    def blocking_failures(self) -> list[str]:
        """Field names that genuinely prevent a Windows install.

        Single source of truth for "is the host ready", so the CLI wizard, the
        Qt wizard and ``doctor`` cannot drift into disagreeing about it.
        """
        blocking: list[str] = []
        if not self.dev_kvm_present:
            blocking.append("dev_kvm_present")
        elif not self.kvm_access_ok:
            blocking.append("dev_kvm_readable")
            if not self.in_kvm_group and self.kvm_group_exists:
                blocking.append("in_kvm_group")
        if not self.subuid_configured:
            blocking.append("subuid_configured")
        if not self.subgid_configured:
            blocking.append("subgid_configured")
        return blocking

    @property
    def is_complete(self) -> bool:
        """All host setup the wizard owns is in place.

        ``/dev/kvm`` presence is host-kernel level and the wizard cannot fix it
        (enable virt in firmware / modprobe), but rootless KVM is meaningless
        without it, so it still counts.
        """
        return not self.blocking_failures

    @property
    def missing_fixable(self) -> list[str]:
        """Human-readable list of items the wizard CAN apply via pkexec."""
        missing: list[str] = []
        if not self.kvm_access_ok and not self.in_kvm_group and self.kvm_group_exists:
            missing.append("kvm-group-membership")
        if not self.subuid_configured:
            missing.append("subuid-entry")
        if not self.subgid_configured:
            missing.append("subgid-entry")
        if self.dev_kvm_present and not self.kvm_module_persistent:
            missing.append("kvm-module-persistence")
        return missing


def _current_username() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def _user_in_group(group: str) -> bool:
    try:
        user = _current_username()
        return any(
            g.gr_name == group and (os.getuid() in g.gr_mem or user in g.gr_mem)
            for g in grp.getgrall()
        )
    except OSError:
        return False


def _group_exists(group: str) -> bool:
    try:
        grp.getgrnam(group)
        return True
    except KeyError:
        return False


def _subid_has_entry(path: str, username: str) -> bool:
    try:
        text = Path(path).read_text()
    except OSError:
        return False
    for line in text.splitlines():
        if line.startswith(f"{username}:"):
            return True
    return False


def _kvm_module_persistent() -> bool:
    """Look for any modules-load.d entry that names kvm_intel / kvm_amd."""
    for conf_dir in ("/etc/modules-load.d", "/usr/lib/modules-load.d"):
        try:
            entries = list(Path(conf_dir).iterdir())
        except (OSError, FileNotFoundError):
            continue
        for entry in entries:
            try:
                content = entry.read_text()
            except OSError:
                continue
            if "kvm_intel" in content or "kvm_amd" in content or "kvm\n" in content:
                return True
    return False


def detect_host_state() -> HostState:
    """Read-only probe of host setup state.

    Pure inspection -- never modifies the system, never raises on missing
    files / unreadable paths. Safe to call from any context (CLI flow,
    GUI startup, doctor command)."""
    user = _current_username()
    dev_kvm = Path("/dev/kvm")
    dev_kvm_present = dev_kvm.exists()
    dev_kvm_readable = dev_kvm_present and os.access(str(dev_kvm), os.R_OK | os.W_OK)

    return HostState(
        in_kvm_group=_user_in_group("kvm"),
        kvm_group_exists=_group_exists("kvm"),
        dev_kvm_present=dev_kvm_present,
        dev_kvm_readable=dev_kvm_readable,
        subuid_configured=_subid_has_entry("/etc/subuid", user),
        subgid_configured=_subid_has_entry("/etc/subgid", user),
        kvm_module_persistent=_kvm_module_persistent(),
    )
