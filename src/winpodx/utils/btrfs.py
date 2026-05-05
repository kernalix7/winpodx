"""btrfs Copy-on-Write detection and one-shot disable helper.

dockur prints `Warning: you are using the BTRFS filesystem for /storage,
this might introduce issues with Windows Setup!` for a real reason: btrfs
defaults to Copy-on-Write on every block of every file, and a Windows VM
raw disk image has the worst possible access pattern for that — every
pagefile / swap / boot write forks a new extent. The disk image fragments
aggressively and grows past its declared size; pod recreates that should
take ~30 s on ext4 take many minutes (and often time out on the 300 s
budget). kernalix7 hit this on cachyos (#121, #122).

This module exposes two operations:

- :func:`detect_storage_fs` — asks the container backend (podman /
  docker) for its graph root and returns ``(fs_type, path)``. Tolerates
  missing tools / unparseable output by returning ``("unknown", path)``.
- :func:`disable_cow_if_btrfs` — when the storage root is btrfs and
  CoW isn't already off, runs ``chattr +C <path>``. ``chattr +C`` on a
  directory only affects NEW files created inside (existing files are
  silently untouched), so this is safe to run on a populated graph
  root. The flag is inherited by new subdirectories — when winpodx's
  named volume is later materialised by ``podman-compose up``, the
  Windows raw disk image lands as NoCoW.

Both helpers are best-effort: every external command is wrapped in
``try/except``, every failure surfaces as a string detail to the
caller (typically winpodx setup) which logs and proceeds. We never
abort the install on a btrfs detection failure.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

__all__ = [
    "detect_storage_fs",
    "disable_cow_if_btrfs",
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


def detect_storage_fs(backend: str) -> tuple[str, Path | None]:
    """Return ``(fs_type, storage_root_path)`` for the container backend.

    fs_type is the lowercase string from ``findmnt`` (``btrfs``, ``ext4``,
    ``xfs``, ...) or ``"unknown"`` when the backend isn't reachable, the
    binaries aren't installed, or output can't be parsed.
    """
    if backend == "podman":
        cmd = ["podman", "info", "--format", "{{.Store.GraphRoot}}"]
    elif backend == "docker":
        cmd = ["docker", "info", "--format", "{{.DockerRootDir}}"]
    else:
        return "unknown", None

    rc, stdout, _ = _run(cmd, timeout=10.0)
    if rc != 0:
        return "unknown", None
    raw = stdout.strip()
    if not raw:
        return "unknown", None
    path = Path(raw)
    if not path.exists():
        return "unknown", path

    if shutil.which("findmnt") is None:
        return "unknown", path

    rc, stdout, _ = _run(["findmnt", "-no", "FSTYPE", "--target", str(path)])
    if rc != 0:
        return "unknown", path
    fs = stdout.strip().lower()
    return fs or "unknown", path


def is_cow_disabled(path: Path) -> bool | None:
    """Return ``True`` if ``+C`` is set on ``path``, ``False`` if not, ``None`` if unknown.

    Uses ``lsattr -d`` (lists the directory's own attributes, not its
    children). The 'C' flag in the leading attribute string means CoW
    is disabled for new files created inside.
    """
    if shutil.which("lsattr") is None:
        return None
    rc, stdout, _ = _run(["lsattr", "-d", str(path)])
    if rc != 0:
        return None
    parts = stdout.split()
    if not parts:
        return None
    attrs = parts[0]
    return "C" in attrs


def disable_cow_if_btrfs(backend: str) -> tuple[str, str]:
    """Run ``chattr +C`` on the container backend's graph root if it's btrfs.

    Returns ``(status, detail)``:

    - ``"disabled"`` — applied chattr +C successfully (or it was already on).
    - ``"already_off"`` — graph root already has +C; no-op.
    - ``"not_btrfs"`` — graph root is some other filesystem; nothing to do.
    - ``"unknown"`` — couldn't determine fs type or graph root.
    - ``"failed"`` — fs is btrfs but chattr couldn't apply (permission /
      kernel reject / etc.); detail carries the reason.

    Idempotent: running this on an already-NoCoW graph root short-circuits
    via ``is_cow_disabled``. Safe to call from every ``winpodx setup`` run.
    """
    fs, path = detect_storage_fs(backend)
    if fs != "btrfs":
        return ("not_btrfs" if fs != "unknown" else "unknown"), f"fs={fs} path={path}"

    assert path is not None  # detect_storage_fs returns Path with btrfs

    state = is_cow_disabled(path)
    if state is True:
        return "already_off", f"path={path}"

    if shutil.which("chattr") is None:
        return "failed", "chattr binary not on PATH (install e2fsprogs)"

    rc, _stdout, stderr = _run(["chattr", "+C", str(path)])
    if rc != 0:
        return "failed", f"chattr +C {path} failed: {stderr.strip() or f'rc={rc}'}"

    return "disabled", f"path={path}"
