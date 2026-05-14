# Install

**English** | [한국어](INSTALL.ko.md)

Every way to install winpodx — the one-line installer, distro package managers, Nix, source builds, and offline scenarios.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

Detects your distro, installs missing system dependencies (Podman, FreeRDP, KVM, Python 3.9+) with your confirmation, drops winpodx into `~/.local/bin/winpodx-app/`. The Windows-app menu populates automatically the first time the pod boots — discovery scans your running Windows guest and registers every installed app with its real icon. No root required except for the dependency install step. Works on openSUSE, Fedora, Debian/Ubuntu, RHEL-family, Arch, and NixOS.

> **Windows licensing.** dockur downloads a Windows ISO from Microsoft at first pod boot. Your use of the resulting Windows guest is governed by Microsoft's Software License Terms (the EULA shown on first activation). winpodx does not redistribute Windows; it only orchestrates the install on your machine. Bring your own Windows license key for activation — Home / Pro / Enterprise are all supported by dockur.

By default the installer pins to the **latest published GitHub release** (currently `v0.5.0`). Pre-release / development versions stay opt-in.

## Choose a version

Pass `--main` (or `--ref TAG`) for development builds, otherwise stick with the default release:

```bash
# Install the latest stable release (default)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash

# Install the latest main HEAD (development; may be unstable)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --main

# Install a specific tag, branch, or commit
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --ref v0.5.0

# Env-var equivalent (works under curl | bash without -s --)
WINPODX_REF=main   curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
WINPODX_REF=v0.5.0 curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

## Offline / air-gapped install

The installer takes three optional flags for machines with no registry / package-repo access:

```bash
# Copy winpodx from a local clone instead of git clone (also env: WINPODX_SOURCE)
./install.sh --source /media/usb/winpodx

# Preload the Windows image tar instead of fetching at first boot (env: WINPODX_IMAGE_TAR)
./install.sh --image-tar /media/usb/windows-image.tar

# Skip distro package install (env: WINPODX_SKIP_DEPS=1) — fails early if deps aren't present
./install.sh --skip-deps

# Everything at once:
./install.sh --source /media/usb/winpodx --image-tar /media/usb/windows-image.tar --skip-deps
```

Env vars are honored even under `curl | bash`, so `WINPODX_SKIP_DEPS=1 curl ... | bash` works.

## Choosing the Windows edition

By default winpodx installs the latest dockur Windows 11 image. Pass `--win-version VER` (or the `WINPODX_WIN_VERSION` env var) to pick a different curated edition during a fresh install:

```bash
# Install Windows 10 LTSC instead of Win11
./install.sh --win-version ltsc10

# IoT Enterprise LTSC (long-term-service for kiosks / appliances)
./install.sh --win-version iot11

# Debloated community build
./install.sh --win-version tiny11

# Server 2022
./install.sh --win-version 2022
```

Curated set: `11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 | tiny10 | 2025 | 2022 | 2019 | 2016`. Pre-Win10 editions (XP / Vista / 7 / 8 / Server 2003-2012) are out of Microsoft security support and don't match the rdprrap / agent.ps1 / install.bat assumptions winpodx is built on — they'll still pass through to dockur with a WARNING but aren't first-class supported.

The `--win-version` flag only applies on fresh installs (no existing `winpodx.toml`). Existing installs change the edition via the GUI Settings → Container/VM → **Windows Edition** dropdown (or `winpodx setup --win-version VER` if you've removed the config).

For booting your own custom ISO with programs pre-installed, see [Advanced: Custom Windows ISO](ARCHITECTURE.md#advanced-custom-windows-iso).

## Native package managers

Prebuilt RPM and `.deb` packages are attached to every [GitHub Release](https://github.com/kernalix7/winpodx/releases/latest) — openSUSE/Fedora RPMs come from the [openSUSE Build Service (`home:Kernalix7/winpodx`)](https://build.opensuse.org/package/show/home:Kernalix7/winpodx), the rest from GitHub Actions. AUR publishing is wired but currently inactive (maintainer SSH key onboarding pending) — Arch users should use `install.sh` for now.

### openSUSE Tumbleweed / Leap 15.6 / Leap 16.0 / Slowroll

```bash
sudo zypper addrepo \
  https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
sudo zypper refresh
sudo zypper install winpodx
```

Replace `openSUSE_Tumbleweed` with `openSUSE_Leap_16.0`, `openSUSE_Leap_15.6`, or `openSUSE_Slowroll` as needed.

### Fedora 42 / 43

```bash
sudo dnf config-manager --add-repo \
  https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx
```

### Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10

Download the matching `.deb` from the [latest release](https://github.com/kernalix7/winpodx/releases/latest) and install:

```bash
sudo apt install ./winpodx_<version>_all_debian13.deb   # pick your flavor
```

### AlmaLinux / Rocky / RHEL 9 & 10

EPEL is required on el9 for `python3-tomli`. Download the matching `.rpm` from the [latest release](https://github.com/kernalix7/winpodx/releases/latest) and install:

```bash
sudo dnf install epel-release                            # el9 only
sudo dnf install ./winpodx-<version>-1.noarch.el9.rpm    # or .el10.rpm
```

### Arch Linux / Manjaro

The AUR publish workflow exists but is dormant until the maintainer's SSH key is provisioned — there is no `winpodx` AUR package yet. In the meantime, use the universal installer above; it detects `pacman` and installs all dependencies cleanly:

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

Once the AUR package goes live, this section will be updated with `yay -S winpodx` / `paru -S winpodx`.

## Nix

A flake is provided for NixOS / nix-on-any-distro users:

```bash
# Run directly without installing
nix run github:kernalix7/winpodx

# Install into your profile
nix profile install github:kernalix7/winpodx

# As a flake input
inputs.winpodx.url = "github:kernalix7/winpodx";
```

The wrapper bundles FreeRDP, podman / podman-compose, iproute2 and libnotify, so the default Podman backend works out of the box. The Docker and libvirt backends still require the respective tools to be present on the host.

## From source

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
./install.sh
```

The source installer automatically:
1. Detects your distro (openSUSE, Fedora, Ubuntu, Arch, ...)
2. Installs missing dependencies (Podman, FreeRDP, KVM), asks before installing
3. Copies winpodx to `~/.local/bin/winpodx-app/`
4. Creates config and `compose.yaml`
5. Auto-discovery (`winpodx app refresh`) fires on first pod boot to populate the menu

### Manual run (no install)

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
export PYTHONPATH="$PWD/src"
python3 -m winpodx app run word
```

## Uninstall

`--confirm` or `--purge` is required under pipe (the interactive prompts can't read from a terminal while bash consumes stdin from curl):

```bash
# Remove winpodx files, keep the Windows container + its data
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --confirm

# Full wipe: container, volume, config, launcher, everything
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge
```

**Uninstall only removes winpodx files.** It never touches:
- Your Podman containers / volumes (Windows VM data) unless `--purge` is passed
- System packages (podman, freerdp, python3)
- Your home directory files
