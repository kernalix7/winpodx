# SPDX-License-Identifier: MIT
"""Read-only detection of host-side state that pkexec apply will fix."""

from __future__ import annotations

import grp
import os
import pwd
from dataclasses import dataclass
from pathlib import Path


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
    def is_complete(self) -> bool:
        """All host setup that the wizard owns is in place. ``/dev/kvm``
        presence is host-kernel level and not something the wizard can
        fix (user has to enable virt in BIOS / modprobe); we still
        require it for completion since rootless KVM is meaningless
        without it.
        """
        return (
            self.in_kvm_group
            and self.dev_kvm_present
            and self.dev_kvm_readable
            and self.subuid_configured
            and self.subgid_configured
        )

    @property
    def missing_fixable(self) -> list[str]:
        """Human-readable list of items the wizard CAN apply via pkexec."""
        missing: list[str] = []
        if not self.in_kvm_group and self.kvm_group_exists:
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
