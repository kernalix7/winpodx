#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

###############################################################################
# winpodx installer (v2)
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
#   or: ./install.sh [--main] [--ref TAG] [--source PATH] [--image-tar PATH]
#                    [--mode r|a|c|n] [--backend podman|docker|manual]
#                    [--no-gui] [--manual] [--skip-deps] [--win-version VER]
#                    [--win-iso PATH] [--storage-dir PATH] [--help]
#
# Installs winpodx to ~/.local/bin/winpodx-app/ and creates a launcher.
# Python always runs from a private venv under
#   ~/.local/bin/winpodx-app/.venv ; no system-python changes.
#
# Install mode (interactive prompt, or preselect with --mode / WINPODX_MODE):
#   --mode r           Recommended — winpodx's recommended stack (Podman backend
#                      + deps); installs missing system packages via the distro
#                      package manager (sudo). This is the historical behaviour.
#   --mode a           Automatic — reuse what's already installed; only install
#                      what's strictly missing; pick the backend from what's
#                      present (prefer an already-working docker/podman).
#   --mode c           Custom — choose backend (podman/docker) and
#                      whether to include the GUI, then install accordingly.
#   --mode n           No — cancel without changing anything.
#                      (env: WINPODX_MODE=<r|a|c|n>)
#                      Default: prompt when a terminal is reachable — including
#                      `curl ... | bash` (prompts read from /dev/tty) — and 'r'
#                      (Recommended) when fully non-interactive (CI / cron /
#                      stdin+/dev/tty both absent).
#
# Backend / GUI:
#   --backend BACKEND  podman | docker | manual. Passed through to
#                      `winpodx setup --backend <x>`. (env: WINPODX_BACKEND)
#                      (libvirt was dropped in 0.6.0 — dockur covers device
#                      passthrough now, #286.)
#   --verbose, -v      Stream raw container logs during the Windows-boot wait.
#                      Default collapses the ISO download to one clean progress
#                      line + hides UEFI boot noise. (env: WINPODX_VERBOSE=1)
#   --no-gui           Headless / CLI-only: skip installing PySide6 into the
#                      venv. Everything else still installs.
#                      (env: WINPODX_NO_GUI=1)
#
# Version selection (default: latest GitHub release):
#   --main             Install from git main HEAD (development, may be unstable).
#                      (env: WINPODX_REF=main)
#   --ref TAG          Install a specific tag/branch/commit.
#                      (env: WINPODX_REF=<ref>)
#
# Local-path options (for offline / air-gapped installs):
#   --source PATH      Copy winpodx from PATH instead of git clone.
#                      (env: WINPODX_SOURCE)
#   --image-tar PATH   Preload Windows container image from PATH via
#                      `podman load -i` (or `docker load -i`).
#                      (env: WINPODX_IMAGE_TAR)
#   --skip-deps        Skip the distro dependency install phase.
#                      Fails early if required tools aren't already present.
#                      (env: WINPODX_SKIP_DEPS=1)
#   --win-version VER  Windows edition for fresh installs
#                      (11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 | tiny10 |
#                       2025 | 2022 | 2019 | 2016 — see docs/ARCHITECTURE.md).
#                      (env: WINPODX_WIN_VERSION)
#   --win-iso PATH     Install from a local Windows ISO at PATH instead of
#                      downloading it from Microsoft. The ISO is staged into
#                      the storage dir as `custom.iso` (dockur's bring-your-own
#                      convention); reflink-copied where the filesystem supports
#                      it, so it costs no extra space on btrfs/xfs. Saves the
#                      ~5-8 GB download on every purge/reinstall cycle (#647).
#                      (env: WINPODX_WIN_ISO)
#   --storage-dir PATH On a FRESH install, put the Windows VM disk + ISO at PATH
#                      (e.g. a roomier partition) instead of
#                      ~/.local/share/winpodx/storage. The dir is created and
#                      gets the same prep as the default (chattr +C on btrfs,
#                      SSD emulation if applicable) (#646). To relocate an
#                      EXISTING install, use `winpodx setup --migrate-storage
#                      --migrate-storage-target PATH`. (env: WINPODX_STORAGE_DIR)
#   --manual           Install winpodx + create the venv only — skip
#                      'winpodx setup', 'winpodx pod wait-ready', app
#                      discovery, and reverse-open. Finish provisioning
#                      yourself via the first-run prompt on the next
#                      'winpodx' invocation. (env: WINPODX_MANUAL=1)
#   -h, --help         Print this help and exit
###############################################################################

INSTALL_DIR="$HOME/.local/bin/winpodx-app"
VENV_DIR="$INSTALL_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
LAUNCHER="$HOME/.local/bin/winpodx-run"
SYMLINK="$HOME/.local/bin/winpodx"
REPO_URL="https://github.com/kernalix7/winpodx.git"
REPO_API="https://api.github.com/repos/kernalix7/winpodx"
# #716-audit follow-up: single source of truth for winpodx's config dir, so
# a custom XDG_CONFIG_HOME is honoured everywhere (matches the install
# marker below and src/winpodx's own XDG handling) instead of some spots
# hardcoding "$HOME/.config".
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

# Local-path overrides (env or flag). Flags take precedence over env.
WINPODX_SOURCE="${WINPODX_SOURCE:-}"
WINPODX_IMAGE_TAR="${WINPODX_IMAGE_TAR:-}"
WINPODX_SKIP_DEPS="${WINPODX_SKIP_DEPS:-}"
# v0.2.2.2: explicit ref selection. Empty -> auto-detect latest release tag
# at install time. Set to "main" via --main flag for development builds.
WINPODX_REF="${WINPODX_REF:-}"
# v0.5.x: --win-version flag picks the dockur Windows edition for fresh
# installs (e.g. ltsc10, iot11, tiny11). Empty -> dockur default "11".
# Ignored when an existing winpodx.toml is present (setup skips
# re-configuration for upgrade flows). See #178.
WINPODX_WIN_VERSION="${WINPODX_WIN_VERSION:-}"
WINPODX_WIN_ISO="${WINPODX_WIN_ISO:-}"
WINPODX_STORAGE_DIR="${WINPODX_STORAGE_DIR:-}"
WINPODX_MANUAL="${WINPODX_MANUAL:-}"
# v2: new knobs.
WINPODX_NO_GUI="${WINPODX_NO_GUI:-}"
WINPODX_BACKEND="${WINPODX_BACKEND:-}"
WINPODX_MODE="${WINPODX_MODE:-}"
# Bypass the too-old-podman guard (#271): proceed with podman even when the
# probe sees major < 4, e.g. you upgraded podman out-of-band since.
WINPODX_ALLOW_OLD_PODMAN="${WINPODX_ALLOW_OLD_PODMAN:-}"
# v2: --verbose streams raw container logs during the Windows-boot wait;
# default collapses the ISO download to a clean progress line + hides UEFI noise.
WINPODX_VERBOSE="${WINPODX_VERBOSE:-}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[WinPodX]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

usage() {
    sed -n '4,93p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || cat <<'USAGE_EOF'
WinPodX installer — see install.sh header for full usage.

Flags:
  --mode r|a|c|n      Install mode (Recommended / Automatic / Custom / No)
  --backend BACKEND   podman | docker | manual
  --no-gui            Headless install — skip PySide6 in the venv
  --verbose, -v       Stream raw container logs during the Windows-boot wait
                      (default: clean ISO-download progress + hidden UEFI noise)
  --main              Install from git main HEAD (development)
  --ref TAG           Install a specific tag/branch/commit
  --source PATH       Copy from local repo instead of git clone
  --image-tar PATH    Load container image from local tar
  --skip-deps         Skip distro dependency install
  --win-version VER   Windows edition for fresh installs
  --win-iso PATH      Install from a local Windows ISO instead of downloading
  --storage-dir PATH  Put the VM disk + ISO at PATH (fresh install; roomier partition)
  --manual            Install binary + venv only — skip provisioning
  --allow-old-podman  Proceed with podman even if its major version is < 4
                      (bypass the too-old-podman guard; #271)
  -h, --help          Print this help and exit
USAGE_EOF
}

# --- Parse flags (must precede any work) ---
while [ $# -gt 0 ]; do
    case "$1" in
        --main|--dev)
            WINPODX_REF="main"
            shift
            ;;
        --ref)
            WINPODX_REF="${2:-}"
            shift 2
            ;;
        --source)
            WINPODX_SOURCE="${2:-}"
            shift 2
            ;;
        --image-tar)
            WINPODX_IMAGE_TAR="${2:-}"
            shift 2
            ;;
        --skip-deps)
            WINPODX_SKIP_DEPS=1
            shift
            ;;
        --win-version)
            WINPODX_WIN_VERSION="${2:-}"
            if [ -z "$WINPODX_WIN_VERSION" ]; then
                err "--win-version requires a value (e.g. ltsc10, iot11, tiny11)"
                exit 1
            fi
            shift 2
            ;;
        --win-iso)
            WINPODX_WIN_ISO="${2:-}"
            if [ -z "$WINPODX_WIN_ISO" ]; then
                err "--win-iso requires a path (e.g. /path/to/Win11.iso)"
                exit 1
            fi
            if [ ! -f "$WINPODX_WIN_ISO" ]; then
                err "--win-iso: no such file: $WINPODX_WIN_ISO"
                exit 1
            fi
            # Resolve to an absolute path now — later `cp` may run from a
            # different cwd, and the value is logged for the user.
            WINPODX_WIN_ISO="$(cd "$(dirname "$WINPODX_WIN_ISO")" && pwd)/$(basename "$WINPODX_WIN_ISO")"
            case "$WINPODX_WIN_ISO" in
                *.iso|*.ISO) : ;;
                *) log "Note: --win-iso path doesn't end in .iso — using it anyway: $WINPODX_WIN_ISO" ;;
            esac
            shift 2
            ;;
        --storage-dir)
            WINPODX_STORAGE_DIR="${2:-}"
            if [ -z "$WINPODX_STORAGE_DIR" ]; then
                err "--storage-dir requires a path (e.g. /mnt/data/winpodx)"
                exit 1
            fi
            if [ -e "$WINPODX_STORAGE_DIR" ] && [ ! -d "$WINPODX_STORAGE_DIR" ]; then
                err "--storage-dir: not a directory: $WINPODX_STORAGE_DIR"
                exit 1
            fi
            # Create it now so we can absolutize + verify it's writable; setup
            # re-creates idempotently and applies the btrfs/SSD prep.
            if ! mkdir -p "$WINPODX_STORAGE_DIR" 2>/dev/null; then
                err "--storage-dir: cannot create $WINPODX_STORAGE_DIR (check the path / permissions)"
                exit 1
            fi
            if [ ! -w "$WINPODX_STORAGE_DIR" ]; then
                err "--storage-dir: not writable: $WINPODX_STORAGE_DIR"
                exit 1
            fi
            WINPODX_STORAGE_DIR="$(cd "$WINPODX_STORAGE_DIR" && pwd)"
            shift 2
            ;;
        --no-gui)
            WINPODX_NO_GUI=1
            shift
            ;;
        --verbose|-v)
            WINPODX_VERBOSE=1
            shift
            ;;
        --backend)
            WINPODX_BACKEND="${2:-}"
            if [ -z "$WINPODX_BACKEND" ]; then
                err "--backend requires a value (podman | docker | manual)"
                exit 1
            fi
            shift 2
            ;;
        --mode)
            WINPODX_MODE="${2:-}"
            if [ -z "$WINPODX_MODE" ]; then
                err "--mode requires a value (r | a | c | n)"
                exit 1
            fi
            shift 2
            ;;
        --manual)
            WINPODX_MANUAL=1
            shift
            ;;
        --allow-old-podman)
            WINPODX_ALLOW_OLD_PODMAN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            err "Unknown argument: $1"
            usage >&2
            exit 1
            ;;
    esac
done

# Normalise + validate --backend.
if [ -n "$WINPODX_BACKEND" ]; then
    WINPODX_BACKEND="$(echo "$WINPODX_BACKEND" | tr '[:upper:]' '[:lower:]')"
    case "$WINPODX_BACKEND" in
        podman|docker|manual) ;;
        *)
            err "--backend must be one of: podman, docker, manual (got '$WINPODX_BACKEND')"
            exit 1
            ;;
    esac
