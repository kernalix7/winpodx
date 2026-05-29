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

# Pod lifecycle (container state only — see `guest` for in-guest ops, `install` for disk / install)
winpodx pod start --wait          # Start and wait for RDP readiness
winpodx pod stop                  # Stop (warns about active sessions)
winpodx pod status                # Status with session count
winpodx pod restart
winpodx pod recreate              # Stop + remove + start (clean container)
winpodx pod wait-ready --logs     # Wait for Windows first-boot with progress + container logs (auto-extends on slow ISO download)

# Guest-side operations (renamed from `pod <x>` in 0.6.0 — old spellings still work through 0.6.x with a deprecation notice)
winpodx guest apply-fixes         # Re-apply Windows-side runtime fixes (idempotent)
winpodx guest sync                # Push host updates (agent / urlacl / rdprrap / fixes) into the guest — no reinstall
winpodx guest sync --force        # Re-sync even when the guest version stamp already matches
winpodx guest sync-password       # Recover from password drift (cfg ↔ Windows)
winpodx guest multi-session on    # Toggle bundled rdprrap multi-session RDP
winpodx guest multi-session status
winpodx guest recover-oem         # Re-stage C:\OEM + run install.bat when dockur's first-boot OEM copy failed (#287)

# Install / disk operations (renamed from `pod install-* / pod grow-disk / pod disk-usage` in 0.6.0)
winpodx install status            # Install progress / pending steps (#271 agent-first installs)
winpodx install resume            # Resume a deferred install step
winpodx install disk-usage        # Show Windows C: size / free / used% + auto-grow status (#318)
winpodx install grow-disk         # Add the auto-grow increment (default 32G) to the disk + extend C: (#318)
winpodx install grow-disk 128G    # Grow to an absolute size
winpodx install grow-disk --extend-only   # Just extend C: into existing unallocated space

# Power management
winpodx power --suspend           # Pause container (free CPU, keep memory)
winpodx power --resume            # Resume paused container

# Security
winpodx rotate-password           # Rotate Windows RDP password (host config + Windows-side guest account)

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
winpodx setup                     # Full setup: config + container + wait-ready + discovery + reverse-open
winpodx setup --customize         # Wizard: backend / specs / edition / language / region / keyboard / timezone / tuning
winpodx setup-host                # Host prep wizard (kvm group, /etc/subuid, kvm module) via one pkexec prompt — AppImage users
winpodx provision                 # Post-pod-running chain (wait-ready → apply-fixes → discovery → reverse-open) — the single source of truth used by install.sh, setup, migrate, and the GUI bring-up (0.6.0 item B)
winpodx provision --retries N     # Override discovery retry count (default 2 — see 0.6.0 item M)
winpodx provision --require-agent # Hard-gate on the in-guest agent (used by fresh installs, #271)
winpodx migrate                   # Upgrade an existing guest in place (refresh agent.ps1 + scripts, re-apply fixes, re-discover, refresh reverse-open)
winpodx doctor                    # Read-only health diagnostic with per-check fix hints (deps / pod / RDP / agent / disk / config / install state)
winpodx doctor --json             # Same checks, machine-readable JSON array of findings
winpodx doctor --quick            # Skip slow probes (container-health, guest exec) — cheap local checks only (< 1 s)
winpodx doctor --fix              # Idempotent auto-remediation for warn/fail findings that carry a fixer (dead agent, stale locks, missing desktop entries, OEM-version drift)
winpodx autostart on|off|status   # Start the Windows pod on login (opt-in; off by default)
winpodx language                  # Show the current UI language
winpodx language ko               # Set UI language: auto | en | ko | zh | ja | de | fr | it (auto = host locale)
# `winpodx info` and `winpodx check` are deprecated aliases of `winpodx doctor` (work through 0.6.x with a notice; removed in 0.7.0).
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

### Tray auto-spawn + UNRESPONSIVE recovery (v0.5.5)

Since v0.5.5 the tray spawns itself automatically from the GUI window and from every CLI subcommand that touches the pod (everything except `setup` / `gui` / `tray`), so a user who only ever runs `winpodx app run` still gets the system-tray indicator + the UNRESPONSIVE auto-recovery driver. A flock under `$XDG_RUNTIME_DIR/winpodx/tray.lock` prevents stacked instances when the user manually re-launches the tray.

The tray context menu now starts with **Open Dashboard** (one-click to the main GUI window). **Quit** confirms via a dialog and on confirmation runs `stop_pod` + `pkill -f 'winpodx gui'` + `app.quit` so a stray click can't cycle the pod's ~30 s restart.

To launch the tray at every login, open the GUI → Settings → tick **"Launch winpodx tray at login (system tray icon + idle-stall auto-recovery)"**. The toggle writes / removes `~/.config/autostart/winpodx-tray.desktop` via the XDG autostart spec; portable across KDE / GNOME / XFCE / Cinnamon. The file is the source of truth — you can also drop it by hand to opt out without launching the GUI. Toggle applies immediately; no Save Settings click needed.

