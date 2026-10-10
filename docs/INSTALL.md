# Install

**English** | [한국어](INSTALL.ko.md)

Every way to install WinPodX — the one-line installer, distro package managers, Nix, AppImage, wheel, source builds, and offline scenarios.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

Detects your distro, installs missing system dependencies (Podman 4+ + podman-compose, FreeRDP 3+, KVM, Python 3.10+) with your confirmation, and drops winpodx into `~/.local/bin/winpodx-app/`. Provisioning scans the guest's Start Menu and registers discovered apps with their icons (`desktop.full_app_scan = true` opts into Registry App Paths and Chocolatey/Scoop shims too). If installation or discovery fails, the menu may remain empty; retry after the guest is ready with `winpodx app refresh`. The desktop app bundles its Selawik fallback font, so it needs no system font package. No root required except for the dependency and host-setup steps. Works on openSUSE, Fedora (including Atomic Desktops: Silverblue, Kinoite, Sericea, Bluefin, Bazzite), Debian/Ubuntu, RHEL-family, and Arch. NixOS uses the flake below.

> **Windows licensing.** WinPodX orchestrates installation on your machine and does not redistribute Windows. You must hold the Windows license and entitlements required for your intended use under Microsoft's applicable terms.
>
> **Check your Windows usage rights.** Activation alone does not establish all applicable virtualization, remote-access, or multi-user rights. Check the Microsoft license terms and entitlements for your edition, licensing channel, jurisdiction, and intended use. WinPodX's MIT license and rdprrap do not extend those Windows rights.

By default the installer pins to the **latest published GitHub release**. Pre-release / development versions stay opt-in.

## Choose a version

Pass `--main` (or `--ref TAG`) for development builds, otherwise stick with the default release:

