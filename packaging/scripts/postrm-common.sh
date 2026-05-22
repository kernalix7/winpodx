#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Common post-remove hook for winpodx packaging (#255).
#
# Invoked by:
#   debian/postrm        ($1 = remove|purge|upgrade|...)
#   rpm %postun          ($1 = number, 0 = removed, >=1 = upgrade)
#   aur winpodx.install  (post_remove function passes "remove")
#
# Behaviour mirrors apt remove vs apt purge:
#
#   * "upgrade" (rpm passes >= 1) -> exit 0; the package isn't going away.
#   * "remove"  -> run uninstall.sh --from-postrm --yes for every user
#                  with a winpodx config dir. Container, podman volume,
#                  config, and storage stay intact.
#   * "purge"   -> add --purge to the above. Container, volume, config,
#                  and storage all wiped.
#
# All cleanup is delegated to /usr/share/winpodx/uninstall.sh -- one
# canonical implementation shared with the curl install, pip install,
# and `winpodx uninstall` paths. See issue #255 for the consolidation
# rationale.
#
# --from-postrm tells uninstall.sh to skip the install-source detect
# step (we ARE the package manager removal -- detecting and re-exec'ing
# would loop) and the host-open stop-listener calls (the binary may
# already be gone).

set -e

MODE="${1:-remove}"
UNINSTALL_SH="/usr/share/winpodx/uninstall.sh"

# Normalise rpm / debian / aur mode arg.
case "$MODE" in
    upgrade|1|2|3|4|5|6|7|8|9)
        exit 0
        ;;
    purge)
        PURGE_FLAG="--purge"
        ;;
    *)
        PURGE_FLAG=""
        ;;
esac

# If for some reason the canonical script is missing (corrupted
# install, manual file removal), fall back to a minimal pkill so we
# don't leave processes pointing at a now-deleted binary.
if [ ! -x "$UNINSTALL_SH" ]; then
    for home in /home/*; do
        [ -d "$home" ] || continue
        user=$(basename "$home")
        [ -d "$home/.config/winpodx" ] || continue
        runuser -u "$user" -- pkill -f 'python.*winpodx' >/dev/null 2>&1 || true
        runuser -u "$user" -- pkill -f 'winpodx-app'    >/dev/null 2>&1 || true
    done
    exit 0
fi

# Iterate every user (including root, in case of system-wide install).
# Each user gets their own uninstall.sh invocation so user-state under
# $HOME is cleaned up regardless of who installed the package.
cleanup_for_user() {
    user="$1"
    home="$2"
    [ -d "$home/.config/winpodx" ] || return 0
    # shellcheck disable=SC2086
    runuser -u "$user" -- "$UNINSTALL_SH" --from-postrm --yes $PURGE_FLAG \
        >/dev/null 2>&1 || true
}

for home in /home/*; do
    [ -d "$home" ] || continue
    user=$(basename "$home")
    cleanup_for_user "$user" "$home"
done

# Also handle root, in case the install was system-wide root state.
if [ -d /root/.config/winpodx ]; then
    # shellcheck disable=SC2086
    "$UNINSTALL_SH" --from-postrm --yes $PURGE_FLAG >/dev/null 2>&1 || true
fi

exit 0
