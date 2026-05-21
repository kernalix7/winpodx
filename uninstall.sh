#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

###############################################################################
# winpodx uninstaller
#
# Usage:
#   ./uninstall.sh              # Interactive: asks before each step, keeps container
#   ./uninstall.sh --confirm    # Auto: removes winpodx files, keeps container
#   ./uninstall.sh --purge      # Full: removes everything including container + data
#
# One-liner (curl | bash):
#   curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --confirm
#   curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge
#
#   --confirm or --purge is required when piping — the interactive prompts
#   cannot read from a terminal while bash is consuming stdin from curl.
###############################################################################

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[winpodx]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }

AUTO=false
PURGE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --confirm) AUTO=true; shift ;;
        --purge)   PURGE=true; AUTO=true; shift ;;
        *) echo "Usage: $0 [--confirm] [--purge]"; exit 1 ;;
    esac
done

ask() {
    if [[ "$AUTO" == true ]]; then return 0; fi
    echo -n "  $1 (y/N): "
    read -r answer
    [[ "$answer" =~ ^[Yy] ]]
}

echo ""
echo "=========================================="
echo " winpodx uninstaller"
echo "=========================================="
if [[ "$PURGE" == true ]]; then
    echo " Mode: FULL PURGE (container + data + config + files)"
else
    echo " Mode: winpodx files only (container and data kept)"
fi
echo ""

REMOVED=0

# Detect runtime (podman preferred, fallback to docker) — needed by
# both the reverse-open teardown and the container removal step.
RUNTIME=""
if command -v podman &>/dev/null; then
    RUNTIME="podman"
elif command -v docker &>/dev/null; then
    RUNTIME="docker"
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
# Python process broadly via the launcher cmdline shape ``python ... -m
# winpodx ...`` (covers gui / tray / app / host-open / setup -- all
# subcommands). The earlier attempt at narrow anchored regexes missed
# wrapper-script invocations whose cmdline didn't include the ``app``
# token, so live tray + GUI survived the uninstall and held the
# install dir open. Listing the pids first makes the kill observable.
WINPODX_PROCS=$(pgrep -fa 'python.*-m[[:space:]]+winpodx' 2>/dev/null || true)
if [[ -n "$WINPODX_PROCS" ]]; then
    log "Stopping winpodx processes:"
    while IFS= read -r line; do
        log "  $line"
    done <<<"$WINPODX_PROCS"
    pkill -f 'python.*-m[[:space:]]+winpodx' 2>/dev/null || true
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
# purge mode — when --purge is set, the container is destroyed in
# step 1 below and the HKCU entries vanish with it. Calling the agent
# in that case is wasted latency (and can hang if the agent isn't
# reachable, slowing down the uninstall path with no payoff).
if [[ -x "$HOME/.local/bin/winpodx" ]]; then
    if "$HOME/.local/bin/winpodx" host-open daemon-status --json 2>/dev/null | grep -q '"running": true'; then
        log "Stopping host-side reverse-open listener..."
        "$HOME/.local/bin/winpodx" host-open stop-listener 2>/dev/null || true
        REMOVED=$((REMOVED + 1))
    fi
    # Skip the guest scrub on --purge: container teardown below
    # destroys the registry anyway.
    if [[ "$PURGE" != true ]] && [[ -n "$RUNTIME" ]] && \
       $RUNTIME ps --format "{{.Names}}" 2>/dev/null | grep -q "winpodx-windows"; then
        log "Scrubbing reverse-open registry entries on the guest..."
        "$HOME/.local/bin/winpodx" host-open unregister-guest 2>/dev/null | sed 's/^/  /' || \
            warn "  guest scrub skipped (agent unreachable)"
    fi
fi

# --- 1. Container (always) + Volume (purge only) ---

if [[ -n "$RUNTIME" ]]; then
    if $RUNTIME ps -a --format "{{.Names}}" 2>/dev/null | grep -q "winpodx-windows"; then
        log "Stopping and removing container ($RUNTIME)..."
        $RUNTIME stop winpodx-windows 2>/dev/null || true
        $RUNTIME rm winpodx-windows 2>/dev/null || true
        REMOVED=$((REMOVED + 1))
    fi

    if [[ "$PURGE" == true ]]; then
        for vol in $($RUNTIME volume ls --format "{{.Name}}" 2>/dev/null | grep winpodx); do
            log "Removing volume: $vol"
            $RUNTIME volume rm "$vol" 2>/dev/null || true
            REMOVED=$((REMOVED + 1))
        done
    fi
