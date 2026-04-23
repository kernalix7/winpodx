<div align="center">

<img src="data/winpodx-icon.svg" alt="winpodx" width="128">

# winpodx

**Run Windows applications seamlessly on Linux**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-green.svg)](https://www.python.org/)
[![Backend: Podman](https://img.shields.io/badge/Backend-Podman-purple.svg)](https://podman.io/)
[![Tests: 225 passed](https://img.shields.io/badge/Tests-225%20passed-brightgreen.svg)](#testing)

**English** | [한국어](docs/README.ko.md)

*Click an app icon in your Linux menu. Word opens. That's it.*

</div>

---

winpodx runs a Windows container (via [dockur/windows](https://github.com/dockur/windows)) in the background and presents Windows apps as native Linux applications through FreeRDP RemoteApp. No manual VM setup, no ISO downloads, no registry editing. **Near-zero external Python dependencies** (stdlib only on Python 3.11+; one pure-Python `tomli` fallback on 3.9/3.10).

## Why winpodx?

Existing tools for running Windows apps on Linux all have trade-offs:

| | winapps | LinOffice | winpodx |
|---|---------|-----------|---------|
| Core tech | dockur/windows + FreeRDP | dockur/windows + FreeRDP | dockur/windows + FreeRDP |
| Setup | Manual (shell scripts, config files, RDP testing) | One-liner script | **Zero-config** (auto on first launch) |
| App scope | Any Windows app | Office only | **Any Windows app** |
| Language | Shell (86%) | Shell (61%) + Python | **Python (100%)** |
| Dependencies | curl, dialog, git, netcat | Podman, FreeRDP | **Python 3.9+ (stdlib on 3.11+; `tomli` on 3.9/3.10)** |
| Auto suspend | No | No | **Yes** |
| Password rotation | No | No | **Yes (7-day cycle)** |
| HiDPI | No | No | **Auto-detect** |
| Sound / Printer | No | No | **Yes (default)** |
| USB sharing | No | No | **Yes (auto drive mapping)** |
| System tray | No | No | **Qt6 tray** |
| License | MIT | AGPL-3.0 | **MIT** |

## Key Features

<table>
<tr><td width="50%">

**Seamless App Windows**
- RemoteApp (RAIL) renders each app as a native Linux window (no full desktop)
- Per-app taskbar icons via WM_CLASS matching
- File associations: double-click `.docx` in your file manager → Word opens
- Multi-session RDP: bundled rdprrap auto-enables independent sessions per app

</td><td width="50%">

**Zero-Config Launch**
- First app click auto-provisions everything: config, container, desktop entries
- 14 bundled app profiles (Office, VS Code, built-in Windows tools)
- Add any Windows app via simple TOML definition
- Interactive setup wizard for advanced configuration

</td></tr>
<tr><td width="50%">

**Peripherals & Sharing**
- **Clipboard**: Bidirectional copy-paste (text + images) enabled by default
- **Sound**: RDP audio streaming (`/sound:sys:alsa`) enabled by default
- **Printer**: Linux printers shared to Windows via RDP redirection
- **USB drives**: Auto-shared via `/drive:media`; plugged after session start still accessible
- **USB devices**: Native USB redirection (`/usb:auto`) when FreeRDP urbdrc plugin is available
- **USB auto drive mapping**: Windows-side FileSystemWatcher script maps USB folders to drive letters (E:, F:, ...) automatically
- **Home directory**: Shared as `\\tsclient\home` for file access

</td><td width="50%">

**Automation & Security**
- Auto suspend/resume: container pauses when idle, resumes on next launch
- Password auto-rotation: 20-char cryptographic password, 7-day cycle with rollback
- Smart DPI scaling: auto-detects from GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb
- Qt6 system tray: pod controls, app launchers, idle monitor
- Multi-backend: Podman (default), Docker, libvirt/KVM, manual RDP
- Windows debloat: disable telemetry, ads, Cortana, search indexing
- Time sync: force Windows clock resync after host sleep/wake

</td></tr>
</table>

## How It Works

```
                     ┌─────────────────────────────┐
  Click "Word"       │     Linux Desktop (KDE,      │
  in app menu  ───>  │     GNOME, Sway, ...)        │
                     └──────────────┬──────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │         winpodx              │
                     │  ┌─────────────────────┐     │
                     │  │ auto-provision:      │     │
                     │  │  config → password   │     │
                     │  │  → container → RDP   │     │
                     │  │  → desktop entries   │     │
                     │  └─────────────────────┘     │
                     └──────────────┬──────────────┘
                                    │ FreeRDP RemoteApp
                     ┌──────────────▼──────────────┐
                     │   Windows Container (Podman) │
                     │   ┌──────────────────────┐   │
                     │   │  Word  Excel  PPT ... │   │
                     │   │  (single-session RDP)  │   │
                     │   └──────────────────────┘   │
                     │   127.0.0.1:3390 (TLS)       │
                     └─────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.9+ (stdlib only on 3.11+; `tomli` fallback on 3.9/3.10) |
| CLI | argparse (stdlib) |
| GUI (optional) | PySide6 (Qt6) |
| Config | TOML (stdlib `tomllib` on 3.11+ / `tomli` on 3.9/3.10; built-in writer) |
| RDP | FreeRDP 3+ (xfreerdp, RemoteApp/RAIL) |
| Container | Podman / Docker ([dockur/windows](https://github.com/dockur/windows)) |
| VM | libvirt / KVM |
| CI | GitHub Actions (lint + test on 3.9-3.13 + pip-audit) |

## Quick Start

### Install

**One-line install** (any supported Linux distro):

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

Detects your distro, installs missing system dependencies (Podman, FreeRDP,
KVM, Python 3.9+) with your confirmation, drops winpodx into
`~/.local/bin/winpodx-app/`, and registers all 14 Windows apps in your desktop
menu. No root required except for the dependency install step. Works on
openSUSE, Fedora, Debian/Ubuntu, RHEL-family, and Arch.

Prefer a native package manager? Prebuilt RPM / `.deb` / AUR packages are
attached to every [GitHub Release](https://github.com/kernalix7/winpodx/releases/latest)
— openSUSE/Fedora RPMs from the
[openSUSE Build Service (`home:Kernalix7/winpodx`)](https://build.opensuse.org/package/show/home:Kernalix7/winpodx),
the rest from GitHub Actions:

**openSUSE Tumbleweed / Leap 15.6 / Leap 16.0 / Slowroll**

```bash
sudo zypper addrepo \
  https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
sudo zypper refresh
sudo zypper install winpodx
```

Replace `openSUSE_Tumbleweed` with `openSUSE_Leap_16.0`, `openSUSE_Leap_15.6`,
or `openSUSE_Slowroll` as needed.

**Fedora 42 / 43**

```bash
sudo dnf config-manager --add-repo \
  https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx
```

**Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10**

Download the matching `.deb` from the
[latest release](https://github.com/Kernalix7/winpodx/releases/latest) and
install:

```bash
sudo apt install ./winpodx_0.1.5_all_debian13.deb   # pick your flavor
```

**AlmaLinux / Rocky / RHEL 9 & 10**

EPEL is required on el9 for `python3-tomli`. Download the matching `.rpm`
from the [latest release](https://github.com/Kernalix7/winpodx/releases/latest)
and install:

```bash
sudo dnf install epel-release                     # el9 only
sudo dnf install ./winpodx-0.1.5-1.noarch.el9.rpm   # or .el10.rpm
```

**Arch Linux (AUR)**

> Note: AUR publishing is wired up but pending a one-time maintainer setup
> (see [`packaging/aur/README.md`](packaging/aur/README.md)). Once activated,
> tag pushes publish automatically.

```bash
yay -S winpodx        # or:
paru -S winpodx
```

**From source (development)**

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
./install.sh
```

The source installer automatically:
1. Detects your distro (openSUSE, Fedora, Ubuntu, Arch, ...)
2. Installs missing dependencies (Podman, FreeRDP, KVM), asks before installing
3. Copies winpodx to `~/.local/bin/winpodx/`
4. Creates config and compose.yaml
5. Registers all 14 apps in your desktop menu

### Launch

```bash
winpodx app run word              # Launch Word
winpodx app run word ~/doc.docx   # Open a file
winpodx app run desktop           # Full Windows desktop
```

Or just click an app icon in your menu.

### Manual Run (no install)

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
export PYTHONPATH="$PWD/src"
python3 -m winpodx app run word
```

---

## CLI Reference

<details>
<summary><b>Click to expand full CLI reference</b></summary>

```bash
# Apps
winpodx app list                  # List available apps
winpodx app run word              # Launch Word (auto-provisions on first run)
winpodx app run word ~/doc.docx   # Open a file in Word
winpodx app run desktop           # Full Windows desktop session
winpodx app install-all           # Register all apps in desktop menu
winpodx app sessions              # Show active sessions
winpodx app kill word             # Kill an active session

# Pod management
winpodx pod start --wait          # Start and wait for RDP readiness
winpodx pod stop                  # Stop (warns about active sessions)
winpodx pod status                # Status with session count
winpodx pod restart

# Power management
winpodx power --suspend           # Pause container (free CPU, keep memory)
winpodx power --resume            # Resume paused container

# Security
winpodx rotate-password           # Rotate Windows RDP password

# Maintenance
winpodx cleanup                   # Remove Office lock files (~$*.*)
winpodx timesync                  # Force Windows time synchronization
winpodx debloat                   # Disable telemetry, ads, bloat
winpodx uninstall                 # Remove winpodx files (keeps container)
winpodx uninstall --purge         # Remove everything including config

# System
winpodx setup                     # Interactive setup wizard
winpodx info                      # Display, dependencies, config diagnostics
winpodx tray                      # Launch Qt system tray icon
winpodx config show               # Show current config
winpodx config set rdp.scale 140  # Change a config value
winpodx config import             # Import existing winapps.conf
```

</details>

## Peripherals & Sharing

| Feature | How it works | Default |
|---------|-------------|---------|
| **Clipboard** | Bidirectional copy-paste via RDP (`+clipboard`) | Enabled |
| **Sound** | Audio streaming via ALSA (`/sound:sys:alsa`) | Enabled |
| **Printer** | Linux printers shared to Windows (`/printer`) | Enabled |
| **Home directory** | Shared as `\\tsclient\home` (`+home-drive`) | Enabled |
| **USB drives** | Media folder shared as `\\tsclient\media` (`/drive:media`); USB drives plugged in after session start are accessible as subfolders | Enabled |
| **USB devices** | Native USB redirection (`/usb:auto`); requires FreeRDP urbdrc plugin | Enabled (fallback to drive sharing) |
| **USB drive mapping** | Windows-side script auto-maps USB subfolders to drive letters (E:, F:, ...) via FileSystemWatcher | Enabled |

### USB Drive Flow

```
Plug in USB on Linux
    │
    ▼
Linux mounts to /run/media/$USER/USBNAME
    │
    ▼
FreeRDP shares as \\tsclient\media\USBNAME
    │
    ▼
media_monitor.ps1 detects → net use E: \\tsclient\media\USBNAME
    │
    ▼
Windows Explorer shows E: drive
```

## Configuration

Config file: `~/.config/winpodx/winpodx.toml` (auto-created, 0600 permissions)

```toml
[rdp]
user = "User"
password = ""                # Auto-generated random password
password_updated = ""        # ISO 8601 timestamp
password_max_age = 7         # Days before auto-rotation (0 = disable)
ip = "127.0.0.1"
port = 3390
scale = 100                  # Auto-detected from your DE
dpi = 0                      # Windows DPI % (0 = auto)
extra_flags = ""             # Additional FreeRDP flags (allowlisted)

[pod]
backend = "podman"
win_version = "11"           # 11 | 10 | ltsc10 | tiny11 | tiny10
cpu_cores = 4
ram_gb = 4
vnc_port = 8007
auto_start = true            # Start pod automatically when launching an app
idle_timeout = 0             # Seconds before auto-suspend (0 = disabled)
```

## App Profiles

App profiles are **metadata only**: they define where a Windows app lives, not the app itself. The actual Windows application must be installed inside the Windows container.

### Bundled Profiles (14 apps)

| Profile | Requires Installation? |
|---------|----------------------|
| Notepad, Explorer, CMD, PowerShell, Paint, Calculator | No (built into Windows) |
| Word, Excel, PowerPoint, Outlook, OneNote, Access | Yes (install Office in the container) |
| VS Code | Yes (install VS Code in the container) |
| Teams | Yes (install Teams in the container) |

<details>
<summary><b>Adding custom app profiles</b></summary>

```bash
mkdir -p data/apps/myapp
cat > data/apps/myapp/app.toml << 'EOF'
name = "myapp"
full_name = "My Application"
executable = "C:\\Program Files\\MyApp\\myapp.exe"
categories = ["Utility"]
mime_types = []
EOF

winpodx app install myapp   # Register in desktop menu
```

</details>

## Multi-Session RDP

Stock Windows Desktop editions limit RDP to one session per user; a second app
would otherwise reconnect and steal the first session. winpodx bundles
[rdprrap](https://github.com/kernalix7/rdprrap) — a Rust reimplementation of
RDPWrap — inside the package itself and installs it automatically during the
Windows unattended install, so each RemoteApp window gets its own independent
session.

**Works fully offline.** The rdprrap zip ships inside winpodx's data directory
(`config/oem/`) and is staged into `C:\OEM\` during the guest's first boot.
sha256 is verified against a pin file before extraction. No network access is
required at install time.

Install is one-shot: the patch is applied during dockur's unattended setup
phase. If anything in that step fails (hash mismatch, extraction, installer
error), winpodx logs a warning and the guest stays in single-session mode —
app launch never blocks on this step. A guest-side management channel
(enable/disable/status after install) is planned for a later release.

## Install / Uninstall

```bash
./install.sh                # Install (detects distro, installs deps, registers apps)
./uninstall.sh              # Uninstall (interactive, asks before each step)
./uninstall.sh --confirm    # Uninstall (auto, keeps config)
./uninstall.sh --purge      # Uninstall (removes everything including config)
```

**Uninstall only removes winpodx files.** It never touches:
- Your Podman containers/volumes (Windows VM data)
- System packages (podman, freerdp, python3)
- Your home directory files

## Project Structure

```
winpodx/
├── install.sh             # One-line installer (no pip)
├── uninstall.sh           # Clean uninstaller
├── src/winpodx/
│   ├── cli/               # argparse commands (app, pod, config, setup, ...)
│   ├── core/              # Config, RDP, pod lifecycle, provisioner, daemon
│   ├── backend/           # Podman, Docker, libvirt, manual
│   ├── desktop/           # .desktop entries, icons, MIME, tray, notifications
│   ├── display/           # X11/Wayland detection, DPI scaling
│   ├── gui/               # Qt6 main window, app dialog, theme
│   └── utils/             # XDG paths, deps, TOML writer, winapps compat
├── data/apps/             # 14 bundled app definitions (TOML)
├── config/oem/            # Windows OEM scripts (post-install)
├── scripts/windows/       # PowerShell scripts (debloat, time sync, USB mapping)
├── .github/workflows/     # CI: lint + test + upstream update checker
└── tests/                 # pytest test suite (225 tests)
```

## Supported Distros

| Distro | Package Manager | Status |
|--------|----------------|--------|
| openSUSE Tumbleweed/Leap | zypper | Tested |
| Fedora / RHEL / CentOS | dnf | Supported |
| Ubuntu / Debian / Mint | apt | Supported |
| Arch / Manjaro | pacman | Supported |

## Testing

```bash
# From repo root (no install needed)
export PYTHONPATH="$PWD/src"
python3 -m pytest tests/ -v    # 225 tests
ruff check src/ tests/         # Lint
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and workflow.

## Releasing & Packaging

Each tag push (`v*.*.*`) publishes to all supported channels automatically:

| Channel | Distros |
|---------|---------|
| RPM (openSUSE / Fedora / Slowroll) | Tumbleweed, Leap 15.6, Leap 16.0, Slowroll, Fedora 42/43 |
| RPM (RHEL-family) | AlmaLinux 9 / 10 (also covers RHEL, Rocky, Oracle Linux 9/10) |
| `.deb` | Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10 |
| AUR | Arch Linux (once activated — see [`packaging/aur/README.md`](packaging/aur/README.md)) |
| `sdist` + `wheel` | PyPI-compatible source/binary distributions |

Maintainer setup for each channel lives under [`packaging/`](packaging/):

- [`packaging/obs/README.md`](packaging/obs/README.md) — openSUSE Build Service (RPM family).
- [`packaging/aur/README.md`](packaging/aur/README.md) — Arch User Repository.
- Debian/Ubuntu and AlmaLinux builds are self-contained in their respective GitHub Actions workflows and need no external setup.

## Security

For security issues, follow the process in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) - Kim DaeHyun (kernalix7@kodenet.io)
