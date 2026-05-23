#!/usr/bin/env bash
# AppImage entrypoint -- forwards all arguments to the bundled winpodx
# console script. python-appimage stages the Python runtime at
# ${APPDIR}/opt/python<MAJ>.<MIN>/ and the winpodx entry script at
# ${APPDIR}/opt/python<MAJ>.<MIN>/bin/winpodx.
#
# Fat AppImage layout (CI bundles these via bundle-system-bins.sh):
#   ${APPDIR}/usr/bin/      -- xfreerdp3, podman, podman-compose, etc.
#   ${APPDIR}/usr/lib/      -- transitive .so deps (host-critical libs
#                              like libX11 / libGL / glibc stay on host)
#
# We prepend the bundled bin + lib paths to PATH / LD_LIBRARY_PATH so
# winpodx's subprocess calls find the bundled binaries first, falling
# back to the host if a bundled copy is absent (e.g. lean-build AppImage
# without the system-bin overlay).
set -euo pipefail

# Prefer bundled binaries + libs.
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
