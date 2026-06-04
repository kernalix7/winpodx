# Features

**English** | [한국어](FEATURES.ko.md)

The full feature set: peripherals & sharing, multi-session RDP, app profiles, and reverse-open (Linux apps in the Windows "Open with…" menu).

## Reverse-open (Linux apps in Windows "Open with…")

Shipped in v0.5.0, default-on since. Right-click any file inside the Windows guest and your Linux-side handler appears in the "Open with…" menu — Kate for `.txt`, gwenview for `.png`, Firefox for `.html`, and so on. Picking one round-trips the file open back to the host's `xdg-open` so it lands in the Linux app you actually configured.

How it works:

```
Windows Explorer right-click  ─┐
                               │  per-slug winpodx-<slug>.exe
                               │  (Rust shim, icon embedded via rcedit)
                               ▼
   atomic JSON write to \\tsclient\home\.local\share\winpodx\reverse-open\incoming\<uuid>.json
                               │
                               ▼
   host listener daemon picks it up, safe_open_unc validates the path
                               │
                               ▼
   subprocess: <app.exec_argv> with the real host file path
```

The feature is **on by default** (`cfg.reverse_open.enabled = true`). Each Linux app the user has set as a Linux default handler — via `xdg-mime default` or their DE's "Default Applications" settings — is registered on the Windows side with the matching extensions. Discovery walks `$XDG_DATA_HOME/applications` + `$XDG_DATA_DIRS` plus every `mimeapps.list` in the freedesktop search path.

Manage via the CLI:

```bash
winpodx host-open status        # listener + manifest state
winpodx host-open list          # apps that would be pushed
winpodx host-open refresh       # rescan + push to guest
winpodx host-open add <slug>    # allowlist
winpodx host-open remove <slug> # remove (or --deny)
winpodx host-open disable       # turn the whole feature off
```

Or via the GUI Settings page → reverse-open panel (same controls).

Per-slug icons render in both the short Open With menu and the long "Choose another app" dialog because each `winpodx-<slug>.exe` is an independent copy of the Rust shim with the matching `.ico` embedded into its PE resource section (via vendored `rcedit.exe`, electron/rcedit v2.0.0, MIT). The chooser icon trade-off: the per-slug `.exe` copies cost ~500 KB × N apps on disk (no hard-link inode sharing) because that's the only chooser-icon path Win10/Win11 reliably honour.

## Seamless app windows

- RemoteApp (RAIL) renders each app as a native Linux window — no full desktop
- Per-app taskbar icons via `WM_CLASS` matching (`/wm-class:<stem>` + `StartupWMClass`)
- File associations: double-click `.docx` in your Linux file manager → Word opens
- Multi-session RDP: bundled rdprrap auto-enables up to 10 independent sessions
- Terminate any running session from the GUI Dashboard (Running-sessions strip) or the system-tray menu
- RAIL prerequisites (`fDisabledAllowList=1` + `fInheritInitialProgram=1` + `MaxInstanceCount=10`) set automatically during unattended install
- Multi-monitor RAIL on by default (`cfg.rdp.multimon = "span"`): a remote-app window keeps working input when dragged onto a second monitor
- UWP/Store apps now appear in the Linux taskbar like any other app

## Zero-config launch

- First app click auto-provisions everything: config, container, desktop entries
- Auto-discovery on first boot scans the running Windows guest and registers every installed app (Registry App Paths, Start Menu, UWP/MSIX, Chocolatey, Scoop) with the real binary's icon
- Manual rescan any time via `winpodx app refresh` or the GUI Refresh button
- Interactive setup wizard for advanced configuration
- Optional pod auto-start on login (opt-in, off by default): `winpodx autostart on|off|status` or the GUI checkbox installs an XDG autostart `.desktop` entry (`~/.config/autostart/winpodx-tray.desktop`) so the tray launches on login and warms the Windows pod before your first app click

## Multilingual UI

The tray, GUI, and CLI are fully translated into 7 languages: English, Korean (한국어), Chinese (中文), Japanese (日本語), German (Deutsch), French (Français), and Italian (Italiano).

- Auto-detected from the system locale on first run; falls back to English when the locale isn't covered
- Switch any time with `winpodx language <code>` (e.g. `winpodx language ja`) or the GUI language dropdown
- Persisted in config under `[ui] language`

