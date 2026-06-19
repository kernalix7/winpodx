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
