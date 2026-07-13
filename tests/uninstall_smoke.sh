#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Smoke test for uninstall.sh (#255 consolidation).
#
# Builds a sandbox $HOME with the file structure a fully-installed
# winpodx leaves behind, runs uninstall.sh against it with --yes,
# and asserts the expected files are gone.
#
# Run:
#   bash tests/uninstall_smoke.sh
#
# Exit codes:
#   0 -- all assertions passed
#   1 -- at least one assertion failed
#   2 -- harness setup failed

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNINSTALL_SH="$REPO_ROOT/uninstall.sh"

if [ ! -x "$UNINSTALL_SH" ]; then
    echo "[smoke] $UNINSTALL_SH not executable" >&2
    exit 2
fi

SANDBOX_BASE="${WINPODX_TMPDIR:-${TMPDIR:-$REPO_ROOT/.priv-storage/sessions}}"
mkdir -p "$SANDBOX_BASE"
SANDBOX="$(mktemp -d -p "$SANDBOX_BASE" winpodx-uninstall-smoke-XXXXXX)"
trap 'rm -rf "$SANDBOX"' EXIT

# Lay down the typical curl-install topology under the sandbox HOME.
mkdir -p "$SANDBOX/.local/bin/winpodx-app"
mkdir -p "$SANDBOX/.local/share/applications"
mkdir -p "$SANDBOX/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$SANDBOX/.local/share/winpodx"
mkdir -p "$SANDBOX/.config/winpodx"
mkdir -p "$SANDBOX/.config/autostart"

touch "$SANDBOX/.local/bin/winpodx-app/marker"
ln -sf "$SANDBOX/.local/bin/winpodx-app/winpodx-run" "$SANDBOX/.local/bin/winpodx"
echo "#!/bin/sh" > "$SANDBOX/.local/bin/winpodx-run"
chmod +x "$SANDBOX/.local/bin/winpodx-run"

touch "$SANDBOX/.local/share/applications/winpodx.desktop"
touch "$SANDBOX/.local/share/applications/winpodx-notepad.desktop"
touch "$SANDBOX/.local/share/applications/winpodx-paint.desktop"
echo "[Desktop Entry]" > "$SANDBOX/.local/share/applications/mimeinfo.cache"
echo "image/png=winpodx-paint.desktop;other.desktop;" \
    >> "$SANDBOX/.local/share/applications/mimeinfo.cache"

touch "$SANDBOX/.local/share/icons/hicolor/scalable/apps/winpodx.svg"
touch "$SANDBOX/.local/share/icons/hicolor/scalable/apps/winpodx-notepad.svg"

echo 'placeholder' > "$SANDBOX/.config/winpodx/winpodx.toml"
touch "$SANDBOX/.config/autostart/winpodx-tray.desktop"
echo 'app-data' > "$SANDBOX/.local/share/winpodx/apps.json"

# Run uninstall.sh against the sandbox HOME with --purge --yes.
#
# CRITICAL: clear every XDG_* var. The script uses
# ``${XDG_DATA_HOME:-$HOME/.local/share}`` style fallbacks, so if the
# caller's XDG_DATA_HOME points at the real $HOME, the script would
# happily scrub the real user's winpodx files. We explicitly unset all
# XDG_* and set HOME = sandbox.
#
# WINPODX_BACKEND=true short-circuits the runtime detect (no podman
# attempts against a non-existent container). PATH restricted to
# /usr/bin:/bin so the script doesn't pick up local winpodx-app shims.
echo "[smoke] running uninstall.sh against sandbox HOME"
env -i \
    HOME="$SANDBOX" \
    PATH="/usr/bin:/bin" \
    XDG_RUNTIME_DIR="$SANDBOX/run" \
    XDG_DATA_HOME="$SANDBOX/.local/share" \
    XDG_CONFIG_HOME="$SANDBOX/.config" \
    XDG_CACHE_HOME="$SANDBOX/.cache" \
    WINPODX_BACKEND="true" \
    WINPODX_CONTAINER_NAME="winpodx-windows-test" \
    WINPODX_STORAGE_PATH="$SANDBOX/.local/share/winpodx/storage" \
    bash "$UNINSTALL_SH" --purge --yes >/dev/null 2>&1 || true

# Assertions.
FAIL=0
assert_gone() {
    if [ -e "$1" ] || [ -L "$1" ]; then
        echo "[smoke] FAIL: still exists -- $1" >&2
        FAIL=$((FAIL + 1))
    else
        echo "[smoke] OK:   removed   -- $1"
    fi
}
assert_mime_cleaned() {
    local cache="$1"
    if [ -f "$cache" ] && grep -q 'winpodx' "$cache"; then
        echo "[smoke] FAIL: winpodx still in $cache" >&2
        FAIL=$((FAIL + 1))
    else
        echo "[smoke] OK:   mime cache scrubbed"
    fi
}

