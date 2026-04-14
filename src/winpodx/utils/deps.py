"""System dependency checking."""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass
class DepCheck:
    name: str
    found: bool
    path: str = ""
    note: str = ""


REQUIRED_DEPS = ["xfreerdp3", "xfreerdp"]
OPTIONAL_DEPS = {
    "docker": "Docker backend",
    "podman": "Podman backend",
    "virsh": "libvirt backend",
    "flatpak": "Flatpak FreeRDP fallback",
}


def check_freerdp() -> DepCheck:
    """Check for any available FreeRDP binary."""
    for name in REQUIRED_DEPS:
        path = shutil.which(name)
        if path:
            return DepCheck(name=name, found=True, path=path)
    return DepCheck(name="xfreerdp", found=False, note="FreeRDP 3+ is required")


def check_backends() -> list[DepCheck]:
    """Check which backends are available on the system."""
    results = []
    for cmd, desc in OPTIONAL_DEPS.items():
        path = shutil.which(cmd)
        results.append(DepCheck(name=cmd, found=bool(path), path=path or "", note=desc))
    return results


def check_all() -> dict[str, DepCheck]:
    """Run all dependency checks."""
    checks: dict[str, DepCheck] = {}
    checks["freerdp"] = check_freerdp()
    for dep in check_backends():
        checks[dep.name] = dep
    return checks
