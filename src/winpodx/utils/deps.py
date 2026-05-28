# SPDX-License-Identifier: MIT
"""System dependency checking.

Single source of truth for "is the host ready to run winpodx" probes. Every
caller (the setup wizard, the GUI Quick Start dialog, `winpodx doctor`, the
backend selector) should go through :func:`check_all` rather than reimplementing
its own ``shutil.which`` loop — that was the source of pre-0.6.0 drift, where
``deps_quickcheck.py`` and ``cli/doctor.py`` each carried a different (and
incomplete) list of FreeRDP binary names.

The shell side of ``install.sh`` keeps its own minimal pre-venv probe (it has
to run before Python is even installed); that's the single intentional shell
duplicate. Every other surface in the project consumes this module.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DepCheck:
    name: str
    found: bool
    path: str = ""
    note: str = ""


# Optional dependencies probed by name on PATH. Order matches what we want
# the setup wizard's status block to show -- container backends first
# (podman / docker / virsh), then the Flatpak fallback.
OPTIONAL_DEPS = {
    "docker": "Docker backend",
    "podman": "Podman backend",
    "virsh": "libvirt backend",
    "flatpak": "Flatpak FreeRDP fallback",
}

# /dev/kvm presence stands in for "hardware virtualization is usable" -- the
# node exists when the host kernel has loaded ``kvm_intel`` / ``kvm_amd``, the
# CPU supports VT-x / AMD-V, and the caller's user is in the ``kvm`` group.
# We do not parse ``/proc/cpuinfo`` or call ``kvm-ok`` -- the file's existence
# is the single signal QEMU itself keys off, so anything else would diverge.
_KVM_NODE = "/dev/kvm"


def check_freerdp() -> DepCheck:
    """Check for any available FreeRDP binary.

    Delegates to :func:`winpodx.core.rdp.find_freerdp` so the dep check accepts
    the same set of binaries the launcher will actually try: xfreerdp3,
    xfreerdp, sdl-freerdp3, sdl-freerdp, and the Flatpak fallback.
    """
    from winpodx.core.rdp import find_freerdp

    found = find_freerdp()
    if found is None:
        return DepCheck(name="xfreerdp", found=False, note="FreeRDP 3+ is required")
    path, variant = found
    # Variant label doubles as a human-friendly name for the setup output.
    return DepCheck(name=variant, found=True, path=path)


def check_kvm() -> DepCheck:
    """Probe ``/dev/kvm`` -- hardware virtualization readiness.

    See the module-level comment on ``_KVM_NODE`` for why we key off the
    device node rather than parsing CPU flags.
    """
    found = Path(_KVM_NODE).exists()
    return DepCheck(
        name="kvm",
        found=found,
        path=_KVM_NODE if found else "",
        note="Hardware virtualization",
    )


def check_all() -> dict[str, DepCheck]:
    """Run every host dep check.

    Returns a dict keyed by canonical short name (``freerdp``, ``podman``,
    ``docker``, ``virsh``, ``flatpak``, ``kvm``). Every dependency has an
    entry; the caller decides what's required vs optional. Missing entries
    indicate a bug in this function, never a missing dependency.
    """
    checks: dict[str, DepCheck] = {}
    checks["freerdp"] = check_freerdp()
    for cmd, desc in OPTIONAL_DEPS.items():
        path = shutil.which(cmd)
        checks[cmd] = DepCheck(name=cmd, found=bool(path), path=path or "", note=desc)
    checks["kvm"] = check_kvm()
    return checks


def podman_major_version() -> int | None:
    """Return the installed podman's major version (e.g. 5 for "5.7.1"), or None.

    Returns None when podman isn't on PATH or when ``podman --version`` failed
    or produced unparseable output. The backend selector uses this to gate
    Ubuntu 22.04's podman 3.4 out of the auto-pick rotation (#271) -- rootless
    dockur needs features that landed in 4.x.
    """
    import re
    import subprocess

    podman = shutil.which("podman")
    if not podman:
        return None
    try:
        result = subprocess.run(
            [podman, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # `podman version 4.9.3` -> 4
    match = re.search(r"\bversion\s+(\d+)\.", result.stdout)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None
