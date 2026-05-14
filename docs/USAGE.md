# Usage

**English** | [한국어](USAGE.ko.md)

CLI, GUI, configuration, and health checks. Everything you need after install.

## Launch an app

```bash
winpodx app run word              # Launch Word
winpodx app run word ~/doc.docx   # Open a file
winpodx app run desktop           # Full Windows desktop
```

Or just click an app icon in your application menu — winpodx registers every discovered Windows app as a `.desktop` entry the first time the pod boots.

## CLI reference

```bash
# Apps
winpodx app list                  # List available apps
winpodx app run word              # Launch Word (auto-provisions on first run)
winpodx app run word ~/doc.docx   # Open a file in Word
winpodx app run desktop           # Full Windows desktop session
winpodx app install-all           # Register all apps in desktop menu
winpodx app sessions              # Show active sessions
winpodx app kill word             # Kill an active session
winpodx app refresh               # Re-scan the guest and rebuild the app list

# Pod management
winpodx pod start --wait          # Start and wait for RDP readiness
winpodx pod stop                  # Stop (warns about active sessions)
winpodx pod status                # Status with session count
winpodx pod restart
winpodx pod apply-fixes           # Re-apply Windows-side runtime fixes (idempotent)
winpodx pod sync-password         # Recover from password drift (cfg ↔ Windows)
winpodx pod multi-session on      # Toggle bundled rdprrap multi-session RDP
winpodx pod multi-session status
winpodx pod wait-ready --logs     # Wait for Windows first-boot with progress + container logs

# Power management
winpodx power --suspend           # Pause container (free CPU, keep memory)
winpodx power --resume            # Resume paused container

# Security
winpodx rotate-password           # Rotate Windows RDP password

# Reverse-open (host listener / guest sync)
winpodx host-open status          # Show listener daemon + manifest state
winpodx host-open list            # List discovered host apps (live or --cached)
winpodx host-open refresh         # Rescan host + push manifest to guest
winpodx host-open enable          # Turn reverse-open on
winpodx host-open disable         # Turn reverse-open off
winpodx host-open add <slug>      # Add app to allowlist
winpodx host-open remove <slug>   # Remove from allowlist (or --deny)
winpodx host-open start-listener
winpodx host-open stop-listener
winpodx host-open daemon-status

# Maintenance
winpodx cleanup                   # Remove Office lock files (~$*.*)
winpodx timesync                  # Force Windows time synchronization
winpodx debloat                   # Disable telemetry, ads, bloat
winpodx uninstall                 # Remove winpodx files (keeps container)
winpodx uninstall --purge         # Remove everything including config

# System
winpodx setup                     # Interactive setup wizard
winpodx info                      # Display, dependencies, config diagnostics
winpodx check                     # Run all health probes (pod / RDP / agent / disk / …)
winpodx check --json              # Same probes, machine-readable JSON
winpodx gui                       # Launch Qt6 main window (Apps / Settings / Tools / Terminal)
winpodx tray                      # Launch Qt system tray icon
winpodx config show               # Show current config
winpodx config set rdp.scale 140  # Change a config value
winpodx config import             # Import existing winapps.conf
```

## GUI

Launch with `winpodx gui`. The Qt6 main window has five pages:

| Page | What it does |
|------|--------------|
| **Apps** | Grid / list view of installed app profiles, search + category filter, per-app launch with 3 s cooldown, Add / Edit / Delete app profile dialogs |
| **Settings** | RDP (user / IP / port / scale / DPI / password rotation), Container (backend / CPU / RAM / idle timeout), and the reverse-open panel (enable toggle, allowlist + denylist, live daemon status, refresh / start / stop buttons) all in one screen |
| **Tools** | Suspend / Resume / Full Desktop buttons, Clean Locks / Sync Time / Debloat, and a one-click Windows Update **enable / disable** toggle |
| **Terminal** | Embedded shell limited to a command allowlist (`podman`, `docker`, `virsh`, `winpodx`, `xfreerdp`, `systemctl`, `journalctl`, `ss`, `ip`, `ping`, ...) with quick buttons (Status / Logs / Inspect / RDP Test / Clear) |
| **Info** | Live **Health** card (pod / RDP / agent / OEM / disk / password age / app count) + System / Display / Dependencies / Pod / Config snapshot |

The system tray (`winpodx tray`) is a lighter-weight alternative — pod controls, app launcher submenu (top 20 + Full Desktop), maintenance submenu (Clean Locks / Sync Time / Suspend), and an optional idle-monitor thread.

## Health checks

`winpodx check` runs every probe used by the GUI Health card and prints a one-line verdict for each:

```
=== winpodx check ===

  [OK  ] pod_running        running (ip=127.0.0.1)  (58ms)
  [OK  ] rdp_port           127.0.0.1:3390 reachable  (0ms)
  [OK  ] agent_health       version=0.2.2-rev4  (63ms)
  [OK  ] agent_auth_ready   bearer token available  (1ms)
  [OK  ] oem_version        bundle=24  (3ms)
  [OK  ] password_age       7d remaining (max_age=7d)  (0ms)
  [OK  ] apps_discovered    41 app(s) in /home/.../discovered  (3ms)
  [OK  ] disk_free          401.0/3725 GiB free  (0ms)

Overall: OK
```

Status legend: `OK` (green) / `WARN` (yellow — informational, exit 0) / `FAIL` (red — exit 1) / `SKIP` (grey — disabled by config). Use `--json` for machine-readable output.

## Configuration

Config file: `~/.config/winpodx/winpodx.toml` (auto-created, `0600` permissions)

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
win_version = "11"                               # 11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 | tiny10 | 2025 | 2022 | 2019 | 2016 — see ARCHITECTURE.md for custom ISOs
cpu_cores = 4
ram_gb = 4
vnc_port = 8007
auto_start = true                                # Start pod automatically when launching an app
idle_timeout = 0                                 # Seconds before auto-suspend (0 = disabled)
boot_timeout = 300                               # Seconds to wait for first-boot unattended install
image = "docker.io/dockurr/windows:latest"       # Container image (override for air-gapped mirror)
disk_size = "64G"                                # Virtual disk size passed to dockur

[reverse_open]
enabled = true                                   # Default since v0.5.0
allow = []                                       # Empty = all discovered apps
deny = []                                        # Apps to exclude from the manifest

[logging]
level = "INFO"                                   # DEBUG | INFO | WARNING | ERROR | CRITICAL — changes what winpodx writes to ~/.config/winpodx/winpodx.log
```

Edit via `winpodx config set <key> <value>` or directly with your editor — TOML is parsed via the stdlib on Python 3.11+ (`tomli` on 3.9/3.10).
