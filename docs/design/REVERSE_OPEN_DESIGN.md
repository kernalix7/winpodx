# Reverse File Associations — Design Document

**Status**: v2, post team review
**Tracking**: #48
**Author**: kernalix7
**Last updated**: 2026-05-07
**Implementation branch**: `feat/reverse-file-associations`

## Review log

| Round | Date | Reviewers | Outcome |
|---|---|---|---|
| v1 | 2026-05-07 | author draft | initial sketch |
| v2 | 2026-05-07 | core, cli, desktop, security, platform-qa | 49 changes integrated; this document |

## Goals

Mirror WinPodX's primary direction (Windows app → Linux `.desktop`)
in the opposite direction: every Linux GUI app that handles a MIME
type (Kate, GIMP, VS Code, Firefox, …) appears as a regular entry
in the Windows "Open with…" right-click menu. Picking a Linux app
opens the file in that app on the host, with the Windows file
manager (Directory Opus, Total Commander, Explorer) acting as the
front-end.

## Non-goals

- Bidirectional clipboard / drag-and-drop — already partly handled by
  FreeRDP (#48 explicitly excludes these).
- MIME-type *registration* on the host — assume the user's `xdg-mime`
  setup is correct.
- Linux GUI apps that don't take a file argv (terminal-only tools,
  daemons) — filtered out at discovery time.
- Windows VM acting as the SOURCE (apps inside the VM picking which
  Linux app handles a file) is the only direction covered. Apps on
  the host opening files inside the VM is the existing WinPodX flow.

## Acceptance criteria (from #48)

1. Right-click a file inside the Windows guest's file manager → "Open
   with…" lists native Linux apps with their proper names and icons.
2. Selecting a Linux app from that list opens the file in that app on
   the Linux host (across `\\tsclient\home`, `\\tsclient\media`, and
   any extra `/drive:NAME,/path` mounts the user has configured).
3. Path translation handles Unicode, spaces, mounted-USB paths.
4. Falls back gracefully when no Linux handler can be resolved.
5. Settings toggle (GUI + CLI) to enable / disable globally and
   per-app allowlist.

## Resolved design decisions

The v1 draft had six open questions. Team review resolved each:

| # | Question | Resolution |
|---|---|---|
| 1 | MIME → Windows extension mapping: curated table or generated? | **Hybrid**. Ship a curated table for the top ~80 unambiguous mappings (text/plain → .txt, application/pdf → .pdf, image/png → .png, …). Generate the rest at refresh-time from `/usr/share/mime/packages/freedesktop.org.xml` via `xdg.Mime`. For genuinely ambiguous types (`image/*`, `application/octet-stream`), register the app under the *first* extension only and surface the rest in the GUI's "More types…" detail. |
| 2 | Icon size set to embed in `.ico`. | **Keep all sizes** (16/24/32/48/64/128/256). Each `.ico` is ~30 KB; Windows scales correctly per display DPI; the bandwidth saving from dropping middles is meaningless on a localhost share. |
| 3 | Listener-per-pod vs single per-host. | **Design the pod-id field in now, defer the multiplexing**. v1 schema includes `pod_id` field (defaults to null for single-pod), so a future multi-pod implementation doesn't need a schema bump. |
| 4 | `refresh` while pod is down. | **Cache and replay**. The host-side scan is pure read; deferring the push to next `pod start` is no-cost. Cache file: `~/.local/share/winpodx/reverse-open/pending-sync.json`. `lifecycle.start_listener_for_pod` checks for it on pod start, fires the push, removes the file. |
| 5 | Symbolic name for the CLI subcommand. | **`winpodx host-open`**. Self-describing ("open on the host"), fits the existing noun-group pattern (`app`, `pod`, `config`, `power`), and avoids "open-back" jargon / "linux-handlers" implementation-detail. |
| 6 | When a new host app is installed between refreshes, auto-register or wait? | **Auto-register on next refresh, unless slug is in the denylist**. Initial `enable` is the opt-in; once enabled, a new app on the host is treated like any existing one — discovered, registered, available. Users who want surgical control use `winpodx host-open add <slug>` / `remove <slug>` to manipulate the allow- and deny-lists explicitly. |

## Architecture overview

