#!/usr/bin/env bash
# AppImage entrypoint -- forwards all arguments to the bundled winpodx
# console script. python-appimage stages the Python runtime at
# ${APPDIR}/opt/python<MAJ>.<MIN>/ and the winpodx entry script at
# ${APPDIR}/opt/python<MAJ>.<MIN>/bin/winpodx.
#
# Thin AppImage layout (0.6.0 item A, CI bundles via bundle-system-bins.sh):
#   ${APPDIR}/usr/bin/      -- xfreerdp3 / wlfreerdp3 / sdl-freerdp3 only
#   ${APPDIR}/usr/lib/      -- transitive .so deps for the above + Python
#                              + Qt (host-critical libs like libX11 /
#                              libGL / glibc stay on host)
#
# NO container runtime is bundled. podman / podman-compose / conmon /
# crun / netavark / aardvark-dns / pasta / slirp4netns all come from
# the HOST package manager -- rootless podman fundamentally needs host
# systemd / subuid integration that an AppImage can't carry. Bundling
# it caused #357 (Ubuntu 26.04, shadowed host podman-compose) and #363
# (Fedora Bluefin, LD_LIBRARY_PATH-poisoned aardvark-dns). The Thin
# redesign acknowledges that limit instead of patching around it.
#
# The PATH + LD_LIBRARY_PATH prepends below are REQUIRED for the
# bundled FreeRDP + Python + Qt to load their bundled .so deps. They
# no longer shadow any host container binary because none are bundled.
# backend/_hostenv.host_env() still strips LD_LIBRARY_PATH when spawning
# the host container runtime (so its host helpers load HOST libs, not
# bundled libcrypto -- the #363 mitigation that survives Thin).
set -euo pipefail

# Bundled FreeRDP + Python + Qt need these prepends to find their .so deps.
if [ -d "${APPDIR}/usr/bin" ]; then
    export PATH="${APPDIR}/usr/bin:${PATH:-}"
fi
if [ -d "${APPDIR}/usr/lib" ]; then
    export LD_LIBRARY_PATH="${APPDIR}/usr/lib:${LD_LIBRARY_PATH:-}"
fi

# Resolve the bundled python entrypoint that python-appimage placed
# under opt/. There is exactly one python<MAJ>.<MIN> directory; the
# glob is stable across recipe builds.
shopt -s nullglob
PY_BIN_GLOB=("${APPDIR}"/opt/python*/bin/winpodx)
if [ ${#PY_BIN_GLOB[@]} -eq 0 ]; then
    echo "winpodx AppImage: bundled winpodx entrypoint missing -- this AppImage is corrupt." >&2
    exit 127
fi

exec "${PY_BIN_GLOB[0]}" "$@"
