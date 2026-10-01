<div align="center">

<img src="docs/images/CI.svg" alt="WinPodX" width="320">

### Click an app. Word opens. That's it.

<p>Windows apps as native Linux windows — real icons, real <code>WM_CLASS</code>, pin-to-taskbar.<br>
FreeRDP RemoteApp over dockur/windows. No permanent full-screen desktop, no manual setup.</p>

<pre><code># Install (latest stable release)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash

# Development branch (may be unstable)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --main

# Uninstall (keeps the Windows VM; add --purge to wipe everything)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --confirm</code></pre>

<a href="docs/images/demo.png">
  <img src="docs/images/demo.png" alt="Windows apps as native Linux windows on KDE" width="720">
</a>

<sub>Windows About / Task Manager / PowerShell, each in its own Linux window, next to the WinPodX Dashboard.</sub>

[![Beta](https://img.shields.io/badge/status-beta-orange?style=for-the-badge)](#status-beta)
[![Latest](https://img.shields.io/github/v/release/kernalix7/winpodx?include_prereleases&style=for-the-badge&label=latest&color=2962FF)](https://github.com/kernalix7/winpodx/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/kernalix7/winpodx/ci.yml?branch=main&style=for-the-badge&label=CI)](https://github.com/kernalix7/winpodx/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-4000%2B-2EA44F?style=for-the-badge)](#testing)
[![Discord](https://img.shields.io/badge/Discord-join-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.gg/j2kmzUNaR3)

[![license](https://img.shields.io/github/license/kernalix7/winpodx?style=flat-square&color=blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![stars](https://img.shields.io/github/stars/kernalix7/winpodx?style=flat-square&color=FFD93D&logo=github&logoColor=white)](https://github.com/kernalix7/winpodx/stargazers)
[![downloads](https://img.shields.io/github/downloads/kernalix7/winpodx/total?style=flat-square&color=2EA44F)](https://github.com/kernalix7/winpodx/releases)

###### Works on

[![openSUSE](https://img.shields.io/badge/openSUSE-73BA25?style=flat-square&logo=opensuse&logoColor=white)](https://www.opensuse.org/)
[![Fedora](https://img.shields.io/badge/Fedora-294172?style=flat-square&logo=fedora&logoColor=white)](https://fedoraproject.org/)
[![Fedora Atomic Desktops](https://img.shields.io/badge/Fedora%20Atomic-294172?style=flat-square&logo=fedora&logoColor=white)](https://fedoraproject.org/atomic-desktops/)
[![Debian](https://img.shields.io/badge/Debian-A81D33?style=flat-square&logo=debian&logoColor=white)](https://www.debian.org/)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-E95420?style=flat-square&logo=ubuntu&logoColor=white)](https://ubuntu.com/)
[![RHEL family](https://img.shields.io/badge/RHEL%20%2F%20Alma%20%2F%20Rocky-EE0000?style=flat-square&logo=redhat&logoColor=white)](https://www.redhat.com/)
[![Arch](https://img.shields.io/badge/Arch-1793D1?style=flat-square&logo=archlinux&logoColor=white)](https://archlinux.org/)
[![NixOS](https://img.shields.io/badge/NixOS-5277C3?style=flat-square&logo=nixos&logoColor=white)](docs/INSTALL.md#nix)
[![AppImage](https://img.shields.io/badge/AppImage-any%20distro-6F42C1?style=flat-square&logo=appimage&logoColor=white)](docs/INSTALL.md)

<sub>**English** · [한국어](docs/README.ko.md) · [Install](docs/INSTALL.md) · [Usage](docs/USAGE.md) · [Features](docs/FEATURES.md) · [Architecture](docs/ARCHITECTURE.md) · [Comparison](docs/COMPARISON.md)</sub>

</div>

---

**Contents:** [Requirements](#requirements) · [Install](#install) · [Launch](#launch) · [Desktop app](#desktop-app) · [Key features](#key-features) · [Documentation](#documentation) · [Testing](#testing) · [Contributing](#contributing-and-license)

---

> ### Status: Beta
>
> WinPodX is in active development, **v0.11.0**.
>
> - A Windows 11 Settings-style desktop app: adaptive navigation, custom window chrome, and a theme that follows your desktop's light or dark scheme
> - Dashboard for pod state, resource use, running apps, and pinned apps in one view
> - dockur HTTP provisioning progress in the CLI and GUI, with a log-based fallback
> - `winpodx launch` as a compact Start-style flyout, plus a refreshed tray launcher
> - Python 3.10 is the new minimum
>
> Full detail in the [CHANGELOG](CHANGELOG.md).

## What you get

Each Windows app opens as its own Linux window, keeping its icon, taskbar entry, and file associations. Clipboard, audio, printers, and your home directory are shared both ways. You only see a full Windows desktop when you ask for one with `winpodx app run desktop`.

WinPodX runs Windows in a container in the background and starts it on demand — the pod pauses when idle and wakes on your next launch.

## Requirements

Virtualization must be available before Windows can boot. These three checks catch almost every "installed fine but Windows never started" report:

| Requirement | Check | Fix |
|---|---|---|
| Intel VT-x or AMD-V enabled | `lscpu \| grep -i virtualization` | Enable Intel Virtualization Technology, SVM Mode, or VT-x in firmware. |
| KVM module loaded | `lsmod \| grep kvm` | `sudo modprobe kvm_intel` or `sudo modprobe kvm_amd`. |
| Your user can reach KVM | `id -nG \| tr ' ' '\n' \| grep kvm` | `sudo usermod -aG kvm $USER`, then log out and back in. |

**Hardware:** x86_64 or aarch64 with virtualization extensions, 8 GB RAM (12 GB recommended), and room for the 64 GB default Windows disk plus an installer ISO.

**Software:** FreeRDP 3+ and Podman with a compose provider, or Docker. Rootless Podman also needs `/etc/subuid` and `/etc/subgid` entries. Run `winpodx setup-host` to fix the group and subuid setup in one prompt, and `winpodx doctor` any time to see what is still missing.

## Install

The curl one-liner in the header works on every supported distribution and finishes setup for you. Native packages are also available:

```bash
# openSUSE Tumbleweed / Leap / Slowroll
sudo zypper addrepo https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
sudo zypper install winpodx

# Fedora 42 / 43 / 44 (dnf5 — Fedora 41+)
sudo dnf config-manager addrepo --from-repofile=https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx

# Debian / Ubuntu — the matching .deb from the latest release
sudo apt install ./winpodx_<version>_all_debian13.deb

# AlmaLinux / Rocky / RHEL 9 / 10 — the matching .rpm
sudo dnf install ./winpodx-<version>-0.noarch.el10.rpm

# Arch
yay -S winpodx

# Nix
nix run github:kernalix7/winpodx

# AppImage — one file, any x86_64 distro
chmod +x winpodx-x86_64.AppImage
./winpodx-x86_64.AppImage setup
```

**After a package, AppImage, source, or wheel install, run setup once.** Package installs deliberately ship the binary only, so `apt install` never triggers a long Windows download on its own:

```bash
winpodx setup              # host-detected defaults, no prompts
winpodx setup --customize  # pick backend, VM settings, storage directory, and optional local Windows ISO
```

Setup writes the configuration, verifies the host, provisions Windows, discovers your apps, and registers desktop entries.
In `--customize`, press Enter at the storage and ISO prompts to keep the default storage directory and download Windows; the wizard reviews both choices before creating the pod. Existing guest storage is not moved or replaced by setup; use `winpodx setup --migrate-storage` to relocate it.

**Updating:** re-run the curl installer to upgrade a curl install in place, keeping your config and VM (this works on Bazzite and other rpm-ostree hosts too). Package and AppImage installs update through whatever you installed them with.

On RHEL 9, AlmaLinux 9, and Rocky Linux 9 the default `python3` is older than WinPodX supports; the el9 package pulls in the Python 3.11 stack from AppStream automatically.

Offline and air-gapped installs, building from source, Nix, and full uninstall are covered in [INSTALL.md](docs/INSTALL.md).

## Launch

```bash
winpodx app run word              # Launch Word
winpodx app run word ~/doc.docx   # Open a file with it
winpodx app run desktop           # Full Windows desktop
winpodx launch                    # Searchable Start-style app picker
winpodx gui                       # Desktop app
```

Or just click a Windows app in your application menu — WinPodX installs real desktop entries for everything it discovers. Bind `winpodx launch` to a custom shortcut (KDE: *System Settings → Shortcuts*; GNOME: *Settings → Keyboard*) for a system-wide hotkey.

## Desktop app

<a href="docs/images/gui-dashboard.png">
  <img src="docs/images/gui-dashboard.png" alt="WinPodX Dashboard" width="720">
</a>

`winpodx gui` opens a Windows 11 Settings-style shell with eight pages:

| Page | What it holds |
|---|---|
| **Dashboard** | Pod state with Start/Stop, RAM / CPU / disk rings, quick actions, running sessions, pinned apps |
| **Applications** | Start Menu tiles with search, category counts, grid or list view, and per-app actions |
| **Settings** | Connection, hardware, Windows Update, integration, and language, grouped by intent, with a marker until you save |
| **Tools** | Pod and guest operations — suspend, resume, grow disk, debloat, apply fixes |
| **Terminal** | Pod and app logs with a filtered command bar |
| **Info** | Version, health checks, and one-click copyable diagnostics |
| **Devices** | USB and PCI passthrough, grouped by bus, with risky assignments flagged |
| **License** | License text and third-party acknowledgements |

The navigation pane is 320 px wide normally and collapses to a 48 px icon rail below 1100 px, where the hamburger button overlays it on top of the content. The theme follows your desktop's light or dark preference; set `WINPODX_COLOR_SCHEME=light|dark` to override it, or `WINPODX_NATIVE_TITLEBAR=1` to use your window manager's decorations instead of the built-in title bar. See the [GUI tour](docs/USAGE.md#qt6-gui-tour) for the rest.

## Key features

| Area | What it does |
|---|---|
| **Seamless apps** | RemoteApp opens each app as a native window with real icons, `WM_CLASS`, taskbar integration, file associations, multi-monitor support, and up to 50 concurrent RDP sessions. |
| **App discovery** | Imports Start Menu-visible Win32 and UWP apps with their icons. Rescan with `winpodx app refresh` or from Applications. |
| **Sharing** | Two-way clipboard, audio, printers, `\\tsclient\home`, removable media, and USB or PCI passthrough. |
| **Reverse-open** | Linux apps appear in the Windows **Open with** menu and receive files back on the host through a controlled listener. |
| **Pod operation** | Podman by default, with Docker and manual RDP supported. Auto-pauses when idle, recovers a stalled guest, rotates passwords, and grows the Windows disk. |
| **Privacy and tuning** | Optional Windows debloat, host-adaptive KVM tuning, DPI detection, an allowlist for FreeRDP flags, time sync, and optional bare-metal disguise. |
| **Languages** | CLI, tray, and desktop app in English, Korean, Chinese, Japanese, German, French, and Italian. |

## Documentation

| Document | Contents |
|---|---|
| [INSTALL.md](docs/INSTALL.md) | Every install path, updating, offline, source, Nix, uninstall |
| [USAGE.md](docs/USAGE.md) | CLI reference, GUI tour, health checks, configuration |
| [FEATURES.md](docs/FEATURES.md) | RemoteApp, reverse-open, peripherals, discovery, passthrough |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System diagram, source tree, data flows |
| [COMPARISON.md](docs/COMPARISON.md) | WinPodX vs winapps, LinOffice, winboat, and Wine |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and workflow |
| [SECURITY.md](SECURITY.md) | Security reporting process |

## Testing

```bash
export PYTHONPATH="$PWD/src"
python3 -m pytest tests/ -n auto     # 4000+ tests, seconds in parallel
ruff check src/ tests/
ruff format --check src/ tests/
```

## Contributing and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) before sending a change; security reports follow [SECURITY.md](SECURITY.md). WinPodX is [MIT licensed](LICENSE), Kim DaeHyun.

For setup help, questions, and release chatter, join the [Discord community](https://discord.gg/j2kmzUNaR3). Bugs and feature requests still belong in [issues](https://github.com/kernalix7/winpodx/issues) so they stay searchable.

## Star History

<a href="https://github.com/kernalix7/winpodx/tree/star-history">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/kernalix7/winpodx/star-history/chart-dark.svg" />
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/kernalix7/winpodx/star-history/chart.svg" />
    <img alt="WinPodX GitHub star history" src="https://raw.githubusercontent.com/kernalix7/winpodx/star-history/chart.svg" width="900" />
  </picture>
</a>

## Support

If WinPodX makes your Linux desktop a little nicer:

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-EA4AAA?logo=githubsponsors&logoColor=white&style=for-the-badge)](https://github.com/sponsors/kernalix7)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-F16061?logo=ko-fi&logoColor=white&style=for-the-badge)](https://ko-fi.com/kernalix7)
[![Fairy](https://img.shields.io/badge/🧚_Fairy-EE6E73?style=for-the-badge&logoColor=white)](https://fairy.hada.io/@kernalix7)

GitHub Sponsors handles recurring or one-time sponsorship, Ko-fi covers international cards and PayPal, and fairy.hada.io is a Korean tipping platform. Bug reports, PRs, and stars are just as welcome — and free.