fi

# Normalise + validate --mode.
if [ -n "$WINPODX_MODE" ]; then
    WINPODX_MODE="$(echo "$WINPODX_MODE" | tr '[:upper:]' '[:lower:]' | cut -c1)"
    case "$WINPODX_MODE" in
        r|a|c|n) ;;
        *)
            err "--mode must be one of: r, a, c, n (got '$WINPODX_MODE')"
            exit 1
            ;;
    esac
fi

# Validate --source
if [ -n "$WINPODX_SOURCE" ]; then
    if [ ! -d "$WINPODX_SOURCE" ]; then
        err "--source path does not exist or is not a directory: $WINPODX_SOURCE"
        exit 1
    fi
    if [ ! -f "$WINPODX_SOURCE/pyproject.toml" ] || [ ! -d "$WINPODX_SOURCE/src/winpodx" ]; then
        err "--source path does not look like a winpodx repo (missing pyproject.toml or src/winpodx/): $WINPODX_SOURCE"
        exit 1
    fi
    log "Using local source: $WINPODX_SOURCE"
fi

# Validate --image-tar
if [ -n "$WINPODX_IMAGE_TAR" ]; then
    if [ ! -f "$WINPODX_IMAGE_TAR" ]; then
        err "--image-tar file does not exist: $WINPODX_IMAGE_TAR"
        exit 1
    fi
    log "Using local image tar: $WINPODX_IMAGE_TAR"
fi

if [ -n "$WINPODX_SKIP_DEPS" ]; then
    log "Skipping distro dependency install (--skip-deps)"
fi

# --- Detect distro & package manager ---
detect_distro() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    else
        echo "unknown"
    fi
}

DISTRO=$(detect_distro)

# Detect host architecture. winpodx ships two dockur image variants:
#
#   x86_64  → dockurr/windows (x86_64 Windows guest, native via KVM)
#   aarch64 → dockurr/windows-arm (Windows-on-ARM guest, native via KVM)
#
# core/config.py:_default_pod_image picks the matching image on a fresh
# install. install.sh only logs the detected arch here for visibility —
# the image picker fires inside `winpodx setup` later.
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|amd64)
        ARCH_LABEL="x86_64"
        ;;
    aarch64|arm64)
        ARCH_LABEL="aarch64"
        ;;
    *)
        ARCH_LABEL="$ARCH"
        ;;
esac

# =====================================================================
# Fresh-install detection (governs rollback scope, see trap below).
#
# Rollback is FRESH-INSTALL ONLY: on an upgrade (a prior config OR a
# prior venv exists) a failure must NOT delete the working install. We
# snapshot this BEFORE creating any artifacts this run.
# =====================================================================
PRIOR_CONFIG="$CONFIG_HOME/winpodx/winpodx.toml"
IS_FRESH_INSTALL=1
if [ -f "$PRIOR_CONFIG" ] || [ -e "$VENV_DIR" ]; then
    IS_FRESH_INSTALL=0
fi

# =====================================================================
# Rollback (LOCKED scope).
#
# On a FAILED fresh-install run, remove ONLY winpodx's own artifacts
# created THIS run: the venv, the winpodx-run launcher + winpodx
# symlink, the desktop entry + icon, and the .install_in_progress
# marker. Never uninstall system packages; never touch a pre-existing
# ~/.config/winpodx config. On an upgrade, leave everything intact.
# =====================================================================
WINPODX_INSTALL_MARKER="$CONFIG_HOME/winpodx/.install_in_progress"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_BASE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
ICON_DIR="$ICON_BASE/scalable/apps"

# Disarmed on success.
ROLLBACK_ARMED=1

# --- Upgrade-atomicity staging state (#716-audit follow-up) ---
# Populated once the clone/copy + venv-build phase runs (see below); declared
# here with unset-safe defaults so rollback() can reference them even if a
# failure happens earlier (dependency install, etc.) under `set -u`.
WORK_DIR=""
INSTALL_DIR_ASIDE=""
SWAP_IN_PROGRESS=0
UPGRADE_SWAP_DONE=0

# --- Pre-existing $SYMLINK backup state (#716-audit follow-up) ---
# Populated just before `ln -sfn ... $SYMLINK` if that path already existed
# and wasn't already our own launcher symlink (e.g. a pip/pipx install).
SYMLINK_BACKED_UP=0
SYMLINK_BACKUP=""

# `winpodx setup` result (#716-audit follow-up), used by the closing banner
# below. Defaults to 1 (success) -- manual-mode installs skip setup entirely
# and exit before the banner, so this default is never actually shown as a
# false success; it only matters once the setup call below runs.
SETUP_OK=1

cleanup_install_marker() {
    # Must never fail: rollback() calls this as its FIRST line, and under
    # `set -euo pipefail` a non-zero rm inside the ERR trap (e.g. an
    # unwritable/immutable config dir) would abort the handler before the
    # mid-swap restore below runs — stranding the user with no install AND no
    # restore. Guard it so the restore path always executes (#716 gate).
    rm -f "$WINPODX_INSTALL_MARKER" 2>/dev/null || true
}

rollback() {
    cleanup_install_marker
    if [ "$ROLLBACK_ARMED" -ne 1 ]; then
        return 0
    fi
    if [ "$IS_FRESH_INSTALL" -ne 1 ]; then
        # #716-audit follow-up: the venv/source tree for an upgrade is now
        # built at a staging path ($WORK_DIR) and only swapped into
        # $INSTALL_DIR after the build succeeds (see the staging section
        # below) -- so the message here must reflect which of those three
        # states we're actually in, instead of always claiming "not touched".
        if [ "$SWAP_IN_PROGRESS" -eq 1 ]; then
            warn "Install failed mid-swap while upgrading -- restoring your previous WinPodX install."
            rm -rf "$INSTALL_DIR" 2>/dev/null || true
            if [ -n "$INSTALL_DIR_ASIDE" ] && [ -d "$INSTALL_DIR_ASIDE" ]; then
                # Guard with `|| warn ...` (not a bare command): a failure here
                # runs under `set -e` INSIDE the ERR trap itself, and an
                # unguarded failing command in a trap aborts the script before
                # the rest of this handler (incl. the warn below) can run.
                mv "$INSTALL_DIR_ASIDE" "$INSTALL_DIR" 2>/dev/null \
                    || warn "Could not restore $INSTALL_DIR_ASIDE -> $INSTALL_DIR automatically -- restore it by hand."
            fi
            warn "Previous install restored -- you are still on the pre-upgrade version."
        elif [ "$UPGRADE_SWAP_DONE" -eq 1 ]; then
            warn "Install failed AFTER the upgraded venv/launcher were swapped into place --"
            warn "they are already updated; only a later step failed (see the error above)."
            warn "Re-run install.sh, or finish the failed step manually."
        else
            warn "Install failed during an upgrade — leaving the existing WinPodX install intact."
            warn "Your previous venv, launcher, and config were not touched."
            if [ -n "$WORK_DIR" ] && [ "$WORK_DIR" != "$INSTALL_DIR" ] && [ -e "$WORK_DIR" ]; then
                rm -rf "$WORK_DIR" 2>/dev/null || true
            fi
        fi
        return 0
    fi
    warn "Rolling back WinPodX install artifacts..."
    # venv + cloned/copied source tree (this whole dir is ours, created
    # this run on a fresh install).
    rm -rf "$INSTALL_DIR" 2>/dev/null || true
    rm -f "$LAUNCHER" 2>/dev/null || true
    rm -f "$SYMLINK" 2>/dev/null || true
    if [ "$SYMLINK_BACKED_UP" -eq 1 ] && [ -e "$SYMLINK_BACKUP" ]; then
        # #716-audit follow-up: restore the pre-existing (e.g. pip/pipx)
        # winpodx entry point we backed up before overwriting -- never leave
        # the user without the binary they had before running install.sh.
        # Guarded with `|| warn ...` for the same reason as the mv above: an
        # unguarded failure here would abort the rest of this ERR-trap handler.
        if mv "$SYMLINK_BACKUP" "$SYMLINK" 2>/dev/null; then
            warn "Restored your previous $SYMLINK (backed up before this run)."
        else
            warn "Could not restore $SYMLINK_BACKUP -> $SYMLINK automatically -- restore it by hand."
        fi
    fi
    rm -f "$DESKTOP_DIR/winpodx.desktop" 2>/dev/null || true
    rm -f "$ICON_DIR/winpodx.svg" 2>/dev/null || true
    # Do NOT remove ~/.config/winpodx itself — only our own marker, which
    # cleanup_install_marker already handled above.
}

# ERR trap: any failed command (set -e is in effect) rolls back, then
# exits non-zero. INT/TERM abort the rest of install.sh too.
rollback_and_exit_err() {
    local rc=$?
    rollback
    exit "$rc"
}
cleanup_and_exit_int()  { rollback; exit 130; }
cleanup_and_exit_term() { rollback; exit 143; }
trap rollback_and_exit_err ERR
trap cleanup_and_exit_int INT
trap cleanup_and_exit_term TERM

# Map generic dependency names to distro-specific package names
pkg_name() {
    local dep="$1"
    case "$DISTRO" in
        opensuse*|sles)
            case "$dep" in
                python3)        echo "python3" ;;
                python3-venv)   echo "python3" ;;
                podman)         echo "podman" ;;
                podman-compose) echo "podman-compose" ;;
                docker)         echo "docker" ;;
                freerdp)        echo "freerdp" ;;
                kvm)            echo "qemu-kvm" ;;
                xcb-cursor)     echo "libxcb-cursor0" ;;
            esac ;;
        fedora|rhel|centos|rocky|alma)
            case "$dep" in
                python3)        echo "python3" ;;
                python3-venv)   echo "python3" ;;
                podman)         echo "podman" ;;
                podman-compose) echo "podman-compose" ;;
                docker)         echo "docker" ;;
                freerdp)        echo "freerdp" ;;
                kvm)            echo "qemu-kvm" ;;
                xcb-cursor)     echo "xcb-util-cursor" ;;
            esac ;;
        ubuntu|debian|linuxmint|pop)
            case "$dep" in
                python3)        echo "python3" ;;
                # Debian/Ubuntu split venv + ensurepip out of python3 into
                # python3-venv; without it `python3 -m venv` fails to
                # bootstrap pip. This is the one package the mandatory-venv
                # step may need to install before the venv can be created.
                python3-venv)   echo "python3-venv" ;;
                podman)         echo "podman" ;;
                podman-compose) echo "podman-compose" ;;
                docker)         echo "docker.io" ;;
                freerdp)
                    # Debian 13+ (Trixie) and recent Ubuntu (24.10+, 25.04, 25.10)
                    # only ship freerdp3-x11; the stock freerdp2-x11 package is
                    # gone. Older systems (Debian <=12, Ubuntu 22.04 stock) only
                    # ship freerdp2-x11. Ubuntu 24.04 LTS ships both.
                    # Prefer freerdp3-x11 — the v0.5.1 launcher detects the
                    # FreeRDP major version at startup and emits the right
                    # /app: syntax for either version, so freerdp3-x11 is the
                    # better default when available. Fall back to freerdp2-x11
                    # only when the user's apt repos don't have a 3 build.
                    if apt-cache show freerdp3-x11 2>/dev/null | grep -q '^Package:'; then
                        echo "freerdp3-x11"
                    elif apt-cache show freerdp2-x11 2>/dev/null | grep -q '^Package:'; then
                        echo "freerdp2-x11"
                    else
                        # Neither in cache — emit freerdp3-x11 so apt produces
                        # a useful "Unable to locate package" error pointing at
                        # the recommended package, instead of a stale one.
                        echo "freerdp3-x11"
                    fi
                    ;;
                kvm)
                    # Ubuntu 24.04+ (and Mint 22+ / xubuntu 26.04 / Debian 13)
                    # made qemu-kvm a virtual package with no installation
                    # candidate; apt errors with
                    # ``E: Package 'qemu-kvm' has no installation candidate``
                    # and lists qemu-system-x86 / qemu-system-x86-hwe as the
                    # real providers (#200, reported by @n-osennij on
                    # xubuntu 26.04). On aarch64 the real package is
                    # qemu-system-arm. Probe apt-cache and pick the available
                    # candidate; fall back to qemu-kvm so apt emits the
                    # legacy error message on truly ancient repos.
                    local kvm_first kvm_second
                    if [ "$ARCH" = "aarch64" ]; then
                        kvm_first="qemu-system-arm"
                        kvm_second="qemu-kvm"
                    else
                        kvm_first="qemu-system-x86"
                        kvm_second="qemu-system-x86-hwe"
                    fi
                    if apt-cache show "$kvm_first" 2>/dev/null | grep -q '^Package:'; then
                        echo "$kvm_first"
                    elif apt-cache show "$kvm_second" 2>/dev/null | grep -q '^Package:'; then
                        echo "$kvm_second"
                    elif apt-cache show qemu-kvm 2>/dev/null | grep -q '^Package:'; then
                        echo "qemu-kvm"
                    else
                        echo "$kvm_first"
                    fi
                    ;;
                xcb-cursor)     echo "libxcb-cursor0" ;;
            esac ;;
        arch|manjaro|endeavouros)
            case "$dep" in
                python3)        echo "python" ;;
                python3-venv)   echo "python" ;;
                podman)         echo "podman" ;;
                podman-compose) echo "podman-compose" ;;
                docker)         echo "docker" ;;
                freerdp)        echo "freerdp" ;;
                kvm)            echo "qemu-full" ;;
                xcb-cursor)     echo "xcb-util-cursor" ;;
            esac ;;
        *)
            echo "$dep" ;;
    esac
}

