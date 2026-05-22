#!/bin/sh
# SPDX-License-Identifier: MIT
#
# Common post-remove hook for winpodx packaging (#255 PR 4).
#
# Invoked by:
#   debian/postrm        ($1 = remove|purge|upgrade|...)
#   rpm %postun          ($1 = number, 0 = removed, >=1 = upgrade)
#   aur winpodx.install  (post_remove function)
#
# Two-stage behaviour, matching apt remove vs apt purge semantics:
#
#   Stage 1 (always on actual remove, not upgrade):
#     * pkill tray / GUI / helper processes for every user with
#       ~/.config/winpodx/ so the user isn't left with stale processes
#       pointing at the now-deleted binary.
#     * stop reverse-open listener daemon (winpodx host-open
#       stop-listener) so the PID file isn't left dangling.
#
#   Stage 2 (debian purge only -- rpm / aur don't have a purge concept):
#     * run 'winpodx uninstall --purge --yes --no-package-prompt' as
#       each user with a winpodx install. The --no-package-prompt
#       suppresses the sudo-package-remove prompt because the package
#       is already being removed by the time this hook fires.
#
# Argv 1: "remove" | "purge" | "upgrade" | "0" | "1" | ...
#         (callers pass through whatever their package system gave
#          them; this script normalises.)
#
# We deliberately do NOT touch the container disk / podman volume in
# Stage 1 -- container data is user-state and apt remove's convention
# is to keep it. Stage 2 (purge) does, via winpodx uninstall --purge.

set -e

MODE="${1:-remove}"

# Normalise the rpm / debian / aur "what mode?" args into a single
# vocabulary: "upgrade" (skip), "purge" (full wipe), or "remove"
# (stage 1 only).
case "$MODE" in
    upgrade|1|2|3|4|5|6|7|8|9)
        # rpm passes a count: 0 = remove, >=1 = upgrade. debian passes
        # the literal word.
        exit 0
        ;;
    purge)
        STAGE2_PURGE=1
        ;;
    *)
        STAGE2_PURGE=0
        ;;
esac

# Iterate users with a winpodx config dir. We deliberately ignore
# /root and other non-/home users -- winpodx is a user-space tool and
# rootless podman state lives in $HOME.
for home in /home/*; do
    [ -d "$home" ] || continue
    user=$(basename "$home")
    [ -d "$home/.config/winpodx" ] || continue

    # Stage 1: kill processes + stop listener. Always best-effort --
    # postrm must not fail the package removal.
    runuser -u "$user" -- pkill -f 'python.*winpodx' >/dev/null 2>&1 || true
    runuser -u "$user" -- pkill -f 'winpodx-app' >/dev/null 2>&1 || true

    # The winpodx binary path varies by install regime. The system
    # package install put it at /usr/bin/winpodx (still in $PATH for
    # the runuser-spawned shell). Curl installs put it at
    # ~/.local/bin/winpodx -- try that as fallback. By the time this
    # hook runs the /usr/bin/winpodx is already gone (rpm scriptlets
    # run after file removal; debian postrm same), so the curl path
    # is actually the only one likely to work.
    user_winpodx="$home/.local/bin/winpodx"
    if [ -x "$user_winpodx" ]; then
        runuser -u "$user" -- "$user_winpodx" host-open stop-listener >/dev/null 2>&1 || true
    fi

    # Stage 2: full cleanup (debian purge only).
    if [ "$STAGE2_PURGE" = "1" ] && [ -x "$user_winpodx" ]; then
        runuser -u "$user" -- "$user_winpodx" uninstall --purge --yes --no-package-prompt \
            >/dev/null 2>&1 || true
    fi
done

exit 0
