#!/usr/bin/env bash
# AppImage entrypoint -- forwards all arguments to the bundled winpodx
# console script. python-appimage stages the Python runtime at
# ${APPDIR}/opt/python<MAJ>.<MIN>/ and the winpodx entry script at
# ${APPDIR}/opt/python<MAJ>.<MIN>/bin/winpodx.
#
# winpodx itself handles first-run setup (cli/first_run.py -> setup_cmd
# wizard) and dependency detection (cli/doctor.py). We do not duplicate
# that logic here -- the AppImage is "just another install method" from
# the rest of the codebase's perspective. System dependencies that the
# AppImage cannot bundle (KVM access via the kvm group, /dev/kvm itself,
# rootless podman subuid/subgid mappings, optionally xfreerdp3 + podman
# if not yet bundled into the AppImage) are surfaced through the normal
# `winpodx setup` / `winpodx doctor` paths with their existing per-distro
# install hints.
set -euo pipefail

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