install_pkg() {
    local pkg="$1"
    local actual
    actual=$(pkg_name "$pkg")
    log "Installing $actual..."

    if command -v zypper >/dev/null 2>&1; then
        sudo zypper install -y "$actual"
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y "$actual"
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y "$actual"
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm "$actual"
    else
        err "No supported package manager found."
        err "Please install '$actual' manually."
        return 1
    fi
}

# --- Atomic Fedora (Silverblue / Kinoite / Sericea / Bluefin / Bazzite / ...) ---
#
# These distros use rpm-ostree (image-based, immutable root) instead of dnf.
# Layering many packages individually with rpm-ostree triggers a reboot per
# layered batch, so we sidestep the per-dependency loop entirely — winpodx is
# published on the openSUSE Build Service (OBS) as a Fedora RPM whose
# Requires: pulls in freerdp >= 3.0, python3-tomli, and Recommends: podman +
# python3-PySide6, so layering just `winpodx` is one transaction. We try
# `rpm-ostree install --apply-live` first to land the layer in the booted
# deployment without a reboot; if the running deployment can't accept the
# live apply we stage normally and prompt the user to reboot once.
#
# rpm-ostree's RPM install path is wholly separate from the venv flow below,
# so it disarms the rollback trap (its artifacts are an OBS repo file +
# layered RPM, neither of which the venv rollback should touch) and exits.
#
# An EXPLICIT source override (--main / --ref / --source / --image-tar) always
# wins: the OBS RPM only ships tagged releases, so honouring those flags means
# falling through to the git/venv flow even on Atomic (#548 — custom-image
# builders layer winpodx from main like any other Fedora package).
#
# Also gated on there being no existing venv install (#752): this branch
# used to fire on `command -v rpm-ostree` alone, so a bare `curl | bash`
# re-run on a host that already has a curl/venv install (e.g. Bazzite,
# installed before the OBS repo existed) would layer a SECOND winpodx
# binary at /usr/bin/winpodx instead of upgrading the one actually in use.
# Since ~/.local/bin precedes /usr/bin on PATH, that layered copy never
# runs — the user's `winpodx` stayed pinned to whatever version the venv
# install last had, forever. The gate is VENV presence, NOT
# IS_FRESH_INSTALL: an RPM-only host has a prior config too (which zeroes
# IS_FRESH_INSTALL), and re-running the installer there should keep
# updating the RPM, not silently switch topology to a venv install.
if command -v rpm-ostree >/dev/null 2>&1 \
   && [ -z "$WINPODX_REF" ] && [ -z "$WINPODX_SOURCE" ] && [ -z "$WINPODX_IMAGE_TAR" ] \
   && [ ! -e "$VENV_DIR" ]; then
    ROLLBACK_ARMED=0
    log "Detected rpm-ostree — Atomic Fedora install path."
    if [ ! -f /etc/os-release ]; then
        err "/etc/os-release missing; can't determine Fedora version for OBS repo selection."
        exit 1
    fi
    . /etc/os-release
    obs_ver="$VERSION_ID"
    repo_url="https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_${obs_ver}/home:Kernalix7.repo"
    log "Probing OBS repo for Fedora ${obs_ver}: $repo_url"
    if ! curl -sSfI "$repo_url" >/dev/null 2>&1; then
        err "No OBS repo published for Fedora_${obs_ver}."
        err "Currently enabled: Fedora 42, 43, 44 at https://build.opensuse.org/project/show/home:Kernalix7"
        err "Open an issue at https://github.com/kernalix7/winpodx/issues if you need another Fedora release added."
        exit 1
    fi
    log "Adding OBS repo to /etc/yum.repos.d/..."
    sudo curl -sSL "$repo_url" -o /etc/yum.repos.d/home-Kernalix7-winpodx.repo
    log "Layering WinPodX via rpm-ostree install --apply-live (one transaction)..."
    if sudo rpm-ostree install --apply-live --idempotent winpodx; then
        log "WinPodX layered into the booted deployment — no reboot required."
        log "Run: winpodx setup"
    else
        warn "Live apply unavailable on this deployment — staging the layer for next boot."
        if sudo rpm-ostree install --idempotent winpodx; then
            log "Staged. Reboot the host, then run: winpodx setup"
        else
            err "rpm-ostree install failed in both live and staged modes. See the rpm-ostree output above for the underlying error."
            exit 1
        fi
    fi
    exit 0
elif command -v rpm-ostree >/dev/null 2>&1 \
   && [ -z "$WINPODX_REF" ] && [ -z "$WINPODX_SOURCE" ] && [ -z "$WINPODX_IMAGE_TAR" ]; then
    # #752: same condition as the branch above, minus the venv check — so
    # getting here means rpm-ostree is present, no explicit source
    # override was given, and $VENV_DIR already exists (a prior curl/venv
    # install). Don't layer a second binary that PATH order would never
    # actually run; upgrade the install that's already in use via the
    # git/venv flow below instead.
    warn "rpm-ostree detected, but an existing venv install was found at $INSTALL_DIR — upgrading that install in place (avoids a PATH-shadowed dual install)."
elif command -v rpm-ostree >/dev/null 2>&1; then
    # rpm-ostree present, but the user asked for a specific source above — note
    # the bypass (the OBS RPM only ships tagged releases) and fall through to
    # the git/venv install path below.
    log "rpm-ostree detected, but --main/--ref/--source/--image-tar was set — installing from source via the venv path."
fi

# =====================================================================
# Pre-sudo system check.
#
# Scan + print a summary BEFORE any sudo / package install so the user
# can see exactly what's present and decide how to proceed.
# =====================================================================

# Helpers used by the scan.
tool_version() {
    # Best-effort single-line version string for a tool, or "".
    local tool="$1"
    case "$tool" in
        podman)  podman --version 2>/dev/null | head -n1 ;;
        docker)  docker --version 2>/dev/null | head -n1 ;;
        python3) python3 --version 2>&1 | head -n1 ;;
        freerdp)
            local c
            for c in xfreerdp3 xfreerdp wlfreerdp3 wlfreerdp; do
                if command -v "$c" >/dev/null 2>&1; then
                    "$c" --version 2>/dev/null | head -n1 || echo "$c (version unknown)"
                    return 0
                fi
            done
            ;;
    esac
}

# Podman major-version gate (#271). dockur/winpodx need rootless
# `group_add: keep-groups` + a modern compose; Ubuntu 22.04 ships
# podman 3.4. Flag podman as too old when major < 4.
PODMAN_PRESENT=false
PODMAN_TOO_OLD=false
PODMAN_MAJOR=0
if command -v podman >/dev/null 2>&1; then
    PODMAN_PRESENT=true
    # `podman --version` -> "podman version 4.9.3"
    PODMAN_MAJOR="$(podman --version 2>/dev/null | sed -n 's/.*version[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n1)"
    PODMAN_MAJOR="${PODMAN_MAJOR:-0}"
    if [ "$PODMAN_MAJOR" -lt 4 ]; then
        PODMAN_TOO_OLD=true
    fi
fi

# winpodx requires the standalone `podman-compose` (NOT `podman compose`, which
# delegates to docker-compose and breaks our keep-groups extension -- #288).
# It's a separate package the `podman` package doesn't pull in.
PODMAN_COMPOSE_PRESENT=false
command -v podman-compose >/dev/null 2>&1 && PODMAN_COMPOSE_PRESENT=true

DOCKER_PRESENT=false
command -v docker >/dev/null 2>&1 && DOCKER_PRESENT=true
# FreeRDP detection — track native client, Flatpak client, and the Flatpak
# runtime separately so the install policy + Custom mode can choose a source.
# Mirrors core/rdp.py:find_freerdp's accepted native set.
FREERDP_NATIVE_PRESENT=false
for _c in xfreerdp3 xfreerdp sdl-freerdp3 sdl-freerdp wlfreerdp3 wlfreerdp; do
    if command -v "$_c" >/dev/null 2>&1; then FREERDP_NATIVE_PRESENT=true; break; fi
done
FLATPAK_PRESENT=false
command -v flatpak >/dev/null 2>&1 && FLATPAK_PRESENT=true
FREERDP_FLATPAK_PRESENT=false
if [ "$FLATPAK_PRESENT" = true ] \
    && flatpak list --app --columns=application 2>/dev/null | grep -qx 'com.freerdp.FreeRDP'; then
    FREERDP_FLATPAK_PRESENT=true
fi
# Any client (native or Flatpak) satisfies the requirement, so we never pull a
# redundant native package when a client is already present (#269). winpodx's
# launcher prefers the FLATPAK client when both are present (core/rdp.py
# auto order is flatpak-first, #401) — so the reported "kind" mirrors that:
# flatpak wins the label when both exist, so it matches the client the
# launcher will actually use.
FREERDP_PRESENT=false
FREERDP_KIND=""
if [ "$FREERDP_FLATPAK_PRESENT" = true ]; then
    FREERDP_PRESENT=true
    FREERDP_KIND="flatpak (com.freerdp.FreeRDP)"
elif [ "$FREERDP_NATIVE_PRESENT" = true ]; then
    FREERDP_PRESENT=true
    FREERDP_KIND="native"
fi
PYTHON3_PRESENT=false
command -v python3 >/dev/null 2>&1 && PYTHON3_PRESENT=true
KVM_PRESENT=false
[ -e /dev/kvm ] && KVM_PRESENT=true

# Probe whether `python3 -m venv` can actually bootstrap (Debian/Ubuntu
# split python3-venv / ensurepip out).
VENV_PROBE_OK=false
if [ "$PYTHON3_PRESENT" = true ] && python3 -c "import venv, ensurepip" >/dev/null 2>&1; then
    VENV_PROBE_OK=true
fi

yesno() { if [ "$1" = true ]; then echo "yes"; else echo "no"; fi; }