```bash
# Install the latest stable release (default)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash

# Install the latest main HEAD (development; may be unstable)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --main

# Install a specific tag, branch, or commit
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --ref vX.Y.Z

# Env-var equivalent (works under curl | bash without -s --)
WINPODX_REF=main  curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
WINPODX_REF=vX.Y.Z curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

> **Upgrading.** Re-running `install.sh` (or upgrading the package) installs the new version in place. A running system tray / GUI restarts itself automatically so the new version applies — no manual restart needed. The Windows pod keeps running throughout. See [Updating winpodx](#updating-winpodx) below for the full breakdown per install method, including Bazzite / rpm-ostree hosts.

## Manual install (skip provisioning)

For users who want to install the binary now and customize the Windows guest later (pick edition / language / debloat / tuning knobs before the ~7.5 GB ISO download + Sysprep + OEM apply kicks off), pass `--manual`:

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --manual
# or via env var:
WINPODX_MANUAL=1 curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

Manual mode installs the binary + desktop entry + icon only -- no `winpodx setup`, no `winpodx provision`, no app discovery, no reverse-open setup. The next bare `winpodx` invocation offers three terminal setup options; `winpodx gui` opens the six-page setup wizard:

- **Auto** -- host-detected defaults, non-interactive (= what default `install.sh` would have done)
- **Customize** -- wizard mode (pick every knob); equivalent to `winpodx setup --customize`
- **Skip** -- exits without changes; re-runs the prompt on next invocation

This is the same flow that fires after a package-manager install. Use it when you want the wizard but prefer not to interrupt `install.sh` half-way through.

The terminal wizard (`winpodx setup --customize`) also asks where to store the Windows disk and whether to use a readable local Windows ISO. Enter keeps the default storage directory and downloads the ISO; a review shows both choices before any pod is created. An explicit storage directory must be writable and empty, and invalid or unreadable paths abort rather than starting Windows. Existing config or named-volume storage is not migrated, overwritten, or replaced by these prompts: use `winpodx setup --migrate-storage` for a deliberate relocation, and use a local ISO only on a fresh install. Scripted setups still accept `winpodx setup --storage-path PATH --win-iso PATH`; `install.sh --storage-dir PATH` remains available. The GUI wizard offers the same fresh-install choices and checks them before provisioning. Reinstall does not move storage or replace installation media, and wipe confirmation is required after review. A local ISO does not by itself make setup fully offline.

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

By default WinPodX installs the latest dockur Windows 11 image. Pass `--win-version VER` (or the `WINPODX_WIN_VERSION` env var) to pick a different curated edition during a fresh install:

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

Curated set: `11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 | tiny10 | 2025 | 2022 | 2019 | 2016`. Pre-Win10 editions (XP / Vista / 7 / 8 / Server 2003-2012) are out of Microsoft security support and don't match the rdprrap / agent.ps1 / install.bat assumptions WinPodX is built on — they'll still pass through to dockur with a WARNING but aren't first-class supported.

The `--win-version` flag only applies on fresh installs (no existing `winpodx.toml`). Existing installs change the edition via the GUI Settings → Container/VM → **Windows Edition** dropdown (or `winpodx setup --win-version VER` if you've removed the config).

For booting your own custom ISO with programs pre-installed, see [Advanced: Custom Windows ISO](ARCHITECTURE.md#advanced-custom-windows-iso).

## Choosing the Windows language

By default, Windows installation language follows the host locale. You can configure the display language, regional format, and keyboard layout by editing `~/.config/winpodx/winpodx.toml` after running the installer (or by creating it beforehand for a fresh install):

```toml
[pod]
# Spanish example
language = "Spanish"
region = "es-ES"
keyboard = "es-ES"
```

Common language configurations:

| Language | `language` | `region` | `keyboard` |
|----------|------------|----------|------------|
| English (US) | `English` | `en-001` | `en-US` |
| Spanish (Spain) | `Spanish` | `es-ES` | `es-ES` |
| Spanish (Latin America) | `Spanish` | `es-MX` | `la-Latin` |
| French (France) | `French` | `fr-FR` | `fr-FR` |
| German (Germany) | `German` | `de-DE` | `de-DE` |
| Italian (Italy) | `Italian` | `it-IT` | `it-IT` |
| Portuguese (Brazil) | `Portuguese` | `pt-BR` | `pt-BR` |
| Portuguese (Portugal) | `Portuguese` | `pt-PT` | `pt-PT` |
| Japanese | `Japanese` | `ja-JP` | `ja-JP` |
| Chinese (Simplified) | `Chinese` | `zh-CN` | `zh-CN` |

These settings only apply to **fresh Windows installations**. If you've already run `winpodx setup` and booted Windows once, you'll need to either:
1. Edit the config, then run `winpodx pod recreate --wipe-storage` to reinstall Windows, **or**
2. Change the language manually inside Windows via Settings → Time & Language → Language & region

For the complete list of supported languages and region codes, see the [dockur/windows documentation](https://github.com/dockur/windows#how-do-i-change-the-language).

## Native package managers

Prebuilt RPM and `.deb` packages are attached to each published [GitHub Release](https://github.com/kernalix7/winpodx/releases/latest) — openSUSE/Fedora RPMs come from the [openSUSE Build Service (`home:Kernalix7/winpodx`)](https://build.opensuse.org/package/show/home:Kernalix7/winpodx), the rest from GitHub Actions. The [`winpodx` AUR package](https://aur.archlinux.org/packages/winpodx) is live as of v0.5.2 — Arch users can install via `yay -S winpodx` or `paru -S winpodx`.

> **After any package-manager install, run `winpodx setup` once.** The package payload installs the application and desktop integration only -- no post-install hook fires the Windows VM provisioning, because `apt install` / `dnf install` / `yay -S` running as root shouldn't start a user-namespace rootless Podman VM or an hours-long first boot. `winpodx setup` is non-interactive auto setup by default (`--customize` opens the wizard) and completes the shared `winpodx provision` chain: wait-ready, guest fixes, discovery, and reverse-open. The curl one-liner invokes that flow itself. First-time flow:
>
> ```bash
> winpodx setup                # auto defaults + complete first provisioning
> winpodx app run desktop      # launch the ready Windows desktop
> ```

### openSUSE Tumbleweed / Leap 15.6 / Leap 16.0 / Slowroll

```bash
sudo zypper addrepo \
  https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
