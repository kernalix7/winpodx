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
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

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


def _kio_fuse_dbus_service_present() -> bool:
    """True if the ``org.kde.KIOFuse`` D-Bus activation service file exists.

    This is the authoritative signal: ``_kio_fuse_mount`` mounts by calling the
    D-Bus service, which the bus auto-activates from this file regardless of
    where the binary lives. Distros that put the binary in a path we don't
    enumerate (e.g. Fedora's KF6-versioned libexec, #697) still register the
    service here. Checks the standard XDG session-bus service dirs.
    """
    import glob

    dirs: list[str] = []
    xdg_data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs.append(xdg_data_home)
    xdg_data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs.extend(d for d in xdg_data_dirs.split(":") if d)
    return any(
        glob.glob(os.path.join(d, "dbus-1", "services", "org.kde.KIOFuse.service")) for d in dirs
    )


def kio_fuse_available() -> bool:
    """True if KDE's kio-fuse (the mount backend that handles the custom SMB
    port) is installed. gvfs can't use the non-standard port, so kio-fuse is
    the real runtime dependency for reverse-open guest-disk access (#616)."""
    import glob
    import shutil

    if shutil.which("kio-fuse"):
        return True
    # Binary locations across distros: plain libexec, KF6-versioned libexec
    # (/usr/libexec/kf6/kio-fuse), lib/lib64, and Debian multiarch
    # (/usr/lib/<triplet>/libexec/kio-fuse -- three levels, missed by the old
    # /usr/lib*/*/kio-fuse two-level glob, #697).
    patterns = [
        "/usr/libexec/kio-fuse",
        "/usr/libexec/*/kio-fuse",
        "/usr/lib/kio-fuse",
        "/usr/lib64/kio-fuse",
        "/usr/lib*/kio-fuse",
        "/usr/lib*/*/kio-fuse",
        "/usr/lib/*/libexec/kio-fuse",
    ]
    if any(glob.glob(p) for p in patterns):
        return True
    # Authoritative fallback: the D-Bus activation service _kio_fuse_mount uses.
    return _kio_fuse_dbus_service_present()


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


def smb_uri(cfg: Config, *, with_password: bool = False) -> str:
    """``smb://<user>[:<pw>]@127.0.0.1:<port>/<share>`` for the guest C: share.

    The password (URL-encoded) is included only for the kio-fuse path, which
    has no out-of-band credential channel; gvfs takes it on stdin instead.
    """
    auth = cfg.rdp.user
    if with_password and cfg.rdp.password:
        auth = f"{cfg.rdp.user}:{quote(cfg.rdp.password, safe='')}"
    return f"smb://{auth}@127.0.0.1:{SMB_HOST_PORT}/{GUEST_SMB_SHARE}"


def _kio_fuse_mount(cfg: Config) -> Path | None:
    """Mount via KDE's kio-fuse and return the real FUSE path, or None.

    kio-fuse (KDE) honours a non-standard SMB port — unlike gvfs, which
    rejects ``smb://host:4445/…`` with "Invalid argument" — so it's the
    primary path for the unprivileged host port rootless podman forces
    (#616). ``mountUrl`` returns a real path under
    ``$XDG_RUNTIME_DIR/kio-fuse-XXXX/`` that any host app can open, and the
    mount is live (edits write straight back to the guest file).

    The credentialed URL is passed as a D-Bus arg; on a single-user desktop
    over loopback that exposure is acceptable (gvfs's stdin path isn't
    available here).
    """
    url = smb_uri(cfg, with_password=True)
    try:
        proc = subprocess.run(
            [
                "dbus-send",
                "--session",
                "--print-reply",
                "--dest=org.kde.KIOFuse",
                "/org/kde/KIOFuse",
                "org.kde.KIOFuse.VFS.mountUrl",
                f"string:{url}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        log.debug("guest mount: kio-fuse mountUrl failed: %s", proc.stderr.strip())
        return None
    m = re.search(r'string\s+"([^"]+)"', proc.stdout)
    if not m:
        return None
    path = Path(m.group(1))
    return path if path.is_dir() else None


def _gvfs_mount(cfg: Config) -> Path | None:
    """Mount via gvfs (GNOME) and return the FUSE path, or None.

    Fallback for non-KDE hosts. NOTE: gvfs rejects a non-standard SMB port,
    so this only succeeds when the host publishes the share on the default
    445 — on the 4445 default it will fail and the caller falls through.
    """
    existing = _find_existing_mount()
    if existing is not None:
        return existing
    uri = smb_uri(cfg)
    password = cfg.rdp.password or ""
    try:
        proc = subprocess.run(
            ["gio", "mount", uri],
            input=password + "\n\n\n",
            text=True,
            capture_output=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        log.debug("guest mount: gio mount rc=%d stderr=%s", proc.returncode, proc.stderr.strip())
    return _find_existing_mount()


def ensure_guest_mount(cfg: Config) -> Path | None:
    """Mount the guest ``C:\\`` SMB share on the host and return its FUSE path.

    Userspace mount — no sudo. Tries KDE's kio-fuse first (it honours the
    non-standard host port rootless podman forces), then gvfs (GNOME; default
    port only). Idempotent. Returns ``None`` (with a logged reason) when no
    backend can reach the share — the reverse-open listener then rejects the
    guest request cleanly instead of raising.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return None

    mount = _kio_fuse_mount(cfg)
    if mount is not None:
        return mount

    mount = _gvfs_mount(cfg)
    if mount is None:
        log.warning("guest mount: could not mount %s via kio-fuse or gvfs", smb_uri(cfg))
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
