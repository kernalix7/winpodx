#!/usr/bin/env bash
set -euo pipefail

###############################################################################
# winpodx installer
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
#   or: ./install.sh [--main] [--ref TAG] [--source PATH] [--image-tar PATH]
#                    [--skip-deps] [--help]
#
# Installs winpodx to ~/.local/bin/winpodx-app/ and creates launcher script.
# No pip, no venv, no root required.
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
###############################################################################

INSTALL_DIR="$HOME/.local/bin/winpodx-app"
LAUNCHER="$HOME/.local/bin/winpodx-run"
REPO_URL="https://github.com/kernalix7/winpodx.git"
REPO_API="https://api.github.com/repos/kernalix7/winpodx"

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

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[winpodx]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
err()  { echo -e "${RED}[error]${NC} $*" >&2; }

usage() {
    sed -n '4,28p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || cat <<'USAGE_EOF'
winpodx installer — see install.sh header for full usage.

Flags:
  --main              Install from git main HEAD (development)
  --ref TAG           Install a specific tag/branch/commit
  --source PATH       Copy from local repo instead of git clone
  --image-tar PATH    Load container image from local tar
  --skip-deps         Skip distro dependency install
  --win-version VER   Windows edition for fresh installs
                      (11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 |
                       tiny10 | 2025 | 2022 | 2019 | 2016 — see
                       docs/ARCHITECTURE.md for custom ISOs)
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
        warn "Untested host architecture: $ARCH"
        warn "winpodx is packaged for x86_64 and aarch64. The container image"
        warn "picker will fall through to the x86_64 default; pod start will"
        warn "likely fail at QEMU. Proceed only if you know what you're doing."
        ;;
esac
log "Detected arch: $ARCH_LABEL"

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
if command -v rpm-ostree >/dev/null 2>&1; then
    log "Detected rpm-ostree — Atomic Fedora install path."
    if [ ! -f /etc/os-release ]; then
        err "/etc/os-release missing; can't determine Fedora version for OBS repo selection."
        exit 1
    fi
    # /etc/os-release was already sourced above for distro detection; re-source
    # here defensively so VERSION_ID is in scope even if the function above
    # ran in a subshell on some bash versions.
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
    log "Layering winpodx via rpm-ostree install --apply-live (one transaction)..."
    if sudo rpm-ostree install --apply-live --idempotent winpodx; then
        log "winpodx layered into the booted deployment — no reboot required."
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
fi

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
    # Pre-install hint. A surprising fraction of user bug reports start
    # here -- the package install loop below will run successfully on
    # most distros because qemu / qemu-kvm is already present, and then
    # the container start later silently fails because hardware virt
    # is off in BIOS. Print the BIOS / module / group check now so the
    # user can stop, fix the actual cause, and re-run -- instead of
    # filing a bug after the install "succeeds" but nothing works.
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
if [ ! -e /dev/kvm ]; then
    err "/dev/kvm still missing after package install."
    err ""
    err "Hardware virtualisation is required for winpodx. Likely causes:"
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

# Resolve the install ref. Default (empty WINPODX_REF) -> latest release.
# `--main` / WINPODX_REF=main bypasses the API call so an unreachable
# api.github.com still lets dev installs proceed.
resolve_ref() {
    if [ -n "$WINPODX_REF" ]; then
        echo "$WINPODX_REF"
        return
    fi
    if ! command -v curl >/dev/null 2>&1; then
        # Fall back to main if we can't query for the latest release.
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
    log "Copying winpodx from --source: $WINPODX_SOURCE"
    copy_from_local "$WINPODX_SOURCE"
else
    INSTALL_REF="$(resolve_ref)"
    if [ "$INSTALL_REF" = "main" ]; then
        log "Installing from git main (development)"
    else
        log "Installing release: $INSTALL_REF (use --main for development build)"
    fi

    if [ -d "$INSTALL_DIR/.git" ]; then
        log "Updating existing installation to $INSTALL_REF..."
        # Fetch tags + branches so checkout works for any ref.
        git -C "$INSTALL_DIR" fetch --quiet --tags --prune origin
        # Detach safely; works for tags, branches, and SHAs alike.
        git -C "$INSTALL_DIR" checkout --quiet --detach "$INSTALL_REF" \
            || git -C "$INSTALL_DIR" checkout --quiet "$INSTALL_REF"
        # If we're on a branch (e.g. main), pull to fast-forward.
        if [ "$INSTALL_REF" = "main" ]; then
            git -C "$INSTALL_DIR" reset --hard --quiet "origin/$INSTALL_REF"
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
                err "git is required for remote install. Install git first or run from the repository."
                exit 1
            fi
            log "Cloning from GitHub..."
            if [ -d "$INSTALL_DIR" ]; then
                rm -rf "$INSTALL_DIR"
            fi
            git clone --quiet "$REPO_URL" "$INSTALL_DIR"
            git -C "$INSTALL_DIR" fetch --quiet --tags --prune origin
            git -C "$INSTALL_DIR" checkout --quiet --detach "$INSTALL_REF" \
                || git -C "$INSTALL_DIR" checkout --quiet "$INSTALL_REF"
            if [ "$INSTALL_REF" = "main" ]; then
                git -C "$INSTALL_DIR" reset --hard --quiet "origin/$INSTALL_REF"
            fi
        fi
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
SETUP_ARGS=(--non-interactive)
if [ -n "$WINPODX_WIN_VERSION" ]; then
    SETUP_ARGS+=(--win-version "$WINPODX_WIN_VERSION")
    log "Installing Windows edition: $WINPODX_WIN_VERSION"
fi
python3 -m winpodx setup "${SETUP_ARGS[@]}" 2>/dev/null || true

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

# v0.1.9: bundled app profiles were dropped. The app menu now populates
# automatically the first time the Windows pod boots — `winpodx app run
# desktop` starts the pod, the provisioner auto-fires discovery, and the
# discovered apps + their real Windows-extracted icons land in the menu.
# Manual trigger any time: `winpodx app refresh`.

# --- Wait for Windows VM to finish first-boot setup ---
# v0.2.0.5: dockur Windows first-boot can take 5-10 minutes (Sysprep,
# OEM apply, account password set, RDP listener up). Without this gate
# the user just saw "Installation complete!" while Windows was still
# silently booting in the background, then had to wait again the first
# time they tried to launch an app. wait-ready surfaces the same wait
# up-front with [1/3] container → [2/3] RDP port → [3/3] activation
# progress + tailed container logs so the user can see what's happening.
# Skip with WINPODX_NO_WAIT=1 (CI / non-interactive setups).
# v0.2.1: track which steps haven't completed so the next CLI / GUI
# launch can auto-resume them. .pending_setup is a newline-separated
# list of step IDs (wait_ready, migrate, discovery). Empty / missing =
# install fully finished.
PENDING_FILE="$HOME/.config/winpodx/.pending_setup"
PENDING_STEPS=""
mark_pending() {
    PENDING_STEPS="${PENDING_STEPS}${1}
"
}

if [ -f "$HOME/.config/winpodx/winpodx.toml" ] && [ "${WINPODX_NO_WAIT:-}" != "1" ]; then
    log "Waiting for Windows VM to finish first-boot (up to 60 min)..."
    log "  Fresh install downloads ~7.5GB Windows ISO + runs Sysprep + OEM apply."
    log "  Subsequent installs reuse the cached ISO and finish in 2-5 min."
    # Capture wait-ready output so we can discriminate the "no such
    # container" failure (half-uninstalled state — config + compose
    # survived but the container is gone) from a generic timeout. The
    # `tee` shows live progress to the user; PIPESTATUS[0] is the
    # winpodx exit code (NOT tee's), and `set -o pipefail` is already
    # in effect from the script header so a non-zero rc would normally
    # abort install.sh — `|| true` keeps us going so we can inspect
    # the captured output.
    WAIT_READY_OUT="$(mktemp)"
    # PYTHONUNBUFFERED=1 forces Python's stdout to line-buffered even
    # when piped (otherwise the pipe to `tee` flips Python from
    # line-buffered to 4KB-block-buffered, batching minutes of progress
    # into a single flush at the end). Without this, `[1/3] container
    # ...`, the [container] log tail, and `OK ...` lines all arrive at
    # once when the step completes — see Task #45 / PR #143 regression.
    PYTHONUNBUFFERED=1 "$HOME/.local/bin/winpodx" pod wait-ready --timeout 3600 --logs 2>&1 \
        | tee "$WAIT_READY_OUT" || true
    WAIT_READY_RC="${PIPESTATUS[0]}"
    if [ "$WAIT_READY_RC" -ne 0 ]; then
        if grep -q "no such container" "$WAIT_READY_OUT"; then
            warn "Container is missing — likely from a partial uninstall."
            warn "Recover with a full reinstall:"
            warn '  curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge'
            warn '  curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --main'
        else
            warn "Windows first-boot didn't complete in 60 minutes."
            warn "Marking remaining steps as pending — they will auto-resume on next"
            warn "\`winpodx\` CLI invocation or when you open \`winpodx gui\`."
            mark_pending "wait_ready"
        fi
    fi
    rm -f "$WAIT_READY_OUT"
fi

# --- Post-install / upgrade migration wizard + discovery ---
#
# Both steps run AFTER wait-ready completes, but install.bat may still
# be in-flight inside the Windows guest (Sysprep + DNS / RDP / firewall /
# rdprrap install / launcher staging / agent spawn / final TermService
# cycle). install.bat is a FirstLogonCommands child of the autologon
# User session; opening a new RDP login from the host BEFORE install.bat
# finishes kicks that session because rdprrap multi-session isn't
# patched yet, so single-session enforcement is in effect. install.bat
# dies mid-stage and the agent never starts (kernalix7 hit this every
# smoke test 2026-05-02 through 2026-05-04 -- setup.log never created,
# C:\OEM\agent.ps1 not even copied).
#
# Defense: WINPODX_REQUIRE_AGENT=1 makes both migrate and app refresh
# refuse to fall back to FreeRDP RemoteApp when the guest agent isn't
# up yet. They exit with a "deferred" status; install.sh marks them
# pending so the next CLI / GUI launch resumes them once the agent has
# come up cleanly.
export WINPODX_REQUIRE_AGENT=1

# If an existing config is present this is an upgrade, not a fresh
# install. Run the migrate wizard so the user sees new-version release
# notes and can opt into app discovery. Opt out with WINPODX_NO_MIGRATE=1.
# `|| true` keeps install.sh's exit code clean if migrate fails.
if [ -f "$HOME/.config/winpodx/winpodx.toml" ] && [ "${WINPODX_NO_MIGRATE:-}" != "1" ]; then
    log "Running post-upgrade migration wizard..."
    "$HOME/.local/bin/winpodx" migrate || mark_pending "migrate"
fi

# --- Wait for agent to settle after migrate's apply chain ---
#
# migrate's apply chain ends with `_apply_multi_session`, which dispatches
# `rdprrap-activate.ps1 -Detached` via the agent. The detached PS then
# cycles TermService so the patched termwrap.dll loads, killing the
# agent's own RDP session in the process. dockur autologon retries within
# a few seconds → HKCU\Run fires → agent restarts. The whole bounce is
# typically under 15s on a healthy boot.
#
# Without this wait, `app refresh` below fires while the agent is mid-
# respawn (kernalix7 saw this on 2026-05-04 smoke: discovery deferred to
# pending right after migrate succeeded). Polling /health here means
# refresh runs against the resurrected agent and the menu populates
# before install.sh exits.
#
# 60s budget covers the cycle plus any slow first-boot autologon. Falls
# through silently after the budget so a genuinely-stuck agent doesn't
# block install.sh — refresh below will still defer to pending if needed.
if [ -f "$HOME/.config/winpodx/winpodx.toml" ] && [ "${WINPODX_NO_WAIT_AGENT:-}" != "1" ]; then
    log "Waiting for guest agent to settle after apply chain..."
    for _ in $(seq 1 30); do
        if curl -fsS --max-time 2 http://127.0.0.1:8765/health >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
fi

# --- Auto-discover apps ---
# v0.2.0.5: trigger discovery so the menu populates before install.sh
# exits.
#
# Retry up to 3 times with 10s spacing. Even after the curl /health wait
# above, the agent can still be in a transient "responsive but not yet
# stable" state right after install.bat's final TermService cycle —
# AgentTransport.health() couples /health with the host-side token check
# and the apply chain's vbs_launchers / multi_session steps may briefly
# disturb either side. kernalix7's 2026-05-05 smoke showed the first
# refresh attempt failing under WINPODX_REQUIRE_AGENT=1 even though the
# same command run manually 30s later succeeded with 58 apps. The retry
# loop closes that window so the menu populates before install.sh exits
# instead of needing a follow-up CLI / GUI launch to fire pending-resume.
#
# Final failure still records the step as pending so pending-resume
# stays as a safety net.
if [ -f "$HOME/.config/winpodx/winpodx.toml" ] && [ "${WINPODX_NO_DISCOVERY:-}" != "1" ]; then
    log "Discovering installed Windows apps..."
    discovery_ok=
    for attempt in 1 2 3; do
        if "$HOME/.local/bin/winpodx" app refresh 2>/dev/null; then
            discovery_ok=1
            break
        fi
        if [ "$attempt" -lt 3 ]; then
            log "  discovery attempt $attempt deferred (agent transitioning); retrying in 10s..."
            sleep 10
        fi
    done
    if [ -z "$discovery_ok" ]; then
        mark_pending "discovery"
    fi
fi

# --- Reverse-open auto-setup ---
# Linux apps in the Windows guest's "Open with..." menu (#48). The
# feature ships default-on (cfg.reverse_open.enabled = True), so a
# fresh install should produce a working menu without the user
# having to know `winpodx host-open` exists. Two-step:
#   1. Start the host-side listener daemon (idempotent — no-op if
#      already running).
#   2. `host-open refresh` — scans the host's .desktop entries,
#      filters to Linux defaults, generates per-app ICOs, stages
#      the manifest under ~/.local/share/winpodx/reverse-open/,
#      and (if the agent is reachable) pushes everything to the
#      guest where register-apps.ps1 writes the per-app
#      Applications\winpodx-<slug>.cmd + Start Menu shortcuts.
# Opt out via WINPODX_NO_REVERSE_OPEN=1 if the user wants to skip.
if [ -f "$HOME/.config/winpodx/winpodx.toml" ] && [ "${WINPODX_NO_REVERSE_OPEN:-}" != "1" ]; then
    log "Setting up reverse-open (Linux apps in Windows 'Open with')..."
    "$HOME/.local/bin/winpodx" host-open start-listener 2>/dev/null || \
        warn "  reverse-open listener didn't start; the feature will activate on next \`winpodx pod start\`"
    if ! "$HOME/.local/bin/winpodx" host-open refresh 2>&1 | sed 's/^/  /'; then
        warn "  reverse-open refresh failed; retry manually with \`winpodx host-open refresh\` once the pod is up"
    fi
fi

unset WINPODX_REQUIRE_AGENT

# Persist the pending list so resume_install_work() can pick it up.
if [ -n "$PENDING_STEPS" ]; then
    mkdir -p "$HOME/.config/winpodx"
    printf '%s' "$PENDING_STEPS" > "$PENDING_FILE"
    warn "Pending steps recorded at $PENDING_FILE — auto-resume will run on next winpodx invocation."
else
    rm -f "$PENDING_FILE"
fi

# --- Done ---
echo ""
echo " Location: $INSTALL_DIR"
echo " Command:  winpodx"
echo ""
echo " Usage:"
echo "   winpodx app run desktop        # Start the Windows pod (first run takes ~5-10 min)"
echo "   winpodx app refresh            # Scan the pod for installed apps + icons"
echo "   winpodx info                   # System / pod / dependency snapshot"
echo "   winpodx setup                  # Reconfigure"
echo ""
echo " On first pod boot the menu auto-populates with the apps actually"
echo " installed in your Windows guest — no curated list, real icons."
echo ""
log "Installation complete!"
