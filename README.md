<div align="center">

<img src="data/winpodx-icon.svg" alt="winpodx" width="128">

# winpodx

**Run Windows applications seamlessly on Linux**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://www.python.org/)
[![Backend: Podman](https://img.shields.io/badge/Backend-Podman-purple.svg)](https://podman.io/)
[![Tests: 92 passed](https://img.shields.io/badge/Tests-92%20passed-brightgreen.svg)](#testing)

**English** | [한국어](docs/README.ko.md)

*Click an app icon in your Linux menu. Word opens. That's it.*

</div>

---

winpodx runs a Windows container (via Podman) in the background and presents Windows apps as native Linux applications through FreeRDP RemoteApp. No manual VM setup. **Zero external Python dependencies** — stdlib only (Python 3.11+).

## Key Features

<table>
<tr><td width="50%">

**Launch & Integration**
- Zero-config auto-provisioning
- 14 bundled app profiles
- `.desktop` entries, icons, MIME types
- Qt6 system tray with full controls
- Per-app taskbar icons (WM_CLASS)
- Smart DPI scaling per DE

</td><td width="50%">

**Automation & Security**
- Auto suspend/resume (saves CPU)
- Password auto-rotation (7-day cycle)
- TLS-only RDP on 127.0.0.1
- Multi-session via RDPWrap
- Windows build pinning
- CI upstream dependency tracking

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
                     │   │  RDPWrap multi-session│   │
                     │   │  Word  Excel  PPT ... │   │
                     │   └──────────────────────┘   │
                     │   127.0.0.1:3390 (TLS)       │
                     └─────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.11+ (stdlib only, no pip) |
| CLI | argparse (stdlib) |
| GUI (optional) | PySide6 (Qt6) |
| Config | TOML (stdlib tomllib + built-in writer) |
| RDP | FreeRDP 3+ (xfreerdp) |
| Multi-session | [RDPWrap](https://github.com/stascorp/rdpwrap) (built from source) + [OffsetFinder](https://github.com/llccd/RDPWrapOffsetFinder) |
| Container | Podman / Docker ([dockur/windows](https://github.com/dockur/windows)) |
| VM | libvirt / KVM |

## Quick Start

### Install

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
./install.sh
```

The installer automatically:
1. Detects your distro (openSUSE, Fedora, Ubuntu, Arch, ...)
2. Installs missing dependencies (Podman, FreeRDP, KVM) — asks before installing
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

App profiles are **metadata only** — they define where a Windows app lives, not the app itself. The actual Windows application must be installed inside the Windows container.

### Bundled Profiles (14 apps)

| Profile | Requires Installation? |
|---------|----------------------|
| Notepad, Explorer, CMD, PowerShell, Paint, Calculator | No — built into Windows |
| Word, Excel, PowerPoint, Outlook, OneNote, Access | Yes — install Office in the container |
| VS Code | Yes — install VS Code in the container |
| Teams | Yes — install Teams in the container |

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

## Multi-Session RDP (RDPWrap)

Windows Desktop edition limits RDP to one session per user — opening a second app disconnects the first. winpodx uses [RDPWrap](https://github.com/stascorp/rdpwrap) to remove this limitation.

| Step | What happens |
|------|-------------|
| **CI build** | RDPWrap + OffsetFinder built from source via GitHub Actions (manual trigger) |
| **First boot** | `setup_rdpwrap.ps1` installs RDPWrap, OffsetFinder generates `rdpwrap.ini` from actual `termsrv.dll` |
| **Symbol source** | Microsoft's official PDB server (`msdl.microsoft.com`) — no third-party downloads |
| **Taskbar** | Each app gets its own `/wm-class` + `StartupWMClass` for independent taskbar icons |
| **Build pinning** | Feature updates blocked via registry policy; security updates install normally |
| **INI update** | Manual only — GUI "Update RDPWrap" button regenerates offsets from current DLL |
| **Upstream watch** | CI checks weekly for new releases, creates PRs (no auto-merge) |

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
├── config/oem/            # Windows OEM scripts (RDPWrap setup, post-install)
├── scripts/windows/       # PowerShell scripts (debloat, time sync, RDP setup)
├── .github/workflows/     # CI: build RDPWrap, check upstream updates
└── tests/                 # pytest test suite (92 tests)
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
python3 -m pytest tests/ -v    # 92 tests
ruff check src/ tests/         # Lint
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and workflow.

## Security

For security issues, follow the process in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) - Kim DaeHyun
