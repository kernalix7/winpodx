"""Configuration management for winpodx.

Persists ``[rdp]``, ``[pod]``, ``[reverse_open]``, and ``[install]``
sections to ``$XDG_CONFIG_HOME/winpodx/winpodx.toml``. The ``[install]``
section drives the agent-first install flow (see
``docs/design/AGENT_FIRST_INSTALL_DESIGN.md``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # Python 3.9, 3.10

from winpodx.reverse_open.config import ReverseOpenConfig
from winpodx.utils.paths import config_dir
from winpodx.utils.toml_writer import dumps as toml_dumps

_VALID_BACKENDS = frozenset({"podman", "docker", "libvirt", "manual"})

# Podman/Docker container name rules: alnum/_/-/., must start with alnum.
_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DEFAULT_CONTAINER_NAME = "winpodx-windows"

# Pinned dockur/windows image — the default ``cfg.pod.image``. Bumping
# this digest is a deliberate per-release decision (so winpodx ships a
# specific tested dockur version with each release), not a side effect
# of dockur pushing a new ``:latest``. ``winpodx setup --update-image``
# resolves a fresh digest from ``docker.io/dockurr/windows:latest`` for
# users who explicitly want to track upstream.
#
# Update procedure (release-time): query Docker Hub registry for the
# current ``:latest`` digest, paste below. See ``winpodx setup --update
# -image`` for the runtime equivalent users invoke explicitly.
#
# As of 2026-05-02:
DOCKUR_IMAGE_PIN = (
    "docker.io/dockurr/windows@sha256:"
    "20b398ab935465f97ec8ab06489f7a85a5ad58e74e036ce66cc3c9172e7dbea8"
)


@dataclass
class RDPConfig:
    user: str = ""
    password: str = ""
    password_updated: str = ""  # ISO 8601 timestamp
    password_max_age: int = 7  # days, 0 = disable rotation
    askpass: str = ""
    domain: str = ""
    ip: str = "127.0.0.1"
    port: int = 3390
    scale: int = 100
    dpi: int = 0  # Windows DPI %, 0 = auto-detect from Linux
    extra_flags: str = ""

    def __post_init__(self) -> None:
        self.port = max(1, min(65535, int(self.port)))
        self.scale = max(100, min(500, int(self.scale)))
        self.dpi = max(0, min(500, int(self.dpi)))
        self.password_max_age = max(0, int(self.password_max_age))


@dataclass
class PodConfig:
    backend: str = "podman"  # podman | docker | libvirt | manual
    vm_name: str = "RDPWindows"
    container_name: str = "winpodx-windows"
    win_version: str = "11"  # 11 | 10 | ltsc10 | tiny11 | tiny10
    cpu_cores: int = 4
    # v0.2.1: default bumped 4 -> 6 GB so the new 25-session default
    # doesn't trip the session-budget warning (2.0 base + 25 × 0.1 ≈
    # 4.5 GB needed). Setup wizard detects host RAM and may override
    # via the auto-tier (low/mid/high) presets.
    ram_gb: int = 6
    vnc_port: int = 8007
    auto_start: bool = True
    idle_timeout: int = 0  # 0 = disabled
    boot_timeout: int = 300  # seconds, max wait for RDP after start_pod
    # Container image for dockur/windows. Pinned to a specific digest by
    # default so dockur pushing a new ``:latest`` doesn't trigger an
    # unsolicited container recreate (which dockur sometimes ships with
    # transient bugs in proc.sh, and which always rebuilds the disk
    # volume → multi-minute Sysprep). Users who want bleeding-edge
    # dockur can override to a tag in ``winpodx.toml``; explicit
    # update is via ``winpodx setup --update-image`` (pulls latest +
    # rewrites the pin).
    image: str = DOCKUR_IMAGE_PIN
    # Virtual disk size exposed in the compose template (e.g. "64G", "128G").
    disk_size: str = "64G"
    # Maximum concurrent RemoteApp sessions. Writes
    # HKLM:\...\Terminal Server\WinStations\RDP-Tcp\MaxInstanceCount
    # + clears fSingleSessionPerUser in the guest so rdprrap can hand
    # out up to N parallel sessions. Clamped to [1, 50] — 50 is the
    # practical ceiling verified against rdprrap; above that
    # responsiveness degrades regardless of ram_gb.
    # v0.2.1: default bumped 10 → 25. 10 was tight for users running
    # Office + Teams + Edge + a couple side apps simultaneously.
    max_sessions: int = 25
    # v0.4.x: storage volume mode for the Windows raw disk image.
    # Empty string → use the legacy named volume `winpodx-data`
    # (backward-compatible for users who installed before this option
    # existed). Non-empty string → use that absolute filesystem path
    # as a host bind mount in compose.yaml. Fresh installs created by
    # `winpodx setup` after this field landed default to a per-user
    # bind mount under `~/.local/share/winpodx/storage`, with
    # `chattr +C` applied automatically when the path is on btrfs so
    # the Windows raw disk image bypasses Copy-on-Write fragmentation
    # (kernalix7 / @xiyeming hit this on cachyos #121, #122). Existing
    # users keep the named volume until they explicitly run
    # `winpodx setup --migrate-storage`.
    storage_path: str = ""

    def __post_init__(self) -> None:
        if self.backend not in _VALID_BACKENDS:
            self.backend = "podman"
        self.cpu_cores = max(1, min(128, int(self.cpu_cores)))
        self.ram_gb = max(1, min(512, int(self.ram_gb)))
        self.vnc_port = max(1, min(65535, int(self.vnc_port)))
        self.idle_timeout = max(0, int(self.idle_timeout))
        self.boot_timeout = max(30, min(3600, int(self.boot_timeout)))
        self.max_sessions = max(1, min(50, int(self.max_sessions)))
        if not isinstance(self.container_name, str) or not _CONTAINER_NAME_RE.match(
            self.container_name
        ):
            # Fall back silently so a hand-edited config does not brick setup.
            self.container_name = _DEFAULT_CONTAINER_NAME
        if not isinstance(self.image, str) or not self.image.strip():
            self.image = DOCKUR_IMAGE_PIN
        if not isinstance(self.disk_size, str) or not self.disk_size.strip():
            self.disk_size = "64G"
        # storage_path: keep empty (named-volume mode) or coerce to a
        # safe absolute string under the user's home or under a known
        # winpodx-managed root. The caller responsible for materialising
        # the directory expands `~` at use time via
        # ``Path(...).expanduser()``.
        #
        # Defence-in-depth (Security review #5: hardening A): a hand-
        # edited TOML must never get this far with a system path like
        # ``/`` or ``/etc`` because winpodx would later run
        # ``chattr +C`` and ``rsync`` against it. We resolve `~` for
        # the check (so `~/.local/share/winpodx/storage` passes) but
        # leave the original string in place for the caller to expand.
        # Anything outside the allowlist or matching a denylist is
        # silently coerced to "" — back to named-volume mode.
        self.storage_path = _sanitise_storage_path(self.storage_path)


@dataclass
class InstallConfig:
    """Agent-first install flow tuning (see AGENT_FIRST_INSTALL_DESIGN.md)."""

    # Phase 1-3 ships with this False (legacy install path is the
    # default); Phase 4 flips the default to True. Persisted absence in
    # an existing TOML reads as False, so the rollout is opt-in until
    # the default flips.
    agent_first: bool = False
    # Stage 2 of host-side wait-ready: agent /health 200 OK.
    # Default 15min covers the slowest healthy case (HDD ext4, Pi 5
    # aarch64) per the design doc's hardware matrix.
    wait_ready_stage2_secs: int = 900
    # Stage 3: install_complete.done present (read via agent /exec).
    # Default 30min covers the long tail (HDD btrfs pre-NoCoW).
    wait_ready_stage3_secs: int = 1800
    # On `winpodx app run`, auto-trigger install-resume if a previous
    # install left install_failure.json behind. Disable to require an
    # explicit `winpodx pod install-resume` for diagnostics.
    auto_resume: bool = True
    # In-process watchdog inside install.bat: how many times to respawn
    # a dead agent before giving up and writing install_failure.json.
    watchdog_max_respawns: int = 3
    # Watchdog probe debounce: how many consecutive failed /health
    # probes before declaring the agent dead and respawning.
    watchdog_probe_debounce_count: int = 2
    # Backoff (seconds) between debounced probe attempts. Length should
    # equal watchdog_probe_debounce_count; the first delay is between
    # probe 1 and 2, etc. List is consumed positionally.
    watchdog_probe_debounce_secs: list[int] = field(default_factory=lambda: [2, 5])

    def __post_init__(self) -> None:
        # Defensive coercion only — never raise on a hand-edited TOML.
        # The install flow has to be the path that recovers a broken
        # config, so we clamp to safe defaults instead of bailing out.
        self.agent_first = bool(self.agent_first)
        self.auto_resume = bool(self.auto_resume)
        self.wait_ready_stage2_secs = _clamp_int(
            self.wait_ready_stage2_secs, lo=60, hi=14400, fallback=900
        )
        self.wait_ready_stage3_secs = _clamp_int(
            self.wait_ready_stage3_secs, lo=60, hi=14400, fallback=1800
        )
        self.watchdog_max_respawns = _clamp_int(self.watchdog_max_respawns, lo=0, hi=20, fallback=3)
        self.watchdog_probe_debounce_count = _clamp_int(
            self.watchdog_probe_debounce_count, lo=1, hi=10, fallback=2
        )
        self.watchdog_probe_debounce_secs = _coerce_positive_int_list(
            self.watchdog_probe_debounce_secs, default=[2, 5]
        )


@dataclass
class Config:
    rdp: RDPConfig = field(default_factory=RDPConfig)
    pod: PodConfig = field(default_factory=PodConfig)
    reverse_open: ReverseOpenConfig = field(default_factory=ReverseOpenConfig)
    install: InstallConfig = field(default_factory=InstallConfig)

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "winpodx.toml"

    @classmethod
    def load(cls) -> Config:
        """Load config from TOML file, falling back to defaults."""
        import logging

        path = cls.path()
        cfg = cls()
        if not path.exists():
            return cfg

        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, PermissionError) as e:
            logging.getLogger(__name__).warning("Corrupted config %s, using defaults: %s", path, e)
            return cfg

        _apply(cfg.rdp, data.get("rdp", {}))
        _apply(cfg.pod, data.get("pod", {}))
        _apply(cfg.reverse_open, data.get("reverse_open", {}))
        _apply(cfg.install, data.get("install", {}))
        cfg.rdp.__post_init__()
        cfg.pod.__post_init__()
        cfg.reverse_open.__post_init__()
        cfg.install.__post_init__()
        return cfg

    def save(self) -> None:
        """Write current config to TOML file with secure permissions."""
        import os
        import tempfile

        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)

        data: dict[str, Any] = {
            "rdp": {
                "user": self.rdp.user,
                "password": self.rdp.password,
                "password_updated": self.rdp.password_updated,
                "password_max_age": self.rdp.password_max_age,
                "askpass": self.rdp.askpass,
                "domain": self.rdp.domain,
                "ip": self.rdp.ip,
                "port": self.rdp.port,
                "scale": self.rdp.scale,
                "dpi": self.rdp.dpi,
                "extra_flags": self.rdp.extra_flags,
            },
            "pod": {
                "backend": self.pod.backend,
                "vm_name": self.pod.vm_name,
                "container_name": self.pod.container_name,
                "win_version": self.pod.win_version,
                "cpu_cores": self.pod.cpu_cores,
                "ram_gb": self.pod.ram_gb,
                "vnc_port": self.pod.vnc_port,
                "auto_start": self.pod.auto_start,
                "idle_timeout": self.pod.idle_timeout,
                "boot_timeout": self.pod.boot_timeout,
                "image": self.pod.image,
                "disk_size": self.pod.disk_size,
                "max_sessions": self.pod.max_sessions,
                "storage_path": self.pod.storage_path,
            },
            "reverse_open": {
                "enabled": self.reverse_open.enabled,
                "allowlist": list(self.reverse_open.allowlist),
                "denylist": list(self.reverse_open.denylist),
                "last_synced_at": self.reverse_open.last_synced_at,
                "deny_dangerous": self.reverse_open.deny_dangerous,
            },
            "install": {
                "agent_first": self.install.agent_first,
                "wait_ready_stage2_secs": self.install.wait_ready_stage2_secs,
                "wait_ready_stage3_secs": self.install.wait_ready_stage3_secs,
                "auto_resume": self.install.auto_resume,
                "watchdog_max_respawns": self.install.watchdog_max_respawns,
                "watchdog_probe_debounce_count": self.install.watchdog_probe_debounce_count,
                "watchdog_probe_debounce_secs": list(self.install.watchdog_probe_debounce_secs),
            },
        }

        # Atomic write: create temp file with 0600, fsync, then rename.
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".winpodx-", suffix=".tmp")
        fd_closed = False
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, toml_dumps(data).encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            fd_closed = True
            os.replace(tmp_path, path)
            # Best-effort parent directory fsync so the rename itself is durable.
            try:
                dir_fd = os.open(path.parent, os.O_DIRECTORY)
            except OSError:
                dir_fd = None
            if dir_fd is not None:
                try:
                    os.fsync(dir_fd)
                except OSError:
                    pass
                finally:
                    os.close(dir_fd)
        except Exception:
            if not fd_closed:
                os.close(fd)
            Path(tmp_path).unlink(missing_ok=True)
            raise


def _clamp_int(value: Any, *, lo: int, hi: int, fallback: int) -> int:
    """Coerce ``value`` to an int clamped to ``[lo, hi]``.

    A hand-edited TOML string (``"30"``) coerces; anything not
    convertible falls back to ``fallback`` (which is then itself
    clamped, so a misuse of this helper still returns a sane value).
    """
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        ivalue = fallback
    return max(lo, min(hi, ivalue))


def _coerce_positive_int_list(value: Any, *, default: list[int]) -> list[int]:
    """Coerce ``value`` to a list of positive ints.

    Returns a copy of ``default`` if ``value`` is not a list, is
    empty, or contains any element that cannot be coerced to a
    positive int. The list is fully validated rather than partially
    repaired so a watchdog backoff schedule is either entirely the
    config-supplied one or entirely the default.
    """
    if not isinstance(value, list) or not value:
        return list(default)
    out: list[int] = []
    for elem in value:
        try:
            ielem = int(elem)
        except (TypeError, ValueError):
            return list(default)
        if ielem <= 0:
            return list(default)
        out.append(ielem)
    return out


def _sanitise_storage_path(value: Any) -> str:
    """Coerce an untrusted ``cfg.pod.storage_path`` value to a safe string.

    Returns either the original string (when it passes all checks) or
    ``""`` (which compose.py interprets as legacy named-volume mode).

    Layered checks, all silent on rejection so a hand-edited TOML can't
    brick startup:

    1. Type — non-string values become ``""``.
    2. Trim + emptiness — pure whitespace becomes ``""``.
    3. Absolute path — rejects relative paths (``./foo``, ``foo/bar``)
       and bare names. Bind-mounting a relative path under podman is
       error-prone; force the user to be explicit.
    4. Denylist of system roots — refuses ``/``, ``/etc``, ``/usr``,
       ``/boot``, ``/proc``, ``/sys``, ``/dev``, ``/var``, ``/lib``,
       ``/lib64``, ``/sbin``, ``/bin``, ``/root``, ``/run``. A hand-
       edited TOML pointing storage_path at one of these would later
       trigger ``chattr +C`` and ``rsync`` against system directories
       — the kind of mistake config validation should catch.
    5. Allowlist of safe parents — the resolved path must live under
       the user's home directory or under one of the explicit
       winpodx-managed roots (``/var/lib/winpodx``, ``/tmp/winpodx-*``).
       Other locations bounce back to ``""``.

    The expanded path is used only for the safety check; the original
    (un-expanded) string is what we store, so ``~/.local/share/...``
    survives a roundtrip and the actual filesystem creation happens
    later via :func:`Path.expanduser` at the call site.
    """
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""

    # Reject characters that would break YAML interpolation or imply
    # shell expansion. This duplicates compose.py's own defence so the
    # bad value never reaches that layer.
    if any(c in raw for c in "\n\r\"'`$") or "{" in raw or "}" in raw:
        return ""

    try:
        expanded = Path(raw).expanduser()
    except (RuntimeError, OSError):
        return ""

    if not expanded.is_absolute():
        return ""

    # Resolve `..` and symlinks so the allowlist sees the final target.
    # Use strict=False so non-existent paths still validate (the caller
    # mkdirs them later).
    try:
        resolved = expanded.resolve(strict=False)
    except (RuntimeError, OSError):
        return ""

    resolved_str = str(resolved)
    if resolved_str == "/":
        return ""

    # Allowlist gate. The path must be under one of these explicit
    # roots. Anything else is rejected — there's no separate denylist
    # because "not in allowlist" already covers it. Allowlist:
    #
    #   - the user's home directory (covers
    #     `~/.local/share/winpodx/storage` and any other user-chosen
    #     path under HOME)
    #   - `/var/lib/winpodx` (system-wide install path; carved out of
    #     `/var` which is otherwise off-limits)
    #   - `/tmp/...` (host-tmpfs in most distros; carved out so pytest
    #     `tmp_path` fixtures and ad-hoc test paths work)
    try:
        home = Path.home().resolve(strict=False)
    except (RuntimeError, OSError):
        home = None

    if home is not None:
        try:
            if resolved.is_relative_to(home):
                return raw
        except (ValueError, OSError):
            pass

    if resolved_str.startswith("/var/lib/winpodx/") or resolved_str == "/var/lib/winpodx":
        return raw

    if resolved_str.startswith("/tmp/") or resolved_str == "/tmp":
        return raw

    return ""


def _apply(obj: Any, data: dict[str, Any]) -> None:
    """Apply dict values to a dataclass instance, with type checking."""
    import dataclasses
    import logging

    log = logging.getLogger(__name__)
    allowed = {f.name for f in dataclasses.fields(obj)}
    for key, val in data.items():
        if key not in allowed:
            continue
        expected = type(getattr(obj, key))
        if expected is not type(None) and not isinstance(val, expected):
            try:
                if expected is bool and isinstance(val, str):
                    val = val.lower() in ("true", "1", "yes")
                else:
                    val = expected(val)
            except (ValueError, TypeError):
                log.warning(
                    "Config key %r: cannot coerce %r to %s, using default",
                    key,
                    val,
                    expected.__name__,
                )
                continue
        setattr(obj, key, val)


def estimate_session_memory(max_sessions: int) -> float:
    """Rough memory footprint estimate (GB) for N concurrent RemoteApp sessions.

    Captures the **fixed** cost of running the guest + RAIL overhead
    per session (~100 MB per session for the RDP channel and session
    process). Per-app working set (Word, Chrome, etc.) is explicitly
    NOT counted — that's the user's responsibility and varies wildly.
    """
    return 2.0 + (max_sessions * 0.1)


def check_session_budget(cfg: Config) -> str | None:
    """Return a human-readable warning when max_sessions over-runs ram_gb, else None.

    Quiet by default: only fires when the rough estimate exceeds the
    pod's advertised RAM budget. The default config (10 sessions, 4 GB)
    is silent; a 30-session bump on 4 GB fires; a 30-session bump on
    8 GB is silent.
    """
    est = estimate_session_memory(cfg.pod.max_sessions)
    if est <= cfg.pod.ram_gb:
        return None
    deficit = est - cfg.pod.ram_gb
    rec = int(est) + 1
    return (
        f"max_sessions={cfg.pod.max_sessions} is estimated to need ~{est:.1f} GB "
        f"(RAIL + guest base); pod has ram_gb={cfg.pod.ram_gb} "
        f"({deficit:.1f} GB short). Consider raising pod.ram_gb to at least {rec}."
    )
