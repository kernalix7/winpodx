# SPDX-License-Identifier: MIT
"""Detect where the running ``winpodx`` binary was installed from.

Used by ``winpodx --version`` and ``winpodx info`` to surface install
provenance in user-facing output, and by ``winpodx uninstall`` to
decide whether to prompt the user about removing the system package.

Detection precedence (returns the first hit):

  1. ``dpkg -S <path>``       -- debian / ubuntu package install
  2. ``rpm -qf <path>``        -- fedora / opensuse / RHEL package install
  3. ``pacman -Qo <path>``     -- arch / AUR package install
  4. ``~/.local/bin/winpodx-app`` ancestor on path -- curl install.sh layout
  5. ``site-packages`` ancestor on path -- ``pip install -e .`` source checkout / wheel install
  6. fallback ``"unknown"``

All probes time out at 3 s so a hung package-manager backend never
blocks the CLI; failures return ``None`` for that probe and the chain
falls through.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstallSource:
    """Where the running ``winpodx`` binary lives in the package graph.

    ``kind`` is the canonical bucket used by callers to branch logic
    (e.g. the uninstall flow's "prompt to also remove the system
    package" path only fires for ``apt`` / ``dnf`` / ``pacman``).
    ``label`` is a short human-readable string for ``--version`` /
    ``info`` output. ``removal_command`` is the literal shell command
    the user should run to remove the package, or ``None`` when there
    is no package-manager entry (curl / source).
    """

    kind: str  # apt | dnf | pacman | curl | source | unknown
    label: str
    package_name: str | None = None
    removal_command: str | None = None


def detect(binary_path: Path | str | None = None) -> InstallSource:
    """Detect the install source for the running ``winpodx`` binary.

    ``binary_path`` defaults to ``shutil.which("winpodx")``. Callers
    can pass an explicit path in tests.

    Never raises; failures fall through to ``InstallSource("unknown",
    ...)``.
    """
    path = _resolve_path(binary_path)
    if path is None:
        return InstallSource(kind="unknown", label="install source not detected")

    for probe in (_probe_dpkg, _probe_rpm, _probe_pacman):
        result = probe(path)
        if result is not None:
            return result

    # Curl install heuristics. Check several signals because the launcher
    # `~/.local/bin/winpodx` is a symlink to `~/.local/bin/winpodx-run`,
    # which is itself a python wrapper script, NOT inside the bundle
    # dir. _resolve_path follows symlinks, so the path we got back may
    # be the launcher script (not `winpodx-app/`), and the substring
    # match against `winpodx-app` alone misses curl installs:
    #
    #   ~/.local/bin/winpodx          (symlink, what `which winpodx` returns)
    #     → ~/.local/bin/winpodx-run  (launcher wrapper, what resolve gives)
    #         → references ~/.local/bin/winpodx-app/src on PYTHONPATH
    #
    # Strongest signal: the bundle dir ``~/.local/bin/winpodx-app/``
    # itself exists. install.sh always creates it; package installs
    # never put files there. Fall back to a launcher / pre-resolve
    # path match for paranoia.
    home = Path.home()
    curl_bundle = home / ".local" / "bin" / "winpodx-app"
    curl_launcher = home / ".local" / "bin" / "winpodx-run"
    unresolved = _which_winpodx_unresolved() if binary_path is None else None
    path_str = str(path)
    unresolved_str = str(unresolved or "")
    if (
        curl_bundle.is_dir()
        or curl_launcher.exists()
        or "/.local/bin/winpodx-app" in path_str
        or "/.local/bin/winpodx-run" in path_str
        or "/.local/bin/winpodx-app" in unresolved_str
        or "/.local/bin/winpodx-run" in unresolved_str
    ):
        return InstallSource(
            kind="curl",
            label="curl install (~/.local/bin/winpodx-app)",
            removal_command=(
                "curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/"
                "main/uninstall.sh | bash"
            ),
        )

    # site-packages -- either a source checkout via ``pip install -e .``
    # or a wheel install. Both share the same path shape.
    if "site-packages" in path_str or "/src/winpodx/" in path_str:
        return InstallSource(
            kind="source",
            label="pip install (source / wheel)",
            removal_command="pip uninstall winpodx",
        )

    return InstallSource(kind="unknown", label=f"install source not detected ({path})")


def _which_winpodx_unresolved() -> Path | None:
    """Return the pre-symlink-resolve path of ``winpodx`` on PATH."""
    found = shutil.which("winpodx")
    return Path(found) if found else None


def _resolve_path(binary_path: Path | str | None) -> Path | None:
    if binary_path is not None:
        path = Path(binary_path)
        return path if path.exists() else None
    found = shutil.which("winpodx")
    if found is None:
        return None
    return Path(found).resolve()


def _probe_dpkg(path: Path) -> InstallSource | None:
    if not shutil.which("dpkg"):
        return None
    try:
        result = subprocess.run(
            ["dpkg", "-S", str(path)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    # Output shape: "winpodx: /usr/bin/winpodx"
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    pkg = line.split(":", 1)[0].strip() if ":" in line else "winpodx"
    return InstallSource(
        kind="apt",
        label=f"installed via apt ({pkg})",
        package_name=pkg,
        removal_command=f"sudo apt remove {pkg}",
    )


def _probe_rpm(path: Path) -> InstallSource | None:
    if not shutil.which("rpm"):
        return None
    try:
        result = subprocess.run(
            ["rpm", "-qf", "--queryformat", "%{NAME}", str(path)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    pkg = result.stdout.strip()
    if not pkg or pkg.startswith("file ") or "is not owned" in pkg:
        return None
    # Distinguish dnf vs zypper via /etc/os-release ID.
    tool = (
        "zypper"
        if _distro_id() in ("opensuse-tumbleweed", "opensuse-leap", "opensuse-slowroll")
        else "dnf"
    )
    return InstallSource(
        kind=tool,
        label=f"installed via {tool} ({pkg})",
        package_name=pkg,
        removal_command=f"sudo {tool} remove {pkg}",
    )


def _probe_pacman(path: Path) -> InstallSource | None:
    if not shutil.which("pacman"):
        return None
    try:
        result = subprocess.run(
            ["pacman", "-Qo", str(path)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    # Output shape: "/usr/bin/winpodx is owned by winpodx 0.5.7-1"
    text = result.stdout.strip()
    if " is owned by " not in text:
        return None
    pkg = text.split(" is owned by ")[1].split(" ")[0]
    return InstallSource(
        kind="pacman",
        label=f"installed via pacman ({pkg})",
        package_name=pkg,
        removal_command=f"sudo pacman -Rns {pkg}",
    )


def _distro_id() -> str:
    """Read ``ID=`` from ``/etc/os-release``. Returns ``""`` on failure."""
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("ID="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""