```
┌────────────────────────────────────────────────────────────────────┐
│                            LINUX HOST                              │
│                                                                    │
│  ┌──────────────────┐   .desktop scan     ┌────────────────────┐   │
│  │ XDG_DATA_DIRS/   ├────────────────────▶│  discovery.py      │   │
│  │ applications/    │   (filtered)        │  (filter, MIME)    │   │
│  └──────────────────┘                     └─────────┬──────────┘   │
│                                                     │              │
│                                                     ▼              │
│  ┌──────────────────┐   PNG/SVG → ICO     ┌────────────────────┐   │
│  │ Hicolor / theme  │◀────────────────────│  icons.py          │   │
│  │ icon themes      │   (cairosvg/Pillow) │  (resolve+convert) │   │
│  └──────────────────┘                     └─────────┬──────────┘   │
│                                                     │              │
│                                ┌────────────────────┘              │
│                                ▼                                   │
│                      ┌────────────────────────┐                    │
│  user runs           │  cli/host_open.py      │                    │
│  `winpodx host-open  │  refresh / enable /    │                    │
│  refresh`            │  disable / status /    │                    │
│                      │  add / remove / list   │                    │
│                      └─────────┬──────────────┘                    │
│                                │ POST /reverse-open/sync           │
│                                ▼ (existing agent transport)        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ~/.local/share/winpodx/reverse-open/                        │   │
│  │   incoming/      ← inotify watch by listener.py             │   │
│  │   apps.json      ← canonical synced app list (host-side)    │   │
│  │   icons/*.ico    ← extracted icons for guest registry       │   │
│  │   pending-sync.json   ← deferred sync if pod was down       │   │
│  │   .seen-uuids    ← replay-defence ring buffer (last 1000)   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                ▲                                   │
│                                │ inotify (IN_MOVED_TO|IN_CLOSE_     │
│                                │           WRITE) + sweeps         │
│                                │                                   │
│                      ┌─────────┴──────────────┐                    │
│                      │  listener.py           │                    │
│                      │  double-fork-spawned   │                    │
│                      │  by `pod start`        │                    │
│                      │  (PID file in xdg run, │                    │
│                      │   ready-sentinel pipe) │                    │
│                      └─────────┬──────────────┘                    │
│                                │ openat2(RESOLVE_NO_SYMLINKS)      │
│                                │ then subprocess.Popen on /proc/   │
│                                │      self/fd/N                    │
│                                ▼                                   │
│                          configured Linux app (by slug)            │
└────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ FreeRDP drive redirect (\tsclient)
                                │ Atomic file rename on shared volume
                                │
┌───────────────────────────────┴────────────────────────────────────┐
│                          WINDOWS GUEST                             │
│                                                                    │
│  ┌──────────────────┐ register-apps.ps1   ┌────────────────────┐   │
│  │ HKCU\Software\   │  + .ico files       │ apps.json (synced  │   │
│  │ Classes\         │◀────────────────────│ from host via      │   │
│  │ Applications\    │                     │ agent endpoint)    │   │
│  │ winpodx-<slug>\  │                     └────────────────────┘   │
│  │ shell\open\      │                                              │
│  │ command          │                                              │
│  └─────────┬────────┘                                              │
│            │ launches                                              │
│            ▼                                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  C:\Users\Public\winpodx\reverse-open\shim.exe              │   │
│  │  --app=<slug> --file="<path>"                               │   │
│  │  (Go binary, ~50KB, single static, ~5ms startup)            │   │
│  │                                                             │   │
│  │  1. Generate UUIDv7                                         │   │
│  │  2. Build JSON: {version, app, path, ts, pod_id}            │   │
│  │  3. Write to                                                │   │
│  │     \\tsclient\home\.local\share\winpodx\reverse-open\      │   │
│  │     incoming\<uuid>.json.tmp                                │   │
│  │  4. Atomic rename → <uuid>.json                             │   │
│  │  5. Exit 0 (best-effort; do not block user)                 │   │
│  └─────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

## Why file-based IPC (not HTTP)

The straightforward HTTP-listener-on-host design from #48's body has
friction:

- Inside the Windows VM, finding the host's reachable IP is fragile.
  dockur uses QEMU user-mode networking (`10.0.2.2` is the host) by
  default but custom configs differ. We'd need probe logic.
- A new listening port (8766) needs firewall consideration.
- Bearer-token rotation adds another secret to manage and stage.

The FreeRDP drive redirect that powers `+home-drive` on every RDP
launch is already a bidirectional mount — the guest sees `\tsclient`
backed by the host's `~`. Files written there land on the host
filesystem under the same user with normal POSIX perms.

So:

- Guest writes `<uuid>.json` to `\\tsclient\home\.local\share\winpodx\
  reverse-open\incoming\` (atomic temp+rename).
- Host's `listener.py` watches that directory with `inotify` for
  `IN_MOVED_TO | IN_CLOSE_WRITE` events (handles both
  temp+rename AND direct-write, deduped by filename) and processes
  each new file → execute → delete.

**Auth**: the FreeRDP drive redirect is mounted with the connecting
user's credentials. Only that user can write there. No bearer token,
no port, no firewall surface. POSIX file ownership IS the auth
boundary, *plus* the listener does a startup `stat()` of
`incoming_dir` and refuses to run if `st_uid != geteuid()` or if the
mode is group/world-writable.

**Latency**: human-paced (right-click → pick app), inotify delivery
within ms of close-on-write. No noticeable difference versus HTTP.

**Replay**: design uses delete-after-process *plus* a persistent
seen-UUID ring buffer (last 1000) so a guest re-submitting the same
UUID after deletion is rejected. (Crash-mid-process recovery is
handled separately — see Lifecycle.)

## Module layout

```
src/winpodx/
  reverse_open/
    __init__.py            # module docstring + arch summary
    paths.py               # UNC ↔ POSIX with TOCTOU-safe spawn helper
    discovery.py           # scan .desktop entries, MIME filter
    icons.py               # PNG/SVG → ICO conversion (pyxdg + cairosvg)
    listener.py            # inotify daemon + JSON dispatch
    lifecycle.py           # double-fork + ready-sentinel + PID
    config.py              # cfg.reverse_open.* schema (ReverseOpenConfig)
    mime.py                # MIME → Windows extension mapping (curated + xdg.Mime)
    seen_uuids.py          # persistent ring buffer for replay defence
  utils/
    compat.py              # is_relative_to compatibility helper (3.9+)
  cli/
    host_open.py           # winpodx host-open {refresh,enable,disable,
                           #                    status,add,remove,list}
  gui/
    settings_host_open.py  # Settings card
config/oem/
  reverse-open/
    shim/
      main.go              # Go shim source
      go.mod               # toolchain go1.23.x pin
      go.sum
      Makefile
    register-apps.ps1      # guest-side registration of synced apps
    unregister-apps.ps1    # cleanup (winpodx host-open disable)
data/oem/                  # CI populates this
  reverse-open-shim.exe    # built by `make` step in release.yml
tests/
  test_reverse_open_paths.py
  test_reverse_open_discovery.py
  test_reverse_open_icons.py
  test_reverse_open_listener.py
  test_reverse_open_lifecycle.py
  test_reverse_open_cli.py
  test_reverse_open_integration.py     # end-to-end host write → listener
  test_reverse_open_mime.py
  fixtures/icons/                       # synthetic Hicolor/Adwaita/Breeze