print_system_check() {
    local osname="$DISTRO"
    if [ -f /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        osname="${PRETTY_NAME:-$DISTRO}"
    fi
    echo ""
    echo "================ WinPodX system check ================"
    echo "  Distro:        $osname"
    echo "  Architecture:  $ARCH_LABEL"
    echo "  python3:       $(yesno "$PYTHON3_PRESENT")  $(tool_version python3)"
    echo "  python venv:   $(yesno "$VENV_PROBE_OK") (python3 -m venv works)"
    if [ "$PODMAN_PRESENT" = true ]; then
        if [ "$PODMAN_TOO_OLD" = true ]; then
            echo "  podman:        yes  $(tool_version podman)  [TOO OLD — major $PODMAN_MAJOR < 4]"
        else
            echo "  podman:        yes  $(tool_version podman)"
        fi
    else
        echo "  podman:        no"
    fi
    echo "  docker:        $(yesno "$DOCKER_PRESENT")  $(tool_version docker)"
    echo "  freerdp:       $(yesno "$FREERDP_PRESENT")  ${FREERDP_KIND:+$FREERDP_KIND }$(tool_version freerdp)"
    echo "  /dev/kvm:      $(yesno "$KVM_PRESENT")"
    echo "======================================================"
    if [ "$PODMAN_TOO_OLD" = true ]; then
        echo ""
        warn "podman $PODMAN_MAJOR.x is too old for WinPodX (need >= 4 for rootless"
        warn "'group_add: keep-groups' + modern compose; Ubuntu 22.04 ships 3.4 — #271)."
        warn "Either:"
        warn "  - upgrade podman (Kubic / devel:kubic:libcontainers repo:"
        warn "    https://software.opensuse.org/download/package?package=podman&project=devel%3Akubic%3Alibcontainers%3Aunstable ), OR"
        warn "  - use the Docker backend (--backend docker, or pick Docker in Custom mode)."
    fi
    echo ""
}

print_system_check

# =====================================================================
# Mode prompt.
#
# Resolve INSTALL_MODE (r/a/c/n). Precedence:
#   1. --mode / WINPODX_MODE if set.
#   2. interactive TTY      -> prompt.
#   3. non-interactive      -> 'r' (recommended), preserving old behaviour.
# =====================================================================
INSTALL_MODE="$WINPODX_MODE"
# Interactive if we can reach a real terminal for INPUT — either stdin is a
# TTY, or (the `curl ... | bash` case, where stdin is the script pipe) the
# controlling terminal /dev/tty is openable. Prompts read from $TTY_DEV so the
# mode menu works even via curl|bash. A run with no controlling terminal
# (CI / cron / `</dev/null`) stays non-interactive and defaults to Recommended.
INTERACTIVE=false
TTY_DEV=/dev/stdin
if [ -t 0 ]; then
    INTERACTIVE=true
    TTY_DEV=/dev/stdin
elif { true </dev/tty; } 2>/dev/null; then
    INTERACTIVE=true
    TTY_DEV=/dev/tty
fi

if [ -z "$INSTALL_MODE" ]; then
    if [ "$IS_FRESH_INSTALL" != "1" ]; then
        # Upgrade / re-run: a winpodx config (and backend) already exists, so
        # the R/A/C/N mode prompt is pointless — there's nothing to choose, we
        # reuse the existing setup and run the migrate path. Resolve to
        # Automatic (reuse what's present, install only what's strictly
        # missing) without prompting.
        INSTALL_MODE="a"
        log "Existing WinPodX install detected — upgrading; reusing current config (skipping install-mode prompt)."
    elif [ "$INTERACTIVE" = true ]; then
        cat <<'MODE_EOF'
Install mode?
  [R]ecommended  WinPodX's recommended stack (Podman backend + deps); installs missing system packages via the distro package manager (sudo).
  [A]utomatic    Reuse what's already installed; only install what's strictly missing; pick the backend from what's present (prefer an already-working docker/podman). Minimal sudo.
  [C]ustom       Choose backend (podman/docker) and whether to include the GUI, then install accordingly.
  [N]o           Cancel without changing anything.
MODE_EOF
        echo -n "Choice [R/a/c/n]: "
        read -r mode_answer < "$TTY_DEV"
        case "$(echo "${mode_answer:-r}" | tr '[:upper:]' '[:lower:]' | cut -c1)" in
            a) INSTALL_MODE="a" ;;
            c) INSTALL_MODE="c" ;;
            n) INSTALL_MODE="n" ;;
            *) INSTALL_MODE="r" ;;
        esac
    else
        INSTALL_MODE="r"
        log "Non-interactive run with no --mode — defaulting to Recommended (r)."
    fi
fi

# Mode N: clean exit, no changes. Rollback is a no-op on a fresh pre-install run.
if [ "$INSTALL_MODE" = "n" ]; then
    log "Cancelled, no changes made."
    ROLLBACK_ARMED=0
    cleanup_install_marker
    trap - EXIT ERR INT TERM
    exit 0
fi

# --- Custom mode: interactively pick the source of each major dependency ---
# Components with a real source choice: the container backend (podman /
# docker), the FreeRDP client (native package / Flatpak), and the GUI
# (PySide6 yes/no). Everything else is a plain distro package with no
# meaningful alternative. Each prompt has a sensible default so just hitting
# Enter reproduces the Recommended stack.
if [ "$INSTALL_MODE" = "c" ]; then
    if [ -z "$WINPODX_BACKEND" ]; then
        if [ "$INTERACTIVE" = true ]; then
            echo -n "Container backend? [podman/docker] (default podman): "
            read -r be_answer < "$TTY_DEV"
            case "$(echo "${be_answer:-podman}" | tr '[:upper:]' '[:lower:]')" in
                docker)  WINPODX_BACKEND="docker" ;;
                *)       WINPODX_BACKEND="podman" ;;
            esac
        else
            WINPODX_BACKEND="podman"
        fi
    fi
    if [ -z "${WINPODX_FREERDP_SOURCE:-}" ] && [ "$INTERACTIVE" = true ]; then
        echo    "FreeRDP client source?"
        echo    "  [A]uto     prefer the Flatpak when present, else native (recommended)"
        echo    "  [N]ative   distro freerdp3 package"
        echo    "  [F]latpak  com.freerdp.FreeRDP via Flatpak (better on distros with a broken native freerdp3-x11)"
        echo -n "Choice [A/n/f]: "
        read -r fr_answer < "$TTY_DEV"
        case "$(echo "${fr_answer:-a}" | tr '[:upper:]' '[:lower:]' | cut -c1)" in
            n) WINPODX_FREERDP_SOURCE="native" ;;
            f) WINPODX_FREERDP_SOURCE="flatpak" ;;
            *) WINPODX_FREERDP_SOURCE="auto" ;;
        esac
    fi
    if [ -z "$WINPODX_NO_GUI" ] && [ "$INTERACTIVE" = true ]; then
        echo -n "Install the GUI (PySide6)? [Y/n]: "
        read -r gui_answer < "$TTY_DEV"
        if [[ "$gui_answer" =~ ^[Nn] ]]; then
            WINPODX_NO_GUI=1
        fi
    fi
fi

# --- Automatic mode: pick a usable, already-present backend ---
# Walk winpodx's RECOMMENDED backend order (podman first, then docker --
# podman is the project default per CLAUDE.md; libvirt was dropped in 0.6.0)
# and select the first one that's both present AND usable. So when several runtimes are
# installed, the recommended one wins; we only move down the list when the
# higher-priority runtime is absent or unusable (e.g. podman < 4 on Ubuntu
# 22.04, #271). Fall back to Recommended behaviour (podman + install missing
# deps) when nothing usable is present.
#
# Single source of truth for this priority + the podman major-version gate
# is src/winpodx/backend/select.py:choose_backend() (Python). This bash mirror
# exists because install.sh runs system-check BEFORE Python is installed and
# can't import the Python helper; tests pin the bash + Python copies to the
# same order + version gate so they cannot drift. See docs/design/ROADMAP-
# 0.6.0.md item E.
if [ "$INSTALL_MODE" = "a" ] && [ -z "$WINPODX_BACKEND" ]; then
    for candidate in podman docker; do
        case "$candidate" in
            podman)
                [ "$PODMAN_PRESENT" = true ] && [ "$PODMAN_TOO_OLD" = false ] || continue
                WINPODX_BACKEND="podman"
                log "Automatic: podman $PODMAN_MAJOR.x present + usable — selecting podman (recommended)."
                ;;
            docker)
                [ "$DOCKER_PRESENT" = true ] || continue
                WINPODX_BACKEND="docker"
                log "Automatic: podman absent/too-old, docker present — selecting docker."
                ;;
        esac
        break
    done
    if [ -z "$WINPODX_BACKEND" ]; then
        WINPODX_BACKEND="podman"
        warn "Automatic: no usable runtime present — falling back to Recommended (podman + install missing deps)."
    fi
fi

# --- Recommended mode (default): podman unless --backend given ---
if [ "$INSTALL_MODE" = "r" ] && [ -z "$WINPODX_BACKEND" ]; then
    WINPODX_BACKEND="podman"
fi

# Backend 'manual' implies --manual (skip the provisioning chain).
if [ "$WINPODX_BACKEND" = "manual" ]; then
    WINPODX_MANUAL=1
fi

# --- Too-old-podman guard (#271, ask 3: graceful handling) ---
# Automatic mode (above) already walks past an unusable podman to docker,
# but Recommended mode and an explicit `--backend podman` do not, so
# a host with podman < 4 (Ubuntu 22.04 ships 3.4) would proceed and then fail
# at provisioning -- AFTER we'd installed packages. Refuse to blindly
# continue: offer a usable alternative when one is present, otherwise exit
# cleanly WITHOUT modifying the system (no package install has run yet here).
# WINPODX_ALLOW_OLD_PODMAN=1 / --allow-old-podman overrides (e.g. podman was
# upgraded out-of-band since the probe).
if [ "$WINPODX_BACKEND" = "podman" ] && [ "$PODMAN_TOO_OLD" = true ] && [ "$WINPODX_ALLOW_OLD_PODMAN" != "1" ]; then
    warn "podman $PODMAN_MAJOR.x is too old for WinPodX (need >= 4; Ubuntu 22.04 ships 3.4 -- #271)."
    if [ "$INTERACTIVE" = true ]; then
        if [ "$DOCKER_PRESENT" = true ]; then
            echo "docker is installed and usable."
            echo "  [d] switch to the docker backend"
            echo "  [c] continue with podman anyway (will likely fail)"
            echo "  [a] abort without changing anything (default)"
            printf 'Choice [d/c/a]: '
            read -r _pm_choice < "$TTY_DEV"
            case "$(echo "${_pm_choice:-a}" | tr '[:upper:]' '[:lower:]')" in
                d|docker)
                    WINPODX_BACKEND="docker"; log "Switched backend to docker."
                    ;;
                c|continue)
                    warn "Continuing with podman $PODMAN_MAJOR.x at your own risk."
                    ;;
                *)
                    err "Aborted: upgrade podman to >= 4 or re-run with --backend docker. No changes were made."
                    exit 1
                    ;;
            esac
        else
            echo "No alternative backend (docker) is installed."
            printf 'Continue with podman %s.x anyway? It will likely fail. [y/N]: ' "$PODMAN_MAJOR"
            read -r _pm_choice < "$TTY_DEV"
            case "$(echo "${_pm_choice:-n}" | tr '[:upper:]' '[:lower:]')" in
                y|yes)
                    warn "Continuing with podman $PODMAN_MAJOR.x at your own risk."
                    ;;
                *)
                    err "Aborted: upgrade podman to >= 4 (e.g. the Kubic repo) or install docker, then re-run. No changes were made."
                    exit 1
                    ;;
            esac
        fi
    else
        # Non-interactive: do NOT silently switch the backend or run a known-
        # failing podman install. Exit cleanly with guidance.
        if [ "$DOCKER_PRESENT" = true ]; then
            warn "docker is installed -- re-run with --backend docker."
        fi
        warn "Or upgrade podman to >= 4, or pass --allow-old-podman / WINPODX_ALLOW_OLD_PODMAN=1 to force."
        err "Non-interactive install aborted before modifying the system: backend=podman but podman $PODMAN_MAJOR.x is too old (#271)."
        exit 1
    fi
fi

log "Detected distro: $DISTRO"
log "Detected arch: $ARCH_LABEL"
log "Install mode: $INSTALL_MODE | backend: ${WINPODX_BACKEND:-podman} | gui: $([ -n "$WINPODX_NO_GUI" ] && echo no || echo yes)"