sudo zypper refresh
sudo zypper install winpodx
```

Replace `openSUSE_Tumbleweed` with `openSUSE_Leap_16.0`, `openSUSE_Leap_15.6`, or `openSUSE_Slowroll` as needed.

### Fedora 42 / 43 / 44

```bash
sudo dnf config-manager addrepo --from-repofile=\
https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx
```

Replace `Fedora_43` with `Fedora_42` or `Fedora_44` as needed.

> **Note:** Fedora 41 + ships dnf5; the syntax above (`addrepo --from-repofile=`) matches it. On dnf4 (Fedora ≤40, EOL) the equivalent is `sudo dnf config-manager --add-repo <URL>`. Reported by @payayas in #228.

### Fedora Atomic Desktops (Silverblue / Kinoite / Sericea / Bluefin / Bazzite)

Atomic Fedora uses `rpm-ostree` instead of `dnf` — the same OBS RPM is layered onto the booted deployment with `--apply-live` (no reboot needed) when the running system accepts it, otherwise staged for the next boot. The universal `install.sh` autodetects `rpm-ostree` and runs the layered path; you can also do it by hand:

```bash
sudo curl -sSL \
  https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo \
  -o /etc/yum.repos.d/home-Kernalix7-winpodx.repo
sudo rpm-ostree install --apply-live winpodx     # try live apply first
# If live apply isn't supported on the booted deployment:
sudo rpm-ostree install winpodx                  # staged; reboot to activate
```

Replace `Fedora_43` with `Fedora_42` or `Fedora_44` to match your base image.

### Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10 / 26.04

Download the matching `.deb` from the [latest release](https://github.com/kernalix7/winpodx/releases/latest) and install:

```bash
sudo apt install ./winpodx_<version>_all_debian13.deb   # pick your flavor
```

### AlmaLinux / Rocky / RHEL 9 & 10

RHEL 9, AlmaLinux 9, and Rocky Linux 9 ship a default `python3` below the supported floor. The el9 package pulls in the Python 3.11 stack from AppStream. Download the matching `.rpm` from the [latest release](https://github.com/kernalix7/winpodx/releases/latest) and install:

```bash
sudo dnf install ./winpodx-<version>-0.noarch.el9.rpm    # or .el10.rpm
```

### Arch Linux / Manjaro

Install from the AUR using your preferred helper:

```bash
yay -S winpodx
# or
paru -S winpodx
```

The PKGBUILD lives at [`packaging/aur/PKGBUILD`](../packaging/aur/PKGBUILD); each tag push (`v*.*.*`) auto-stamps the version + tarball sha256 and pushes to `aur.archlinux.org/winpodx.git`.

**Development build — track `main` (`winpodx-git`).** To run the latest unreleased code instead of the tagged release:

```bash
yay -S winpodx-git
```

`winpodx-git` builds from the `main` branch (the version is derived from git, so each rebuild pulls the newest commit — use `yay -Syu --devel` to auto-update). It `provides`/`conflicts` `winpodx`, so install one or the other, not both. Recipe + maintainer notes: [`packaging/aur-git/`](../packaging/aur-git/).

## AppImage (Thin bundle: Python + Qt + FreeRDP + winpodx; host container runtime required)

WinPodX uses a Thin x86_64 AppImage model introduced in 0.6.0. It bundles Python, WinPodX, selected Qt6 modules through PySide6, and FreeRDP clients, while using the host's container runtime. The earlier Fat bundle included container tools and libraries that could conflict with the host stack (#357, #363). Unused Qt modules are removed by `packaging/appimage/slim-pyside6.sh`.

The 0.12.0 candidate recipe uses Ubuntu 24.04 FreeRDP `3.32.1+dfsg-0ubuntu0.24.04.1`, Python `3.11.17+20261009`, and PySide6 `6.12.0`. Its provenance collector records package ownership, license texts, wheel URLs, and download hashes. The implemented source pipeline SHA256-verifies and mirrors Qt/PySide, CPython, PyPI, Ubuntu, and AppImage runtime originals into `winpodx-appimage-sources.tar.gz`, with a source/use index, licenses, input locks, and recipes; tag builds attach the archive, `PROVENANCE.json`, `SOURCE-OFFER.txt`, and `SHA256SUMS` alongside the binary. Each artifact has its own provenance and source-delivery record; this is not blanket legal or security clearance. Unlocked Alpine apk inputs limit runtime reconstruction, and byte-identical reconstruction of every native component is not claimed.

> **FreeRDP client selection.** The client source is configurable. Auto mode prefers native `xfreerdp` when it meets the RAIL version floor; otherwise it tries Flatpak, with the native client retained as fallback when Flatpak is absent.

Host-side requirements:

- A container runtime installed via your distro's package manager: **`podman ≥ 4` + standalone `podman-compose` recommended**, or Docker with its Compose v2 plugin (the manual backend uses an existing RDP host instead). Use `install.sh` if you want these installed for you.
- `/dev/kvm` exposed by the host kernel (most distros do this by default once VT-x / AMD-V is enabled in BIOS).
- The current user belongs to the `kvm` group (and `/etc/subuid` + `/etc/subgid` entries exist for rootless Podman — usually preconfigured on modern distros; check with `cat /etc/subuid`).

The `setup-host` wizard still ships in the AppImage for the kvm-group / subuid step (one polkit prompt instead of a manual sudo dance):

```bash
# 1. Download the AppImage from the latest GitHub release
#    -> winpodx-x86_64.AppImage