docs/
  design/REVERSE_OPEN_DESIGN.md         # this document
  REVERSE_OPEN.md                       # user-facing guide (English)
  REVERSE_OPEN.ko.md                    # 사용자 가이드 (한글)
```

## Component contracts

### `paths.py` — UNC translation with TOCTOU-safe spawn

```python
class ReversePathError(ValueError): ...

def translate_unc_to_posix(
    unc_path: str,
    share_roots: dict[str, Path],   # from RDPConfig.drives at runtime
) -> Path:
    """Translate a guest UNC path to a host POSIX Path.

    `share_roots` maps share name (e.g. 'home', 'media', or any user-
    configured `/drive:NAME,/path`) → resolved root Path. The function
    walks the input's leading share component case-insensitively
    (Windows is case-insensitive) and re-resolves both the candidate
    and the matching root via Path.resolve(strict=False), then
    enforces is_relative_to. Catches `..` traversal and symlink
    escape; does NOT cover hard links (documented limitation —
    `resolve()` doesn't see them).

    Raises ReversePathError on:
      - non-string / empty / NUL-containing input
      - input not starting with a known share prefix
      - resolved path outside the share root
      - resolved path under /proc, /sys, or /dev (denylist)
    """

@dataclass
class SafeFile:
    fd: int                # opened with O_PATH | O_NOFOLLOW
    proc_path: Path        # /proc/self/fd/N — pass to argv

    def close(self) -> None: ...

def open_for_spawn(resolved: Path) -> SafeFile:
    """Open a resolved host path with TOCTOU-safe semantics for spawn.

    Uses os.open with O_PATH | O_NOFOLLOW (or openat2 with
    RESOLVE_NO_SYMLINKS where available, Linux 5.6+) so that even if
    a symlink is swapped between resolve() and Popen() the spawn
    targets the inode validated at this call. Returns a SafeFile with
    `proc_path = /proc/self/fd/N` which the caller passes as argv to
    the spawned app. The fd is closed by SafeFile.close() after
    Popen returns.

    On kernels < 5.6, falls back to O_PATH | O_NOFOLLOW per-component.
    """
```

**Allowed share roots** are derived from the live `RDPConfig.drives`
list (mirroring `core.rdp.linux_to_unc`'s behaviour for the same
data). Hardcoded `{home, media}` was a v1 mistake; users who add
custom mounts via cfg shouldn't have those mounts blocked here.

`utils/compat.py:is_relative_to` provides the check (Python 3.9
support; `Path.is_relative_to` arrived in 3.9 but `PurePath.is_relative_to`'s
semantics shifted in 3.12 — wrap to keep behaviour identical across
versions).

### `discovery.py` — Linux app scanner

```python
@dataclass
class LinuxApp:
    slug: str          # 'org-kde-kate', stable across runs
    name: str          # 'Kate'
    comment: str       # tooltip text
    exec_argv: list[str]  # pre-split, with %f/%u placeholders preserved
    icon_name: str     # 'kate' — input to icons.py
    mime_types: list[str]
    desktop_file: Path
    is_default_for: list[str]   # MIME types where this is the user's
                                # default per ~/.config/mimeapps.list

