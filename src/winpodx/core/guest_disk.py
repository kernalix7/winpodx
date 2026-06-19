# SPDX-License-Identifier: MIT
"""Guest-disk sharing constants (#616).

The guest's own filesystem (``C:\\``) is exposed to the host over SMB so a
host Linux app can open a Windows-local file picked from the guest's
"Open with → [Linux app]" menu (reverse-open, ``origin="guest"``).

Wiring:

- The guest runs the built-in SMB server (LanmanServer) and shares ``C:\\``
  as :data:`GUEST_SMB_SHARE`, granting the winpodx user. Set up at runtime by
  the guest agent (``provisioner._apply_guest_share``), never by install.bat.
- ``compose`` adds ``445`` to dockur's ``USER_PORTS`` so the container
  forwards it into the VM, and publishes ``127.0.0.1:{SMB_HOST_PORT}:445`` so
  the host can reach it on loopback only.
- The host mounts ``smb://127.0.0.1:{SMB_HOST_PORT}/{GUEST_SMB_SHARE}`` (gvfs,
  userspace, no sudo) and the reverse-open listener maps a guest path
  ``C:\\rest`` onto that mount.

Loopback-only publishing keeps the share off the network; the only credential
is the winpodx Windows account password (already rotated by winpodx).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from winpodx.core.config import Config

log = logging.getLogger(__name__)

#: Guest-side SMB port forwarded into the VM via dockur ``USER_PORTS``.
GUEST_SMB_PORT = 445

#: Host loopback port published to reach the guest SMB service. Must be an
#: UNPRIVILEGED port (>= 1024): rootless podman/docker can't bind 445 on the
#: host ("rootlessport cannot expose privileged port 445"). The guest still
#: listens on the standard 445 (:data:`GUEST_SMB_PORT`); the host SMB client
#: connects to this port explicitly (``smb://127.0.0.1:4445/…`` / cifs
#: ``-o port=4445``).
SMB_HOST_PORT = 4445

#: Name of the explicit share the guest creates for ``C:\``. An explicit
#: share (not the built-in ``C$`` admin share) avoids the local-account
#: admin-share network restrictions.
GUEST_SMB_SHARE = "winpodx-c"


def _gvfs_root() -> Path:
    """The user's gvfs FUSE mount root (``$XDG_RUNTIME_DIR/gvfs``)."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / "gvfs"


def _find_existing_mount() -> Path | None:
    """Return the gvfs FUSE dir for our guest share if it's already mounted.

    The gvfs SMB mount dir name encodes the connection
    (``smb-share:server=127.0.0.1,port=4445,share=winpodx-c,user=…``); the
    exact key order varies by gvfs version, so we match on substrings rather
    than reconstruct the name.
    """
    root = _gvfs_root()
    try:
        for entry in root.iterdir():
            name = entry.name
            if name.startswith("smb-share:") and f"share={GUEST_SMB_SHARE}" in name:
                if entry.is_dir():
                    return entry
    except OSError:
        pass
    return None


def smb_uri(cfg: Config) -> str:
    """``smb://<user>@127.0.0.1:<port>/<share>`` for the guest C: share."""
    return f"smb://{cfg.rdp.user}@127.0.0.1:{SMB_HOST_PORT}/{GUEST_SMB_SHARE}"


def ensure_guest_mount(cfg: Config) -> Path | None:
    """Mount the guest ``C:\\`` SMB share on the host (gvfs) and return its path.

    Userspace gvfs mount — no sudo. Idempotent: returns the existing mount if
    already present. Returns ``None`` (with a logged reason) if gvfs is
    missing, the password is unknown, or the share can't be reached — the
    reverse-open listener then rejects the guest request cleanly instead of
    raising.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return None

    existing = _find_existing_mount()
    if existing is not None:
        return existing

    password = cfg.rdp.password or ""
    uri = smb_uri(cfg)
    # gio mount reads the password from stdin when stdin is not a TTY. The
    # username is already in the URI, so the only prompt is the password;
    # the trailing newlines cover gvfs builds that also prompt for an empty
    # domain / re-confirm. gio writes the prompt to stderr and the mount is
    # async-but-settled by the time the process exits 0.
    try:
        proc = subprocess.run(
            ["gio", "mount", uri],
            input=password + "\n\n\n",
            text=True,
            capture_output=True,
            timeout=30,
        )
    except FileNotFoundError:
        log.warning("guest mount: 'gio' not found — install the gvfs SMB backend")
        return None
    except subprocess.TimeoutExpired:
        log.warning("guest mount: gio mount timed out for %s", uri)
        return None

    if proc.returncode != 0:
        # Non-zero often just means "already mounted"; fall through to the
        # mount-dir scan and only warn if that also turns up nothing.
        log.debug("guest mount: gio mount rc=%d stderr=%s", proc.returncode, proc.stderr.strip())

    mount = _find_existing_mount()
    if mount is None:
        log.warning(
            "guest mount: mounted %s but no smb-share dir under %s (rc=%d, stderr=%s)",
            uri,
            _gvfs_root(),
            proc.returncode,
            proc.stderr.strip(),
        )
    return mount


def guest_win_path_to_host(win_path: str, mount_root: Path) -> Path | None:
    """Map a guest ``C:\\…`` path onto the host gvfs mount of the guest C:.

    Only the ``C:`` drive is shared (``winpodx-c`` → ``C:\\``). Returns
    ``None`` for any other drive, a non-drive path, or a path containing a
    ``..`` traversal component.
    """
    if len(win_path) < 3 or win_path[1] != ":" or win_path[2] not in ("\\", "/"):
        return None
    if win_path[0].upper() != "C":
        return None
    rest = win_path[3:].replace("\\", "/")
    parts = [p for p in rest.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return mount_root.joinpath(*parts) if parts else mount_root