# 2. Make it executable
chmod +x winpodx-x86_64.AppImage

# 3. (Optional) First-run host setup — kvm group, subuid/subgid, kvm module
./winpodx-x86_64.AppImage setup-host          # detect + interactive prompt
./winpodx-x86_64.AppImage setup-host --apply  # apply without prompt
./winpodx-x86_64.AppImage setup-host --status # detect only, no mutation

# 4. Log out + back in so the new kvm group membership takes effect.

# 5. Standard winpodx setup (auto-detect cores / RAM / timezone)
./winpodx-x86_64.AppImage setup

# 6. Launch the desktop (or any installed Windows app by name)
./winpodx-x86_64.AppImage app run desktop
```


Best fit for:

- **Immutable distros** (Fedora Silverblue / Kinoite, openSUSE Aeon, Steam Deck) once a host podman is layered in.
- **Locked-down environments** where users can't run `curl ... | bash` but can run a single downloaded executable.
- **Quick try-it-on-a-friend's-laptop** when a host podman / docker is already installed.

Not the best fit if you prefer:

- Native package-manager integration (use the RPM / DEB / AUR paths above instead).
- Auto-update from the system update cycle (the AppImage is downloaded + replaced manually unless you wire it into AppImageUpdate yourself).
- A truly zero-host-prep run — the Thin AppImage needs a host container runtime; if you want the installer to install one for you, use `install.sh`.

Recipe and CI live under `packaging/appimage/` — see `packaging/appimage/README.md` for local-build instructions if you want to roll your own. A regression test (`tests/test_appimage_recipe.py`) fails CI the moment the recipe re-introduces any of `podman` / `podman-compose` / `conmon` / `crun` / `netavark` / `aardvark-dns` / `passt` / `pasta` / `slirp4netns` / `fuse-overlayfs`.

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

The wrapper adds FreeRDP, Podman, podman-compose, iproute2, and libnotify to `PATH`, so the default Podman backend has its runtime tools available. The host still needs working KVM and rootless-container setup. The Docker backend requires Docker to be present on the host; the manual backend connects to an RDP host you provide.

## From source

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
./install.sh
```

The source installer automatically:
1. Detects your distro (openSUSE, Fedora, Ubuntu, Arch, ...)
2. Installs missing dependencies (Podman, podman-compose, FreeRDP, KVM), asks before installing
3. Copies winpodx to `~/.local/bin/winpodx-app/`
4. Creates config and `compose.yaml`
5. Completes provisioning and auto-discovery to populate the menu

## Python wheel (`pip`)

WinPodX is not currently published on PyPI. Each GitHub Release includes a wheel; install the downloaded asset into a virtual environment (add extras as needed):

```bash
python3 -m venv ~/.local/share/winpodx-venv
~/.local/share/winpodx-venv/bin/pip install './winpodx-<version>-py3-none-any.whl[gui,docker,reverse-open]'
~/.local/share/winpodx-venv/bin/winpodx setup
```

The core wheel requires `tomli` on Python 3.10 because `tomllib` arrives in Python 3.11. PySide6, docker-py, and reverse-open icon conversion are optional extras; system requirements such as FreeRDP 3+, a container runtime, and KVM are not installed by pip.

