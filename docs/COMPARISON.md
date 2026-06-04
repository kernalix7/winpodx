# Comparison

**English** | [한국어](COMPARISON.ko.md)

How WinPodX compares to other tools for running Windows applications on Linux.

## Why WinPodX?

Existing tools for running Windows apps on Linux all have trade-offs:

| | winapps | LinOffice | winboat | WinPodX |
|---|---|---|---|---|
| Core tech | Any RDP-capable Windows host (cloud / physical / container) + FreeRDP | dockur + FreeRDP | dockur + FreeRDP | dockur (Podman) + FreeRDP + HTTP guest agent |
| Setup | Manual (shell + config + RDP testing) | One-liner script | One-click GUI installer | **Zero-config** (auto on first launch) |
| Interface | CLI only | CLI only | Electron GUI | **Qt6 GUI + CLI + tray** |
| App scope | Any Windows app | Office only | Any Windows app | Any Windows app |
| Language | Shell (86%) | Shell + Python | TypeScript / Vue / Go | **Python (100%)** |
| Runtime deps | curl, dialog, git, netcat | Podman, FreeRDP | Electron, Docker/Podman, FreeRDP | **Python 3.9+, FreeRDP, Podman** |
| Auto suspend / resume | No | No | Not documented | **Yes (idle timeout)** |
| Password rotation | No | No | Not documented | **Yes (7-day, atomic)** |
| HiDPI auto-detect | No | No | Not documented | **GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb** |
| Sound default | No | No | Yes (FreeRDP) | Yes (FreeRDP) |
| Printer redirection default | No | No | Not documented | Yes (FreeRDP) |
| USB drive auto-mapping | No | No | Smartcard passthrough | **Drive subfolders → drive letters via FileSystemWatcher** |
| Host USB / PCI device passthrough | No | No | Smartcard only | **Yes (`device list / attach / detach`, GUI Devices page, tray USB switcher; USB live hot-plug, PCI boot-added)** |
| Discovery (auto-scan installed apps) | No | No | Yes | **Yes (Registry + Start Menu + UWP + choco/scoop)** |
| Multi-session RDP | No | No | Not documented | **Yes (bundled rdprrap, up to 10)** |
| Reverse file open (guest → host xdg-open) | No | No | No | **Yes (Linux apps in Windows "Open with…" menu)** |
| Windows disk auto-grow | No | No | No | **Yes (idle, bounded by host free space)** |
| Guest sync (in-place update, no reinstall) | No | No | No | **Yes (auto on pod start + `sync-guest`)** |
| Multilingual UI | English only | English only | English only | **Yes (7 languages, locale auto-detect)** |
| Offline / air-gapped install | No | No | No | **Yes (`--source` + `--image-tar`)** |
| License | MIT | AGPL-3.0 | MIT | MIT |

> winboat is the closest peer in scope and was an inspiration. We focus on a different mix — stdlib-leaning Python + Qt6 instead of Electron, deeper auto-config (auto suspend, 7-day password rotation, multi-DE HiDPI), reverse-open (the only project where Linux apps appear in the Windows "Open with…" menu by default), a multilingual UI (7 languages, auto-detected from the locale), self-managing Windows disk that auto-grows as it fills, in-place guest sync that pushes host updates into a running guest without reinstalling, and an explicit air-gapped install path. Both projects build on dockur/windows; that ecosystem is bigger than any one app.

## WinPodX vs Wine

**WinPodX is not a Wine replacement.** Wine translates Windows API calls; WinPodX runs the actual Windows OS in a container. The two solve different problems and many users have both installed.

| When you need... | Use |
|---|---|
| Older Win32 apps, indie games, lightweight utilities | **Wine / Bottles / Lutris** |
| GPU-accelerated games / 3D apps (DirectX 9 – 12) | **Wine** — DXVK / VKD3D give near-native frame rates. WinPodX has no GPU passthrough by default; QEMU CPU rendering is much slower. (GPU passthrough via VFIO is a manual bring-your-own setup — not yet packaged.) |
| Microsoft 365 with full Outlook + Teams + OneDrive integration | **WinPodX** |
| Adobe Creative Suite (Photoshop, Illustrator, Premiere, Lightroom) | WinPodX — but heavy GPU effects will be CPU-bound (see GPU row above) |
| Anti-cheat games (Valorant, EAC, BattlEye) | **TBD** — anti-cheats vary by VM-detection policy (Vanguard needs TPM 2.0 + no hypervisor, EAC mostly blocks VMs, VAC is lenient). Test before committing. |
| DRM-heavy software / hardware dongle apps | **WinPodX** |
| Apps that ship kernel-mode drivers (some VPNs, security suites) | **WinPodX** |
| Banking / tax / government tools with regional certificates | **WinPodX** |
| Visual Studio, WinUI 3 / WinRT, .NET features Wine hasn't caught up to | **WinPodX** |
| IE-only legacy enterprise web apps | **WinPodX** |
| Anything where "mostly works" isn't acceptable | **WinPodX** |

Wine wins on speed and on GPU when DXVK/VKD3D translate cleanly. WinPodX wins on **100% Windows feature parity** for everything else — every app runs on a real Windows kernel, rendered into your Linux desktop as a native window via FreeRDP RemoteApp.
