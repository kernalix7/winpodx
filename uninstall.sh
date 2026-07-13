#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

# Suppress auto-spawn of the system tray during uninstall. install.sh
# uses a marker file for this; uninstall.sh uses an env var instead
# because the marker would block tray spawn far longer than we want
# (uninstall is expected to be short, and leaving a stale marker would
# leak into the next install / GUI session). Every ``winpodx`` CLI call
# this script makes (host-open stop-listener, unregister-guest, etc.)
# inherits the env, so ``maybe_spawn_tray`` short-circuits cleanly --
# without this the section-0a pkill below was promptly undone by the
# next CLI subcommand auto-spawning a fresh tray.
export WINPODX_NO_TRAY_SPAWN=1

###############################################################################
# winpodx uninstaller -- single source of truth for every install channel.
#
# Usage:
#   ./uninstall.sh                # Interactive: asks before each step, keeps container + config
#   ./uninstall.sh --yes          # Non-interactive: auto-yes, keeps container + config
#   ./uninstall.sh --purge        # Non-interactive: full wipe (container, volumes, config)
#   ./uninstall.sh --purge --yes  # Same as --purge (purge implies --yes)
#
# One-liner (curl | bash):
#   curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --yes
#   curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge
#
#   --yes or --purge is required when piping -- interactive prompts cannot
#   read from a terminal while bash is consuming stdin from curl.
#
# Internal flag (set by package post-remove hooks, not for end users):
#   --from-postrm   Re-entered from a deb/rpm/aur postrm hook. Skips the
#                   install-source detect step (avoids re-exec'ing the
#                   package manager that's already removing us) and skips
#                   reverse-open daemon calls (the winpodx binary may
#                   already be gone at this point).
#
# Env overrides (for non-default deployments; rarely needed):
#   WINPODX_CONTAINER_NAME   default: winpodx-windows
#   WINPODX_STORAGE_PATH     default: ~/.local/share/winpodx/storage
#   WINPODX_BACKEND          default: auto-detect (podman > docker)
###############################################################################

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[WinPodX]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

AUTO=false
PURGE=false
FROM_POSTRM=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|--confirm) AUTO=true; shift ;;
        --purge)         PURGE=true; AUTO=true; shift ;;
        --from-postrm)   FROM_POSTRM=true; AUTO=true; shift ;;
        -h|--help)       sed -n '/^# Usage/,/^###/p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown flag: $1" >&2
           echo "Usage: $0 [--yes] [--purge] [--from-postrm]" >&2
           exit 1 ;;
    esac
done

# Env-driven config overrides. Defaults match Config()'s factory defaults
# in src/winpodx/core/config.py so the common case Just Works without
# parsing the user's winpodx.toml from bash.
CONTAINER_NAME="${WINPODX_CONTAINER_NAME:-winpodx-windows}"
STORAGE_PATH="${WINPODX_STORAGE_PATH:-$HOME/.local/share/winpodx/storage}"
BACKEND_OVERRIDE="${WINPODX_BACKEND:-}"

ask() {
    if [[ "$AUTO" == true ]]; then return 0; fi
    echo -n "  $1 (y/N): "
    read -r answer
    [[ "$answer" =~ ^[Yy] ]]
}

# Locate the winpodx binary across all install topologies. Used by
# section 0b (reverse-open teardown). Empty if not installed at all or
# if the binary was removed by the package manager before postrm fired.
find_winpodx_bin() {
    local cand
    for cand in "$HOME/.local/bin/winpodx" "/usr/bin/winpodx" "/usr/local/bin/winpodx"; do
        if [[ -x "$cand" ]]; then echo "$cand"; return 0; fi
    done
    command -v winpodx 2>/dev/null || true
}