# Mark the install as in-progress so child winpodx CLI invocations
# (winpodx setup, pod wait-ready, migrate, app refresh, host-open
# refresh, ...) skip the tray auto-spawn AND the tray's UNRESPONSIVE
# auto-recovery transition. Without this, install.sh's wait windows --
# where the guest is genuinely booting and RDP legitimately isn't
# reachable -- would have the tray fire a "Pod stopped responding"
# notification + try to Restart-Service TermService against a guest
# that's still running first-boot Sysprep. Marker is removed on every
# exit path via the traps above.
mkdir -p "$(dirname "$WINPODX_INSTALL_MARKER")"
echo "$$" > "$WINPODX_INSTALL_MARKER"
chmod 600 "$WINPODX_INSTALL_MARKER" 2>/dev/null || true

# =====================================================================
# Which required system deps are missing?
#
# Mode shapes what we install:
#   R  -> install all genuinely-missing required deps via pkg mgr.
#   A  -> install only genuinely-missing required deps (same set; A
#         differs only in backend selection, which is already done).
#   C  -> install deps appropriate to the chosen backend.
# The chosen backend decides whether podman/docker is required.
# =====================================================================
log "Checking dependencies..."

MISSING=()

# Backend runtime requirement.
case "${WINPODX_BACKEND:-podman}" in
    podman)
        if [ "$PODMAN_PRESENT" = false ] || [ "$PODMAN_TOO_OLD" = true ]; then
            # In A mode we only install genuinely-missing deps; a too-old
            # podman is "present", so don't try to replace it via pkg mgr
            # (the distro repo would reinstall the same old version). Warn
            # instead. In R/C we add podman to MISSING only when absent.
            if [ "$PODMAN_PRESENT" = false ]; then
                MISSING+=("podman")
            else
                warn "podman is present but too old (major $PODMAN_MAJOR); see the note above. Continuing — pod start may fail until you upgrade podman or switch to --backend docker."
            fi
        fi
        # podman-compose is required but ships separately from podman; without
        # it pod creation later fails with "compose command not found" (#503,
        # #580). Install it alongside podman.
        [ "$PODMAN_COMPOSE_PRESENT" = false ] && MISSING+=("podman-compose")
        ;;
    docker)
        [ "$DOCKER_PRESENT" = false ] && MISSING+=("docker")
        ;;
esac

# FreeRDP is required for every backend (the launcher shells out to it).
# Source resolution (auto | native | flatpak). Custom mode may set
# WINPODX_FREERDP_SOURCE; default auto. winpodx's launcher prefers the FLATPAK
# client when present (flatpak-first auto order, #401). install.sh still
# installs the lightweight NATIVE package when NO client is present at all --
# pulling the whole Flatpak runtime during install is heavy, and the launcher
# uses whatever is there. An already-present Flatpak is reused (never install a
# redundant client). Only an explicit `--freerdp-source flatpak` (Custom)
# installs the Flatpak. The resolved value -> `winpodx setup --freerdp-source`.
WINPODX_FREERDP_SOURCE="${WINPODX_FREERDP_SOURCE:-auto}"
INSTALL_FREERDP_FLATPAK=false
case "$WINPODX_FREERDP_SOURCE" in
    flatpak)
        # Explicit Flatpak choice (e.g. native freerdp3-x11 is broken, #393).
        if [ "$FREERDP_FLATPAK_PRESENT" = false ]; then
            INSTALL_FREERDP_FLATPAK=true
            [ "$FLATPAK_PRESENT" = false ] && MISSING+=("flatpak")
        fi
        ;;
    *)  # auto: the launcher prefers an existing Flatpak (flatpak-first), but
        # install the lightweight native package only when NO client (native
        # or Flatpak) is present — an existing Flatpak is reused, never a
        # redundant install.
        [ "$FREERDP_PRESENT" = false ] && MISSING+=("freerdp")
        ;;
esac

# python3 is mandatory (venv host interpreter).
[ "$PYTHON3_PRESENT" = false ] && MISSING+=("python3")

# The Qt6 GUI (PySide6) needs the xcb-cursor platform-plugin runtime library
# (libxcb-cursor.so.0). Qt 6.5+ refuses to start its xcb platform plugin
# without it -- "could not load the Qt platform plugin 'xcb'" -- and it isn't
# pulled in transitively on a minimal desktop (e.g. fresh Linux Mint 22, #712).
# Only add it when the GUI is enabled and the lib isn't already present.
#
# Detection probes the real .so on disk, NOT `ldconfig -p`: on openSUSE the
# library exists at /usr/lib64/libxcb-cursor.so.0 but is absent from the ld.so
# cache, so the ldconfig probe false-negatived and re-prompted every run even
# with libxcb-cursor0 installed (#712 follow-up). Check the standard lib dirs
# (incl. Debian/Ubuntu multiarch) directly, then fall back to ldconfig for any
# non-standard prefix.
_has_libxcb_cursor() {
    local d
    for d in /usr/lib64 /usr/lib /lib64 /lib \
             /usr/lib/x86_64-linux-gnu /usr/lib/aarch64-linux-gnu; do
        [ -e "$d/libxcb-cursor.so.0" ] && return 0
    done
    ldconfig -p 2>/dev/null | grep -q 'libxcb-cursor\.so\.0'
}
if [ -z "$WINPODX_NO_GUI" ] && ! _has_libxcb_cursor; then
    MISSING+=("xcb-cursor")
fi

if [ "$KVM_PRESENT" = false ]; then
    # Pre-install hint. A surprising fraction of user bug reports start
    # here -- the package install loop below will run successfully on
    # most distros because qemu / qemu-kvm is already present, and then
    # the container start later silently fails because hardware virt
    # is off in BIOS. Print the BIOS / module / group check now so the
    # user can stop, fix the actual cause, and re-run.
    warn "/dev/kvm not found -- KVM hardware virtualization is required."
    warn ""
    warn "Before continuing, please verify:"
    warn "  1. Intel VT-x / AMD-V is enabled in your BIOS / UEFI."
    warn "     (Reboot -> firmware setup -> 'Intel Virtualization Technology' /"
    warn "      'SVM Mode' / 'VT-x'. The setting is OFF by default on many laptops.)"
    warn "  2. The kvm kernel module is loaded:"
    if command -v lsmod >/dev/null 2>&1; then
        modules=$(lsmod 2>/dev/null | grep -E '^(kvm|kvm_intel|kvm_amd)\b' | awk '{print $1}' | tr '\n' ' ')
        warn "       Currently loaded: ${modules:-none}"
        warn "       Load it with: sudo modprobe kvm_intel  (or kvm_amd on AMD)"
    fi
    warn "  3. Your user is in the 'kvm' group: id -nG | tr ' ' '\\n' | grep kvm"
    warn ""
    warn "Installing the qemu package alone won't fix BIOS / module / group issues."
    warn "Most 'install ran fine but Windows never boots' bug reports trace back here."
    warn ""
    MISSING+=("kvm")
fi

# --- Plan summary -----------------------------------------------------------
# Now that the mode + every dependency source is resolved, show exactly what
# this run will do BEFORE touching the system (installing packages, sudo,
# provisioning Windows). Every major component is listed evenly with its
# detected state + the action this run will take. Pure logging — non-blocking.
#
# Per-component action helper: "$present" true => use existing, else (if the
# component name is in MISSING) install, else a component-specific note.
_in_missing() { local x; for x in "${MISSING[@]}"; do [ "$x" = "$1" ] && return 0; done; return 1; }
_plan_action() {  # <present-bool> <missing-key>
    if [ "$1" = true ]; then echo "use existing"; elif _in_missing "$2"; then echo "install"; else echo "—"; fi
}
_resolved_backend="${WINPODX_BACKEND:-podman}"
case "$_resolved_backend" in
    podman)  _backend_present="$PODMAN_PRESENT" ;;
    docker)  _backend_present="$DOCKER_PRESENT" ;;
    *)       _backend_present=false ;;
esac
echo ""
log "==================== install plan ===================="
log "  Mode:        $INSTALL_MODE  (r=recommended / a=automatic / c=custom)"
log "  python3:     $(yesno "$PYTHON3_PRESENT") $(tool_version python3)  [host interpreter + private venv]"
log "  venv:        $(yesno "$VENV_PROBE_OK")  [python3 -m venv works]"
log "  Backend:     $_resolved_backend  ($(_plan_action "$_backend_present" "$_resolved_backend"))"
if [ "$FREERDP_PRESENT" = true ]; then
    log "  FreeRDP:     use existing — $FREERDP_KIND"
elif [ "$INSTALL_FREERDP_FLATPAK" = true ]; then
    log "  FreeRDP:     install Flatpak (com.freerdp.FreeRDP)"
else
    log "  FreeRDP:     install native package"
fi
log "  KVM:         $(yesno "$KVM_PRESENT") /dev/kvm  [host virtualization — required, not installed]"
log "  GUI:         $([ -n "$WINPODX_NO_GUI" ] && echo 'no (--no-gui)' || echo 'yes (PySide6, into venv)')"
if [ "${#MISSING[@]}" -gt 0 ]; then
    log "  Will install: ${MISSING[*]}  (via distro package manager, sudo)"
else
    log "  Will install: nothing — all required system packages already present"
fi
if [ "${WINPODX_MANUAL:-0}" = "1" ] || [ "$WINPODX_BACKEND" = "manual" ]; then
    log "  Windows VM:  NOT provisioned (manual mode); run 'winpodx setup' later to finish"
else
    log "  Windows VM:  setup -> first-boot (~7.5GB ISO on a fresh install)"
    log "               -> apply-fixes -> discovery -> reverse-open"
fi
log "======================================================"
echo ""

