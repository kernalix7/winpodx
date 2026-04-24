#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# winpodx installer
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
#   or: ./install.sh [--source PATH] [--image-tar PATH] [--skip-deps] [--help]
#
# Installs winpodx to ~/.local/bin/winpodx-app/ and creates launcher script.
# No pip, no venv, no root required.
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
###############################################################################

INSTALL_DIR="$HOME/.local/bin/winpodx-app"
LAUNCHER="$HOME/.local/bin/winpodx-run"
REPO_URL="https://github.com/kernalix7/winpodx.git"

# Local-path overrides (env or flag). Flags take precedence over env.
WINPODX_SOURCE="${WINPODX_SOURCE:-}"
WINPODX_IMAGE_TAR="${WINPODX_IMAGE_TAR:-}"
WINPODX_SKIP_DEPS="${WINPODX_SKIP_DEPS:-}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[winpodx]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

usage() {
    sed -n '4,22p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || cat <<'USAGE_EOF'
winpodx installer — see install.sh header for full usage.

Flags:
  --source PATH       Copy from local repo instead of git clone
  --image-tar PATH    Load container image from local tar
  --skip-deps         Skip distro dependency install
  -h, --help          Print this help and exit
USAGE_EOF
}

# --- Parse flags (must precede any work) ---
while [ $# -gt 0 ]; do
    case "$1" in
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
log "Detected distro: $DISTRO"

# Map generic dependency names to distro-specific package names
pkg_name() {
    local dep="$1"
    case "$DISTRO" in
        opensuse*|sles)
            case "$dep" in
                python3)        echo "python3" ;;
                podman)         echo "podman" ;;
                podman-compose) echo "podman-compose" ;;
                freerdp)        echo "freerdp" ;;
                kvm)            echo "qemu-kvm" ;;
            esac ;;
        fedora|rhel|centos|rocky|alma)
            case "$dep" in
                python3)        echo "python3" ;;
                podman)         echo "podman" ;;
                podman-compose) echo "podman-compose" ;;
                freerdp)        echo "freerdp" ;;
                kvm)            echo "qemu-kvm" ;;
            esac ;;
        ubuntu|debian|linuxmint|pop)
            case "$dep" in
                python3)        echo "python3" ;;
                podman)         echo "podman" ;;
                podman-compose) echo "podman-compose" ;;
                freerdp)        echo "freerdp2-x11" ;;
                kvm)            echo "qemu-kvm" ;;
            esac ;;
        arch|manjaro|endeavouros)
            case "$dep" in
                python3)        echo "python" ;;
                podman)         echo "podman" ;;
                podman-compose) echo "podman-compose" ;;
                freerdp)        echo "freerdp" ;;
                kvm)            echo "qemu-full" ;;
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

# --- Check / install dependencies ---
log "Checking dependencies..."

MISSING=()

if ! command -v python3 >/dev/null 2>&1; then
    MISSING+=("python3")
fi

if ! command -v podman >/dev/null 2>&1; then
    MISSING+=("podman")
fi

if ! command -v podman-compose >/dev/null 2>&1 && ! podman compose version >/dev/null 2>&1; then
    MISSING+=("podman-compose")
fi

# FreeRDP check
FREERDP_OK=false
for cmd in xfreerdp3 xfreerdp wlfreerdp3 wlfreerdp; do
    if command -v "$cmd" >/dev/null 2>&1; then
        FREERDP_OK=true
        break
    fi
done
if [ "$FREERDP_OK" = false ]; then
    MISSING+=("freerdp")
fi

if [ ! -e /dev/kvm ]; then
    warn "/dev/kvm not found (KVM required for Windows container)"
    warn "Enable virtualization in BIOS"
    MISSING+=("kvm")
fi

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
    echo -n "  Proceed with installation? (Y/n): "
    read -r answer
    if [[ "$answer" =~ ^[Nn] ]]; then
        err "Aborted. Install dependencies manually and try again."
        exit 1
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

# winpodx uses only stdlib on 3.11+; on 3.9/3.10 tomli backfills tomllib.

# --- Check Python version ---
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]; }; then
    err "Python 3.9+ required (found $PY_VERSION)"
    exit 1