else
    warn "Neither podman nor docker found; skipping container cleanup"
fi

# --- 2. Desktop entries ---
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
# Remove winpodx GUI launcher
if [[ -f "$DESKTOP_DIR/winpodx.desktop" ]]; then
    rm -f "$DESKTOP_DIR/winpodx.desktop"
    log "Removed winpodx GUI launcher"
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
# Update desktop database
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

# --- 3. Icons ---
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
if [[ -d "$ICON_DIR" ]]; then
    ICON_COUNT=$(find "$ICON_DIR" -name "winpodx-*" -o -name "winpodx.svg" 2>/dev/null | wc -l)
    if [[ "$ICON_COUNT" -gt 0 ]]; then
        if ask "Remove $ICON_COUNT icons?"; then
            find "$ICON_DIR" \( -name "winpodx-*" -o -name "winpodx.svg" \) -delete
            log "Removed $ICON_COUNT icons"
            REMOVED=$((REMOVED + ICON_COUNT))
        fi
    fi
    # Refresh icon cache
    if command -v gtk-update-icon-cache &>/dev/null; then
        gtk-update-icon-cache -f -t "$ICON_DIR" 2>/dev/null || true
    fi
    # Rebuild KDE Plasma sycoca cache
    kbuildsycoca6 --noincremental 2>/dev/null || kbuildsycoca5 --noincremental 2>/dev/null || true
fi

# --- 4. MIME associations ---
MIME_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
MIMEINFO="$MIME_DIR/mimeinfo.cache"
if [[ -f "$MIMEINFO" ]] && grep -q "winpodx" "$MIMEINFO" 2>/dev/null; then
    sed -i '/winpodx/d' "$MIMEINFO" 2>/dev/null || true
    log "Cleaned winpodx MIME associations"
fi

# --- 5. App definitions ---
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/winpodx"
if [[ -d "$DATA_DIR" ]]; then
    if ask "Remove app definitions ($DATA_DIR)?"; then
        rm -rf "$DATA_DIR"
        log "Removed $DATA_DIR"
        REMOVED=$((REMOVED + 1))
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

# --- 7. Config ---
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/winpodx"
if [[ -d "$CONFIG_DIR" ]]; then
    if [[ "$PURGE" == true ]]; then
        rm -rf "$CONFIG_DIR"
        log "Removed $CONFIG_DIR"
        REMOVED=$((REMOVED + 1))
    elif ask "Remove config ($CONFIG_DIR)?"; then
        rm -rf "$CONFIG_DIR"
        log "Removed $CONFIG_DIR"
        REMOVED=$((REMOVED + 1))
    else
        warn "Config preserved at $CONFIG_DIR"
    fi
fi

# --- 8. Launcher scripts ---
for f in "$HOME/.local/bin/winpodx-run" "$HOME/.local/bin/winpodx"; do
    if [[ -e "$f" || -L "$f" ]]; then
        rm -f "$f"
        log "Removed $f"
        REMOVED=$((REMOVED + 1))
    fi
done

# --- 9. Installation directory ---
INSTALL_DIR="$HOME/.local/bin/winpodx-app"
if [[ -d "$INSTALL_DIR" ]]; then
    if ask "Remove winpodx installation ($INSTALL_DIR)?"; then
        rm -rf "$INSTALL_DIR"
        log "Removed $INSTALL_DIR"
        REMOVED=$((REMOVED + 1))
    fi
fi

# --- Summary ---
echo ""
if ! [[ "$PURGE" == true ]]; then
    if [[ -n "$RUNTIME" ]] && $RUNTIME ps -a --format "{{.Names}}" 2>/dev/null | grep -q "winpodx-windows"; then
        echo " Container 'winpodx-windows' was kept."
        echo " To remove it too: ./uninstall.sh --purge"
        echo ""
    fi
fi
echo " NOT removed: system packages (podman, freerdp, python3)"
echo ""
log "Uninstall complete ($REMOVED items removed)"