# Detect which package manager owns the winpodx binary, if any. Echoes
# one of:
#   apt|<pkg-name>|<sudo-removal-command>
#   dnf|<pkg-name>|<sudo-removal-command>
#   zypper|<pkg-name>|<sudo-removal-command>
#   pacman|<pkg-name>|<sudo-removal-command>
#   curl||                              (curl-installed bundle present)
#   unknown||                           (pip / source / unknown)
detect_install_source() {
    local bin
    bin="$(find_winpodx_bin)"

    if [[ -n "$bin" ]]; then
        local resolved
        resolved="$(readlink -f "$bin" 2>/dev/null || echo "$bin")"

        if command -v dpkg >/dev/null 2>&1; then
            local pkg
            pkg="$(dpkg -S "$resolved" 2>/dev/null | head -n1 | cut -d: -f1 || true)"
            if [[ -n "$pkg" && "$pkg" != *"no path found"* ]]; then
                echo "apt|$pkg|sudo apt-get remove $pkg"
                return 0
            fi
        fi

        if command -v rpm >/dev/null 2>&1; then
            local pkg
            pkg="$(rpm -qf --queryformat '%{NAME}' "$resolved" 2>/dev/null || true)"
            if [[ -n "$pkg" && "$pkg" != *"not owned"* && "$pkg" != *"file "* ]]; then
                local mgr="dnf"
                if grep -qiE '^ID=.*(opensuse|suse|sles)' /etc/os-release 2>/dev/null; then
                    mgr="zypper"
                fi
                echo "$mgr|$pkg|sudo $mgr remove $pkg"
                return 0
            fi
        fi

        if command -v pacman >/dev/null 2>&1; then
            local pkg
            pkg="$(pacman -Qo "$resolved" 2>/dev/null | awk '{print $(NF-1)}' || true)"
            if [[ -n "$pkg" && "$pkg" != "winpodx" && "$pkg" != *"No package"* ]]; then
                # awk strip can drop the package name on some pacman outputs;
                # fall back to the literal package name if our parse is wrong.
                if ! pacman -Q "$pkg" >/dev/null 2>&1; then pkg="winpodx"; fi
                echo "pacman|$pkg|sudo pacman -Rns $pkg"
                return 0
            elif [[ "$pkg" == "winpodx" ]]; then
                echo "pacman|winpodx|sudo pacman -Rns winpodx"
                return 0
            fi
        fi
    fi

    # Not owned by any package manager. Check for the curl-install
    # bundle directory as a positive signal -- the symlink alone is not
    # enough (we may have already removed the symlink in a prior run).
    if [[ -d "$HOME/.local/bin/winpodx-app" ]]; then
        echo "curl||"
        return 0
    fi

    # pip / source install detection: resolved binary lands in a
    # site-packages tree (system or venv) or inside a /src/winpodx/
    # dev checkout. Heuristic only -- no package manager owns the
    # file, and we don't know the venv path to drive `pip uninstall`
    # for the user, so we just print the canonical command as a hint.
    if [[ -n "$bin" ]]; then
        local resolved
        resolved="$(readlink -f "$bin" 2>/dev/null || echo "$bin")"
        if [[ "$resolved" == *site-packages* ]] || [[ "$resolved" == */src/winpodx/* ]]; then
            echo "pip||pip uninstall winpodx"
            return 0
        fi
    fi

    echo "unknown||"
}

echo ""
echo "=========================================="
echo " WinPodX uninstaller"
echo "=========================================="
if [[ "$PURGE" == true ]]; then
    echo " Mode: FULL PURGE (container + volumes + storage + config + files)"
else
    echo " Mode: WinPodX files only (container + volumes + config kept)"
fi
[[ "$FROM_POSTRM" == true ]] && echo " (re-entered from package post-remove hook)"
echo ""

REMOVED=0

# Detect runtime (podman preferred, fallback to docker, or env override).
RUNTIME=""
if [[ -n "$BACKEND_OVERRIDE" ]] && command -v "$BACKEND_OVERRIDE" >/dev/null 2>&1; then
    RUNTIME="$BACKEND_OVERRIDE"
elif command -v podman >/dev/null 2>&1; then
    RUNTIME="podman"
elif command -v docker >/dev/null 2>&1; then
    RUNTIME="docker"
fi

# --- Install-source detect + package-manager-first ordering ---
# If installed via a package manager, the *correct* sequence is:
#   1. sudo apt remove winpodx  (or dnf/zypper/pacman equivalent)
#   2. post-remove hook fires
#   3. hook calls /usr/share/winpodx/uninstall.sh --from-postrm [--purge] --yes
#   4. user-side cleanup runs against still-present $HOME state
# Running the user cleanup first would leave the dpkg/rpm db inconsistent
# (db says files exist, disk says otherwise) and postrm may fail to
# locate the binary it tries to invoke.
#
# When --from-postrm is set we skip this block (we ARE the postrm
# re-entry) and proceed directly to user-side cleanup.
if [[ "$FROM_POSTRM" != true ]]; then
    SRC="$(detect_install_source)"
    SRC_KIND="${SRC%%|*}"
    case "$SRC_KIND" in
        apt|dnf|zypper|pacman)
            SRC_PKG="$(echo "$SRC" | cut -d'|' -f2)"
            SRC_CMD="$(echo "$SRC" | cut -d'|' -f3)"
            echo " Install source: $SRC_KIND ($SRC_PKG)"
            echo ""
            echo " Recommended order:"
            echo "   1. Remove the package via the system package manager."
            echo "   2. Its post-remove hook will re-run this script for user-side cleanup."
            echo ""
            if ask "Run now: $SRC_CMD ?"; then
                # Forward purge intent so postrm-common.sh re-invokes
                # uninstall.sh with --purge.
                if [[ "$PURGE" == true ]]; then
                    export WINPODX_PURGE_REQUESTED=1
                fi
                log "Handing off to package manager: $SRC_CMD"
                # shellcheck disable=SC2086
                exec $SRC_CMD
            else
                warn "Package not removed. Continuing with user-side cleanup only."
                warn "  (You can run '$SRC_CMD' later to remove the package itself.)"
                echo ""
            fi
            ;;
        pip)
            SRC_CMD="$(echo "$SRC" | cut -d'|' -f3)"
            echo " Install source: pip / source (site-packages or dev checkout)"
            echo ""
            echo " This script will clean up user-side state (containers, configs,"
            echo " desktop entries, launchers). To remove the winpodx Python package"
            echo " itself, run after this script finishes:"
            echo "   $SRC_CMD"
            echo ""
            ;;
        curl|unknown)
            : # No package manager involved; proceed.
            ;;
    esac
fi

# --- 0a. Stop running winpodx processes (tray + GUI + any helper) ---
# Both GUI and tray hold open file handles into
# ~/.local/bin/winpodx-app/ and the runtime / config directories we're
# about to remove. The tray additionally owns the flock under
# $XDG_RUNTIME_DIR/winpodx/tray.lock and drives UNRESPONSIVE recovery
# notifications -- leaving it alive across the uninstall surfaces
# recovery-attempt notifications fire against a now-gone container.
#
# Uninstall is intentional + user-initiated, so kill every winpodx
# Python process broadly. Three launcher cmdline shapes exist in the
# wild:
#   (a) install.sh wrapper:   python -m winpodx tray
#   (b) pip / venv entry pt:  python /.../venv/bin/winpodx tray
#   (c) source path-style:    python /.../src/winpodx/__main__.py tray
# Common substring: "python" ... "winpodx" ... <subcommand>. The
# pattern below matches all three. False positives (e.g. pytest
# tests/test_winpodx_*.py) are acceptable -- uninstall is explicit
# and ``--purge`` is the expected mode, so over-killing is the
# safer failure shape than leaving FDs open into the dir we're
# about to rm -rf.
#
# Listing the matched pids before the kill makes the action
# observable so a surprise hit is at least obvious in the output.
WINPODX_PROCS=$(pgrep -fa 'python.*winpodx' 2>/dev/null || true)
if [[ -n "$WINPODX_PROCS" ]]; then
    log "Stopping WinPodX processes:"
    while IFS= read -r line; do
        log "  $line"
    done <<<"$WINPODX_PROCS"
    pkill -f 'python.*winpodx' 2>/dev/null || true
    REMOVED=$((REMOVED + 1))
fi
# Brief grace so the killed processes release their file handles
# before later steps rm -rf their install directory.
sleep 1

# --- 0b. Reverse-open teardown (BEFORE container removal) ---
# Stops the host-side listener daemon so the runtime/winpodx/ cleanup
# below doesn't leave an orphan process when the pid file is deleted.
#
# Guest-side registry scrub (unregister-apps.ps1) only runs in non-
# purge mode -- when --purge is set, the container is destroyed in
# step 1 below and the HKCU entries vanish with it. Calling the agent
# in that case is wasted latency (and can hang if the agent isn't
# reachable, slowing down the uninstall path with no payoff).
#
# Skip this entire block when --from-postrm: the package manager has
# already removed the winpodx binary, and the listener process was
# already killed by the pkill above.
if [[ "$FROM_POSTRM" != true ]]; then
    WINPODX_BIN="$(find_winpodx_bin)"
    if [[ -n "$WINPODX_BIN" ]]; then
        if "$WINPODX_BIN" host-open daemon-status --json 2>/dev/null | grep -q '"running": true'; then
            log "Stopping host-side reverse-open listener..."
            "$WINPODX_BIN" host-open stop-listener 2>/dev/null || true
            REMOVED=$((REMOVED + 1))
        fi
        # Skip the guest scrub on --purge: container teardown below
        # destroys the registry anyway.
        if [[ "$PURGE" != true ]] && [[ -n "$RUNTIME" ]] && \
           $RUNTIME ps --format "{{.Names}}" 2>/dev/null | grep -q "$CONTAINER_NAME"; then
            log "Scrubbing reverse-open registry entries on the guest..."
            "$WINPODX_BIN" host-open unregister-guest 2>/dev/null | sed 's/^/  /' || \
                warn "  guest scrub skipped (agent unreachable)"
        fi
    fi
fi

# --- 1. Container (always) + Volumes + Storage bind-mount (purge only) ---
if [[ -n "$RUNTIME" ]]; then
    if $RUNTIME ps -a --format "{{.Names}}" 2>/dev/null | grep -q "$CONTAINER_NAME"; then
        if [[ "$PURGE" == true ]]; then
            log "Stopping and removing container ($RUNTIME)..."
            $RUNTIME stop "$CONTAINER_NAME" 2>/dev/null || true
            $RUNTIME rm   "$CONTAINER_NAME" 2>/dev/null || true
        else
            log "Stopping container (keeping disk for re-install): $CONTAINER_NAME"
            $RUNTIME stop "$CONTAINER_NAME" 2>/dev/null || true
        fi
        REMOVED=$((REMOVED + 1))
    fi

    if [[ "$PURGE" == true ]]; then
        for vol in $($RUNTIME volume ls --format "{{.Name}}" 2>/dev/null | grep '^winpodx' || true); do
            log "Removing volume: $vol"
            $RUNTIME volume rm "$vol" 2>/dev/null || true
            REMOVED=$((REMOVED + 1))
        done
    fi
else
    warn "Neither podman nor docker found; skipping container cleanup"
fi

# Storage bind-mount wipe (purge only). The Windows disk image and any
# guest-side scratch live under this directory when dockur's STORAGE is
# bind-mounted from the host (the default for most install topologies).
if [[ "$PURGE" == true ]] && [[ -d "$STORAGE_PATH" ]]; then
    log "Wiping storage bind-mount contents: $STORAGE_PATH"
    rm -rf "${STORAGE_PATH:?}"/* "${STORAGE_PATH:?}"/.[!.]* 2>/dev/null || true
    rmdir "$STORAGE_PATH" 2>/dev/null || true
    REMOVED=$((REMOVED + 1))
fi

# --- 2. Desktop entries ---
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
# Remove winpodx GUI launcher
if [[ -f "$DESKTOP_DIR/winpodx.desktop" ]]; then
    rm -f "$DESKTOP_DIR/winpodx.desktop"
    log "Removed WinPodX GUI launcher"
    REMOVED=$((REMOVED + 1))
fi
# Remove app desktop entries
DESKTOP_COUNT=$(find "$DESKTOP_DIR" -maxdepth 1 -name "winpodx-*.desktop" 2>/dev/null | wc -l)
if [[ "$DESKTOP_COUNT" -gt 0 ]]; then
    if ask "Remove $DESKTOP_COUNT app desktop entries?"; then
        rm -f "$DESKTOP_DIR"/winpodx-*.desktop
        log "Removed $DESKTOP_COUNT app desktop entries"
        REMOVED=$((REMOVED + DESKTOP_COUNT))
    fi
fi
# Remove the consolidated "winpodx" menu folder definitions (desktop/menu.py
# writes both on every app install). Without this an empty "WinPodX (Windows
# Apps)" submenu lingers in KDE/XFCE/Cinnamon/MATE/LXQt after uninstall, since
# the .menu fragment auto-merges into applications.menu. Always remove -- both
# files are winpodx-specific and useless without the app entries.
MENU_DIRFILE="${XDG_DATA_HOME:-$HOME/.local/share}/desktop-directories/winpodx-windows.directory"
MENU_FRAGMENT="${XDG_CONFIG_HOME:-$HOME/.config}/menus/applications-merged/winpodx.menu"
for mf in "$MENU_DIRFILE" "$MENU_FRAGMENT"; do
    if [[ -e "$mf" ]]; then
        rm -f "$mf"
        log "Removed $mf"
        REMOVED=$((REMOVED + 1))
    fi
done
# Update desktop database
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

# --- 3. Icons ---
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
if [[ -d "$ICON_DIR" ]]; then
    ICON_COUNT=$(find "$ICON_DIR" \( -name "winpodx-*" -o -name "winpodx.svg" \) 2>/dev/null | wc -l)
    if [[ "$ICON_COUNT" -gt 0 ]]; then
        if ask "Remove $ICON_COUNT icons?"; then
            find "$ICON_DIR" \( -name "winpodx-*" -o -name "winpodx.svg" \) -delete 2>/dev/null || true
            log "Removed $ICON_COUNT icons"
            REMOVED=$((REMOVED + ICON_COUNT))
        fi
    fi
    # Refresh icon cache
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$ICON_DIR" 2>/dev/null || true
    fi
    # Rebuild KDE Plasma sycoca cache
    kbuildsycoca6 --noincremental 2>/dev/null || kbuildsycoca5 --noincremental 2>/dev/null || true
fi

# --- 4. MIME associations ---
MIMEINFO="${XDG_DATA_HOME:-$HOME/.local/share}/applications/mimeinfo.cache"
if [[ -f "$MIMEINFO" ]] && grep -q "winpodx" "$MIMEINFO" 2>/dev/null; then
    sed -i '/winpodx/d' "$MIMEINFO" 2>/dev/null || true
    log "Cleaned WinPodX MIME associations"
fi
# mimeapps.list keeps [Default Applications] / [Added Associations] entries
# pointing at winpodx-*.desktop (written by `winpodx app install --mime` and
# `winpodx doctor --fix` via xdg-mime default). Left behind they dangle at
# removed handlers. Drop any line that references a winpodx handler.
MIMEAPPS="${XDG_CONFIG_HOME:-$HOME/.config}/mimeapps.list"
if [[ -f "$MIMEAPPS" ]] && grep -q "winpodx-" "$MIMEAPPS" 2>/dev/null; then
    sed -i '/winpodx-/d' "$MIMEAPPS" 2>/dev/null || true
    log "Cleaned WinPodX entries from mimeapps.list"
fi

# --- 5. App definitions ---
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/winpodx"
if [[ -d "$DATA_DIR" ]]; then
    if ask "Remove app definitions ($DATA_DIR)?"; then
        # DATA-LOSS GUARD (#716): the default VM storage lives *under* DATA_DIR
        # (~/.local/share/winpodx/storage/data.img). A non-purge uninstall is
        # documented to KEEP the Windows VM data, so it must never rm -rf the
        # parent DATA_DIR out from under the storage subtree. When STORAGE_PATH
        # is DATA_DIR itself or nested beneath it and we are NOT purging,
        # preserve the top-level storage component and remove only the rest.
        _canon() { readlink -f "$1" 2>/dev/null || echo "${1%/}"; }
        _dd="$(_canon "$DATA_DIR")"
        _sp="$(_canon "$STORAGE_PATH")"
        if [[ "$PURGE" != true ]] && { [[ "$_sp" == "$_dd" ]] || [[ "$_sp" == "$_dd"/* ]]; }; then
            # First path component of STORAGE_PATH relative to DATA_DIR (e.g.
            # "storage"). Keep that whole top-level entry; delete DATA_DIR's
            # other top-level children (apps/, run/, icons cache, …).
            _rel="${_sp#"$_dd"/}"
            _keep="${_rel%%/*}"
            if [[ "$_sp" == "$_dd" ]] || [[ -z "$_keep" ]]; then
                # STORAGE_PATH == DATA_DIR: nothing safe to delete recursively.
                log "Kept $DATA_DIR (holds the Windows VM storage; use --purge to wipe)"
            else
                find "$DATA_DIR" -mindepth 1 -maxdepth 1 ! -name "$_keep" \
                    -exec rm -rf {} + 2>/dev/null || true
                log "Removed $DATA_DIR contents (preserved VM storage: $STORAGE_PATH)"
                REMOVED=$((REMOVED + 1))
            fi
        else
            rm -rf "$DATA_DIR"
            log "Removed $DATA_DIR"
            REMOVED=$((REMOVED + 1))
        fi
    fi
fi

# --- 6. Runtime PID files ---
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/winpodx"
if [[ -d "$RUNTIME_DIR" ]]; then
    rm -rf "$RUNTIME_DIR"
    log "Removed runtime files"
    REMOVED=$((REMOVED + 1))
fi

# --- 6b. Autostart entry (XDG ~/.config/autostart/winpodx-tray.desktop) ---
# The Settings-page "Launch winpodx tray at login" checkbox writes this
# file via the XDG autostart spec; leaving it around after uninstall
# means the user gets a "winpodx tray" command-not-found at next login.
# Always remove regardless of purge mode -- the .desktop is winpodx-
# specific and useless without the binary.
AUTOSTART_DESKTOP="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/winpodx-tray.desktop"
if [[ -e "$AUTOSTART_DESKTOP" ]]; then
    rm -f "$AUTOSTART_DESKTOP"
    log "Removed $AUTOSTART_DESKTOP"
    REMOVED=$((REMOVED + 1))
fi

# --- 7. Config (purge only) ---
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/winpodx"
if [[ -d "$CONFIG_DIR" ]]; then
    if [[ "$PURGE" == true ]]; then
        rm -rf "$CONFIG_DIR"
        log "Removed $CONFIG_DIR"
        REMOVED=$((REMOVED + 1))
    else
        warn "Config preserved at $CONFIG_DIR (use --purge to remove)"
    fi
fi

# --- 7b. KVM module-load drop-in (purge only) ---
# install.sh (#541) writes /etc/modules-load.d/winpodx-kvm.conf via sudo when
# /dev/kvm is missing but the CPU supports virtualization. It's a winpodx-
# created root-owned file, so a full --purge should remove it. Best-effort:
# needs sudo and only loads a stock kernel module, so a non-purge run leaves it.
KVM_MODCONF="/etc/modules-load.d/winpodx-kvm.conf"
if [[ "$PURGE" == true && -e "$KVM_MODCONF" ]]; then
    if sudo rm -f "$KVM_MODCONF" 2>/dev/null; then
        log "Removed $KVM_MODCONF"
        REMOVED=$((REMOVED + 1))
    else
        warn "Could not remove $KVM_MODCONF (needs sudo); remove it manually if desired"
    fi
fi

# --- 8. Curl-install bundle dir + launcher symlinks (~/.local/bin) ---
# These exist only when installed via curl install.sh. On package
# installs, $HOME/.local/bin/winpodx-app/ doesn't exist and the
# symlinks point at /usr/bin (or wherever the package manager put
# them) -- those are owned by the package and will be removed by it,
# not by us. The conditionals below make this a no-op on package
# installs without special-casing.
INSTALL_DIR="$HOME/.local/bin/winpodx-app"
if [[ -d "$INSTALL_DIR" ]]; then
    if ask "Remove curl-install bundle ($INSTALL_DIR)?"; then
        rm -rf "$INSTALL_DIR"
        log "Removed $INSTALL_DIR"
        REMOVED=$((REMOVED + 1))
    fi
fi
for f in "$HOME/.local/bin/winpodx-run" "$HOME/.local/bin/winpodx"; do
    # Only touch symlinks or files clearly owned by curl install (the
    # winpodx-run wrapper script). Don't break a system-wide install
    # by removing /usr/bin/winpodx -- those paths aren't in this list.
    if [[ -L "$f" || -e "$f" ]]; then
        rm -f "$f"
        log "Removed $f"
        REMOVED=$((REMOVED + 1))
    fi
done

# --- Final tray / GUI sweep ---
# Belt-and-braces against tray respawn paths the section-0a kill +
# WINPODX_NO_TRAY_SPAWN env didn't cover. Examples:
#   - User had a GUI window open in another terminal that spawned
#     a tray via maybe_spawn_tray() *before* uninstall.sh started.
#   - A KDE/GNOME autostart-triggered launch raced the env export.
#   - dbus-activated launch path bypasses cli/main.py's spawn check.
# Section 0a only sweeps what's alive at the start of the script;
# this final pass catches anything that came up during the 10-30 s
# uninstall window. Quiet by default -- absence of victims is
# normal and noisy logging would distract from the summary below.
pkill -f 'python.*winpodx' 2>/dev/null || true
pkill -f 'winpodx-app' 2>/dev/null || true

# --- Summary ---
echo ""
if [[ "$PURGE" != true ]]; then
    if [[ -n "$RUNTIME" ]] && $RUNTIME ps -a --format "{{.Names}}" 2>/dev/null | grep -q "$CONTAINER_NAME"; then
        echo " Container '$CONTAINER_NAME' was kept (re-install will reuse the disk)."
        echo " To remove it too: $0 --purge"
        echo ""
    fi
fi
if [[ "$FROM_POSTRM" != true ]]; then
    echo " NOT removed: system packages (podman, freerdp, python3)"
    echo ""
fi
log "Uninstall complete ($REMOVED items removed)"