fi
# On 3.9/3.10 tomllib is not in stdlib — install tomli via the system package
# manager if available so the winpodx runtime import doesn't fail.
if [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; then
    if ! python3 -c "import tomli" >/dev/null 2>&1; then
        if [ -n "$WINPODX_SKIP_DEPS" ]; then
            err "Python $PY_VERSION needs tomli but --skip-deps is set and it's missing."
            err "Install tomli manually (e.g., 'pip install tomli') and re-run."
            exit 1
        fi
        log "Python $PY_VERSION needs tomli (stdlib tomllib arrived in 3.11). Installing..."
        if command -v zypper >/dev/null 2>&1; then
            sudo zypper install -y python3-tomli || warn "tomli install failed; winpodx may fail to start"
        elif command -v dnf >/dev/null 2>&1; then
            sudo dnf install -y python3-tomli || warn "tomli install failed; winpodx may fail to start"
        elif command -v apt-get >/dev/null 2>&1; then
            sudo apt-get install -y python3-tomli || warn "tomli install failed; winpodx may fail to start"
        elif command -v pacman >/dev/null 2>&1; then
            sudo pacman -S --noconfirm python-tomli || warn "tomli install failed; winpodx may fail to start"
        fi
    fi
fi
log "Python $PY_VERSION OK"

# --- Clone, update, or copy winpodx source ---
mkdir -p "$(dirname "$INSTALL_DIR")"

copy_from_local() {
    local src="$1"
    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
    fi
    mkdir -p "$INSTALL_DIR"
    for item in src data config scripts install.sh uninstall.sh pyproject.toml README.md LICENSE; do
        if [ -e "$src/$item" ]; then
            cp -r "$src/$item" "$INSTALL_DIR/"
        fi
    done
}

if [ -n "$WINPODX_SOURCE" ]; then
    # --source wins over every other path; no git at all.
    log "Copying winpodx from --source: $WINPODX_SOURCE"
    copy_from_local "$WINPODX_SOURCE"
elif [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing installation..."
    git -C "$INSTALL_DIR" pull --quiet
else
    # If running from repo, copy only needed files (skip .venv, .git, etc.).
    # When piped via `curl ... | bash`, bash reads from stdin and BASH_SOURCE[0]
    # is unset — `set -u` would abort here without the default expansion. Fall
    # through to the git-clone path when there is no local source tree.
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
            err "git is required for remote install. Install git first or run from the repository."
            exit 1
        fi
        log "Cloning from GitHub..."
        if [ -d "$INSTALL_DIR" ]; then
            rm -rf "$INSTALL_DIR"
        fi
        git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    fi
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
cat > "$LAUNCHER" << 'LAUNCHER_EOF'
#!/usr/bin/env bash
WINPODX_DIR="$HOME/.local/bin/winpodx-app"
export PYTHONPATH="$WINPODX_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m winpodx "$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"

# --- Create 'winpodx' command (symlink to launcher) ---
ln -sfn "$LAUNCHER" "$HOME/.local/bin/winpodx"

# Ensure ~/.local/bin is in PATH
if ! echo "$PATH" | grep -q "$HOME/.local/bin"; then
    warn "$HOME/.local/bin is not in PATH"
    warn "Add this to your ~/.bashrc or ~/.zshrc:"
    warn '  export PATH="$HOME/.local/bin:$PATH"'
fi

# --- Run setup ---
log "Running winpodx setup..."
export PYTHONPATH="$INSTALL_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m winpodx setup --non-interactive 2>/dev/null || true

# --- Install winpodx GUI desktop entry & icon ---
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_BASE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
ICON_DIR="$ICON_BASE/scalable/apps"
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
log "Installed winpodx GUI launcher and icon"

# --- Register app desktop entries ---
log "Registering apps in desktop menu..."
python3 -c "
import sys
sys.path.insert(0, '$INSTALL_DIR/src')
from winpodx.core.provisioner import _install_bundled_apps_if_needed, _ensure_desktop_entries
_install_bundled_apps_if_needed()
_ensure_desktop_entries()
" 2>/dev/null || true

# --- Post-upgrade migration wizard ---
# If an existing config is present this is an upgrade, not a fresh
# install. Run the migrate wizard so the user sees new-version release
# notes and can opt into app discovery. Opt out with WINPODX_NO_MIGRATE=1.
# `|| true` keeps install.sh's exit code clean if migrate fails.
if [ -f "$HOME/.config/winpodx/winpodx.toml" ] && [ "${WINPODX_NO_MIGRATE:-}" != "1" ]; then
    log "Running post-upgrade migration wizard..."
    "$HOME/.local/bin/winpodx" migrate || true
fi

# --- Done ---
echo ""
echo " Location: $INSTALL_DIR"
echo " Command:  winpodx"
echo ""
echo " Usage:"
echo "   winpodx app run word           # Launch Word"
echo "   winpodx app run excel          # Launch Excel"
echo "   winpodx app run desktop        # Full Windows desktop"
echo "   winpodx setup                  # Reconfigure"
echo ""
echo " Apps are in your application menu. Just click and go."
echo ""
log "Installation complete!"
