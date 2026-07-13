#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Common pre-remove cleanup helper for winpodx packaging (#255).
#
# Invoked by:
#   debian/prerm                  mode "remove", BEFORE dpkg deletes
#                                  the package's files.
#   debian/postrm                 mode "purge", via a STAGED copy at
#                                  /var/lib/winpodx/pkg-cleanup/ --
#                                  postrm itself runs AFTER files are
#                                  removed, so it can no longer reach
#                                  the package-owned copy of this
#                                  script (debian/prerm stages the
#                                  copy for exactly this reason).
#   rpm %preun                    mode "remove" (spec gates the call
#                                  on `[ "$1" -eq 0 ]`, i.e. erase, not
#                                  upgrade), BEFORE rpm removes files.
#   aur/aur-git winpodx.install   pre_remove() passes "remove", BEFORE
#                                  pacman removes files.
#
# Every channel that calls this script directly does so from a
# PRE-remove hook, while the package's own files -- including this
# script and uninstall.sh -- are still on disk. Nothing calls this
# script from a post-remove hook any more: package managers delete
# /usr/share/winpodx/* before post-remove hooks run, which made the
# old post-hook delegation dead code on every channel (see the #255
# packaging audit). The one exception (debian purge) works around
# that by staging a copy somewhere the package doesn't own -- see
# debian/prerm and debian/postrm.
#
# Mode -> action:
#
#   "remove" (default; also any value not matched below) -> run
#       uninstall.sh --from-postrm --yes for every user with a
#       winpodx config dir. Container, podman volume, config, and
#       storage (the VM disk) stay intact.
#   "purge" -> add --purge to the above. Container, volume, config,
#       and storage are all wiped too. Only ever reached via the
#       debian staged-copy path described above -- rpm and pacman
#       have no purge concept.
#   "upgrade", or a bare rpm relcount >= 1 -> exit 0 immediately. The
#       package isn't going away; an in-place version bump must never
#       touch user state (container/VM/config).
#
# --from-postrm tells uninstall.sh to skip the install-source detect
# step (we ARE the package manager's own removal -- detecting and
# re-exec'ing the package manager here would loop) and the host-open
# stop-listener calls made through the winpodx binary (which may
# already be gone by the time the debian staged-purge path runs).

set -e

MODE="${1:-remove}"

# Resolve uninstall.sh relative to *this* script rather than a fixed
# /usr/share path. This script runs both from its normal install
# location (/usr/share/winpodx/packaging/postrm-common.sh, sibling
# "../uninstall.sh") and from debian's purge staging copy
# (/var/lib/winpodx/pkg-cleanup/postrm-common.sh, flat layout,
# sibling "./uninstall.sh").
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ -x "$SCRIPT_DIR/../uninstall.sh" ]; then
    UNINSTALL_SH="$SCRIPT_DIR/../uninstall.sh"
elif [ -x "$SCRIPT_DIR/uninstall.sh" ]; then
    UNINSTALL_SH="$SCRIPT_DIR/uninstall.sh"
else
    UNINSTALL_SH="/usr/share/winpodx/uninstall.sh"
fi

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

# Run a command as $1 (user), with $2 as its HOME, and a scrubbed
# environment. root's XDG_*/DBUS_*/etc must never leak into per-user
# cleanup -- that produces wrong-user paths and a stale root D-Bus
# session address. `runuser` alone does not clear the caller's
# environment, so wrap it in `env -i`.
run_as_user() {
    _user="$1"; _home="$2"; shift 2
    _uid=$(id -u "$_user" 2>/dev/null || echo 0)
    env -i \
        HOME="$_home" \
        USER="$_user" \
        LOGNAME="$_user" \
        XDG_RUNTIME_DIR="/run/user/$_uid" \
        PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        runuser -u "$_user" -- "$@" >/dev/null 2>&1 || true
}

# If for some reason the canonical script is missing (corrupted
# install, manual file removal, or the debian purge staging copy
# failed), fall back to a minimal pkill so we don't leave processes
# pointing at a now-deleted binary.
if [ ! -x "$UNINSTALL_SH" ]; then
    for home in /home/*; do
        [ -d "$home" ] || continue
        user=$(basename "$home")
        [ -d "$home/.config/winpodx" ] || continue
        run_as_user "$user" "$home" pkill -f 'python.*winpodx'
        run_as_user "$user" "$home" pkill -f 'winpodx-app'
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
    run_as_user "$user" "$home" "$UNINSTALL_SH" --from-postrm --yes $PURGE_FLAG
}

for home in /home/*; do
    [ -d "$home" ] || continue
    user=$(basename "$home")
    cleanup_for_user "$user" "$home"
done

# Also handle root, in case the install was system-wide root state.
if [ -d /root/.config/winpodx ]; then
    # shellcheck disable=SC2086
    run_as_user root /root "$UNINSTALL_SH" --from-postrm --yes $PURGE_FLAG
fi

exit 0
