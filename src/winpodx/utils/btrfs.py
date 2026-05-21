# SPDX-License-Identifier: MIT
"""btrfs Copy-on-Write detection and per-path disable helper.

dockur prints `Warning: you are using the BTRFS filesystem for /storage,
this might introduce issues with Windows Setup!` for a real reason: btrfs
defaults to Copy-on-Write on every block of every file, and a Windows VM
raw disk image has the worst possible access pattern for that — every
pagefile / swap / boot write forks a new extent. The disk image fragments
aggressively and grows past its declared size; pod recreates that should
take ~30 s on ext4 take many minutes (and often time out on the 300 s
budget). kernalix7 hit this on cachyos (#121, #122).

This module's helpers operate **only on paths the caller provides** —
typically the winpodx bind-mount storage directory
``~/.local/share/winpodx/storage``. We deliberately do NOT chattr the
container backend's graph root: that would silently flip every future
podman volume on the host to NoCoW, which is too broad and surprises
users who use btrfs snapshots (NoCoW files behave inconsistently across
snapshots).

Operations:

- :func:`detect_path_fs` — ``findmnt --target`` for any path; returns
  the filesystem type as a lowercase string (``btrfs``, ``ext4``, ...)
  or ``"unknown"`` when the binaries aren't installed or output can't
  be parsed.
- :func:`is_cow_disabled` — ``lsattr -d`` checks the ``C`` flag on the
  directory. Returns ``True`` / ``False`` / ``None`` (unknown).
- :func:`disable_cow_on_path` — runs ``chattr +C`` on the given path.
  ``chattr +C`` on a directory affects only NEW files created inside;
  existing files keep their previous CoW state. Idempotent — running
  on an already-NoCoW path short-circuits via :func:`is_cow_disabled`.

All helpers are best-effort: every external command is wrapped in
``try/except``, every failure surfaces as a status string to the
caller. We never abort the install on a btrfs detection or chattr
failure — the worst case is a slower pod, not a broken one.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

__all__ = [
    "detect_path_fs",
    "disable_cow_on_path",
    "is_cow_disabled",
]


def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    """Run a subprocess and return (rc, stdout, stderr). Returns (-1, '', err) on missing binary."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError as e:
        return -1, "", str(e)
    except (subprocess.SubprocessError, OSError) as e:
        return -1, "", str(e)


def detect_path_fs(path: Path) -> str:
    """Return the filesystem type under ``path``.

    Runs ``findmnt -no FSTYPE --target <path>`` to find the nearest
    mountpoint and report its fs type. Returns ``"unknown"`` when
    findmnt isn't installed or every lookup fails.

    Path-existence handling: contrary to its docs, ``findmnt --target``
    returns rc=1 with empty output on at least some build/distro combos
    (verified: util-linux on opensuse Tumbleweed 2026-05-06) when the
    path doesn't exist — the user's auto-migration silently classified
    a fresh `~/.local/share/winpodx/storage` (target dir didn't exist
    yet at plan time) as ``"unknown"``, which made
    ``chattr_will_run`` False, which silently skipped the entire
    NoCoW path. To survive this, we walk the parent chain until we
    hit an existing dir before invoking findmnt — guaranteed to
    terminate at ``/`` which always exists. Callers therefore get
    the fs type they actually care about (the mountpoint that will
    contain ``path`` once it's created).
    """
    if shutil.which("findmnt") is None:
        return "unknown"
    # Walk up to the nearest existing ancestor so findmnt never sees a
    # non-existent target. ``path`` may be relative or absolute; either
    # way we resolve to an absolute existing dir before the probe.
    probe = path
    try:
        # Use parts iteration instead of plain `while not probe.exists()`
        # so a pathological symlink loop can't spin us forever.
        for _ in range(64):  # path depth ceiling is generous
            if probe.exists():
                break
            parent = probe.parent
            if parent == probe:  # hit "/" or current dir
                break
            probe = parent
    except OSError:
        # PermissionError / loop / etc. — fall back to original path and
        # let findmnt do whatever it does.
        probe = path
    rc, stdout, _ = _run(["findmnt", "-no", "FSTYPE", "--target", str(probe)])
    if rc != 0 or not stdout.strip():
        return "unknown"
    return stdout.strip().lower()


def is_cow_disabled(path: Path) -> bool | None:
    """Return ``True`` if ``+C`` is set on ``path``, ``False`` if not, ``None`` if unknown.

    Uses ``lsattr -d`` (lists the directory's own attributes, not its
    children). The ``C`` flag in the leading attribute string means CoW
    is disabled for new files created inside.
    """
    if shutil.which("lsattr") is None:
        return None
    if not path.exists():
        return None
    rc, stdout, _ = _run(["lsattr", "-d", str(path)])
    if rc != 0:
        return None
    parts = stdout.split()
    if not parts:
        return None
    attrs = parts[0]
    return "C" in attrs


def disable_cow_on_path(path: Path) -> tuple[str, str]:
    """Run ``chattr +C`` on ``path`` if it's on btrfs and CoW isn't already off.

    Returns ``(status, detail)``:

    - ``"disabled"`` — applied chattr +C successfully.
    - ``"already_off"`` — path already has +C; no-op.
    - ``"not_btrfs"`` — path is on some other filesystem; nothing to do.
    - ``"unknown_fs"`` — couldn't determine fs type (findmnt missing, etc).
    - ``"path_missing"`` — path doesn't exist; caller should create it first.
    - ``"failed"`` — fs is btrfs but chattr couldn't apply; detail carries reason.

    Idempotent: running on an already-NoCoW path short-circuits via
    :func:`is_cow_disabled`.

    Note: ``chattr +C`` on a directory affects only NEW files created
    inside. Existing files keep their CoW state. So for the bind mount
    use case, the caller MUST chattr +C BEFORE the directory is
    populated (i.e., before the first ``podman-compose up`` that
    materialises the Windows raw disk inside).
    """
    if not path.exists():
        return "path_missing", f"path={path} does not exist; create it before chattr"

    fs = detect_path_fs(path)
    if fs == "unknown":
        return "unknown_fs", f"path={path} (findmnt unavailable or lookup failed)"
    if fs != "btrfs":
        return "not_btrfs", f"path={path} fs={fs}"

    state = is_cow_disabled(path)
    if state is True:
        return "already_off", f"path={path}"

    if shutil.which("chattr") is None:
        return "failed", "chattr binary not on PATH (install e2fsprogs)"

    rc, _stdout, stderr = _run(["chattr", "+C", str(path)])
    if rc != 0:
        return "failed", f"chattr +C {path} failed: {stderr.strip() or f'rc={rc}'}"

    return "disabled", f"path={path}"