if [ ${#MISSING[@]} -gt 0 ]; then
    if [ -n "$WINPODX_SKIP_DEPS" ]; then
        err "--skip-deps is set but required tools are missing: ${MISSING[*]}"
        err "Install them manually and re-run, or drop --skip-deps."
        exit 1
    fi
    log "Missing: ${MISSING[*]}"
    echo ""
    echo "  The following will be installed via $(command -v zypper || command -v dnf || command -v apt-get || command -v pacman):"
    for dep in "${MISSING[@]}"; do
        echo "    - $(pkg_name "$dep")"
    done
    echo ""
    if [ "$INTERACTIVE" = true ]; then
        echo -n "  Proceed with installation? (Y/n): "
        read -r answer < "$TTY_DEV"
        if [[ "$answer" =~ ^[Nn] ]]; then
            err "Aborted. Install dependencies manually and try again."
            exit 1
        fi
    else
        log "  Non-interactive — proceeding with package install."
    fi

    INSTALL_FAIL=0
    for dep in "${MISSING[@]}"; do
        if ! install_pkg "$dep"; then
            warn "Failed to install: $(pkg_name "$dep")"
            INSTALL_FAIL=$((INSTALL_FAIL + 1))
        fi
    done
    if [ "$INSTALL_FAIL" -gt 0 ]; then
        err "$INSTALL_FAIL package(s) failed to install. Fix manually and re-run."
        exit 1
    fi
    log "All dependencies installed successfully"
else
    log "All dependencies OK"
fi

# Install the Flatpak FreeRDP when that's the resolved source (auto with a
# flatpak runtime but no client yet, or an explicit --freerdp-source flatpak).
# Best-effort: if the Flatpak install fails (no flathub remote, offline, …)
# fall back to the native package so the user still ends up with a client.
if [ "$INSTALL_FREERDP_FLATPAK" = true ]; then
    log "Installing FreeRDP via Flatpak (com.freerdp.FreeRDP)..."
    flatpak remote-add --if-not-exists --user flathub \
        https://dl.flathub.org/repo/flathub.flatpakrepo >/dev/null 2>&1 || true
    if flatpak install -y --user flathub com.freerdp.FreeRDP >/dev/null 2>&1 \
        || flatpak install -y flathub com.freerdp.FreeRDP >/dev/null 2>&1; then
        log "  Flatpak FreeRDP installed."
        FREERDP_PRESENT=true
        WINPODX_FREERDP_SOURCE="flatpak"
    else
        warn "  Flatpak FreeRDP install failed — falling back to the native package."
        WINPODX_FREERDP_SOURCE="auto"
        if [ "$FREERDP_NATIVE_PRESENT" = false ]; then
            if install_pkg freerdp; then
                FREERDP_PRESENT=true
            else
                warn "  Native FreeRDP install also failed — install a FreeRDP 3 client manually."
            fi
        fi
    fi
fi

# Re-verify /dev/kvm after the install loop. Installing the qemu /
# qemu-kvm package alone does NOT enable hardware virtualisation if
# the CPU extension is off in BIOS, the kvm kernel module isn't
# loaded, or the user isn't in the `kvm` group -- @pnogaret2019-code
# hit this on Linux Mint LMDE 7 (#220) where apt happily said
# "qemu-system-x86 already up to date" while /dev/kvm stayed absent,
# and install.sh printed "All dependencies installed successfully"
# anyway. Without this guard the container starts but the VM never
# boots, and the user sees a silent stall instead of the actionable
# diagnostic below.
#
# #541: /dev/kvm is often absent simply because the kvm kernel module
# isn't loaded -- even though the CPU fully supports virtualization
# (vmx/svm flags present; e.g. confirmed by VirtualBox working on the
# same box). Try to load it before failing -- many "virtualization
# unsupported" reports just needed a modprobe.
if [ ! -e /dev/kvm ] && grep -Eq '(vmx|svm)' /proc/cpuinfo 2>/dev/null; then
    if grep -q 'vmx' /proc/cpuinfo 2>/dev/null; then KVM_MOD=kvm_intel; else KVM_MOD=kvm_amd; fi
    log "/dev/kvm missing but the CPU reports virtualization (vmx/svm) -- loading $KVM_MOD..."
    sudo modprobe "$KVM_MOD" 2>/dev/null || true
    if [ -e /dev/kvm ]; then
        KVM_PRESENT=true
        log "/dev/kvm is now present -- hardware virtualization enabled."
        # Persist so it auto-loads on the next boot (don't make the user redo this).
        if [ -d /etc/modules-load.d ]; then
            printf '%s\n' "$KVM_MOD" | sudo tee /etc/modules-load.d/winpodx-kvm.conf >/dev/null 2>&1 || true
        fi
    fi
fi

if [ ! -e /dev/kvm ]; then
    err "/dev/kvm still missing after package install."
    err ""
    err "Hardware virtualisation is required for WinPodX. Likely causes:"
    err "  1. Intel VT-x / AMD-V disabled in BIOS / UEFI."
    err "     -> Reboot, enter setup, look for 'Intel Virtualization Technology'"
    err "        / 'SVM Mode' / 'VT-x' and enable it."
    err "  2. kvm kernel module not loaded:"
    if command -v lscpu >/dev/null 2>&1; then
        vmx=$(lscpu 2>/dev/null | grep -i 'virtualization\|vmx\|svm' | head -1)
        err "     lscpu: ${vmx:-no virtualization line found}"
    fi
    if command -v lsmod >/dev/null 2>&1; then
        modules=$(lsmod 2>/dev/null | grep -E '^(kvm|kvm_intel|kvm_amd)\b' | awk '{print $1}' | tr '\n' ' ')
        err "     Loaded kvm modules: ${modules:-none}"
        err "     -> 'sudo modprobe kvm_intel' (Intel) or 'sudo modprobe kvm_amd' (AMD)"
    fi
    err "  3. Your user is not in the 'kvm' group:"
    if command -v id >/dev/null 2>&1; then
        if id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx kvm; then
            err "     id: '$USER' is in the kvm group (this one is fine)."
        else
            err "     id: '$USER' is NOT in the kvm group."
            err "     -> 'sudo usermod -aG kvm $USER' then log out + back in."
        fi
    fi
    err ""
    err "Fix one of the above and re-run install.sh."
    exit 1
fi

# --- Check Python version (host interpreter that builds the venv) ---
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    err "Python 3.9+ required (found $PY_VERSION)"
    exit 1
fi
log "Python $PY_VERSION OK"

# =====================================================================
# Upgrade atomicity (#716-audit follow-up).
#
# On a FRESH install we build directly at $INSTALL_DIR / $VENV_DIR, exactly
# as before. On an UPGRADE (IS_FRESH_INSTALL=0) we build the new source tree
# + venv at a staging path ($WORK_DIR / $WORK_DIR/.venv) instead, and only
# swap it into $INSTALL_DIR once the whole build succeeds (see the atomic
# swap block after the venv/pip-install section below). This way a
# mid-install failure -- a bad git ref, a pip install failure, a disk-full
# venv creation -- leaves the previous working install completely untouched
# instead of half-replaced. rollback() (above) mirrors this staging state.
# =====================================================================
if [ "$IS_FRESH_INSTALL" -eq 1 ]; then
    WORK_DIR="$INSTALL_DIR"
else
    WORK_DIR="$INSTALL_DIR.new"
    rm -rf "$WORK_DIR" 2>/dev/null || true
fi
WORK_VENV_DIR="$WORK_DIR/.venv"
WORK_VENV_PY="$WORK_VENV_DIR/bin/python"

# --- Clone, update, or copy winpodx source ---
mkdir -p "$(dirname "$WORK_DIR")"

copy_from_local() {
    local src="$1"
    if [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
    mkdir -p "$WORK_DIR"
    for item in src data config scripts install.sh uninstall.sh pyproject.toml README.md LICENSE; do
        if [ -e "$src/$item" ]; then
            cp -r "$src/$item" "$WORK_DIR/"
        fi
    done
}

# Resolve the install ref. Default (empty WINPODX_REF) -> latest release.
# `--main` / WINPODX_REF=main bypasses the API call so an unreachable
# api.github.com still lets dev installs proceed.
resolve_ref() {
    if [ -n "$WINPODX_REF" ]; then
        echo "$WINPODX_REF"
        return
    fi
    if ! command -v curl >/dev/null 2>&1; then
        echo "main"
        return
    fi
    local latest
    latest=$(curl -fsSL "$REPO_API/releases/latest" 2>/dev/null \
        | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        | head -n1)
    if [ -n "$latest" ]; then
        echo "$latest"
    else
        echo "main"
    fi
}

if [ -n "$WINPODX_SOURCE" ]; then
    # --source wins over every other path; no git at all.
    log "Copying WinPodX from --source: $WINPODX_SOURCE"
    copy_from_local "$WINPODX_SOURCE"
else
    INSTALL_REF="$(resolve_ref)"
    if [ "$INSTALL_REF" = "main" ]; then
        log "Installing from git main (development)"
    else
        log "Installing release: $INSTALL_REF (use --main for development build)"
    fi

    if [ -d "$INSTALL_DIR/.git" ]; then
        if [ "$IS_FRESH_INSTALL" -eq 1 ]; then
            log "Updating existing installation to $INSTALL_REF..."
            git -C "$WORK_DIR" fetch --quiet --tags --prune origin
            # Check out the freshly-fetched ref. Prefer origin/<ref> so a BRANCH
            # advances to its latest remote tip — `git fetch` updates origin/<ref>
            # but NOT the stale local branch, so a plain `checkout <branch>` would
            # pin the install to whatever commit was first cloned (#616: every
            # re-run reinstalled the same old commit). Fall back to <ref> for tags
            # / commits, which aren't origin/-prefixed.
            git -C "$WORK_DIR" checkout --quiet --detach "origin/$INSTALL_REF" 2>/dev/null \
                || git -C "$WORK_DIR" checkout --quiet --detach "$INSTALL_REF" 2>/dev/null \
                || git -C "$WORK_DIR" checkout --quiet "$INSTALL_REF"
        else
            # Upgrade (#716-audit): stage the update via a local clone of the
            # existing checkout instead of fetching/checking out $INSTALL_DIR
            # in place. A local-path clone hardlinks git objects (fast, no
            # re-download) and only reproduces tracked/checked-out content
            # (the untracked .venv is never copied), leaving the live install
            # untouched until the atomic swap below. Re-point origin at the
            # real repo before fetching, since a local clone's origin is the
            # source path, not GitHub.
            log "Updating existing installation to $INSTALL_REF (staged)..."
            git clone --quiet "$INSTALL_DIR" "$WORK_DIR"
            git -C "$WORK_DIR" remote set-url origin "$REPO_URL"
            git -C "$WORK_DIR" fetch --quiet --tags --prune origin
            git -C "$WORK_DIR" checkout --quiet --detach "origin/$INSTALL_REF" 2>/dev/null \
                || git -C "$WORK_DIR" checkout --quiet --detach "$INSTALL_REF" 2>/dev/null \
                || git -C "$WORK_DIR" checkout --quiet "$INSTALL_REF"
        fi
    else
        # If running from repo, copy only needed files (skip .venv, .git, etc.).
        # When piped via `curl ... | bash`, bash reads from stdin and
        # BASH_SOURCE[0] is unset — `set -u` would abort here without the
        # default expansion. Fall through to git-clone when there's no local tree.
        _src="${BASH_SOURCE[0]:-}"
        if [ -n "$_src" ]; then
            SCRIPT_DIR="$(cd "$(dirname "$_src")" && pwd)"
        else
            SCRIPT_DIR=""
        fi
        if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/src/winpodx/__init__.py" ]; then
            log "Installing from local repository..."
            copy_from_local "$SCRIPT_DIR"
        else
            if ! command -v git >/dev/null 2>&1; then
                # git isn't preinstalled on some fresh systems (e.g. Linux Mint);
                # auto-install it like every other dependency so the one-liner
                # is self-contained (#705). Fall back to a clear error if the
                # package manager can't provide it.
                log "git is required to fetch WinPodX but isn't installed; installing it..."
                install_pkg git || true
                if ! command -v git >/dev/null 2>&1; then
                    err "git is required for remote install but could not be installed automatically."
                    err "Install git manually (e.g. 'sudo apt-get install git') and re-run, or run from a cloned repository."
                    exit 1
                fi
            fi
            log "Cloning from GitHub..."
            if [ -d "$WORK_DIR" ]; then
                rm -rf "$WORK_DIR"
            fi
            git clone --quiet "$REPO_URL" "$WORK_DIR"
            git -C "$WORK_DIR" fetch --quiet --tags --prune origin
            # origin/<ref> first so a branch lands on its latest tip; fall
            # back to <ref> for tags / commits (see the update path above).
            git -C "$WORK_DIR" checkout --quiet --detach "origin/$INSTALL_REF" 2>/dev/null \
                || git -C "$WORK_DIR" checkout --quiet --detach "$INSTALL_REF" 2>/dev/null \
                || git -C "$WORK_DIR" checkout --quiet "$INSTALL_REF"
        fi
    fi
fi

# =====================================================================
# Mandatory venv (all modes).
#
# Python ALWAYS runs from a private venv. Create it; if creation fails
# because python3-venv / ensurepip is missing, install that one package
# via the distro pkg mgr (sudo) in R/A/C, then retry; if still failing,
# error out (the ERR trap rolls back on a fresh install).
# =====================================================================
create_venv() {
    rm -rf "$WORK_VENV_DIR"
    python3 -m venv "$WORK_VENV_DIR"
}

log "Creating private virtualenv at $WORK_VENV_DIR ..."
if ! create_venv 2>/dev/null; then
    if [ -n "$WINPODX_SKIP_DEPS" ]; then
        err "python3 -m venv failed and --skip-deps is set."
        err "Install your distro's python3-venv / ensurepip package and re-run."
        exit 1
    fi
    warn "venv creation failed — likely a missing python3-venv / ensurepip. Installing it..."
    install_pkg "python3-venv" || true
    if ! create_venv; then
        err "venv creation still failing after installing $(pkg_name python3-venv)."
        err "Install your distro's python3-venv / ensurepip package manually and re-run."
        exit 1
    fi
fi

# Upgrade pip/setuptools/wheel in the venv (quiet; non-fatal cosmetics).
"$WORK_VENV_PY" -m pip install --quiet --upgrade pip setuptools wheel || \
    warn "pip self-upgrade failed; continuing with the bundled pip."

# Install winpodx itself from the in-place source tree. This resolves
# winpodx's own declared runtime deps (tomli on 3.9/3.10 via the
# python_version marker) from pyproject. We then add the reverse-open
# icon deps (cairosvg + pyxdg) and, unless --no-gui, PySide6 — pinned to
# the same ranges pyproject declares so we don't invent versions.
log "Installing WinPodX into the venv (pip install '$WORK_DIR')..."
if [ -n "$WINPODX_NO_GUI" ]; then
    # Headless: winpodx core + reverse-open icon quality, no PySide6.
    "$WORK_VENV_PY" -m pip install --quiet "${WORK_DIR}[reverse-open]"
    log "Headless install (--no-gui): PySide6 skipped."
else
    # Full: winpodx core + reverse-open + GUI.
    "$WORK_VENV_PY" -m pip install --quiet "${WORK_DIR}[gui,reverse-open]"
fi

# Belt-and-suspenders: ensure cairosvg + pyxdg are present even if a
# future pyproject reshuffle moves them out of the reverse-open extra.
# These two drive SVG / themed app-icon conversion for reverse-open.
if ! "$WORK_VENV_PY" -c "import cairosvg, xdg" >/dev/null 2>&1; then
    log "Ensuring reverse-open icon deps (cairosvg + pyxdg) in the venv..."
    "$WORK_VENV_PY" -m pip install --quiet "cairosvg>=2.7,<3.0" "pyxdg>=0.27,<1.0" || \
        warn "cairosvg/pyxdg install into venv failed; SVG/themed icons will use a placeholder."
fi

# =====================================================================
# Atomic swap-in (#716-audit follow-up, upgrade only).
#
# The staged tree + venv at $WORK_DIR built successfully above. Move the
# live install aside, move the staged one into place, then drop the aside
# copy. SWAP_IN_PROGRESS marks the narrow window between those two moves so
# rollback() (above) can restore the aside copy if something interrupts us
# right there; UPGRADE_SWAP_DONE marks the swap as complete so a LATER
# failure doesn't also claim the previous install was "not touched".
# =====================================================================
if [ "$WORK_DIR" != "$INSTALL_DIR" ]; then
    log "Swapping in the upgraded install..."
    INSTALL_DIR_ASIDE="$INSTALL_DIR.old"
    rm -rf "$INSTALL_DIR_ASIDE" 2>/dev/null || true
    # The upgrade flag is keyed off the config, which a non-purge uninstall
    # KEEPS while removing the install dir — so we can be on the "upgrade" path
    # with no INSTALL_DIR to move aside. Only stash it (and arm the mid-swap
    # rollback) when it actually exists; otherwise this is a fresh drop-in.
    if [ -d "$INSTALL_DIR" ]; then
        mv "$INSTALL_DIR" "$INSTALL_DIR_ASIDE"
        SWAP_IN_PROGRESS=1
    fi
    mv "$WORK_DIR" "$INSTALL_DIR"
    SWAP_IN_PROGRESS=0
    UPGRADE_SWAP_DONE=1
    rm -rf "$INSTALL_DIR_ASIDE" 2>/dev/null || true
    log "Upgrade swapped in; previous install removed."
fi

# --- Load Windows container image from local tar (--image-tar) ---
# Runs AFTER the winpodx source is in place so the rest of the install
# can still proceed if the load fails (first-boot would pull from the
# registry as a fallback — warn but don't abort).
if [ -n "$WINPODX_IMAGE_TAR" ]; then
    log "Loading Windows container image from $WINPODX_IMAGE_TAR..."
    if command -v podman >/dev/null 2>&1; then
        podman load -i "$WINPODX_IMAGE_TAR" || warn "image load failed; first boot may try the registry"
    elif command -v docker >/dev/null 2>&1; then
        docker load -i "$WINPODX_IMAGE_TAR" || warn "image load failed; first boot may try the registry"
    else
        warn "neither podman nor docker found; cannot load image tar"
    fi
fi

# --- Create launcher script ---
# v2: exec the VENV's python, not system python3 + PYTHONPATH. No system
# python pollution; winpodx + its deps live entirely under the venv.
cat > "$LAUNCHER" << 'LAUNCHER_EOF'
#!/usr/bin/env bash
WINPODX_DIR="$HOME/.local/bin/winpodx-app"
exec "$WINPODX_DIR/.venv/bin/python" -m winpodx "$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"

# --- Create 'winpodx' command (symlink to launcher) ---
# #716-audit follow-up: don't blindly clobber a pre-existing `winpodx` entry
# point at $SYMLINK -- e.g. `pip install --user winpodx` / pipx would put a
# real launcher script there, and `ln -sfn` overwriting it destroys that
# install with no way back. Back up anything that isn't already our own
# launcher symlink before replacing it; a fresh-install rollback restores the
# backup (see rollback() above). If this was a pip/pipx install, the user can
# instead remove it with `pip uninstall winpodx` and re-run.
if [ -e "$SYMLINK" ] || [ -L "$SYMLINK" ]; then
    if ! { [ -L "$SYMLINK" ] && [ "$(readlink "$SYMLINK" 2>/dev/null || true)" = "$LAUNCHER" ]; }; then
        SYMLINK_BACKUP="$SYMLINK.pre-install"
        warn "$SYMLINK already exists and isn't WinPodX's own launcher symlink"
        warn "(looks like a pip/pipx 'winpodx' install?). Backing it up to"
        warn "$SYMLINK_BACKUP before installing WinPodX's launcher there."
        warn "To remove the pip install instead: pip uninstall winpodx"
        rm -f "$SYMLINK_BACKUP" 2>/dev/null || true
        mv "$SYMLINK" "$SYMLINK_BACKUP"
        SYMLINK_BACKED_UP=1
    fi
fi
ln -sfn "$LAUNCHER" "$SYMLINK"

# Ensure ~/.local/bin is in PATH
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    warn "$HOME/.local/bin is not in PATH"
    warn "Add this to your ~/.bashrc or ~/.zshrc:"
    warn '  export PATH="$HOME/.local/bin:$PATH"'
fi

# --- PATH-shadow check (#752) ---
# A distro package (OBS RPM, AUR, .deb) can drop its own `winpodx` at
# /usr/bin, and this curl/venv install always lives at $SYMLINK
# ($HOME/.local/bin/winpodx). Only one of them is what the shell actually
# runs, decided purely by PATH order -- so if `command -v winpodx` doesn't
# resolve to the copy this run just installed/updated, tell the user which
# copy is shadowing which instead of leaving them to discover it via a
# version that silently never changes (see uninstall.sh's find_winpodx_bin
# for the same PATH-precedence concern on the removal side).
RESOLVED_WINPODX="$(command -v winpodx 2>/dev/null || true)"
if [ -n "$RESOLVED_WINPODX" ] && [ "$RESOLVED_WINPODX" != "$SYMLINK" ]; then
    warn "PATH resolves 'winpodx' to $RESOLVED_WINPODX, not $SYMLINK (the copy this run just installed/updated)."
    warn "That other copy is what actually runs. Remove it, or reorder PATH so $HOME/.local/bin comes first, for this install to take effect."
fi

# --- Run setup ---
# --manual / WINPODX_MANUAL=1: skip setup + the entire provisioning
# chain below (wait-ready / migrate / discovery / reverse-open). The
# binary + venv are installed, but the Windows VM stays unprovisioned
# until the user picks one of the first-run prompt options on the next
# `winpodx` invocation (CLI Y/C/n or GUI modal -- #255 PR 1).
if [ "${WINPODX_MANUAL:-0}" = "1" ]; then
    log "Manual mode (--manual / WINPODX_MANUAL=1 / --backend manual) — skipping setup + Windows provisioning."
    log "  Run 'winpodx setup' to finish setup (or 'winpodx gui' for the graphical first-run with auto / customize / skip)."
else
    log "Running winpodx setup..."
    # 0.6.0 item B: --create-only is gone. setup writes config + creates the
    # container; the post-create chain runs once via `winpodx provision`
    # (below) — the single source of truth shared with setup_cmd / migrate /
    # pending.resume. WINPODX_NO_PROVISION makes setup itself skip its own
    # full-provision tail so install.sh's explicit `winpodx provision` is the
    # only run (setup would otherwise also run finish_provisioning).
    SETUP_ARGS=(--non-interactive)
    if [ -n "$WINPODX_BACKEND" ] && [ "$WINPODX_BACKEND" != "manual" ]; then
        SETUP_ARGS+=(--backend "$WINPODX_BACKEND")
        log "Backend: $WINPODX_BACKEND"
    fi
    if [ -n "$WINPODX_WIN_VERSION" ]; then
        SETUP_ARGS+=(--win-version "$WINPODX_WIN_VERSION")
        log "Installing Windows edition: $WINPODX_WIN_VERSION"
    fi
    if [ -n "$WINPODX_STORAGE_DIR" ]; then
        SETUP_ARGS+=(--storage-path "$WINPODX_STORAGE_DIR")
        log "Storage location: $WINPODX_STORAGE_DIR"
    fi
    # #647: hand the local ISO to setup so it stages <storage>/custom.iso
    # BEFORE compose-up (dockur needs it present when the container boots).
    if [ -n "$WINPODX_WIN_ISO" ]; then
        SETUP_ARGS+=(--win-iso "$WINPODX_WIN_ISO")
        log "Local Windows ISO: $WINPODX_WIN_ISO"
    fi
    # Persist the resolved FreeRDP source so the launcher honours it
    # (cfg.rdp.freerdp_source). Skip "auto" — that's the default.
    if [ -n "${WINPODX_FREERDP_SOURCE:-}" ] && [ "$WINPODX_FREERDP_SOURCE" != "auto" ]; then
        SETUP_ARGS+=(--freerdp-source "$WINPODX_FREERDP_SOURCE")
        log "FreeRDP source: $WINPODX_FREERDP_SOURCE"
    fi
    # WINPODX_NO_PROVISION=1: setup creates the container but skips its own
    # full-provision tail; install.sh runs the chain once via the explicit
    # `winpodx provision` call below (0.6.0 item B, replaces --create-only).
    #
    # #716-audit follow-up: this used to redirect all output to /dev/null and
    # swallow the exit code with `|| true`, so a failed `winpodx setup` (bad
    # backend, compose error, ...) was completely silent and the unconditional
    # "installed" banner at the bottom still claimed everything worked. Capture
    # the output instead and check the exit code so a failure is visible and
    # the closing banner reflects reality.
    SETUP_OUT="$(mktemp)"
    if WINPODX_NO_PROVISION=1 "$VENV_PY" -m winpodx setup "${SETUP_ARGS[@]}" >"$SETUP_OUT" 2>&1; then
        SETUP_OK=1
    else
        SETUP_OK=0
        err "winpodx setup failed. Last output:"
        tail -n 20 "$SETUP_OUT" | sed 's/^/    /' >&2
        warn "Setup did not finish -- run \`winpodx setup\` manually to retry (or see the full error above)."
    fi
    rm -f "$SETUP_OUT"
fi

# NOTE: --win-iso staging now happens INSIDE `winpodx setup` (passed via
# SETUP_ARGS above), before compose-up, so dockur finds custom.iso when the
# container boots (#647). It used to be staged here, after setup had already
# started the container + dockur had begun downloading — too late.

# --- Install winpodx GUI desktop entry & icon ---
mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
cp "$INSTALL_DIR/data/winpodx.desktop" "$DESKTOP_DIR/winpodx.desktop"
cp "$INSTALL_DIR/data/winpodx-icon.svg" "$ICON_DIR/winpodx.svg"

# Ensure index.theme exists (required for KDE icon cache)
if [ ! -f "$ICON_BASE/index.theme" ]; then
    if [ -f /usr/share/icons/hicolor/index.theme ]; then
        cp /usr/share/icons/hicolor/index.theme "$ICON_BASE/index.theme"
    else
        cat > "$ICON_BASE/index.theme" << 'INDEXEOF'
[Icon Theme]
Name=Hicolor
Comment=Fallback icon theme
Hidden=true
Directories=scalable/apps

[scalable/apps]
Size=64
MinSize=1
MaxSize=512
Context=Applications
Type=Scalable
INDEXEOF
    fi
fi

gtk-update-icon-cache -f -t "$ICON_BASE" 2>/dev/null || true
# Rebuild KDE Plasma sycoca cache
kbuildsycoca6 --noincremental 2>/dev/null || kbuildsycoca5 --noincremental 2>/dev/null || true
log "Installed WinPodX GUI launcher and icon"

# v0.1.9: bundled app profiles were dropped. The app menu now populates
# automatically the first time the Windows pod boots — `winpodx app run
# desktop` starts the pod, the provisioner auto-fires discovery, and the
# discovered apps + their real Windows-extracted icons land in the menu.
# Manual trigger any time: `winpodx app refresh`.

# In manual mode the provisioning chain below is skipped entirely.
if [ "${WINPODX_MANUAL:-0}" = "1" ]; then
    ROLLBACK_ARMED=0
    cleanup_install_marker
    trap - EXIT ERR INT TERM
    echo ""
    echo " Location: $INSTALL_DIR"
    echo " Command:  winpodx"
    echo ""
    echo " Manual mode — Windows VM was NOT provisioned."
    echo ""
    echo " Next step (pick one):"
    echo "   winpodx gui          # GUI first-run modal: auto / customize / skip"
    echo "   winpodx setup        # Run setup directly (non-interactive auto)"
    echo "   winpodx setup --customize"
    echo "                        # Run setup wizard (pick every knob)"
    echo ""
    log "Installation complete!"
    exit 0
fi

# --- Finish provisioning the Windows VM (0.6.0 item B) ---
# The wait-ready → agent-settle → apply-fixes → discovery → reverse-open
# chain used to be ~140 lines of bash here (with its own /health curl poll,
# 6× `app refresh` retry loop, and host-open listener-start). It is now the
# single `winpodx provision` command — the same chain setup_cmd, migrate,
# and pending.resume run — so there is exactly one place to fix a bug and
# one shared progress surface. install.sh just forwards $WINPODX_VERBOSE.
#
# Fresh vs upgrade are DIFFERENT flows (a blind `provision`-for-both was the
# regression in the first cut of item B):
#
#   * FRESH (no prior config): `winpodx provision --require-agent`. The new
#     container's install.bat already laid down the current guest scripts, so
#     no guest_sync is needed. --require-agent (#271) makes discovery/apply
#     refuse the FreeRDP fallback and DEFER (exit 5 -> pending) rather than
#     race FreeRDP into install.bat's autologon session while the agent is
#     still flapping during first boot.
#
#   * UPGRADE (prior config existed): `winpodx migrate`. migrate FIRST pushes
#     the refreshed guest scripts into the existing guest (guest_sync) + pins
#     the image + shows release notes, THEN runs the same apply -> discovery ->
#     reverse-open chain. Skipping migrate (as the first item-B cut did) left
#     upgraded guests running STALE agent.ps1 / OEM scripts.
#
# Both commands stream the live boot progress (the #126 wget-ETA dynamic
# deadline + self-erasing line) and both honour the .pending_setup safety net.
#
# Exit codes (both):
#   0  — chain succeeded
#   4  — Windows guest didn't become responsive in time (wait-ready timeout)
#   5  — deferred (agent-first: agent never came up; resume via pending)
# 4 and 5 are non-fatal: the .pending_setup machinery (utils/pending.py) is the
# safety net — the next `winpodx` CLI / GUI launch resumes the chain.
# Skip the whole step with WINPODX_NO_WAIT=1 (CI / non-interactive setups).
# #716-audit follow-up: use CONFIG_HOME (honours XDG_CONFIG_HOME) instead of
# hardcoding "$HOME/.config" -- otherwise resume/detection breaks for anyone
# with a custom XDG_CONFIG_HOME.
PENDING_FILE="$CONFIG_HOME/winpodx/.pending_setup"

if [ -f "$CONFIG_HOME/winpodx/winpodx.toml" ] && [ "${WINPODX_NO_WAIT:-}" != "1" ]; then
    PROVISION_OUT="$(mktemp)"
    if [ "$IS_FRESH_INSTALL" = "1" ]; then
        log "Finishing Windows provisioning (wait-ready + apply-fixes + discovery + reverse-open)..."
        log "  Fresh install downloads ~7.5GB Windows ISO + runs Sysprep + OEM apply (auto-extends on slow links)."
        log "  Subsequent installs reuse the cached ISO and finish in 2-5 min."
        PROVISION_CMD=(provision --require-agent)
        [ -n "$WINPODX_VERBOSE" ] && PROVISION_CMD+=(--verbose)
    else
        log "Upgrade detected — running migration (sync guest scripts + apply-fixes + discovery + reverse-open)..."
        log "  Re-uses the existing Windows install; no ISO re-download."
        PROVISION_CMD=(migrate --non-interactive)
        [ -n "$WINPODX_VERBOSE" ] && PROVISION_CMD+=(--verbose)
    fi
    # PYTHONUNBUFFERED=1 keeps the streamed per-stage progress line-buffered
    # when piped. We inspect the rc explicitly and tee the output so the
    # `no such container` partial-uninstall case is still detectable.
    #
    # Disarm the ERR-trap rollback around this call. `set +e` alone is NOT
    # enough: bash fires the ERR trap on a failing *pipeline* even with
    # errexit off, so a deferred (exit 5) or slow (exit 4) provision —
    # where Windows is already downloaded, booted, and recoverable via the
    # pending machinery — would otherwise roll back the whole fresh install
    # before the rc handling below ever runs. We branch on the rc ourselves.
    set +e
    trap - ERR
    PYTHONUNBUFFERED=1 "$SYMLINK" "${PROVISION_CMD[@]}" 2>&1 \
        | tee "$PROVISION_OUT"
    PROVISION_RC="${PIPESTATUS[0]}"
    trap rollback_and_exit_err ERR
    set -e
    # Ctrl+C / SIGTERM: bail out. The traps fire in the parent shell too, but
    # this covers the piped-install case where the child died from the signal
    # and the parent didn't see it directly.
    if [ "$PROVISION_RC" -eq 130 ] || [ "$PROVISION_RC" -eq 143 ]; then
        err "Install cancelled (winpodx ${PROVISION_CMD[0]} returned $PROVISION_RC)."
        err "Re-run install.sh to continue from where you left off."
        rm -f "$PROVISION_OUT"
        ROLLBACK_ARMED=0
        cleanup_install_marker
        trap - EXIT ERR INT TERM
        exit "$PROVISION_RC"
    fi
    if [ "$PROVISION_RC" -eq 4 ] || [ "$PROVISION_RC" -eq 5 ]; then
        # DEFERRED, not failed. Windows is downloaded, booted, and the pod is
        # up; only the agent-first discovery deferred (5) or wait-ready ran
        # long (4). Rolling back here would throw away ~15 min of ISO download
        # + boot for a state that finishes itself. Record the remaining steps
        # as pending — they auto-resume on the next `winpodx` invocation — and
        # treat the install as a SUCCESS so the artifacts stay in place.
        warn "Windows is installed and the pod is up, but app discovery deferred"
        warn "until the in-guest agent finishes coming up (it can lag a minute"
        warn "after the first-boot reboot). This is not a failure — it finishes"
        warn "automatically on your next \`winpodx\` run, or force it now with:"
        warn "  winpodx app refresh"
        mkdir -p "$CONFIG_HOME/winpodx"
        printf 'wait_ready\nmigrate\ndiscovery\n' > "$PENDING_FILE"
        warn "Pending steps recorded at $PENDING_FILE."
    elif [ "$PROVISION_RC" -ne 0 ]; then
        if grep -q "no such container" "$PROVISION_OUT"; then
            warn "Container is missing. This usually means the container was never"
            warn "created in the first place -- most often a missing compose provider"
            warn "(podman-compose / the \`podman compose\` plugin). Check for one and"
            warn "re-run setup first:"
            warn "  command -v podman-compose || sudo apt install podman-compose   # or dnf/zypper"
            warn "  winpodx setup"
            warn "If that's not it (e.g. a partial uninstall), recover with a full reinstall:"
            warn '  curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge'
            warn '  curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --main'
        else
            warn "Provisioning didn't complete in time (Windows first-boot can run long"
            warn "on slow links -- see #126). Marking remaining steps as pending — they"
            warn "auto-resume on the next \`winpodx\` CLI invocation or when you open the GUI."
            mkdir -p "$CONFIG_HOME/winpodx"
            # wait_ready first so pending.resume re-runs the full chain.
            printf 'wait_ready\nmigrate\ndiscovery\n' > "$PENDING_FILE"
            warn "Pending steps recorded at $PENDING_FILE."
        fi
    else
        rm -f "$PENDING_FILE"
    fi
    rm -f "$PROVISION_OUT"
fi

# --- Done. Disarm rollback (success). ---
ROLLBACK_ARMED=0
cleanup_install_marker
trap - EXIT ERR INT TERM

INSTALLED_VER="$("$VENV_PY" -c 'import winpodx; print(winpodx.__version__)' 2>/dev/null || echo '?')"
GUI_LABEL="yes"
[ -n "$WINPODX_NO_GUI" ] && GUI_LABEL="no (--no-gui)"

# Apply the update to an already-RUNNING WinPodX immediately. A long-lived tray
# or GUI keeps the OLD code in memory, so on an upgrade the new code otherwise
# only takes effect after the user manually restarts or re-logs in (this caused
# repeated "the fix didn't work" confusion). Restart ONLY the winpodx APP
# processes by targeting the 'tray' / 'gui' entrypoints precisely.
#
# NEVER use a bare `pkill -f winpodx`: that also matches conmon (the container
# monitor for the 'winpodx-windows' pod) and would tear down the running
# Windows VM + every live RDP session. The 'tray'/'gui' patterns below do not
# match conmon, the QEMU process, or this installer's own provision/migrate run.
# A fresh install has nothing running here, so this is a no-op.
if command -v pgrep >/dev/null 2>&1; then
    _wpx_was_tray=0
    _wpx_was_gui=0
    pgrep -f 'winpodx tray' >/dev/null 2>&1 && _wpx_was_tray=1
    pgrep -f 'winpodx gui' >/dev/null 2>&1 && _wpx_was_gui=1
    if [ "$_wpx_was_tray" = 1 ] || [ "$_wpx_was_gui" = 1 ]; then
        log "Restarting WinPodX (tray/GUI) to apply the update (the pod keeps running)…"
        pkill -f 'winpodx tray' 2>/dev/null || true
        pkill -f 'winpodx gui' 2>/dev/null || true
        sleep 1
        # If the GUI was up, relaunch it (the GUI auto-spawns its own tray);
        # otherwise just relaunch the tray. Avoids a double tray.
        if [ "$_wpx_was_gui" = 1 ]; then
            setsid "$SYMLINK" gui >/dev/null 2>&1 </dev/null &
        elif [ "$_wpx_was_tray" = 1 ]; then
            setsid "$SYMLINK" tray >/dev/null 2>&1 </dev/null &
        fi
    fi
fi

echo ""
# #716-audit follow-up: only show the all-good banner when `winpodx setup`
# actually succeeded (SETUP_OK, set above) -- a failure already printed the
# error + a "run winpodx setup manually" hint at the point it happened.
if [ "$SETUP_OK" -eq 1 ]; then
    echo -e "${GREEN}  ┌─ WinPodX installed ──────────────────────────────────────${NC}"
    printf "  │  version   %s\n" "$INSTALLED_VER"
    printf "  │  backend   %s\n" "${WINPODX_BACKEND:-podman}"
    printf "  │  GUI       %s\n" "$GUI_LABEL"
    printf "  │  location  %s\n" "$INSTALL_DIR  (private venv)"
    echo -e "${GREEN}  └──────────────────────────────────────────────────────────${NC}"
    echo ""
    echo "  Next steps:"
    if [ -z "$WINPODX_NO_GUI" ]; then
        echo "    winpodx gui                # open the GUI — system tray + app manager"
    fi
    echo "    winpodx app run desktop    # the full Windows desktop"
    echo "    winpodx app refresh        # rescan the apps installed in Windows"
    echo "    winpodx doctor             # health check if something looks off"
    echo "    winpodx --help             # every command"
    echo ""
    echo "  Your Windows apps are already in the application menu — click any one"
    echo "  and WinPodX handles the pod, the RDP session, and the window for you."
    echo ""
    log "Installation complete!"
else
    echo "  WinPodX binary + venv installed at: $INSTALL_DIR  (private venv)"
    echo "  But \`winpodx setup\` did not finish — see the error output above."
    echo "  Fix the underlying issue, then run: winpodx setup"
    echo ""
    warn "Installation finished with a setup error — run \`winpodx setup\` manually."
fi
