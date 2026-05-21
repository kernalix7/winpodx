<div align="center">

<img src="docs/images/CI.svg" alt="winpodx" width="320">

### Click an app. Word opens. That's it.

<p>Native Linux windows for every Windows app — real icons, real <code>WM_CLASS</code>,<br>
pin-to-taskbar. FreeRDP RemoteApp + dockur/windows. Zero config.</p>

<pre><code># Latest stable release (default)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash

# Latest main HEAD (development; may be unstable)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --main

# Uninstall (keeps Windows VM data; pass --purge to wipe everything)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --confirm</code></pre>

<a href="docs/images/demo.png">
  <img src="docs/images/demo.png" alt="winpodx in action — Windows apps as native Linux windows on KDE" width="720">
</a>

<sub>Windows About / Performance Monitor / PowerShell each in their own Linux window, alongside the winpodx Apps grid.</sub>

[![Beta](https://img.shields.io/badge/status-beta-orange?style=for-the-badge)](#status-beta)
[![Latest](https://img.shields.io/github/v/release/kernalix7/winpodx?include_prereleases&style=for-the-badge&label=latest&color=2962FF)](https://github.com/kernalix7/winpodx/releases)

[![license](https://img.shields.io/github/license/kernalix7/winpodx?style=flat-square&color=blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![tests](https://img.shields.io/badge/tests-1240%2B-2EA44F?style=flat-square)](#testing)
[![CI](https://img.shields.io/github/actions/workflow/status/kernalix7/winpodx/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/kernalix7/winpodx/actions/workflows/ci.yml)
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

<sub>**English** &nbsp;·&nbsp; [한국어](docs/README.ko.md) &nbsp;·&nbsp; [Install](docs/INSTALL.md) &nbsp;·&nbsp; [Usage](docs/USAGE.md) &nbsp;·&nbsp; [Features](docs/FEATURES.md) &nbsp;·&nbsp; [Architecture](docs/ARCHITECTURE.md) &nbsp;·&nbsp; [Comparison](docs/COMPARISON.md)</sub>

</div>

---

> ### Status: Beta
> winpodx is in active development (**v0.5.7**). Reverse-open (v0.5.0) — Linux apps in the Windows "Open with…" menu — is default-on with per-app icons that round-trip to the host's `xdg-open`. v0.5.5 added a host-adaptive Windows-on-KVM tuning profile (`+invtsc`, `platform_tick`, more flags gated by host capability) plus automatic `UNRESPONSIVE → recover` for stalled RDP sessions. v0.5.7 fixes "Launching… but no RDP window" on modern rootless podman + pasta. v0.5.7 lands the Windows-guest timezone wiring via dockur's `TZ` env var (auto-detected from the host), a per-item debloat picker (CLI `--list` / `--preset` / `--items` / `--undo` / `--menu` plus a Qt picker dialog with risk badges), `winpodx pod recreate [--wipe-storage]`, and Settings → Container/VM dropdowns for Language / Region / Keyboard / Timezone. First install still takes ~5–10 minutes (Windows VM ISO download + Sysprep + OEM apply); `winpodx pod wait-ready --logs` shows live progress. Please file issues at <https://github.com/kernalix7/winpodx/issues> if something breaks.

**No full-screen RDP.** Each Windows app becomes its own Linux window with its real icon — pinnable, alt-tabbable, file-associated, both directions. Drop into a full Windows desktop only when you actually want one (`winpodx app run desktop`).

winpodx runs a Windows container (via [dockur/windows](https://github.com/dockur/windows)) in the background and presents Windows apps as native Linux applications through FreeRDP RemoteApp, while a bearer-authed HTTP agent inside the guest handles the host→guest command channel without flashing a PowerShell window. The reverse direction — Linux apps surfaced in the Windows "Open with…" menu — is handled by a host-side listener that consumes JSON requests written by per-slug Rust shims inside the guest. **Near-zero external Python dependencies** (stdlib only on Python 3.11+; one pure-Python `tomli` fallback on 3.9/3.10).

## Minimum requirements

**Before installing**, make sure your machine actually supports virtualisation. winpodx runs Windows in a KVM-backed container; without these three, the install will run to completion but Windows will never boot.

| Requirement | How to check | Fix |
|---|---|---|
| **Intel VT-x or AMD-V enabled in BIOS / UEFI** | `lscpu \| grep -i virtualization` shows `VT-x` or `AMD-V` | Reboot → firmware setup → enable "Intel Virtualization Technology" / "SVM Mode" / "VT-x". OFF by default on many laptops. |
| **kvm kernel module loaded** | `lsmod \| grep kvm` lists `kvm_intel` or `kvm_amd` | `sudo modprobe kvm_intel` (Intel) or `sudo modprobe kvm_amd` (AMD). Auto-loads on next boot once BIOS allows it. |
| **Your user is in the `kvm` group** | `id -nG \| tr ' ' '\n' \| grep kvm` returns `kvm` | `sudo usermod -aG kvm $USER`, then log out + back in. |

Hardware: x86_64 or aarch64 CPU with virtualisation extensions, 8 GB+ RAM (12 GB+ recommended), ~30 GB free disk for the Windows image. `install.sh` aborts with the same diagnostic if `/dev/kvm` is missing after the package install step — most "install ran fine but Windows never boots" bug reports trace back to one of the rows above.

## Quick install

One-liner (any supported Linux distro):

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

Or via a native package manager:

```bash
# openSUSE Tumbleweed / Leap / Slowroll
sudo zypper addrepo https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
sudo zypper install winpodx

# Fedora 42 / 43 / 44 (dnf5 — Fedora 41+)
sudo dnf config-manager addrepo --from-repofile=https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx

# Debian / Ubuntu — grab the matching .deb from the latest release
sudo apt install ./winpodx_0.5.7_all_debian13.deb

# AlmaLinux / Rocky / RHEL 9 / 10 — grab the matching .rpm
sudo dnf install ./winpodx-0.5.7-0.noarch.el10.rpm

# Arch
yay -S winpodx

# Nix
nix run github:kernalix7/winpodx
```

> **After a package-manager install:** run `winpodx setup` once to generate `~/.config/winpodx/winpodx.toml` + compose.yaml. The curl one-liner does this for you (and waits ~5–10 min for the Windows first boot); package installs ship the binary only so `apt install` / `dnf install` / `yay -S` don't trigger a 10-minute Windows ISO download out of the blue. After setup, just launching an app (`winpodx app run desktop`) auto-provisions the pod the first time.

See [docs/INSTALL.md](docs/INSTALL.md) for offline / air-gapped builds, source installs, version pinning, and uninstall.

## Launch

```bash
winpodx app run word              # Launch Word
winpodx app run word ~/doc.docx   # Open a file
winpodx app run desktop           # Full Windows desktop
```

Or just click an app icon in your application menu. See [docs/USAGE.md](docs/USAGE.md) for the full CLI, the Qt6 GUI, health checks, and configuration.

## Key features

<table>
<tr><td width="50%">

**Reverse-open**
- Linux apps appear in the Windows guest's right-click "Open with…" menu by default
- Correct per-app icons in both the short menu and the long "Choose another app" dialog
- Selecting one round-trips the file open to host `xdg-open`
- Auto-discovers host-side Linux apps + their MIME associations from freedesktop standards
- Manage via `winpodx host-open` CLI or the GUI Settings panel
- [Details →](docs/FEATURES.md#reverse-open-linux-apps-in-windows-open-with)

</td><td width="50%">

**Seamless app windows**
- RemoteApp (RAIL) renders each Windows app as a native Linux window — no full desktop
- Per-app taskbar icons via `WM_CLASS` matching (`/wm-class:<stem>` + `StartupWMClass`)
- Bidirectional file associations: double-click `.docx` in your file manager → Word opens
- Multi-session RDP: bundled [rdprrap](https://github.com/kernalix7/rdprrap) auto-enables up to 10 independent sessions
- RAIL prerequisites set automatically during unattended install

</td></tr>
<tr><td width="50%">

**Zero-config launch**
- First app click auto-provisions everything: config, container, desktop entries
- Auto-discovery on first boot scans the running Windows guest and registers every installed app with its real icon (Registry App Paths, Start Menu, UWP/MSIX, Chocolatey, Scoop)
- Manual rescan any time via `winpodx app refresh` or the GUI Refresh button
- Multi-backend: Podman (default), Docker, libvirt/KVM, manual RDP

</td><td width="50%">

**Peripherals & sharing**
- **Clipboard**: bidirectional copy-paste (text + images) — on by default
- **Sound**: RDP audio streaming (`/sound:sys:alsa`) — on by default
- **Printer**: Linux printers shared to Windows — on by default
- **Home directory**: shared as `\\tsclient\home`
- **USB drives**: auto-mapped to drive letters (E:, F:, …) via FileSystemWatcher; subfolders work for drives plugged in after session start
- **USB device passthrough**: opt-in via `extra_flags` (`/usb:auto`)

</td></tr>
<tr><td width="50%">

**Automation & security**
- Auto suspend / resume: container pauses when idle, resumes on next launch
- UNRESPONSIVE → recover (v0.5.5): stalled RDP guest is detected on `RUNNING → UNRESPONSIVE` and self-healed via in-guest TermService cycle, no `pod restart` needed
- Host-adaptive Windows-on-KVM tuning profile (v0.5.5): `+invtsc`, `platform_tick` and more, gated by host capability — `tuning_profile = auto|safe|off`
- Password auto-rotation: 20-char cryptographic password, 7-day cycle with atomic rollback
- Smart DPI scaling: auto-detects from GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb
- Windows debloat: telemetry, ads, Cortana, search indexing disabled by default
- FreeRDP `extra_flags` allowlist (regex-validated) as the user-input safety boundary
- Time sync: force Windows clock resync after host sleep/wake

</td><td width="50%">

**Operations & resilience**
- Offline / air-gapped install (`--source` + `--image-tar`)
- One-line uninstall (keeps Windows VM data unless `--purge`)
- Health checks via `winpodx check` (pod / RDP / agent / disk / round-trip / password age)
- Qt6 GUI: Apps / Settings / Tools / Terminal / Info pages — plus a lighter system tray
- Stdlib-leaning Python (no pip-deps on 3.11+; one `tomli` fallback on 3.9 / 3.10)

</td></tr>
</table>

See [docs/FEATURES.md](docs/FEATURES.md) for deep dives, including multi-session RDP internals, app profile schema, and the reverse-open architecture.

## Documentation

| Document | What's inside |
|----------|---------------|
| [INSTALL.md](docs/INSTALL.md) | Every install path — one-liner, package managers, offline, Nix, source |
| [USAGE.md](docs/USAGE.md) | CLI reference, Qt6 GUI tour, health checks, configuration file |
| [FEATURES.md](docs/FEATURES.md) | Reverse-open, multi-session RDP, peripherals, app profiles, auto-discovery |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it works (diagram), tech stack, source tree, data flows |
| [COMPARISON.md](docs/COMPARISON.md) | winpodx vs winapps / LinOffice / winboat, and winpodx vs Wine |
| [CHANGELOG.md](CHANGELOG.md) | Full version history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and workflow |
| [SECURITY.md](SECURITY.md) | Security disclosure process |

## Supported distros

| Distro | Package manager | Status |
|--------|-----------------|--------|
| openSUSE Tumbleweed / Leap 15.6 / Leap 16.0 / Slowroll | zypper | Tested |
| Fedora 42 / 43 / 44 / Rawhide | dnf | Supported |
| Fedora Silverblue / Kinoite / Sericea / Bluefin / Bazzite (42 / 43 / 44) | rpm-ostree (OBS, `--apply-live`) | Supported |
| Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10 / 26.04 | apt | Supported |
| AlmaLinux / Rocky / RHEL 9 / 10 | dnf | Supported |
| Arch / Manjaro | pacman + `yay -S winpodx` | Supported |
| NixOS (and Nix on any distro) | nix flake | Supported |

Each tag push (`v*.*.*`) publishes to all channels automatically — see [packaging/](packaging/) for maintainer details.

## Testing

```bash
# From repo root (no install needed)
export PYTHONPATH="$PWD/src"
python3 -m pytest tests/    # 1240+ tests
ruff check src/ tests/      # Lint
ruff format --check src/ tests/
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, branch naming, commit conventions, and CI expectations.

## Security

For security issues, follow the process in [SECURITY.md](SECURITY.md).

## Star History

<a href="https://star-history.com/#kernalix7/winpodx&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=kernalix7/winpodx&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=kernalix7/winpodx&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=kernalix7/winpodx&type=Date" />
  </picture>
</a>

## Support

If winpodx makes your Linux desktop a little nicer:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-F16061?logo=ko-fi&logoColor=white&style=for-the-badge)](https://ko-fi.com/kernalix7)
[![Fairy](https://img.shields.io/badge/🧚_Fairy-EE6E73?style=for-the-badge&logoColor=white)](https://fairy.hada.io/@kernalix7)

Ko-fi handles international cards and PayPal; fairy.hada.io is a Korean tipping platform. Bug reports, PRs, and stars on the repo are equally appreciated and free.

## License

[MIT](LICENSE) — Kim DaeHyun (kernalix7@kodenet.io)
