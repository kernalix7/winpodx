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

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DepCheck:
    name: str
    found: bool
    path: str = ""
    note: str = ""
    # For container backends only: True/False once the daemon/socket has been
    # probed (via check_all(probe_daemons=True)), None when not probed. `found`
    # means the CLI is on PATH; daemon_reachable means a command actually talks
    # to its daemon (#395 — a docker CLI with DOCKER_HOST set to a dead podman
    # socket is `found` but not `daemon_reachable`).
    daemon_reachable: bool | None = None


# Optional dependencies probed by name on PATH. Order matches what we want
# the setup wizard's status block to show -- container backends first
# (podman / docker), then the Flatpak fallback. (The libvirt backend was
# dropped in 0.6.0, so ``virsh`` is no longer probed.)
OPTIONAL_DEPS = {
    "docker": "Docker backend",
    "podman": "Podman backend",
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


def check_backend_daemon(cmd: str, *, timeout: float = 8.0) -> tuple[bool, str]:
    """Probe whether ``<cmd> info`` can actually reach its daemon / socket.

    ``shutil.which`` only proves the CLI is installed — not that the daemon is
    running or that ``DOCKER_HOST`` points somewhere valid. #395: a ``docker``
    CLI with ``DOCKER_HOST`` set to a non-running podman socket looks present,
    but every compose call fails with "Cannot connect to the Docker daemon".

    Returns ``(reachable, hint)``; ``hint`` is an actionable message when
    unreachable, else ``""``.
    """
    import subprocess

    exe = shutil.which(cmd)
    if not exe:
        return (False, "")
    try:
        result = subprocess.run(
            [exe, "info"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return (False, f"{cmd} found but its daemon did not respond in {int(timeout)}s")
    if result.returncode == 0:
        return (True, "")
    low = (result.stderr or result.stdout or "").lower()
    hint = f"{cmd} CLI found but its daemon is unreachable"
    if "podman.sock" in low or "/podman/" in low:
        hint += (
            " — DOCKER_HOST points at a podman socket; start it with "
            "`systemctl --user start podman.socket`, unset DOCKER_HOST, or use "
            "the podman backend (`winpodx config set pod.backend podman`)"
        )
    elif "cannot connect" in low or "daemon" in low or "refused" in low:
        hint += " — is the daemon running? (check DOCKER_HOST)"
    return (False, hint)


def check_all(probe_daemons: bool = False) -> dict[str, DepCheck]:
    """Run every host dep check.

    Returns a dict keyed by canonical short name (``freerdp``, ``podman``,
    ``docker``, ``flatpak``, ``kvm``). Every dependency has an entry; the
    caller decides what's required vs optional. Missing entries indicate a bug
    in this function, never a missing dependency.

    ``probe_daemons`` (default False) additionally verifies, for any container
    backend that's on PATH, that its daemon actually answers (``<cmd> info``)
    and records the result in ``DepCheck.daemon_reachable`` + an actionable
    ``note`` when it doesn't. Off by default because the probe spawns a
    subprocess per backend; the setup wizard opts in, the fast callers (backend
    selector, GUI quick-check) don't.
    """
    checks: dict[str, DepCheck] = {}
    checks["freerdp"] = check_freerdp()
    for cmd, desc in OPTIONAL_DEPS.items():
        path = shutil.which(cmd)
        dep = DepCheck(name=cmd, found=bool(path), path=path or "", note=desc)
        if probe_daemons and path and cmd in ("docker", "podman"):
            reachable, hint = check_backend_daemon(cmd)
            dep.daemon_reachable = reachable
            if not reachable and hint:
                dep.note = hint
        checks[cmd] = dep
    checks["kvm"] = check_kvm()
    return checks


# Homebrew-on-Linux install prefixes for `podman-compose` (#765, #725).
# Homebrew is the standard way to get CLI tools on immutable distros
# (Bazzite, Fedora Silverblue) where there's no system package manager to
# `apt`/`dnf install` into. None of these are reliably on `$PATH`: brew only
# adds itself via `eval "$(brew shellenv)"` in the user's shell rc, which an
# interactive terminal sources but a desktop-session-launched process (the
# tray / GUI autostart entry in particular) never does — so `shutil.which`
# alone false-negatives even though the binary is genuinely installed.
_BREW_COMPOSE_DIRS = (
    "/home/linuxbrew/.linuxbrew/bin",
    "~/.linuxbrew/bin",
    "/opt/homebrew/bin",
    "~/.local/bin",
)


def find_podman_compose() -> str | None:
    """Locate the ``podman-compose`` binary: PATH first, then known off-PATH dirs.

    Falls back to probing Homebrew's install prefixes (see
    ``_BREW_COMPOSE_DIRS``) when ``shutil.which`` misses, so a
    Homebrew-installed ``podman-compose`` is still found even when brew's
    bin dir isn't on the caller's ``$PATH`` (#765, #725).

    Always returns an ABSOLUTE path (never the bare ``"podman-compose"``
    string). Callers MUST use that absolute path in subprocess argv rather
    than re-deriving the name — a spawned subprocess only inherits the
    *current* process's ``$PATH``, not whatever this function used to find
    the binary, so a bare name would fail at exec time even after being
    "found" here.
    """
    found = shutil.which("podman-compose")
    if found:
        return found
    for raw_dir in _BREW_COMPOSE_DIRS:
        candidate = Path(raw_dir).expanduser() / "podman-compose"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


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