assert_gone "$SANDBOX/.local/bin/winpodx-app"
assert_gone "$SANDBOX/.local/bin/winpodx"
assert_gone "$SANDBOX/.local/bin/winpodx-run"
assert_gone "$SANDBOX/.local/share/applications/winpodx.desktop"
assert_gone "$SANDBOX/.local/share/applications/winpodx-notepad.desktop"
assert_gone "$SANDBOX/.local/share/applications/winpodx-paint.desktop"
assert_gone "$SANDBOX/.local/share/icons/hicolor/scalable/apps/winpodx.svg"
assert_gone "$SANDBOX/.local/share/icons/hicolor/scalable/apps/winpodx-notepad.svg"
assert_gone "$SANDBOX/.local/share/winpodx"
assert_gone "$SANDBOX/.config/winpodx"
assert_gone "$SANDBOX/.config/autostart/winpodx-tray.desktop"
assert_mime_cleaned "$SANDBOX/.local/share/applications/mimeinfo.cache"

if [ "$FAIL" -gt 0 ]; then
    echo "[smoke] $FAIL assertion(s) failed" >&2
    exit 1
fi

# --- #716 data-loss regression: a non-purge uninstall must KEEP the VM disk ---
#
# The default VM storage lives under DATA_DIR (~/.local/share/winpodx/storage).
# A non-purge `uninstall.sh --confirm` is documented to preserve Windows VM
# data, so it must NOT rm -rf the parent DATA_DIR out from under storage/.
assert_exists() {
    if [ -e "$1" ]; then
        echo "[smoke] OK:   preserved -- $1"
    else
        echo "[smoke] FAIL: deleted   -- $1" >&2
        FAIL=$((FAIL + 1))
    fi
}

# Seed the full install topology so uninstall.sh (set -euo pipefail) runs to
# completion — a missing ~/.local/share/applications makes an early `find` exit
# non-zero and abort before the App-definitions step we're testing.
_seed_topology() {
    local sb="$1"
    mkdir -p "$sb/.local/share/applications" \
             "$sb/.local/share/icons/hicolor/scalable/apps" \
             "$sb/.config/winpodx" "$sb/.config/autostart"
    touch "$sb/.local/share/applications/winpodx.desktop"
    echo '[Desktop Entry]' > "$sb/.local/share/applications/mimeinfo.cache"
    echo 'placeholder' > "$sb/.config/winpodx/winpodx.toml"
}

echo "[smoke] #716 non-purge preserves nested VM storage"
SB2="$(mktemp -d -p "$SANDBOX_BASE" winpodx-uninstall-nonpurge-XXXXXX)"
trap 'rm -rf "$SANDBOX" "$SB2" "${SB3:-}"' EXIT
_seed_topology "$SB2"
mkdir -p "$SB2/.local/share/winpodx/storage" "$SB2/.local/share/winpodx/apps"
echo 'VMDISK' > "$SB2/.local/share/winpodx/storage/data.img"
echo 'app-data' > "$SB2/.local/share/winpodx/apps/notepad.toml"
env -i HOME="$SB2" PATH="/usr/bin:/bin" \
    XDG_RUNTIME_DIR="$SB2/run" XDG_DATA_HOME="$SB2/.local/share" \
    XDG_CONFIG_HOME="$SB2/.config" XDG_CACHE_HOME="$SB2/.cache" \
    WINPODX_BACKEND="true" WINPODX_CONTAINER_NAME="winpodx-windows-test" \
    WINPODX_STORAGE_PATH="$SB2/.local/share/winpodx/storage" \
    bash "$UNINSTALL_SH" --from-postrm --confirm >/dev/null 2>&1 || true
assert_exists "$SB2/.local/share/winpodx/storage/data.img"    # VM disk kept
assert_gone   "$SB2/.local/share/winpodx/apps/notepad.toml"   # app state gone

echo "[smoke] #716 non-purge leaves an OUTSIDE custom storage untouched + removes DATA_DIR"
SB3="$(mktemp -d -p "$SANDBOX_BASE" winpodx-uninstall-extstore-XXXXXX)"
_seed_topology "$SB3"
mkdir -p "$SB3/.local/share/winpodx/apps" "$SB3/ext-store"
echo 'VMDISK' > "$SB3/ext-store/data.img"
echo 'app-data' > "$SB3/.local/share/winpodx/apps/notepad.toml"
env -i HOME="$SB3" PATH="/usr/bin:/bin" \
    XDG_RUNTIME_DIR="$SB3/run" XDG_DATA_HOME="$SB3/.local/share" \
    XDG_CONFIG_HOME="$SB3/.config" XDG_CACHE_HOME="$SB3/.cache" \
    WINPODX_BACKEND="true" WINPODX_CONTAINER_NAME="winpodx-windows-test" \
    WINPODX_STORAGE_PATH="$SB3/ext-store" \
    bash "$UNINSTALL_SH" --from-postrm --confirm >/dev/null 2>&1 || true
assert_exists "$SB3/ext-store/data.img"          # external storage untouched
assert_gone   "$SB3/.local/share/winpodx"        # DATA_DIR removed wholesale

if [ "$FAIL" -gt 0 ]; then
    echo "[smoke] $FAIL assertion(s) failed" >&2
    exit 1
fi

echo "[smoke] all assertions passed"