## Start-menu GUI & Dashboard

The desktop GUI is built around a Start-menu-style layout: a left vertical navigation sidebar (one row per page) with a **Dashboard** home you land on first.

- **Dashboard** shows live Pod / RAM / CPU ring gauges plus disk usage, an auto-recovery status card, pinned and recent workspace tiles, and a reverse-open toggle.
- The app launcher is now the **All apps** page.
- A **Devices** page provides the two-column host ↔ guest device mover for USB / PCI passthrough.
- A unified design system with an in-house SVG icon set (no more unicode-glyph icons), responsive layouts that reflow on narrow or fractionally-scaled windows, and fit-to-screen sizing.
- A hero search at the top doubles as a command bar.

## Peripherals & Sharing

| Feature | How it works | Default |
|---------|-------------|---------|
| **Clipboard** | Bidirectional copy-paste via RDP (`+clipboard`) | Enabled |
| **Sound** | Audio streaming via ALSA (`/sound:sys:alsa`) | Enabled |
| **Printer** | Linux printers shared to Windows (`/printer`) | Enabled |
| **Home directory** | Shared as `\\tsclient\home` (`+home-drive`) | Enabled |
| **USB drives** | Media folder shared as `\\tsclient\media` (`/drive:media`); USB drives plugged in after session start are accessible as subfolders. The guest-side USB shortcut always resolves even when no media is mounted | Enabled |
| **USB device passthrough** | Native USB redirection (`/usb:auto`) — requires FreeRDP urbdrc plugin | **Opt-in** (add to `extra_flags`) |
| **Host USB / PCI passthrough** | Map a host USB or PCI device straight into the Windows guest (`winpodx device list / attach <id> / detach <id>`, GUI Devices tab, tray USB switcher). USB hot-plugs live; PCI is boot-added and needs a guest restart + safety confirmation | USB live (`cfg.pod.usb_live`, default on) |
| **USB drive mapping** | Windows-side script auto-maps USB subfolders to drive letters (E:, F:, ...) via FileSystemWatcher | Enabled |
| **Reverse file open** | Linux apps appear in the Windows guest's right-click "Open with…" menu; selecting one round-trips the file open to host `xdg-open` | Enabled |

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

### Host USB / PCI device passthrough

Map a real host peripheral all the way into the Windows guest, not just a shared folder:

```bash
winpodx device list            # host devices + current guest attachment state
winpodx device attach <id>     # attach a USB or PCI device to the guest
winpodx device detach <id>     # detach it again
```

- **USB** hot-plugs live (`cfg.pod.usb_live`, default on) — attach/detach without restarting the guest.
- **PCI** is boot-added: it needs a guest restart to take effect and asks for a safety confirmation (`--force` on the CLI, or the dialog in the GUI).
- A **GUI Devices tab** gives you a two-column host ↔ guest mover, and the **system-tray USB switcher** lets you flip a USB device in or out without opening the full window.

**GPU acceleration:** not yet supported. dockur/windows runs under QEMU/KVM with software graphics — DirectX-heavy games and 3D apps will be CPU-bound. GPU passthrough via VFIO is feasible but not packaged. (See [COMPARISON.md](COMPARISON.md) → WinPodX vs Wine — Wine + DXVK is the right tool when you need GPU.)

## Automation & Security

- Auto suspend / resume: container pauses when idle, resumes on next launch
- Password auto-rotation: 20-char cryptographic password, 7-day cycle with rollback
- Smart DPI scaling: auto-detects from GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb
- Multi-backend: Podman (default), Docker, manual RDP
- Windows build pinned to 11 25H2 (`TargetReleaseVersionInfo=25H2`, 365-day feature-update defer)
- Windows debloat: disable telemetry, ads, Cortana, search indexing, services (DiagTrack / dmwappushservice / WSearch / SysMain)
- High-performance power plan + hibernation off + tzutil UTC + Cloudflare DNS
- Time sync: force Windows clock resync after host sleep/wake
- FreeRDP `extra_flags` allowlist (regex-validated) as the user-input safety boundary

## Windows disk auto-grow

The Windows `C:` drive grows itself as it fills, so you don't have to pre-provision a huge virtual disk or run out of space mid-install.

