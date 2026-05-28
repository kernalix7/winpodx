# SPDX-License-Identifier: MIT
"""Clean-host-env helper for container-backend subprocesses in a Thin AppImage.

Background (#357, #363) -- what this used to be
================================================
The fat AppImage bundled a full podman stack -- podman, conmon, crun,
netavark, pasta -- into ``${APPDIR}/usr/bin``, and the entrypoint prepended
that directory to ``PATH`` plus ``${APPDIR}/usr/lib`` to ``LD_LIBRARY_PATH``
so the bundled binaries won. That broke two classes of host that already had
a working podman:

* **#357 (Ubuntu 26.04):** ``podman-compose`` resolved to the *bundled* one
  (PATH prepend), which probed for ``podman`` and the bundled podman could
  not run standalone (no host ``/etc/containers`` config, no subuid/subgid,
  no systemd integration) -> ``it seems that you do not have podman installed``
  and container start died. The host's working stack was shadowed.

* **#363 (Fedora Bluefin):** podman shelled out to the HOST ``systemd-run``
  (rootless aardvark-dns), but the prepended ``${APPDIR}/usr/lib`` forced
  that host binary to load the AppImage's bundled ``libcrypto.so.3`` ->
  ``OPENSSL_3.4.0 not found`` -> aardvark-dns failed -> container start died.

PR #365 patched around the symptoms with a host-first ``resolve_backend_bin``
+ an ``LD_LIBRARY_PATH`` strip. 0.6.0 item A removes the root cause instead:
the Thin AppImage no longer bundles the podman stack at all (see
``packaging/appimage/bundle-system-bins.sh`` + the workflow). The host
``podman`` / ``podman-compose`` are reached via standard ``PATH`` resolution
because nothing in ``${APPDIR}/usr/bin`` shadows them anymore.

What survives in Thin
=====================
The ``LD_LIBRARY_PATH`` strip survives because the bundled FreeRDP + Python +
Qt still need ``${APPDIR}/usr/lib`` on ``LD_LIBRARY_PATH`` (the entrypoint
prepends it for them), and when the host container runtime spawns host
helpers (``systemd-run`` / ``netavark`` / ``aardvark-dns``) those inherit
that env. They must NOT load the bundled ``libcrypto.so.3`` / ``libssl.so.3``
-- they must load the HOST libs. :func:`host_env` returns an ``os.environ``
copy with ``${APPDIR}`` entries removed from both ``PATH`` and
``LD_LIBRARY_PATH``; callers pass it as ``env=`` to ``subprocess`` for every
container-backend invocation.

DO NOT apply this to FreeRDP (``core/rdp.py`` / ``core/windows_exec.py``).
FreeRDP is a leaf binary that integrates with the user's X / Wayland session
and does not spawn host helpers; it should keep the AppImage env.

No-op guarantee
===============
Outside an AppImage (``APPDIR`` unset) :func:`host_env` returns ``None`` so
callers pass ``env=None`` -> ``subprocess`` inherits the current environment
unchanged. That is the safety guarantee for the ~99% of installs that are
not AppImages.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def in_appimage() -> bool:
    """Return True when winpodx is running inside an AppImage.

    Detected via the ``APPDIR`` env var, which the AppRun / python-appimage
    entrypoint always sets to the mounted AppDir root. (``APPIMAGE`` -- the
    path to the .AppImage file itself -- is also exported by the AppImage
    runtime, but ``APPDIR`` is the one we key off because it is the path
    prefix we need to strip.)
    """
    return bool(os.environ.get("APPDIR"))


def _appdir() -> str:
    """Return the normalised ``${APPDIR}`` value, or empty string."""
    return (os.environ.get("APPDIR") or "").rstrip("/")


def _strip_appdir_from_path_list(value: str, appdir: str) -> str:
    """Drop every ``${APPDIR}``-rooted entry from a ``:``-joined path list.

    Used for both ``PATH`` (strip ``${APPDIR}/usr/bin``) and
    ``LD_LIBRARY_PATH`` (strip ``${APPDIR}/usr/lib``). We drop *any* entry
    that lives under ``${APPDIR}`` rather than matching the two exact
    directories the entrypoint prepends, so a future entrypoint change that
    adds e.g. ``${APPDIR}/usr/lib64`` is still neutralised.

    Empty entries (a leading / trailing / doubled ``:``) are preserved as-is
    so we don't change the search semantics of a path the user authored;
    they only ever appear when the original already had them.
    """
    if not value:
        return value
    kept: list[str] = []
    for entry in value.split(os.pathsep):
        norm = entry.rstrip("/")
        if appdir and (norm == appdir or norm.startswith(appdir + "/")):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


def host_env() -> dict[str, str] | None:
    """Return a clean host environment for a container-backend subprocess.

    Returns ``None`` outside an AppImage so callers can pass it straight to
    ``subprocess`` as ``env=`` and get byte-for-byte unchanged behaviour
    (``env=None`` means "inherit the current environment").

    Inside an AppImage, returns a copy of ``os.environ`` with:

    * ``${APPDIR}/usr/bin`` (and any ``${APPDIR}`` entry) removed from ``PATH``
    * ``${APPDIR}`` entries removed from ``LD_LIBRARY_PATH`` (and the key
      dropped entirely if that empties it, so the host dynamic linker uses
      its default search path rather than seeing an empty override)

    Every other variable is preserved.

    Thin AppImage (0.6.0 item A): the ``PATH`` strip is mostly belt-and-
    braces now that the podman stack is no longer bundled -- there is
    nothing in ``${APPDIR}/usr/bin`` to shadow the host container runtime,
    so standard ``subprocess`` PATH resolution finds the host ``podman`` /
    ``podman-compose`` directly. The ``LD_LIBRARY_PATH`` strip is the
    load-bearing part: the bundled FreeRDP / Python / Qt still need
    ``${APPDIR}/usr/lib`` on the AppImage's own ``LD_LIBRARY_PATH``, and
    when the host container runtime spawns host helpers (``systemd-run`` /
    ``netavark`` / ``aardvark-dns``) those helpers MUST load HOST libcrypto
    / libssl, not the bundled ones (the #363 root cause).
    """
    if not in_appimage():
        return None

    appdir = _appdir()
    env = dict(os.environ)

    env["PATH"] = _strip_appdir_from_path_list(env.get("PATH", ""), appdir)

    ld = env.get("LD_LIBRARY_PATH")
    if ld is not None:
        cleaned = _strip_appdir_from_path_list(ld, appdir)
        if cleaned:
            env["LD_LIBRARY_PATH"] = cleaned
        else:
            # An empty LD_LIBRARY_PATH is NOT equivalent to unset -- the
            # dynamic linker treats "" as the current directory. Drop the
            # key so the host linker falls back to its default search path.
            env.pop("LD_LIBRARY_PATH", None)

    return env