def discover_apps(
    extra_dirs: Sequence[Path] | None = None,
) -> list[LinuxApp]:
    """Walk XDG dirs in order ($XDG_DATA_HOME first, then
    XDG_DATA_DIRS left-to-right). Index by basename; first hit wins
    (matches xdg-open shadowing semantics).

    Filters:
      - require non-empty MimeType= (else not a "handler" app)
      - skip Hidden=true (tombstone)
      - skip NoDisplay=true (default; protocol handlers etc. — use
        --include-nodisplay flag to override on `host-open list`)
      - respect OnlyShowIn / NotShowIn against XDG_CURRENT_DESKTOP
      - validate TryExec= path exists
      - exclude winpodx-generated entries (Name starts with our
        prefix OR Exec invokes winpodx itself — recursion defence)
      - exclude Wine/winapps wrappers via Exec-prefix scan
        ('wine ', 'winapps ', 'winpodx ')
      - reject Exec lines containing shell metacharacters
        ($, `, ;, &&, ||, |, redirects) — flagged at discovery, not
        at execute. A malicious .desktop file in
        ~/.local/share/applications/ is already game-over for the
        user, but we won't be the vector that runs it.
      - strip %c, %k, %i field codes (icon path, .desktop path,
        comment — not user data)
```

`slug` is the desktop_file basename with `.desktop` removed and
`.` → `-` substitution. Example:
`/usr/share/applications/org.kde.kate.desktop` → `org-kde-kate`.
Stable across refresh runs; the user's allowlist persists.

### `icons.py` — icon resolution + ICO conversion

```python
def resolve_icon(icon_name: str) -> Path | None:
    """Resolve a freedesktop icon-name to a concrete PNG/SVG path.

    Uses pyxdg's xdg.IconTheme.getIconPath, which already implements
    the freedesktop theme inheritance spec (Hicolor → user theme).
    Returns None if no match (caller falls back to a generic icon).
    """

def convert_to_ico(src: Path, dst: Path) -> None:
    """Rasterise PNG / SVG → multi-resolution Windows .ico.

    Sizes embedded: 16, 24, 32, 48, 64, 128, 256.

    PNG: Pillow's native ICO writer.
    SVG: cairosvg → PIL.Image → multi-size ICO.
    Fallback chain: cairosvg → svglib (Pillow-compatible) → ship a
    placeholder ico and log a warning. Graceful degrade: the app
    still launches, just without a custom icon.
    """
```

**Licence note**: most freedesktop icon themes are CC-BY-SA / GPL /
LGPL. We never *redistribute* — the `.ico` is generated at runtime
on the user's machine from icons they already have, and lives in
their own `~/.local/share/winpodx/`. No shipping concern.

### `mime.py` — MIME → Windows extension mapping

```python
# Curated table for unambiguous mappings.
CURATED_MIME_EXT: dict[str, list[str]] = {
    "text/plain": [".txt"],
    "text/xml": [".xml"],
    "text/html": [".html", ".htm"],
    "application/json": [".json"],
    "application/pdf": [".pdf"],
    "image/png": [".png"],
    "image/jpeg": [".jpg", ".jpeg"],
    # ... ~80 entries
}

def mime_to_extensions(mime_type: str) -> list[str]:
    """Return Windows extensions for a MIME type.

    Tries curated table first; on miss, falls back to xdg.Mime
    parsing of /usr/share/mime/packages/freedesktop.org.xml. For
    genuinely ambiguous types (image/*, application/octet-stream),
    returns the first extension only — caller can surface the rest
    via 'More types…' in the GUI.
    """
```

### `listener.py` — inotify daemon

```python
class ReverseOpenListener:
    def __init__(
        self,
        incoming_dir: Path,
        apps_db: AppsDatabase,
        seen_uuids: SeenUUIDs,
        max_request_bytes: int = 64 * 1024,   # JSON size cap
        max_request_depth: int = 8,           # JSON nesting cap
        max_in_flight: int = 200,             # incoming/ file count cap
        janitor_age_seconds: int = 300,       # discard stale requests
    ): ...

    def run_forever(self) -> None:
        """Blocking loop.

        Pre-flight: stat incoming_dir, refuse if owner != geteuid()
        or mode permits group/world write.

        Main loop watches IN_MOVED_TO | IN_CLOSE_WRITE (deduped by
        filename); also handles:
          - IN_Q_OVERFLOW → full directory rescan (kernel dropped
            events; we don't trust the queue any more)
          - SIGCONT (after suspend/resume) → directory sweep in
            case events were lost during suspend
          - 60s periodic reconciliation sweep regardless

        Per request:
          1. Stat the file; reject if larger than max_request_bytes.
          2. json.load with custom decoder enforcing max_request_depth.
          3. Validate schema (version, app, path, ts, pod_id;
             optional from_user must match [A-Za-z0-9_-]{1,32}).
          4. Check seen_uuids; reject duplicates.
          5. Validate `app` slug exists in apps_db (no arbitrary exec).
          6. Translate `path` via paths.translate_unc_to_posix,
             passing share_roots derived from RDPConfig.drives.
          7. open_for_spawn(resolved) — TOCTOU-safe fd, /proc/self/fd/N.
          8. Build argv from apps_db[app].exec_argv; substitute %f/%u
             with the proc_path (single argv slot, never re-shell).
          9. subprocess.Popen(argv, start_new_session=True, shell=False).
         10. Record UUID in seen_uuids (persistent ring).
         11. Delete the request file.
         12. Close the SafeFile fd.
        """

    def stop(self) -> None: ...
```

### `lifecycle.py` — daemon spawn / kill

```python
def start_listener_for_pod(cfg: Config) -> int:
    """Spawn listener.py as a daemon and return its PID.

    Daemonisation sequence (parent's perspective):
      1. Create pipe(read_end, write_end) for ready sentinel.
      2. fork().
         - Child: setsid(), fork() again. First child exits.
                  Second child: chdir('/'), close stdin, redirect
                  stdout/stderr to log file.
         - Parent: close write_end, read 1 byte from read_end with
           5s timeout. If sentinel arrives → child is up; return its
           PID. If pipe closes without sentinel → child failed;
           raise ListenerStartFailed.
      3. PID + start_time written to
         $XDG_RUNTIME_DIR/winpodx/reverse-open.pid (mode 0600).
      4. If a pending-sync.json exists, fire the push to guest now;
         delete the file on success.

    Idempotent: if a live PID is already in the file, returns it.
    Stale PID (process gone) → starts new, replaces file.
    """

def stop_listener() -> None: ...
def is_listener_running() -> bool: ...
```

Hook integration:

| Hook | Action |
|---|---|
| `winpodx pod start` (host-only entry, before RDP wait) | mkdir incoming/; start listener; defer guest sync |
| `ensure_ready` returns | if pending-sync.json exists, push to guest |
| `winpodx pod stop` | stop listener (preserves apps.json + guest registry) |
| `winpodx pod restart` | stop + start |
| Listener crash | next `winpodx app run` invocation calls `start_listener_for_pod` defensively (recovery) |

Listener fork happens **before** the Windows-ready probe — the
listener watches a host directory, not the guest. Gives early
event coverage. Guest registration push is deferred until after
`ensure_ready` (RDP must be up so the agent endpoint is reachable).

### `config.py` — schema

```python
@dataclass
class ReverseOpenConfig:
    enabled: bool = False                          # opt-in
    allowlist: list[str] = field(default_factory=list)   # empty = all discovered
    denylist: list[str] = field(default_factory=list)
    last_synced_at: str = ""                       # ISO-8601
    deny_dangerous: bool = True                    # default-deny for code/term apps

    DANGEROUS_DEFAULTS: ClassVar[set[str]] = frozenset({
        # Apps that auto-execute code from opened files.
        "code", "vscodium", "atom",
        # Terminal emulators (Terminal=true in their .desktop).
        "gnome-terminal", "konsole", "xfce4-terminal", "alacritty",
        "kitty", "wezterm", "foot", "tilix",
    })

    def __post_init__(self) -> None:
        if not isinstance(self.allowlist, list):
            self.allowlist = []
        if not isinstance(self.denylist, list):
            self.denylist = []
        # Slugs must match a stable form; reject anything else so
        # malformed cfg can't smuggle exec strings.
        slug_re = re.compile(r"^[a-z0-9-]+$")
        self.allowlist = [s for s in self.allowlist if slug_re.fullmatch(s)]
        self.denylist = [s for s in self.denylist if slug_re.fullmatch(s)]
        # Fold the dangerous defaults into denylist when deny_dangerous=True.
        if self.deny_dangerous:
            for slug in self.DANGEROUS_DEFAULTS:
                if slug not in self.denylist:
                    self.denylist.append(slug)
        # last_synced_at must parse as ISO-8601 or be empty.
        if self.last_synced_at:
            try:
                datetime.fromisoformat(self.last_synced_at.replace("Z", "+00:00"))
            except ValueError:
                self.last_synced_at = ""
```

Persisted under `[reverse_open]` in `winpodx.toml`. `enabled=False`
default means the migration path adds nothing intrusive — feature
appears in `winpodx host-open enable` only when the user runs that.

## File schema (guest → host)

```json
{
  "version": 1,
  "app": "org-kde-kate",
  "path": "\\\\tsclient\\home\\kernalix7\\Documents\\notes.xml",
  "ts": "2026-05-07T10:23:45.123Z",
  "pod_id": null
}
```

Field validation (listener.py rejects the request and logs WARNING
on any failure):

| Field | Required | Validation |
|---|---|---|
| `version` | yes | exact `1` (forward compat — v2 schemas dropped at v1 listener) |
| `app` | yes | matches `^[a-z0-9-]+$`, must exist in apps_db |
| `path` | yes | string, ≤ 4096 bytes, NUL-free, starts with `\\tsclient\` |
| `ts` | yes | ISO-8601 (informational; not used for auth) |
| `pod_id` | optional | null OR `^[a-z0-9-]+$` (multi-pod future-proofing) |

`from_user` is **dropped** in v2 of this design — FS ownership is
already auth, the field added log-injection surface for no benefit.

## Guest side

### Go shim (`config/oem/reverse-open/shim/main.go`)

Reads `--app=<slug> --file=<path>` from argv. Builds the JSON above
with monotonic UUIDv7 (sortable for debugging). Writes
`<uuid>.json.tmp` under `\\tsclient\home\.local\share\winpodx\
reverse-open\incoming\`, then `os.Rename` to `<uuid>.json` (atomic
on NTFS via the SMB redirector). Exits within ~10ms.

Failure modes:

- Share unreachable (no RDP session active) → shim writes a fallback
  notice to event log and exits 1.
- Filesystem error mid-write → cleanup .tmp, exit 1.
- Path or app argv malformed → exit 2 with stderr message.

**Build**:
```
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 \
  go build -trimpath -ldflags "-s -w" -o shim.exe ./...
```

`go.mod` pins the toolchain via `toolchain go1.23.4` so CI uses a
consistent compiler. CI uses `actions/setup-go@v5` with caching of
`~/.cache/go-build` and `~/go/pkg/mod` keyed on `go.sum`. The built
`.exe` ships in `data/oem/reverse-open-shim.exe`, included in all
release artifacts (deb / rpm / wheel / tar / AUR / nix) via the
single-source-of-truth manifest variable (see CI/build section).

### `register-apps.ps1`

Reads `C:\Users\Public\winpodx\reverse-open\apps.json` (synced from
host) plus `icons/*.ico`. For each entry, writes:

```
HKCU\Software\Classes\Applications\winpodx-<slug>.exe
  Default = '<App Name>'
  FriendlyAppName = '<App Name>'
  ApplicationDescription = '<comment>'
  DefaultIcon = 'C:\Users\Public\winpodx\reverse-open\icons\<slug>.ico'

HKCU\Software\Classes\Applications\winpodx-<slug>.exe\shell\open\command
  Default = '"C:\Users\Public\winpodx\reverse-open\shim.exe" --app=<slug> --file="%1"'

HKCU\Software\Classes\Applications\winpodx-<slug>.exe\SupportedTypes
  '<.ext>' = '' for each ext mapped from the app's MIME types
```

### `unregister-apps.ps1`

Walks `HKCU\Software\Classes\Applications\winpodx-*` and deletes the
subtree. Run by `winpodx host-open disable` and from `uninstall.sh`.

## CLI: `winpodx host-open`

```
winpodx host-open enable [--non-interactive]
   set cfg.reverse_open.enabled=True; immediately syncs apps to guest.

winpodx host-open disable [--non-interactive]
   clears guest registrations + sets enabled=False (apps.json kept
   for re-enable).

winpodx host-open status
   shows: enabled, app counts, last sync, listener PID + uptime,
   broken icons, pending sync. Output:

       Reverse file associations  enabled
       Apps registered            14 / 21 discovered
       Last sync                  2026-05-06 09:41 (4 h ago)
       Listener                   running  (PID 18342, up 4h 02m)
       Broken icons               2  (run `winpodx host-open refresh --fix-icons`)
       Pending sync               none

   Six lines max, left-aligned labels, right-aligned values. "Broken
   icons" / "Pending sync" only appear when nonzero. If disabled,
   suppresses everything except the first line.

winpodx host-open refresh [--fix-icons]
   rescan host apps, push to guest. Idempotent; diffs against last
   sync. If pod is down, caches to pending-sync.json and exits 0.

winpodx host-open list [--include-nodisplay]
   show discovered apps with slug / name / mime types and which are
   registered in guest right now.

winpodx host-open add <slug>
   add slug to allowlist (and remove from denylist).

winpodx host-open remove <slug>
   add slug to denylist (and remove from allowlist).
```

`--non-interactive` only on `enable` and `disable` — those mutate
state and could in principle prompt. Other subcommands are read-
only or idempotent-write with no confirmation semantics.
`install.sh` calling `winpodx host-open enable --non-interactive`
+ `winpodx host-open refresh` covers the automated path.

## GUI

`Settings → "Reverse file associations"` card, positioned below
the existing RDP card (RDP is more frequently touched) and above
the Pod card.

```
QGroupBox "Reverse file associations"
├─ QCheckBox  "Enable Linux apps in Windows Open with…"  [master]
├─ QLabel     "Last synced 5 min ago — 23 apps registered"
├─ QLineEdit  [filter — search by name]
├─ QListView  [model=QStandardItemModel, checkable, with QIcon]
│   - Each row: [icon] [name] [comment dimmed] [(default for: .xml, .json)]
│   - Toggle row → adds to allowlist OR denylist as appropriate
│   - setMaximumHeight(300) so >50 apps doesn't dominate the page
├─ HBox: [Refresh] [Select all] [Deselect all]
└─ QLabel (small) "Tip: Apps without MIME types are not shown."
```

Per-row icon: render the actual extracted `.ico` (loaded back via
`QIcon(QPixmap.loadFromData(...))`). Reasoning: card's purpose is
"what your Windows guest will see" — `QIcon.fromTheme` would hide
divergence (theme inheritance picked a different icon than Hicolor
fallback, or our 16/32 rasterisation is ugly). Cache QIcon in the
model so re-renders are free.

`is_default_for` (from `~/.config/mimeapps.list`) shows inline
in the dimmed comment. Single biggest UX win: users immediately
recognise "yes, that's my XML editor."

## Lifecycle and edge cases

| Scenario | Behaviour |
|---|---|
| Pod start (cold, feature disabled) | listener forks (host-only); no app sync |
| Pod start (enabled) | listener forks; if last_synced_at > 24h, fire refresh |
| Pod start (pending-sync.json present) | listener forks + fires the deferred push, deletes the file |
| Pod stop | listener stops cleanly; guest registrations remain intact |
| Pod restart | listener restarts; guest registrations untouched |
| `enable` first time | refresh + push to guest + register |
| `disable` | clear guest registry + set enabled=False (apps.json kept) |
| `refresh` while pod down | host-side scan succeeds, write pending-sync.json, exit 0 with informational message |
| New host app installed | not auto-detected; user runs `refresh` (or auto-refresh on next pod start ≥ 24h since last) |
| Host app removed | next refresh sees stale slug, removes from guest |
| Listener crash | next `app run` notices via `is_listener_running()`, calls `start_listener_for_pod` |
| FreeRDP redirect not mounted (no RDP session) | shim exits with error; host listener has nothing to process |
| Multiple users on same host | each user's listener watches their own incoming/, ownership-checked; PID file in per-user `$XDG_RUNTIME_DIR/winpodx/` |
| Suspend / resume | listener catches SIGCONT, sweeps incoming/ for missed events |
| inotify queue overflow (`IN_Q_OVERFLOW`) | full directory rescan + WARNING log |

## Security threat model

Trust boundaries and threats:

### 1. Guest input is untrusted

A compromised Windows VM (driver-by, social-eng, rdprrap edge) can
write arbitrary JSON to `incoming/`. Defences:

- `app` slug must match a host-discovered app in `apps_db` (default-
  deny). Discovery itself rejects `Exec=` lines containing shell
  metachars, so a malicious `.desktop` file in
  `~/.local/share/applications/` doesn't smuggle anything via this
  path either.
- `app.exec_argv` is **pre-split** by discovery (per freedesktop spec
  field-code substitution rules); `%f` / `%u` substitutes into a
  single argv slot, never re-shelled. `%c` / `%k` / `%i` field codes
  are stripped at discovery (could leak `.desktop` path).
- `path` must resolve under a known share root (paths.py validates
  via openat2 RESOLVE_NO_SYMLINKS or O_PATH + O_NOFOLLOW; see
  TOCTOU defence below).
- Resolved path under `/proc`, `/sys`, `/dev` is rejected post-
  resolve (denylist).
- Resolved file must exist before launch (clearer error than the
  app's own).

### 2. TOCTOU symlink swap

Classic race: guest writes a benign symlink target, validation passes,
guest swaps to `/etc/passwd` before listener spawns the app.
Defence: validation and spawn share an FD obtained with
`O_PATH | O_NOFOLLOW` (or `openat2(RESOLVE_NO_SYMLINKS)` on Linux
5.6+). The argv path passed to the spawned app is `/proc/self/fd/N`
which references the inode validated at validation time.

**Hard links** are not covered (`resolve()` doesn't see them).
Documented limitation: guest-side hard-link of a privileged file
into `~` only works if the user already had read access to that
file, so escalation is bounded but audit logs become confusing.
Mitigation belongs in the apps; we cannot prevent it at this
layer.

### 3. Replay

Two distinct concerns:

- **Crash-mid-process recovery** — the listener died after deleting
  the JSON request from `incoming/` but before completing
  `subprocess.Popen`. On restart, the file is gone, so no replay
  happens. ✓
- **Replay attack** — guest re-submits the same UUID after the
  listener processed and deleted the original. Defence: persistent
  seen-UUID ring buffer (`.seen-uuids`, last 1000) checked before
  spawn. Same UUID → reject + WARNING.

### 4. JSON parser abuse

Defences:

- `stat()` request file before read; reject if `> 64 KB`.
- `json.load` with custom decoder enforcing nesting depth `≤ 8`
  (parser stack bound).
- Schema-validate every required field; unknown fields ignored.

### 5. DoS

- File-count cap on `incoming/`: 200 max. Janitor sweeps files older
  than 300s; on overflow new shim writes silently lose (correct
  behaviour — a flood IS the attack).
- inotify `fs.inotify.max_queued_events` (kernel-bounded, default
  16384) overflow drops events → `IN_Q_OVERFLOW` triggers full
  rescan, no spawn loss.

### 6. Compromised guest — bounded blast radius

A malicious guest can, at worst:

- Open any file the user already reads in any registered Linux app.
  Registered set is filtered: `deny_dangerous = True` excludes
  `code` / `vscodium` / terminal emulators / anything with
  `Terminal=true`.
- Pick which app handles a file. So `gimp` on a malicious SVG
  triggers ImageMagick-style CVEs in *gimp*; the file-format CVE
  surface is the user's app responsibility, not ours, but we surface
  it explicitly here so users understand the trust model.

This is **strictly less** than the user typing `xdg-open` themselves
(where any app could be handler), and bounded further by the
`deny_dangerous` default. Users opting `code` back in via
`winpodx host-open add code` accept the risk explicitly.

### 7. Multi-user privilege escalation

Listener `stat()`s `incoming_dir` on startup and refuses to run if
`st_uid != geteuid()` or if the directory is group/world-writable.
PID file path under `$XDG_RUNTIME_DIR/winpodx/` (per-user, mode
0700 by systemd-logind). User A's listener cannot accept user B's
files even with intentional misconfig.

### 8. Token-free design vs HTTP+bearer

File-based IPC has strictly smaller surface than an HTTP server: no
listening port, no token rotation, no parser, no firewall surface.
The "endpoint" is a directory the FreeRDP redirect already mounts,
gated by POSIX file ownership.

## Test plan

### Unit — `test_reverse_open_paths.py`

- happy path home: `\\tsclient\home\foo` → `$HOME/foo`
- happy path media: `\\tsclient\media\USB\x` → `/run/media/$USER/USB/x`
- happy path custom drive: cfg with `/drive:work,/mnt/work` →
  `\\tsclient\work\report.pdf` resolves to `/mnt/work/report.pdf`
- traversal: `\\tsclient\home\..\..\etc\passwd` → ReversePathError
- symlink escape: `$HOME/link` → `/etc` outside, ReversePathError
- /proc denylist: resolved under `/proc/self/cwd/...` rejected
- empty / NUL / non-string → ReversePathError
- bare share root `\\tsclient\home` → ReversePathError
- forward-slash variant `//tsclient/home/foo` → ReversePathError
- Unicode: `\\tsclient\home\한글\notes.txt` → `$HOME/한글/notes.txt`
- case-insensitive prefix match: `\\TSCLIENT\HOME\foo` → `$HOME/foo`
- `open_for_spawn` returns SafeFile with `/proc/self/fd/N`; symlink
  swap after the call doesn't change resolved inode

**Hypothesis property tests**: random byte strings, mixed
slashes, NUL embedding — invariant: function either returns a
Path under a share root or raises ReversePathError; never crashes.

### Unit — `test_reverse_open_discovery.py`

- finds `kate.desktop` and parses Name/Exec/MimeType
- excludes winpodx-generated entries (no recursion)
- excludes Wine/winapps wrappers via Exec prefix
- skip Hidden=true
- skip NoDisplay=true (default)
- respect OnlyShowIn/NotShowIn against XDG_CURRENT_DESKTOP
- TryExec= resolves to existing $PATH entry
- shadowing: same basename in $XDG_DATA_HOME shadows $XDG_DATA_DIRS
- malformed `.desktop` (missing `[Desktop Entry]`, broken keys) →
  logged WARNING, scan continues
- slug determinism: same `.desktop` file → same slug across runs
- Exec= containing shell metachars rejected at discovery, with
  WARNING log

### Unit — `test_reverse_open_icons.py`

- PNG → ICO with all sizes
- missing icon name → returns None (caller substitutes default)
- SVG with cairosvg available → ICO
- SVG fallback to svglib when cairosvg absent
- SVG with neither → placeholder ico + WARNING log
- icon rendering uses synthetic icon-theme fixture under
  `tests/fixtures/icons/` (does NOT depend on system-installed
  GIMP / Adwaita)

### Unit — `test_reverse_open_listener.py`

- valid JSON → subprocess invocation with right argv
- unknown `app` slug → no subprocess, request deleted, WARNING
- malformed JSON → request deleted, WARNING
- JSON > 64KB → rejected without parse
- JSON depth > 8 → rejected
- path translation failure → no subprocess, request deleted, WARNING
- nonexistent target file → no subprocess, request deleted, INFO
- replay (same UUID twice) → second invocation rejected by ring
- crash-mid-process recovery (delete JSON before listener restart)
  → no replay
- listener stop signal → run_forever returns within 1s
- IN_Q_OVERFLOW → directory rescan triggered
- SIGCONT (suspend/resume) → directory sweep triggered
- incoming_dir ownership mismatch → listener refuses to start
- 200 in-flight cap → 201st request silently dropped (not parsed)

### Unit — `test_reverse_open_lifecycle.py`

- start → PID file written, process alive, ready sentinel received
- stop → PID file removed, process dead
- start while running → returns existing PID (idempotent)
- start with stale PID file (process dead) → starts new, replaces
- daemonisation: child detached from parent's session (kill
  parent, child survives)
- ready sentinel timeout → ListenerStartFailed raised
- pending-sync.json present at start → push fired, file deleted

### Unit — `test_reverse_open_cli.py`

- enable → cfg updated, refresh fired
- disable → cfg updated, guest unregister attempted
- status → human-readable output matches the spec layout
- refresh on pod down → caches to pending-sync.json, exits 0 with
  informational message
- add / remove → allowlist / denylist updated, mutually exclusive

### Integration — `test_reverse_open_integration.py`

End-to-end host-side: write `.json.tmp` → rename to `.json` →
listener picks up via inotify → mocked subprocess invoked → request
file deleted → seen-UUIDs updated. Uses real `inotify_simple`
against a `tmp_path`.

### Cross-distro icon matrix — `test_reverse_open_icons_themes.py`

Parametrised over fixture themes (Hicolor, Adwaita, Breeze,
Papirus stubs). Runs on `ubuntu-latest` + `fedora` + `archlinux`
containers in CI matrix.

### Filesystem-specific — pytest markers

`@pytest.mark.btrfs` / `@pytest.mark.ext4` for inotify behaviour
that may differ. CI matrix runs both via a btrfs-loopback job.

### Daemon lifecycle — pytest-xprocess

Use `pytest-xprocess` (or a `Listener` context manager wrapping
`fork-exec` + `atexit`-kill) to fork a real daemon, assert PID
file ownership / mode / contents. Wrap each test with
`@pytest.fixture(autouse=True)` that `pkill -f reverse-open-listener`
on teardown to prevent CI process leaks.

### Manual smoke (until automated guest CI exists)

- Right-click `.xml` in Windows Explorer → Linux `Kate` listed →
  click → file opens in host's Kate.
- Right-click `.txt` in Directory Opus → multiple Linux apps (Kate,
  GNOME Text Editor, …) → pick one → opens.
- File on USB drive (`/run/media/$USER/THUMB/x.txt`) → same flow.
- Restart pod → registrations survive, listener restarts.

## CI / build / packaging

### Go shim build

`.github/workflows/release.yml` (and a `golang` CI job for every
push):

```yaml
- uses: actions/setup-go@v5
  with:
    go-version-file: 'config/oem/reverse-open/shim/go.mod'
    cache: true
    cache-dependency-path: 'config/oem/reverse-open/shim/go.sum'

- name: Build reverse-open Go shim
  run: |
    make -C config/oem/reverse-open/shim
    install -Dm755 config/oem/reverse-open/shim/shim.exe \
      data/oem/reverse-open-shim.exe

- name: Smoke test on Wine
  run: |
    sudo apt-get install -y wine
    wine data/oem/reverse-open-shim.exe --help

- name: Sign with cosign keyless
  run: |
    cosign sign-blob --yes data/oem/reverse-open-shim.exe \
      --output-signature data/oem/reverse-open-shim.exe.sig
```

`go.mod` pins `toolchain go1.23.4`. Module + build caches keyed on
`go.sum`. Reproducibility: `-trimpath` strips local paths from the
binary so successive builds on different hosts produce identical
bytes.

### Single-source-of-truth: `OEM_BLOBS`

A new file `packaging/OEM_BLOBS.txt` lists every binary blob that
ships in releases:

```
data/oem/reverse-open-shim.exe
data/oem/agent.zip                # existing
data/oem/rdprrap.zip              # existing
```

Each packaging manifest sources from this:

- `pyproject.toml`'s `[tool.hatch.build.targets.wheel.shared-data]`
  section reads it via a `tool.winpodx.oem-blobs` glob.
- `debian/winpodx.install` globs `data/oem/*`.
- `packaging/rpm/winpodx.spec`'s `%files` section globs
  `%{_datadir}/winpodx/oem/*`.
- `packaging/aur/PKGBUILD` source array adds the shim sha to the
  source/sha tuple via a `update-aur-sums.sh` helper.
- `flake.nix`'s `postInstall` globs `data/oem/*`.

A CI job `verify-oem-blobs` checks every manifest references every
blob in `OEM_BLOBS.txt`. Drift → fail.

### Test infrastructure

- `tests/fixtures/icons/` — minimal Hicolor/Adwaita/Breeze trees
  (small PNG/SVG, no actual themed icons, just the index.theme
  hierarchy and a few placeholders).
- `pytest-xprocess` added to `requirements-dev.txt`.
- Cross-distro CI matrix as described above.

## Roll-out

Feature flag (`cfg.reverse_open.enabled`) defaults to `False`. All
phases land in `0.4.x` patches with the flag off; the default flips
to `True` in `0.5.0` after smoke-testing across the supported distro
matrix.

| Phase | Scope | PR target |
|---|---|---|
| 1 | foundations: paths, discovery, icons, listener, lifecycle, config schema, mime, seen_uuids, compat helper. CLI surface stub. All host-side. Tests for everything. Feature flag off. | early 0.4.x |
| 2 | Go shim + register-apps.ps1 + install.bat staging + CI build pipeline + cosign signing. Wine smoke test. End-to-end manual on Tumbleweed. | mid 0.4.x |
| 3 | GUI Settings card. Auto-refresh on pod start. MIME → ext mapping with mimeapps.list integration. | late 0.4.x |
| 4 | Polish: empty-allowlist UX, broken-icon fallback, stale-sync warnings, English + Korean docs, release notes. Default flip in `0.5.0`. | 0.5.0 |

Phases land in order; each is its own PR so the team can review in
isolation. The feature flag means a phase-1 merge is shippable —
existing users see no behaviour change.

## Out of scope (related, deferred)

- Reverse drag-and-drop (dropping a file from Windows Explorer onto
  a Linux app's window). Solvable by extending the same channel —
  Windows DnD invokes the shim with `--operation=drop` — but adds
  X11/Wayland window-manager wiring on the host. Punt.
- "Open with…" picker showing BOTH Windows and Linux apps in one
  menu. Cosmetic; users can find both already.
- Setting a Linux app as the **default** Windows handler in registry
  (`HKCU\Software\Classes\<ext>\OpenWithProgids`). Possible but
  invasive — defer to user request.
- Auto-detect new host apps installed (`/usr/share/applications`
  inotify on the host) and re-sync without manual `refresh`. Phase
  4+ if demand exists.