The tray watches the pod state every 30 s. On a `RUNNING → UNRESPONSIVE` transition (container alive long enough that an RDP-port miss can't be confused with a fresh boot) it fires a desktop notification and spawns a background worker that asks the agent to cycle Windows `TermService`. On recovery a "Pod recovered" notification fires; on failure a "needs manual restart" notification points at `winpodx pod restart`. While `install.sh` is running its `[3/4]` / `[4/4]` Sysprep + OEM-reboot phases, the marker file `~/.config/winpodx/.install_in_progress` suppresses the recovery path so genuine install-time RDP gaps don't fire spurious notifications.

## Health checks

`winpodx doctor` runs every probe used by the GUI Health card and prints a one-line verdict for each:

```
=== winpodx doctor ===

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

## Changing the Windows password

Use `winpodx rotate-password` — never reuse `winpodx setup` for this. The two have very different effects on an already-running install:

| Command | Host config (`winpodx.toml`) | Windows guest account |
|---|---|---|
| `winpodx rotate-password` | Updated atomically (with rollback on failure) | Updated via Windows-side change mechanism |
| `winpodx setup` (rerun) | Preserved as-is (since v0.5.5) | Not touched |
| `winpodx setup` (fresh install, no prior config) | Generated / prompted | Applied on first boot via dockur `USERNAME`/`PASSWORD` env vars |

Re-running `winpodx setup` to bump cores / RAM / `win_version` is safe and will not touch your credentials. On pre-v0.5.5 releases the wizard reprompted for the password every run and silently overwrote `winpodx.toml` — but dockur honors the password env var only on first boot, so the host config desynced from the Windows guest account and the next RDP launch failed with `LOGON_FAILED_BAD_PASSWORD`.

### Recovering from a desynced password (pre-v0.5.5 lockout)

If you ran `winpodx setup` on an older release and can no longer log in:

1. **Restore the old password** if you still have it (in a `winpodx.toml` backup, your password manager, or your shell history):
   ```bash
   winpodx config set rdp.password '<old-password>'
   winpodx pod start
   winpodx rotate-password
   ```
2. **Otherwise**, the only path is `winpodx pod purge` + reinstall, which loses any in-Windows state (installed apps, documents, settings). Make a fresh `winpodx setup` your first step after reinstall, then never touch the password through `setup` again — use `rotate-password`.

## Performance tuning profile

`cfg.pod.tuning_profile` controls how aggressively winpodx tunes the dockur compose for the underlying host. It defaults to `"auto"` — winpodx probes the host once at compose time and turns on the matching subset of safe Windows-on-KVM tweaks. Look at the `[Tuning]` block in `winpodx doctor` to see what was detected and applied:

```
[Tuning]
  invtsc:        yes   (intel)
  io_uring:      yes   (kernel 6.18, need >= 5.6)
  hugepages:     no    (sysctl vm.nr_hugepages)
  dedicated:     yes
  nested_kvm:    yes   (/sys/module/kvm_*/parameters/nested)

  Profile: auto
    +invtsc:        yes
    io_uring aio:   yes
    hugepages:      no
    CPU pinning:    yes
    platform_tick:  yes
    no balloon:     yes
    hv-* + no-hpet: yes
    virtio-rng:     yes
    nested virt:    yes
    hv-evmcs:       yes
```

Profiles:

| `tuning_profile` | What it does |
|---|---|
| `auto` (default) | Detect host capability + apply every safe tuning the host can support, including the Hyper-V enlightenments, virtio-rng, and nested-virt pass-through when `/sys/module/kvm_*/parameters/nested` is set. CPU pinning + no-balloon gated on `dedicated_host` (idle CPU + free RAM ≥ 2× VM allocation) so we don't starve other host workloads. Recommended for most users. |
| `performance` | Same as `auto` but bypasses the `dedicated_host` gate: CPU pinning + no-balloon flip on regardless of current host load. Use when the box is mostly dedicated to winpodx and you want minimum guest latency at the cost of other host workloads. Hard-gated knobs (`+invtsc`, `io_uring`) still respect capability detection -- `performance` can't force a CPU flag QEMU would reject or a kernel feature that crashes. |
| `safe` | Apply the Windows-guest-only subset that requires no host configuration: `+invtsc` (when supported), `platform_tick` BCD tweak, Hyper-V enlightenments (`hv-relaxed`, `hv-vapic`, `hv-vpindex`, `hv-runtime`, `hv-synic`, `hv-reset`, `hv-frequencies`, `hv-reenlightenment`, `hv-tlbflush`, `hv-ipi`, `hv-spinlocks=0x1fff`, `hv-stimer`, `hv-stimer-direct`, `-no-hpet`), and `virtio-rng`. Excludes nested-virt + `hv-evmcs` which need explicit host-side opt-in. |
| `off` | Apply nothing; the dockur defaults stand. Use when troubleshooting suspected tuning interaction. |
| `manual` | Same shape as `safe`; reserved for future per-knob overrides. |

### What each tuning does

* **`+invtsc`** — exposes invariant TSC so Windows uses TSC as the clock source instead of HPET (lower IRQ overhead).
* **`hv-*` enlightenments + `-no-hpet`** (#245) — tells Windows it's running under a paravirtualised hypervisor. Cuts spinlock / VM-exit overhead on every workload; doubly noticeable on multi-vCPU guests. `hv-spinlocks=0x1fff` is the upstream-recommended retry budget.
* **`virtio-rng-pci` backed by `/dev/urandom`** (#245) — fills the Windows entropy pool quickly on first boot so CryptoAPI / TLS handshakes don't stall waiting for kernel randomness.
* **`+vmx` / `+svm` nested virt** (#245) — auto-enabled when `/sys/module/kvm_intel/parameters/nested` or `kvm_amd` reads `Y`. Required for Hyper-V / WSL2 / Docker Desktop inside the Windows guest. No effect when the host kernel hasn't opted in.
* **`hv-evmcs`** (#245) — Intel-only nested-VMCS optimisation, paired with `+vmx`. Zero overhead when the guest doesn't run nested VMs.
* **`io_uring` AIO** — kernel ≥ 5.6 disk I/O backend; lower latency than legacy threads.
* **Hugepages** — backs the QEMU memory with 2 MB pages. Requires `vm.nr_hugepages` reserved on the host (winpodx does not auto-reserve).
* **CPU pinning** — winpodx flags the host as `dedicated` and applies QEMU vCPU pinning when host idle CPU + RAM ≥ 2× VM allocation.

### One-shot override

`winpodx pod start --tuning {auto,safe,off,manual}` overrides `cfg.pod.tuning_profile` for the lifetime of that container run only. The user's persisted preference in `winpodx.toml` is left untouched. Useful for A/B testing — flip back and forth without `winpodx config set` round-trips.

### Items that require host-side setup (not auto-applied)

These are standard Windows-on-KVM tweaks that need operator action on the Linux host before winpodx can take advantage of them. The `[Tuning]` block in `winpodx doctor` will show them as `no` until the host is set up; flipping to `yes` happens automatically the next time `cfg.pod.tuning_profile = auto` runs.

* **Transparent hugepages / explicit hugepages.** Set `vm.nr_hugepages` via `sysctl` (or use `madvise` THP) so the QEMU process can back its memory with hugepages. winpodx detects `HugePages_Total > 0` in `/proc/meminfo` and skips the auto-apply if hugepages aren't reserved.
* **CPU pinning.** winpodx flags the host as `dedicated` when the current idle CPU + RAM is at least twice the VM's allocation. Pinning the QEMU thread to specific cores via `taskset` (or systemd `CPUAffinity=`) is then up to the operator; winpodx will not modify host scheduling.
* **VFIO GPU passthrough.** Out of scope for the RDP-based winpodx architecture. Use a libvirt setup directly if you need bare-metal GPU performance.

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
auto_start = false                               # Opt-in login auto-start: tray starts the pod on login (toggle via `winpodx autostart on|off|status`)
idle_timeout = 0                                 # Seconds before auto-suspend (0 = disabled)
boot_timeout = 300                               # Seconds to wait for first-boot unattended install
image = "docker.io/dockurr/windows:latest"       # Container image (override for air-gapped mirror)
disk_size = "64G"                                # Virtual disk size passed to dockur (grows via `install grow-disk`)
disk_autogrow = true                             # Auto-grow C: when it fills past the threshold (idle only)
disk_autogrow_threshold_pct = 80                 # Used-% that triggers an auto-grow (50-99)
disk_autogrow_target_free_pct = 30               # Grow is sized to restore this much free (not a flat step)
disk_autogrow_increment = "32G"                  # Grow granularity / minimum step
disk_max_size = ""                               # Optional hard ceiling; empty = bounded only by host free space
guest_autosync = true                            # After a host upgrade, push updated guest artifacts in (no reinstall)

[ui]
language = "auto"                                # UI language: auto | en | ko | zh | ja | de | fr | it (auto = host locale, falls back to English; change via `winpodx language` or GUI Settings)

[reverse_open]
enabled = true                                   # Default since v0.5.0
allow = []                                       # Empty = all discovered apps
deny = []                                        # Apps to exclude from the manifest

[logging]
level = "INFO"                                   # DEBUG | INFO | WARNING | ERROR | CRITICAL | RAW — RAW = DEBUG + pod logs (podman logs -f) interleaved in GUI Terminal
```

Edit via `winpodx config set <key> <value>` or directly with your editor — TOML is parsed via the stdlib on Python 3.11+ (`tomli` on 3.9/3.10).
