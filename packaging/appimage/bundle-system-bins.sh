#!/usr/bin/env bash
# Stage system-level FreeRDP 3+ binaries into an existing python-
# appimage AppDir so the resulting AppImage runs on hosts that do not
# have a FreeRDP 3 client installed.
#
# Thin AppImage (0.6.0 item A): the container stack (podman /
# podman-compose / conmon / crun / netavark / aardvark-dns / pasta /
# passt / slirp4netns / fuse-overlayfs) is INTENTIONALLY NOT bundled.
# Rootless podman fundamentally needs host systemd / subuid integration
# that an AppImage cannot carry, and bundling it caused #357 (Ubuntu
# 26.04 -- bundled podman-compose shadowed the host's working stack)
# and #363 (Fedora Bluefin -- LD_LIBRARY_PATH prepend poisoned host
# systemd-run / aardvark-dns with bundled libcrypto). The Thin AppImage
# requires the user to install podman / docker / libvirt from their
# distro package manager (same model as install.sh). FreeRDP stays
# bundled because it is a leaf binary that does not spawn host helpers.
#
# Caveats (FreeRDP-only):
#
# - libX11 / libXrandr / libxkbcommon / libGL / libwayland-client stay
#   on the host so xfreerdp3 integrates with the user's actual X /
#   Wayland session (RAIL would break otherwise).
# - glibc + libdl / libpthread / libc / libm / libresolv / libnsl /
#   libcrypt + ld-linux likewise stay on the host. Bundling glibc into
#   an AppImage is a well-known footgun; the runner-side Fedora glibc
#   would conflict with the user-side glibc on every reasonable distro.
#
# Usage:
#   bundle-system-bins.sh <AppDir>
#
# Must be invoked from a Fedora 41+ environment that already has the
# packages below installed via dnf. See appimage-publish.yml workflow
# for the CI variant.
set -euo pipefail

APPDIR="${1:?usage: $0 <AppDir>}"
if [ ! -d "$APPDIR" ]; then
    echo "[bundle] AppDir not found: $APPDIR" >&2
    exit 1
fi

mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib"

# Binaries to bundle. Names match the Fedora package layout; the
# script tolerates missing entries so it can be re-used on newer
# distros where the FreeRDP 3 binary naming shifts (xfreerdp /
# xfreerdp3 / wlfreerdp / sdl-freerdp).
#
# Thin AppImage: only FreeRDP 3 client binaries here. The container
# stack (podman / podman-compose / conmon / crun / netavark /
# slirp4netns / pasta / passt) is intentionally NOT bundled -- see the
# header comment for the root-cause rationale (#357 / #363).
BINARIES=(
    xfreerdp3
    xfreerdp
    wlfreerdp3
    wlfreerdp
    sdl-freerdp3
    sdl-freerdp
)

echo "[bundle] Copying binaries into $APPDIR/usr/bin/ ..."
for bin in "${BINARIES[@]}"; do
    for path in "/usr/bin/$bin" "/usr/libexec/podman/$bin" "/usr/libexec/$bin"; do
        if [ -f "$path" ]; then
            cp -L "$path" "$APPDIR/usr/bin/"
            echo "  + $bin (from $path)"
            break
        fi
    done
done

# Defensive sweep: if the FreeRDP package shipped any /usr/bin/*freerdp*
# binary we didn't enumerate, grab it. Fedora package naming for the
# FreeRDP 3 client has changed across releases (xfreerdp / xfreerdp3 /
# freerdp / sdl-freerdp + arch suffix); this catches whichever variant
# is present in the install.
echo "[bundle] Defensive freerdp glob:"
for path in /usr/bin/*freerdp* /usr/libexec/*freerdp*; do
    if [ -f "$path" ]; then
        base="$(basename "$path")"
        if [ ! -f "$APPDIR/usr/bin/$base" ]; then
            cp -L "$path" "$APPDIR/usr/bin/"
            echo "  + $base (from $path, defensive)"
        fi
    fi
done

# Library exclude list -- these MUST come from the host even on
# distro-agnostic AppImages. Bundling them is either a crash hazard
# (glibc family) or a desktop-integration hazard (X / Wayland /
# GL stack).
HOST_LIBS_REGEX='^/(usr/)?(lib(64)?(/[^/]+)?)/('\
'ld-linux[^/]*\.so[^/]*'\
'|libc\.so[^/]*'\
'|libm\.so[^/]*'\
'|libdl\.so[^/]*'\
'|libpthread\.so[^/]*'\
'|librt\.so[^/]*'\
'|libutil\.so[^/]*'\
'|libresolv\.so[^/]*'\
'|libnsl\.so[^/]*'\
'|libcrypt\.so[^/]*'\
'|libgcc_s\.so[^/]*'\
'|libstdc\+\+\.so[^/]*'\
'|libX11\.so[^/]*'\
'|libXrandr\.so[^/]*'\
'|libXi\.so[^/]*'\
'|libXcursor\.so[^/]*'\
'|libXfixes\.so[^/]*'\
'|libXrender\.so[^/]*'\
'|libXext\.so[^/]*'\
'|libxcb[^/]*\.so[^/]*'\
'|libxkbcommon[^/]*\.so[^/]*'\
'|libwayland-[^/]+\.so[^/]*'\
'|libGL\.so[^/]*'\
'|libGLX\.so[^/]*'\
'|libGLdispatch\.so[^/]*'\
'|libEGL\.so[^/]*'\
'|libdbus-1\.so[^/]*'\
')$'

echo "[bundle] Traversing ldd for transitive deps ..."
declare -A SEEN_LIBS=()
copy_lib() {
    local lib="$1"
    [ -f "$lib" ] || return 0
    local base
    base="$(basename "$lib")"
    [ -z "${SEEN_LIBS[$base]:-}" ] || return 0
    SEEN_LIBS[$base]=1
    if [[ "$lib" =~ $HOST_LIBS_REGEX ]]; then
        return 0
    fi
    cp -L "$lib" "$APPDIR/usr/lib/" 2>/dev/null || true
}

# ldd-traverse every bundled binary + every lib we copy in (transitive).
# Two-pass: collect, then copy, then re-traverse the copies until
# fixpoint (libs depending on other libs).
queue=()
for bin in "$APPDIR/usr/bin"/*; do
    [ -f "$bin" ] || continue
    queue+=("$bin")
done

while [ ${#queue[@]} -gt 0 ]; do
    next_queue=()
    for item in "${queue[@]}"; do
        # ldd output: "  libfoo.so.1 => /usr/lib/libfoo.so.1 (0x...)"
        while read -r lib; do
            [ -n "$lib" ] || continue
            local_before_count=${#SEEN_LIBS[@]}
            copy_lib "$lib"
            # If we copied a new lib, queue it for transitive ldd
            base="$(basename "$lib")"
            if [ -f "$APPDIR/usr/lib/$base" ] && [ "${SEEN_LIBS[$base]:-}" = "1" ] && [ $local_before_count -lt ${#SEEN_LIBS[@]} ]; then
                next_queue+=("$APPDIR/usr/lib/$base")
            fi
        done < <(ldd "$item" 2>/dev/null | grep -oE '/[^ ]+\.so[^ ]*')
    done
    queue=("${next_queue[@]}")
done

echo "[bundle] Bundled binaries:"
ls -1 "$APPDIR/usr/bin" | sed 's/^/  /'
echo "[bundle] Bundled libraries: $(ls "$APPDIR/usr/lib" | wc -l) files"
echo "[bundle] AppDir size: $(du -sh "$APPDIR" | cut -f1)"
