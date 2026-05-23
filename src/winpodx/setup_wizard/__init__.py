# SPDX-License-Identifier: MIT
"""Host-side setup wizard for the fat AppImage build (#227).

The AppImage bundles FreeRDP + Podman + podman-compose + their runtime
libraries, so users on immutable distros (Fedora Silverblue / Kinoite /
Aeon, Steam Deck) can run winpodx without layering system packages.
What an AppImage CANNOT do from user space:

- Add the current user to the ``kvm`` group (``usermod -aG kvm``).
  Required because rootless QEMU opens ``/dev/kvm`` with group-rw,
  and a user not in ``kvm`` cannot use hardware virtualisation.
- Write ``/etc/subuid`` + ``/etc/subgid`` entries for the current user.
  Required by rootless Podman to map container UIDs into the host
  user namespace.
- Persist a ``/etc/modules-load.d/kvm.conf`` entry to load the
  ``kvm_intel`` / ``kvm_amd`` module at boot when distros default it off.

This module wraps all three with a ``pkexec`` polkit prompt sequence
so the user only needs to authenticate once per session. The detection
half (``detect_host_state``) is read-only and safe to run any time;
``winpodx doctor`` already calls into it indirectly.
"""

from __future__ import annotations

from winpodx.setup_wizard.host_state import HostState, detect_host_state
from winpodx.setup_wizard.pkexec import apply_via_pkexec

__all__ = [
    "HostState",
    "apply_via_pkexec",
    "detect_host_state",
]