- **Auto-grow** runs only while the pod is idle, expands `C:` when it's nearly full, and is bounded by available host free space so it never overcommits the underlying storage. It correctly handles dockur's WinRE recovery partition that sits at the end of the disk.
- **Manual control**: `winpodx install grow-disk [SIZE|--extend-only]` to add space (or just extend the partition into existing free space), and `winpodx install disk-usage` to inspect current allocation.
- Config keys: `disk_autogrow*` (enable / thresholds / step) and `disk_max_size` (hard ceiling).

## Guest sync

Push host-side updates into a running Windows guest without reinstalling it. When WinPodX ships a newer guest agent, urlacl reservation, rdprrap build, or post-install fix, the guest picks it up in place.

- **Automatic** on pod start when `guest_autosync` is enabled — the guest is reconciled to the current host version every time it comes up.
- **Manual**: `winpodx guest sync [--force]` to reconcile on demand (`--force` re-pushes even when versions already match).

## App Profiles

App profiles are **metadata only**: they describe where a Windows app lives so WinPodX can launch it through FreeRDP RemoteApp. The actual Windows application must be installed inside the Windows container.

### Auto-discovery (default)

Starting from v0.1.9 WinPodX ships **no curated profile list**. The first time the Windows pod boots, the provisioner runs `winpodx app refresh` and that scans the running guest:

- Registry `App Paths` (`HKLM` + `HKCU`)
- Start Menu `.lnk` recursion (depth-capped)
- UWP / MSIX packages via `Get-AppxPackage` + `AppxManifest.xml`
- Chocolatey + Scoop shims

For each result it extracts the icon directly from the binary (or the package's logo asset for UWP) and writes the entry to `~/.local/share/winpodx/discovered/<slug>/`. Re-run any time:

```bash
winpodx app refresh        # CLI
# or click "Refresh Apps" on the GUI Apps page
```

### Adding a custom app profile manually

User-authored profiles live under `~/.local/share/winpodx/apps/` and override anything discovery finds with the same `name`:

```bash
mkdir -p ~/.local/share/winpodx/apps/myapp
cat > ~/.local/share/winpodx/apps/myapp/app.toml << 'EOF'
name = "myapp"
full_name = "My Application"
executable = "C:\\Program Files\\MyApp\\myapp.exe"
categories = ["Utility"]
mime_types = []
EOF

winpodx app install myapp   # Register in desktop menu
```

## Multi-Session RDP

Stock Windows Desktop editions limit RDP to one session per user; a second app would otherwise reconnect and steal the first session. WinPodX bundles [rdprrap](https://github.com/kernalix7/rdprrap) — a Rust reimplementation of RDPWrap — inside the package itself and installs it automatically during the Windows unattended install, so each RemoteApp window gets its own independent session.

**RAIL prerequisites.** RemoteApp itself requires three registry settings that WinPodX applies during unattended setup: `fDisabledAllowList=1` (enables RemoteApp publishing), `fInheritInitialProgram=1` (required for `/app:program:...` to launch the target executable instead of a shell), and `MaxInstanceCount=10` paired with `fSingleSessionPerUser=0` (lifts the single-session cap up to 10 concurrent RemoteApp windows). These are set regardless of whether rdprrap installs successfully — rdprrap is what makes the sessions *independent*, but the registry keys are what make RemoteApp work at all. After rdprrap install `TermService` is cycled so the wrapper DLL activates without a reboot.

**Authentication channel.** NLA is disabled (`UserAuthentication=0`) so the FreeRDP command line can authenticate unattended from under `podman unshare --rootless-netns`, but `SecurityLayer=2` keeps the RDP channel itself encrypted with TLS (so `/sec:tls /cert:ignore` against `127.0.0.1` is the full authenticated + encrypted path — no cleartext on the wire even though NLA is off).

**Works fully offline.** The rdprrap zip ships inside WinPodX's data directory (`config/oem/`) and is staged into `C:\OEM\` during the guest's first boot. sha256 is verified against a pin file before extraction. No network access is required at install time.

Install is one-shot: the patch is applied during dockur's unattended setup phase. If anything in that step fails (hash mismatch, extraction, installer error), WinPodX logs a warning and the guest stays in single-session mode — app launch never blocks on this step. A guest-side management channel (enable/disable/status after install) is planned for a later release.
