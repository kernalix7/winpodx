#!/usr/bin/env bash
# Local AppImage build helper.
#
# Builds an x86_64 AppImage of winpodx using python-appimage. The
# resulting `winpodx-<version>-x86_64.AppImage` lands in this
# directory. CI uses the same flow via .github/workflows/appimage-
# publish.yml.
#
# Prerequisites (host):
#   - Python 3.11+ with pip
#   - The python-appimage package on PATH (`pip install python-appimage`)
#   - `build` (PyPA build) for generating the local wheel
#   - Internet access (PySide6 + Pillow + cairosvg wheels pulled from PyPI)
#
# Output:
#   - winpodx-<version>-x86_64.AppImage in packaging/appimage/
#
# The recipe directory describes a "lean" AppImage: Python runtime +
# winpodx wheel + PySide6 (Qt6) + reverse-open extras. System binaries
# (FreeRDP, podman, libvirt) are NOT bundled; winpodx's existing
# `winpodx setup` / `winpodx doctor` paths detect them and emit per-
# distro install hints if any are missing. Bundling the system layer
# is tracked separately (see #300-class follow-up issues).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"

cd "${REPO_ROOT}"

echo "[appimage] Building winpodx wheel + sdist into ./dist/ ..."
python3 -m pip install --upgrade build python-appimage >/dev/null
python3 -m build --outdir dist

# Find the wheel we just built and rewrite the recipe's requirements.txt
# to point at it. This keeps the AppImage in sync with the current
# branch's code instead of pulling whatever's on PyPI.
WHEEL="$(ls -t dist/winpodx-*.whl | head -n1)"
if [ -z "${WHEEL}" ]; then
    echo "[appimage] No wheel produced under dist/; aborting." >&2
    exit 1
fi
echo "[appimage] Using wheel: ${WHEEL}"

WHEEL_ABS="$(readlink -f "${WHEEL}")"
REQ="${HERE}/recipe/requirements.txt"
# Pin to absolute path of the freshly-built wheel + keep the extras so
# pip pulls PySide6 + Pillow + cairosvg into the AppImage.
printf '%s[gui,reverse-open]\n' "${WHEEL_ABS}" > "${REQ}"

cd "${HERE}"
echo "[appimage] Running python-appimage build ..."
python3 -m python_appimage build app -p 3.11 recipe/

echo "[appimage] Resulting AppImage(s):"
ls -lh winpodx-*-x86_64.AppImage 2>/dev/null || {
    echo "[appimage] No AppImage produced. Check python-appimage output above." >&2
    exit 1
}

# Restore the recipe to its committed shape so the next git status is clean.
printf 'winpodx[gui,reverse-open]\n' > "${REQ}"
echo "[appimage] Done."