### Manual run (no install)

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
export PYTHONPATH="$PWD/src"
python3 -m winpodx app run word
```

## Updating winpodx

There's no separate update command — you update the same way you installed.

**curl one-liner / source install** (`~/.local/bin/winpodx-app/`): re-run the exact command you used to install:

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

It detects the existing install and upgrades it in place — venv swap, same config, same Windows VM disk. On the first launch after upgrading, winpodx runs its `migrate` chain automatically: it pushes the refreshed guest scripts (agent, rdprrap, OEM fixes) into the running guest and shows release notes before continuing on to your app, so the guest doesn't keep running stale scripts after a host upgrade. A running system tray / GUI restarts itself so the new version takes effect — no manual restart needed, and the Windows pod keeps running throughout.

This also covers **Bazzite and other rpm-ostree (Atomic Fedora) hosts whose install came from the curl one-liner** rather than the OBS RPM below (common if you installed before the OBS repo existed, or just ran the one-liner instead of layering the package): the same bare command upgrades that install in place too — no `--main` or other flag needed, and it never touches the Windows VM.

Cloned the repo instead of using curl? `git pull && ./install.sh` does the same thing from your local checkout.

**openSUSE / Fedora (OBS RPM repo already added):**

```bash
sudo zypper refresh && sudo zypper update winpodx      # openSUSE
sudo dnf update winpodx                                  # Fedora
```

**Fedora Atomic Desktops (rpm-ostree, layered from the OBS repo):**

```bash
sudo rpm-ostree upgrade
```

Reboot only if the output says the update was staged rather than live-applied.

**Debian / Ubuntu (`.deb`):** download the newer `.deb` from the [latest release](https://github.com/kernalix7/winpodx/releases/latest) and install it over the existing package:

```bash
sudo apt install ./winpodx_<version>_all_debian13.deb
```

**AlmaLinux / Rocky / RHEL (`.rpm`):** same idea — download the newer `.rpm` and install it over the existing package:

```bash
sudo dnf install ./winpodx-<version>-0.noarch.el10.rpm
```

**Arch (AUR):**

```bash
yay -S winpodx        # or: paru -S winpodx
```

`winpodx-git` tracks `main` — `yay -Syu --devel` pulls the newest commit.

**AppImage:** download the newer AppImage from the [latest release](https://github.com/kernalix7/winpodx/releases/latest) and replace the old file. There's no in-place self-update.

**Nix:** `nix profile upgrade winpodx` for a profile install, or just re-run `nix run github:kernalix7/winpodx` — it always fetches the flake's current `main`.

Whichever path you used, check what you're running with `winpodx --version`. If the guest ever falls behind the host (e.g. a package-manager upgrade that hasn't started the pod since), `winpodx doctor` flags the version drift and `winpodx guest sync --force` re-pushes the guest-side scripts — though this normally happens on its own the next time the pod starts.

## Uninstall

One canonical script -- same behavior regardless of how WinPodX was installed (curl / wheel / deb / rpm / aur).

Two equivalent entry points:

```bash
# Stop everything, keep Windows container + config (apt-remove semantics)
./uninstall.sh
# OR
winpodx uninstall

# Full wipe: container, Windows disk, config, launcher (apt-purge semantics)
./uninstall.sh --purge --yes
# OR
winpodx uninstall --purge --yes
```

`winpodx uninstall` is a thin wrapper that locates and execs the same `uninstall.sh` shipped at one of: `/usr/share/winpodx/uninstall.sh` (deb/rpm/aur), `~/.local/bin/winpodx-app/uninstall.sh` (curl), or the wheel's shared-data dir (pip).

`--yes` or `--purge` is required when piping from curl (interactive prompts can't read from a terminal while bash consumes stdin):

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --yes
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge
```

If installed via a package manager (apt/dnf/zypper/pacman), `uninstall.sh` detects this and prompts you to run the package-manager removal first -- that's the correct order: the package manager's post-remove hook re-runs `uninstall.sh` for the user-side cleanup, keeping the package database in sync with disk state.

**Default mode never touches** (use `--purge` to also remove these):
- Podman container / volumes (Windows VM disk; 64 GB configured by default)
- `~/.config/winpodx/` config + compose.yaml
- Storage bind-mount contents

**Never removed by either mode:**
- System packages (podman, freerdp, python3) -- the package-manager removal prompt handles those
- Other files in your home directory
