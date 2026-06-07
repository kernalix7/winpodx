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


def _distro_id_like() -> str:
    """Read ``ID_LIKE=`` from ``/etc/os-release`` (space-separated). ``""`` on miss."""
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("ID_LIKE="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def _apt_has_candidate(pkg: str) -> bool:
    """True when ``apt-cache policy <pkg>`` reports an installable candidate.

    Guards against suggesting a package that doesn't exist in the user's apt
    archive -- e.g. ``python3-pyside6.qtwidgets`` has *no installation
    candidate* on Ubuntu 24.04 LTS (PySide6 only entered the archive in later
    releases), the exact failure reported in #502.
    """
    try:
        out = subprocess.run(
            ["apt-cache", "policy", pkg],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Candidate:"):
            cand = stripped.split(":", 1)[1].strip()
            return bool(cand) and cand != "(none)"
    return False


def _apt_pyside6_command() -> str | None:
    """An ``apt install`` line using only PySide6 package names that actually
    have a candidate on this system, or ``None`` when apt packages none of
    them (the Ubuntu 24.04 LTS case -- there the AppImage is the only path).

    Package naming varies across releases, so probe in preference order: the
    split Qt-module packages (Debian / Ubuntu >= 24.10), then a metapackage.
    """
    if not shutil.which("apt-cache"):
        return None
    split = ["python3-pyside6.qtwidgets", "python3-pyside6.qtsvg"]
    if all(_apt_has_candidate(p) for p in split):
        return "sudo apt install " + " ".join(split)
    for meta in ("python3-pyside6", "python3-qtpy-pyside6"):
        if _apt_has_candidate(meta):
            return f"sudo apt install {meta}"
    return None


def _pyside6_pkg_command() -> str | None:
    """The distro package-manager command that installs PySide6 (Qt6), or
    ``None`` when no distro package is available (caller falls back to the
    AppImage). Never a bare ``pip install`` -- that fails with
    ``externally-managed-environment`` on modern Debian/Ubuntu/Fedora (#502).

    Debian/Ubuntu is probed at runtime (``apt-cache``) because the package
    name + availability differ per release; the other families ship a stable
    package name so they stay static.
    """
    family = f"{_distro_id()} {_distro_id_like()}".lower()
    if any(d in family for d in ("debian", "ubuntu", "mint", "pop", "raspbian")):
        return _apt_pyside6_command()
    if any(d in family for d in ("fedora", "rhel", "centos", "almalinux", "rocky", "nobara")):
        return "sudo dnf install python3-pyside6"
    if any(d in family for d in ("arch", "manjaro", "endeavouros", "cachyos")):
        return "sudo pacman -S pyside6"
    if any(d in family for d in ("opensuse", "suse", "sles")):
        return "sudo zypper install python3-PySide6"
    return None


def pyside6_install_hint() -> str:
    """Actionable, distro-aware message for the GUI's missing-PySide6 case.

    The old hint (``pip install PySide6``) fails on PEP 668 externally-managed
    Pythons, and #502's reporter then found the apt package we named doesn't
    even exist on Ubuntu 24.04 LTS. So lead with the AppImage (works on every
    distro, no Python setup), then install.sh / pip, and only show a distro
    package command when one is actually available (see _pyside6_pkg_command).
    """
    from winpodx.core.i18n import tr  # lazy import: avoid an import cycle

    lines = [
        tr("PySide6 (Qt6) is required to launch the GUI. Easiest path: use the AppImage."),
        "  - "
        + tr(
            "AppImage (bundles the GUI, no Python setup): download "
            "winpodx-x86_64.AppImage from the Releases page, then run "
            "`chmod +x winpodx-x86_64.AppImage && ./winpodx-x86_64.AppImage gui`."
        ),
        "  - "
        + tr("install.sh installer: sets up a private venv with the GUI (no PEP 668 issue)."),
        "  - " + tr("If you installed winpodx with pip:") + "  pip install 'winpodx[gui]'",
    ]
    pkg = _pyside6_pkg_command()
    if pkg:
        lines.append("  - " + tr("Distro package:") + f"  {pkg}")
    else:
        lines.append(
            "  - " + tr("(Your distro may not package PySide6 for apt — use the AppImage above.)")
        )
    return "\n".join(lines)
