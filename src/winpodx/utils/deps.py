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


# Kept for backward compatibility — runtime detection now delegates to
# winpodx.core.rdp.find_freerdp which also handles sdl-freerdp and the
# Flatpak fallback. Previously check_freerdp() only probed this list,
# so a user with only sdl-freerdp3 installed saw `winpodx setup` report
# FreeRDP missing even though launch_app would have worked fine.
REQUIRED_DEPS = ["xfreerdp3", "xfreerdp"]
OPTIONAL_DEPS = {
    "docker": "Docker backend",
    "podman": "Podman backend",
    "virsh": "libvirt backend",
    "flatpak": "Flatpak FreeRDP fallback",
}


def check_freerdp() -> DepCheck:
    """Check for any available FreeRDP binary.

    Delegates to ``winpodx.core.rdp.find_freerdp`` so the dep check accepts
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
