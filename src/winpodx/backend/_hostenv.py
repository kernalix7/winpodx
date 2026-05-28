# SPDX-License-Identifier: MIT
"""Host-first container-backend invocation inside an AppImage.

Background (#357, #363)
=======================
The fat AppImage bundles a full podman stack -- podman, conmon, crun,
netavark, pasta -- into ``${APPDIR}/usr/bin`` and bundles their transitive
``.so`` deps into ``${APPDIR}/usr/lib``. The AppImage entrypoint then
*prepends* those two directories to ``PATH`` and ``LD_LIBRARY_PATH`` so the
bundled binaries win. That is the right behaviour for the *self-contained*
goal (a host with no podman at all), but it actively breaks two classes of
host that already have a working podman:

* **#357 (Ubuntu 26.04):** ``podman-compose`` resolves to the *bundled* one
  (PATH prepend), which probes for ``podman`` and the bundled podman cannot
  run standalone (no host ``/etc/containers`` config, no subuid/subgid, no
  systemd integration) -> podman-compose prints
  ``it seems that you do not have podman installed`` and container start dies.
  The host's working podman 5.7 + podman-compose 1.5 are shadowed.

* **#363 (Fedora Bluefin):** podman shells out to the HOST ``systemd-run``
  (rootless aardvark-dns), but the prepended ``${APPDIR}/usr/lib`` forces
  that host binary to load the AppImage's bundled ``libcrypto.so.3`` ->
  ``OPENSSL_3.4.0 not found (required by host libsystemd-shared)`` ->
  aardvark-dns fails -> container start dies.

Both reporters HAVE a working host podman; in both cases the bundled stack +
the prepended PATH / LD_LIBRARY_PATH shadow or poison it.

Fix: host-first
===============
When winpodx runs inside an AppImage, invoke the *container backend*
(podman, podman-compose, and helpers like ``podman pause`` / ``inspect`` /
``restart``) using:

1. **Host-resolved binaries** -- resolve from the HOST ``PATH`` (the PATH
   with the ``${APPDIR}/usr/bin`` prefix removed), preferring a host binary
   when present; fall back to the bundled ``${APPDIR}/usr/bin`` copy only
   when the host genuinely lacks it.
2. **A clean host environment for the subprocess** -- strip the AppImage's
   ``${APPDIR}`` paths from ``LD_LIBRARY_PATH`` and the ``${APPDIR}/usr/bin``
   prefix from ``PATH``, so the host podman + the host
   ``systemd-run`` / netavark / aardvark it spawns load HOST libraries.
   Everything else in the environment is preserved.

DO NOT apply this to FreeRDP (``core/rdp.py`` / ``core/windows_exec.py``).
The bundled FreeRDP + its libs SHOULD keep the AppImage env -- FreeRDP is a
leaf that integrates with the user's X / Wayland session and does not spawn
host helpers.

No-op guarantee
===============
Outside an AppImage (``APPDIR`` unset) every function here is a strict
no-op: :func:`host_env` returns ``None`` (callers pass ``env=None`` ->
unchanged ``subprocess`` behaviour) and :func:`resolve_backend_bin` returns
the bare name unchanged. That is the safety guarantee for the ~99% of
installs that are not AppImages.
"""

from __future__ import annotations

import logging
import os
import shutil

log = logging.getLogger(__name__)

# Log the "falling back to bundled podman" WARNING at most once per process
# so a host that genuinely lacks podman doesn't spam stderr on every probe.
_BUNDLED_FALLBACK_WARNED: set[str] = set()


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


def host_path() -> str:
    """Return the host ``PATH`` -- the process PATH with ``${APPDIR}`` entries removed.

    Outside an AppImage this is just ``os.environ["PATH"]`` unchanged.
    """
    path = os.environ.get("PATH", "")
    if not in_appimage():
        return path
    return _strip_appdir_from_path_list(path, _appdir())


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


def resolve_backend_bin(name: str) -> str:
    """Resolve a container-backend binary host-first inside an AppImage.

    * Outside an AppImage: returns ``name`` unchanged (strict no-op -- the
      caller's normal ``PATH`` resolution applies, exactly as before).
    * Inside an AppImage: prefer the host copy found on the host ``PATH``
      (``${APPDIR}/usr/bin`` removed). Only when the host genuinely lacks
      the binary do we fall back to the bundled ``${APPDIR}/usr/bin/<name>``
      -- logging a one-time WARNING because a bundled-only podman stack is
      known-incomplete (the original self-contained best-effort path).

    Returns an absolute path when one is resolved, else the bare ``name``
    (so the caller still gets a usable argv[0] and a clean FileNotFoundError
    if nothing exists anywhere).
    """
    if not in_appimage():
        return name

    appdir = _appdir()

    # 1. Host-first: search the host PATH (APPDIR stripped).
    host = shutil.which(name, path=host_path())
    if host:
        return host

    # 2. Bundled fallback -- best-effort, may be incomplete.
    bundled = os.path.join(appdir, "usr", "bin", name)
    if appdir and os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        if name not in _BUNDLED_FALLBACK_WARNED:
            _BUNDLED_FALLBACK_WARNED.add(name)
            log.warning(
                "No host %r found; falling back to the AppImage-bundled %s. "
                "The bundled container stack is best-effort and may be "
                "incomplete on this host (missing /etc/containers config, "
                "subuid/subgid, or systemd integration). Installing %r on "
                "the host is recommended.",
                name,
                bundled,
                name,
            )
        return bundled

    # 3. Nothing anywhere -- return the bare name so the caller raises a
    #    clean FileNotFoundError with a recognisable argv[0].
    return name
