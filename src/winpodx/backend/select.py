# SPDX-License-Identifier: MIT
"""Backend auto-selection (0.6.0 item E).

Single source of truth for "given what's installed on the host, which container
backend should winpodx use." Pre-0.6.0 this lived in three places that had
drifted:

* ``install.sh`` Automatic-mode picker (the smartest version: knew about the
  podman major-version gate from #271 / Ubuntu 22.04, walked
  ``podman → docker → libvirt``).
* ``cli/setup_cmd.py`` non-interactive branch: a one-line
  ``"podman" if which("podman") else "docker"`` -- no libvirt, no version
  gate, picked the wrong backend on Ubuntu 22.04.
* ``uninstall.sh`` runtime-detect: ``podman > docker`` priority, no gate.

This module is the Python single source. ``install.sh``'s bash picker stays as
the *one* intentional shell duplicate (same pattern as the pre-venv deps probe
in :mod:`winpodx.utils.deps` -- the shell runs before Python is installed, so
it has to know the priorities itself). Both copies are pinned to the same
priorities by tests so they cannot drift again.

See ``docs/design/ROADMAP-0.6.0.md`` item E.
"""

from __future__ import annotations

from typing import Optional

from winpodx.utils.deps import DepCheck, podman_major_version

# Priority order for auto-pick. Lowest index wins when present + usable.
# Tuple form so a test (or a future packager) can iterate the same way the
# bash mirror in install.sh does.
AUTO_PRIORITY: tuple[str, ...] = ("podman", "docker", "libvirt")

# Rootless dockur/windows needs features that landed in podman 4.x; Ubuntu
# 22.04's podman 3.4 doesn't work (#271). Bump this if a future minimum
# changes; the bash mirror in install.sh uses the same constant.
PODMAN_MIN_MAJOR_VERSION = 4

# Valid backend identifiers winpodx supports. Includes "manual" for the no-
# backend, raw-RDP path (Config.pod.backend is the same set).
VALID_BACKENDS: frozenset[str] = frozenset({"podman", "docker", "libvirt", "manual"})


def choose_backend(
    *,
    prefer: Optional[str] = None,
    deps: Optional[dict[str, DepCheck]] = None,
    podman_min_major: int = PODMAN_MIN_MAJOR_VERSION,
) -> str:
    """Pick a container backend for ``cfg.pod.backend``.

    Selection order:

    1. ``prefer`` (the explicit ``--backend`` flag from CLI / install.sh) wins
       when set. Validated against :data:`VALID_BACKENDS` -- an unknown value
       raises ``ValueError`` so a typo fails loudly rather than silently
       falling through to podman.
    2. Otherwise walk :data:`AUTO_PRIORITY` (``podman → docker → libvirt``)
       and return the first that is present in ``deps`` AND usable. "Usable"
       for podman means the installed major version is at least
       ``podman_min_major`` -- podman 3.x is treated as absent so the rotation
       falls through to docker / libvirt rather than choosing a runtime
       dockur can't drive.
    3. Fall back to ``"podman"``. The recommended-mode install path then
       installs the missing podman packages; this matches install.sh's
       Recommended fallback, where the same string flows into apt/dnf.

    ``deps`` defaults to a fresh :func:`winpodx.utils.deps.check_all` call;
    pass an explicit dict to skip re-running the probes (the setup wizard
    has already done it).
    """
    if prefer is not None:
        if prefer not in VALID_BACKENDS:
            raise ValueError(f"unknown backend {prefer!r}; valid: {sorted(VALID_BACKENDS)}")
        return prefer

    if deps is None:
        from winpodx.utils.deps import check_all

        deps = check_all()

    for candidate in AUTO_PRIORITY:
        dep = deps.get(candidate if candidate != "libvirt" else "virsh")
        if dep is None or not dep.found:
            continue
        if candidate == "podman":
            major = podman_major_version()
            if major is None or major < podman_min_major:
                # Treat too-old podman as absent: fall through to next
                # candidate. The Ubuntu-22.04 / #271 case.
                continue
        return candidate

    # Nothing usable -- recommend podman so the install path can install it.
    return "podman"
