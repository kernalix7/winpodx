# Comparison

**English** | [한국어](COMPARISON.ko.md)

How WinPodX compares to other tools for running Windows applications on Linux.

## Why WinPodX?

Existing tools for running Windows apps on Linux all have trade-offs:

| | winapps | LinOffice | winboat | WinPodX |
|---|---|---|---|---|
| Core tech | Any RDP-capable Windows host (cloud / physical / container) + FreeRDP | dockur + FreeRDP | dockur + FreeRDP | dockur (Podman) + FreeRDP + HTTP guest agent |
| Setup | Manual VM/config/RDP setup + installer wizard | One-liner + Qt GUI | One-click GUI installer | **One-liner or non-interactive auto setup** |
| Interface | Shell CLI + optional launcher/tray | Qt6 GUI + shell CLI | Electron GUI | **Qt6 GUI + CLI + tray** |
| App scope | Any Windows app | Office only | Any Windows app | Any Windows app |
| Language | Shell | Shell + Python | TypeScript / Vue / Go | **Python-first + guest PowerShell/Rust shim** |
| Runtime deps | Shell tools + FreeRDP; VM backend separately | Podman, podman-compose, FreeRDP 3; Python/PySide6 for GUI | Electron, Docker/Podman, FreeRDP | **Python 3.9+, FreeRDP 3+, Podman 4+ + podman-compose (default)** |
| Auto suspend / resume | Yes (optional) | Yes | Not documented | **Yes (idle timeout, opt-in)** |
| Password rotation | No | No | Not documented | **Yes (7-day, atomic)** |
| HiDPI auto-detect | Manual scale setting | Host-detected display scaling | User-configured scale | **GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb** |
| Sound default | Yes (`/sound`) | No | Yes (FreeRDP) | Yes (FreeRDP) |
| Printer redirection default | No | No | Not documented | Yes (FreeRDP) |
| Removable-media sharing | Configurable host media path | Home share | Home share; smartcard passthrough | **`\\tsclient\media\<LABEL>` + USB desktop shortcut** |
| Host USB / PCI device passthrough | No | No | Smartcard only | **Yes (`device list / attach / detach`, GUI Devices page, tray USB switcher; USB live hot-plug, PCI boot-added)** |
| Discovery (auto-scan installed apps) | Yes (Registry + tested profiles) | No (Office-focused profiles) | Yes (including UWP) | **Yes (Start Menu + UWP by default; full Registry/choco/scoop opt-in)** |
| Multi-session RDP | No | No | Not documented | **Yes (bundled rdprrap, 25 by default; configurable 1–50)** |
| Reverse file open (guest → host xdg-open) | No | No | No | **Yes (Linux apps in Windows "Open with…" menu)** |
| Windows disk auto-grow | No | No | No | **Yes (idle, bounded by host free space)** |
| Guest sync (in-place update, no reinstall) | No | No | No | **Yes (auto on pod start + `guest sync`)** |
| Multilingual UI | English | English UI with automatic Windows locale setup | English | **Yes (7 languages, locale auto-detect)** |
| Offline / air-gapped install | No | No | No | **Yes (`--source` + `--image-tar`)** |
| License | Mixed (mostly AGPL-3.0; inherited unlicensed files remain) | AGPL-3.0 | MIT | MIT |

> winboat is the closest peer in scope and was an inspiration. We focus on a different mix — stdlib-leaning Python + Qt6 instead of Electron, 7-day atomic password rotation, multi-DE HiDPI auto-detection, reverse-open (Linux apps appear in the Windows "Open with…" menu by default), a multilingual UI (7 languages, auto-detected from the locale), a self-managing Windows disk that auto-grows as it fills, in-place guest sync that pushes host updates into a running guest without reinstalling, and an explicit air-gapped install path. Both projects build on dockur/windows; that ecosystem is bigger than any one app.

## WinPodX vs Wine

**WinPodX is not a Wine replacement.** Wine translates Windows API calls; WinPodX runs the actual Windows OS in a container. The two solve different problems and many users have both installed.

| When you need... | Use |
|---|---|
| Older Win32 apps, indie games, lightweight utilities | **Wine / Bottles / Lutris** |
| GPU-accelerated games / 3D apps (DirectX 9 – 12) | **Wine** — DXVK / VKD3D give near-native frame rates. WinPodX has no GPU passthrough by default; QEMU CPU rendering is much slower. (GPU passthrough via VFIO is a manual bring-your-own setup — not yet packaged.) |
| Microsoft 365 desktop apps, including Outlook, Teams, and OneDrive | **WinPodX** |
| Adobe Creative Suite (Photoshop, Illustrator, Premiere, Lightroom) | WinPodX — but heavy GPU effects will be CPU-bound (see GPU row above) |
| Anti-cheat games (Valorant, EAC, BattlEye) | **TBD** — anti-cheats vary by VM-detection policy (Vanguard needs TPM 2.0 + no hypervisor, EAC mostly blocks VMs, VAC is lenient). Test before committing. |
| DRM-heavy software / hardware dongle apps | **WinPodX, when the device can be passed through and the software permits VMs** |
| Apps that ship kernel-mode drivers (some VPNs, security suites) | **WinPodX, unless the driver blocks virtual machines** |
| Banking / tax / government tools with regional certificates | **WinPodX** — subject to the tool's VM policy |
| Visual Studio, WinUI 3 / WinRT, .NET features Wine hasn't caught up to | **WinPodX** — GPU-heavy workloads still need manual passthrough |
| IE-only legacy enterprise web apps | **WinPodX** |
| Apps that require a genuine Windows userspace/kernel rather than Wine's compatibility layer | **WinPodX** — subject to hardware and VM-detection constraints |

Wine wins on speed and on GPU when DXVK/VKD3D translate cleanly. WinPodX provides a genuine Windows kernel and userspace for applications that depend on Windows components Wine does not implement, rendered into your Linux desktop through FreeRDP RemoteApp. That improves compatibility, but does not guarantee every app: VM detection, anti-cheat, GPU requirements, and unsupported hardware can still block software.
