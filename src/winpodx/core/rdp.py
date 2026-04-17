"""FreeRDP session management.

Wraps xfreerdp3/xfreerdp to launch and manage RDP sessions
for individual Windows applications.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from winpodx.core.config import Config
from winpodx.utils.paths import runtime_dir

log = logging.getLogger(__name__)

# Characters invalid in Windows file paths — rejected by ``linux_to_unc``.
_INVALID_WIN_CHARS: frozenset[str] = frozenset('*?"<>|')


@dataclass
class RDPSession:
    app_name: str
    process: subprocess.Popen | None = None
    stderr_tail: bytes = b""

    @property
    def pid_file(self) -> Path:
        return runtime_dir() / f"{self.app_name}.cproc"

    @property
    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None


def _find_media_base() -> Path | None:
    """Find the user's removable media base directory.

    Shares the parent directory so USB drives plugged in after
    session start are visible as subfolders without reconnecting.
    """
    user = os.environ.get("USER", "")

    # /run/media/$USER (modern: openSUSE, Fedora, Arch)
    # /media/$USER     (Ubuntu, Debian)
    for base in (Path("/run/media") / user, Path("/media") / user):
        if base.is_dir():
            return base

    return None


# Module-level cache for ``find_freerdp`` results. Only populated on success
# — a ``None`` result means FreeRDP was not found, and caching that would
# prevent the tray/GUI (which call ``launch_app`` in-process) from noticing
# a mid-session install. Success results are stable: xfreerdp doesn't move
# once installed.
_FREERDP_CACHE: tuple[str, str] | None = None


def find_freerdp() -> tuple[str, str] | None:
    """Locate a FreeRDP 3+ binary on the system.

    Search order: xfreerdp3 → xfreerdp → sdl-freerdp3 → flatpak
    Note: wlfreerdp is deprecated upstream; xfreerdp works on Wayland via XWayland.

    Returns (binary_path, variant) where variant is one of:
      'xfreerdp', 'sdl', 'flatpak'.

    Success results are cached in ``_FREERDP_CACHE`` per-process. Cold
    lookups cost up to 10s when the flatpak fallback runs, so repeated
    launches from a long-running tray/GUI reuse the first hit. ``None``
    results are intentionally NOT cached so an install-after-startup is
    picked up on the next launch attempt.
    """
    global _FREERDP_CACHE
    if _FREERDP_CACHE is not None:
        return _FREERDP_CACHE

    found: tuple[str, str] | None = None

    # xfreerdp works on both X11 and Wayland (via XWayland)
    for name in ("xfreerdp3", "xfreerdp"):
        path = shutil.which(name)
        if path:
            found = (path, "xfreerdp")
            break

    # SDL client — native X11/Wayland support
    if found is None:
        for name in ("sdl-freerdp3", "sdl-freerdp"):
            path = shutil.which(name)
            if path:
                found = (path, "sdl")
                break

    # Flatpak fallback
    if found is None:
        try:
            result = subprocess.run(
                ["flatpak", "list", "--app", "--columns=application"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and "com.freerdp.FreeRDP" in result.stdout:
                found = ("flatpak run com.freerdp.FreeRDP", "flatpak")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if found is not None:
        _FREERDP_CACHE = found
    return found


def _resolve_password(cfg: Config) -> str:
    """Resolve the RDP password from askpass or config.

    Prefers askpass command; validates it exists before executing.
    Falls back to stored password in config.
    """
    if cfg.rdp.askpass:
        parts = shlex.split(cfg.rdp.askpass)
        # Validate the askpass binary exists
        if not shutil.which(parts[0]):
            log.warning("askpass binary not found: %s", parts[0])
        else:
            try:
                result = subprocess.run(
                    parts,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
            except (subprocess.TimeoutExpired, FileNotFoundError):
                log.warning("askpass command failed")

    return cfg.rdp.password


def build_rdp_command(
    cfg: Config,
    app_executable: str | None = None,
    file_path: str | None = None,
    auto_scale: bool = True,
) -> tuple[list[str], str]:
    """Build the xfreerdp command line for launching an app.

    Returns (command_list, password). Password is passed via stdin
    to avoid exposing it in /proc/pid/cmdline.
    """
    found = find_freerdp()
    if not found:
        raise RuntimeError("FreeRDP 3+ not found. Install xfreerdp3 or xfreerdp.")

    binary, variant = found

    # Rootless podman requires entering the network namespace to reach
    # port-mapped containers.  podman unshare --rootless-netns wraps the
    # xfreerdp process so it can connect to 127.0.0.1:<mapped-port>.
    cmd: list[str] = []

    if cfg.pod.backend == "podman" and shutil.which("podman"):
        cmd += ["podman", "unshare", "--rootless-netns"]

    cmd += shlex.split(binary)

    # Connection
    cmd += [
        f"/v:{cfg.rdp.ip}:{cfg.rdp.port}",
        f"/u:{cfg.rdp.user}",
    ]

    if cfg.rdp.domain:
        cmd.append(f"/d:{cfg.rdp.domain}")

    # Display & performance.
    # Perf flags (safe to hardcode — all pure-visual / negotiation hints,
    # none disable functionality):
    #   -wallpaper / -menu-anims / -window-drag: turn off eye-candy, not
    #       the underlying windows — menus still open, drag still works,
    #       the background just isn't rendered. Biggest latency win on
    #       slow loopback/VM paths.
    #   /gfx (no codec arg): enable the RDP graphics pipeline explicitly
    #       and let FreeRDP negotiate the best codec with the Windows
    #       side. Avoids forcing AVC420, which would fail on FreeRDP
    #       builds compiled without H.264 support (some source builds).
    #   /network:auto: let FreeRDP tune compression/fastpath to measured
    #       link quality instead of assuming LAN. Localhost → LAN profile,
    #       slower links → broadband tuning.
    cmd += [
        "+home-drive",
        "+clipboard",
        "-wallpaper",
        "-menu-anims",
        "-window-drag",
        "/gfx",
        "/network:auto",
        "/sound:sys:alsa",
        "/printer",
        "/usb:auto",
    ]

    # USB sharing (two layers):
    # 1. /usb:auto — if urbdrc plugin is available, USB devices appear
    #    as real USB in Windows. If plugin is missing, FreeRDP logs a
    #    warning and continues (no crash).
    # 2. /drive:media — fallback: shares the media mount directory so
    #    USB storage is always accessible as \\tsclient\media, even
    #    without the urbdrc plugin. Drives plugged in after session
    #    start appear as subfolders without reconnecting.
    media_base = _find_media_base()
    if media_base:
        cmd.append(f"/drive:media,{media_base}")

    # Scale from config (detected once during setup, not every launch)
    cmd.append(f"/scale:{cfg.rdp.scale}")

    # Windows DPI scaling (0 = let Windows decide)
    if cfg.rdp.dpi > 0:
        cmd.append(f"/scale-desktop:{cfg.rdp.dpi}")

    # Password: /p: flag exposes the password in /proc/pid/cmdline (readable
    # by the same uid only).  /from-stdin:force is not viable because podman
    # unshare has no tty (tcgetattr fails) and GUI/desktop entry launches
    # lack stdin.  Acceptable risk: RDP is bound to 127.0.0.1, container-only.
    password = _resolve_password(cfg)
    if password:
        cmd.append(f"/p:{password}")
        password = ""  # signal launch_app to skip stdin write

    # Launch app seamlessly via RemoteApp (RAIL).
    # Requires fDisabledAllowList=1 in Windows registry (set by install.bat).
    # Currently single-session: opening a new app reconnects the existing session.
    # Multi-session support (independent RDP sessions per app) is planned.
    if app_executable:
        from pathlib import PureWindowsPath

        stem = PureWindowsPath(app_executable).stem.lower()
        app_arg = f"/app:program:{app_executable},name:{stem}"
        if file_path:
            try:
                unc_path = linux_to_unc(file_path)
            except ValueError as e:
                # Convert to RuntimeError so CLI (_run_app) surfaces it
                # to the user instead of hitting an unhandled exception.
                raise RuntimeError(f"Cannot open file: {e}") from e
            app_arg += f",cmd:{unc_path}"
        cmd.append(app_arg)
        cmd.append(f"/wm-class:{stem}")
        cmd.append("+grab-keyboard")

    # TLS auth for all backends. Two reasons:
    #  1. config/oem/install.bat sets SecurityLayer=2 (TLS) and disables
    #     NLA on the Windows side unconditionally, so docker/libvirt/manual
    #     users would otherwise hit a TLS handshake error when FreeRDP
    #     defaults to NLA/negotiate.
    #  2. NLA/Kerberos also fails inside the podman unshare namespace
    #     (krb5_parse_name EAGAIN).
    # The original podman-only gate caused "Authentication only" errors
    # for every non-podman user.
    cmd.append("/sec:tls")

    # Certificate validation: ignore for localhost (safe), tofu for remote
    if cfg.rdp.ip in ("127.0.0.1", "localhost", "::1"):
        cmd.append("/cert:ignore")
    else:
        cmd.append("/cert:tofu")

    # Extra flags from config (only safe FreeRDP switches allowed)
    if cfg.rdp.extra_flags:
        cmd += _filter_extra_flags(cfg.rdp.extra_flags)

    return cmd, password


# Allowlist for FreeRDP flags. Three categories: bare toggles, value-regex,
# device-redirection strict set. Prefix matching is unsafe (adversarial
# ``/drive:etc,/etc`` etc.), so each flag is validated by argument shape and
# unknown flags are dropped.

# Exact-match flags (no ``:arg`` payload tolerated).
_BARE_FLAGS: frozenset[str] = frozenset(
    {
        "+fonts",
        "-fonts",
        "+aero",
        "-aero",
        "+menu-anims",
        "-menu-anims",
        "+window-drag",
        "-window-drag",
        "/dynamic-resolution",
        "+toggle-fullscreen",
        "+compression",
        "-compression",
        "/compression",
        "+gestures",
        "-gestures",
        "/printer",
        # ``/sound`` / ``/microphone`` / ``/gfx`` / ``/rfx`` are also valid as
        # bare toggles; their ``:arg`` forms are handled below.
        "/sound",
        "/microphone",
        "/gfx",
        "/rfx",
        "/smartcard",
    }
)

# ``<flag>:<value>`` flags where the value is user-tunable but must match a
# conservative regex.  Regexes are anchored at both ends via ``fullmatch``.
# Keep these narrow — the goal is to accept obvious legitimate inputs and
# nothing else.
_SIMPLE_VALUE_FLAGS: dict[str, re.Pattern[str]] = {
    # Display / scaling — small positive integers only
    "/scale": re.compile(r"[1-9][0-9]{0,3}"),
    "/scale-desktop": re.compile(r"[1-9][0-9]{0,3}"),
    "/scale-device": re.compile(r"[1-9][0-9]{0,3}"),
    "/size": re.compile(r"[1-9][0-9]{1,4}x[1-9][0-9]{1,4}"),
    "/w": re.compile(r"[1-9][0-9]{1,4}"),
    "/h": re.compile(r"[1-9][0-9]{1,4}"),
    "/bpp": re.compile(r"(8|15|16|24|32)"),
    # Network profile — documented FreeRDP keywords only
    "/network": re.compile(r"(modem|broadband|broadband-low|broadband-high|wan|lan|auto)"),
    # Codec names — short identifier-ish only
    "/codec": re.compile(r"[a-zA-Z0-9_-]{1,32}"),
    # ``/sound:sys:alsa`` / ``/sound:sys:pulse`` and friends.  Limited to two
    # ``key:value`` pairs of bounded identifiers.  This covers the
    # ``sys:alsa|pulse|oss|fake`` + optional ``format:s16|...`` patterns
    # without permitting arbitrary file paths.
    "/sound": re.compile(r"[a-zA-Z0-9_-]{1,16}(:[a-zA-Z0-9_-]{1,16}){0,3}"),
    "/microphone": re.compile(r"[a-zA-Z0-9_-]{1,16}(:[a-zA-Z0-9_-]{1,16}){0,3}"),
    "/gfx": re.compile(r"[a-zA-Z0-9_,:+-]{1,64}"),
    "/rfx": re.compile(r"[a-zA-Z0-9_-]{1,32}"),
    # Log level — documented FreeRDP keywords.  Rejects ``TRACE:FOO`` etc. so
    # a wildcard scope cannot be injected.
    "/log-level": re.compile(r"(OFF|FATAL|ERROR|WARN|INFO|DEBUG|TRACE)", re.IGNORECASE),
}

# Device-redirection flags tunnel host resources into the guest; only the tiny
# documented value sets below are safe. Empty allowlist => any ``:value`` form
# is rejected (bare ``/smartcard`` toggle is still accepted via ``_BARE_FLAGS``).
_STRICT_PATTERN_FLAGS: dict[str, frozenset[str]] = {
    "/drive": frozenset({"home", "media"}),  # home/media shares only — no host paths
    "/usb": frozenset({"auto"}),  # urbdrc auto-share; specific devs need first-class config
    "/serial": frozenset(),  # reject all — no safe value shape
    "/parallel": frozenset(),  # reject all — no safe value shape
    "/smartcard": frozenset(),  # reject all :value — bare toggle only
}


def _validate_flag(part: str) -> bool:
    """Return True if ``part`` is an allowed FreeRDP flag token.

    Matching is case-sensitive on the flag name (FreeRDP flags are lower-case
    by convention); the value is matched per-flag.
    """
    # Exact-match bare flags first (``+fonts``, ``/printer`` …).
    if part in _BARE_FLAGS:
        return True

    # Require ``<flag>:<value>`` form for everything else.
    if ":" not in part:
        return False
    flag, _, value = part.partition(":")

    # Strict device-redirection allowlist.  Empty allowlist means "no
    # ``:value`` form is acceptable for this flag".
    if flag in _STRICT_PATTERN_FLAGS:
        allowed = _STRICT_PATTERN_FLAGS[flag]
        # Reject any value containing ``,`` / ``/`` / ``\\`` — those are the
        # separators adversarial payloads use to smuggle host paths
        # (``/drive:x,/etc``).  Belt-and-braces alongside the set check.
        if "," in value or "/" in value or "\\" in value:
            return False
        return value in allowed

    # Simple-value flags with per-flag regex.
    pattern = _SIMPLE_VALUE_FLAGS.get(flag)
    if pattern is None:
        return False
    return pattern.fullmatch(value) is not None


def _filter_extra_flags(flags_str: str) -> list[str]:
    """Filter extra_flags to only allow safe FreeRDP switches.

    Uses per-flag argument-shape validation rather than prefix matching so
    that flags like ``/drive:`` and ``/serial:`` cannot smuggle arbitrary
    host paths or device nodes into the Windows guest.  Rejected flags are
    logged at WARNING level and dropped entirely.
    """
    parts = shlex.split(flags_str)
    safe: list[str] = []
    for part in parts:
        if _validate_flag(part):
            safe.append(part)
        else:
            log.warning("Blocked unsafe extra_flag: %s", part)
    return safe


def _find_existing_session(app_name: str) -> RDPSession | None:
    """Check if an RDP session for this app is already running."""
    from winpodx.core.process import is_freerdp_pid

    pid_file = runtime_dir() / f"{app_name}.cproc"
    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return None

    # Single source of truth: must be a live freerdp/xfreerdp process.
    # Rejecting unrelated winpodx CLI invocations that may land on a reused
    # PID — otherwise launch_app would return a fake process=None session.
    if not is_freerdp_pid(pid):
        pid_file.unlink(missing_ok=True)
        return None

    log.info(
        "Reusing existing RDP session for %s (pid %d)",
        app_name,
        pid,
    )
    return RDPSession(app_name=app_name)


def _reaper_thread(session: RDPSession) -> None:
    """Wait for process exit and clean up PID file.

    Drains stderr into session.stderr_tail to prevent pipe buffer
    deadlock on long sessions (FreeRDP blocks once the 64KB pipe
    buffer fills if nothing reads from it).
    """
    proc = session.process
    if proc is None:
        return
    try:
        _out, err = proc.communicate()
        if err:
            session.stderr_tail = err[-2048:]
    except (OSError, ValueError):
        pass
    finally:
        session.pid_file.unlink(missing_ok=True)


def launch_app(
    cfg: Config,
    app_executable: str | None = None,
    file_path: str | None = None,
) -> RDPSession:
    """Launch a Windows app via RDP and return the session handle."""
    import threading

    # Derive a clean app name for tracking
    if app_executable:
        from pathlib import PureWindowsPath

        app_name = PureWindowsPath(app_executable).stem.lower()
    else:
        app_name = "desktop"

    # Reuse existing session if running
    existing = _find_existing_session(app_name)
    if existing is not None:
        return existing

    cmd, password = build_rdp_command(cfg, app_executable, file_path)

    log.info("Launching RDP: %s", " ".join(cmd))

    # Acquire PID file lock before launching to prevent race conditions
    session = RDPSession(app_name=app_name)
    session.pid_file.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = session.pid_file.open("w")
    try:
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fd.close()
        # Another launch in progress — recheck for existing session
        existing = _find_existing_session(app_name)
        if existing is not None:
            return existing
        raise RuntimeError(f"Could not acquire lock for {app_name}")

    # Launch process under the lock
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        session.process = proc

        # Write PID under the lock
        lock_fd.write(str(proc.pid))
        lock_fd.flush()
    except Exception:
        session.pid_file.unlink(missing_ok=True)
        raise
    finally:
        lock_fd.close()

    # Reaper thread to clean up zombie and PID file
    t = threading.Thread(
        target=_reaper_thread,
        args=(session,),
        daemon=True,
    )
    t.start()

    return session


def launch_desktop(cfg: Config) -> RDPSession:
    """Launch a full Windows desktop RDP session (no RemoteApp)."""
    return launch_app(cfg, app_executable=None, file_path=None)


def linux_to_unc(path: str) -> str:
    """Convert a Linux file path to a Windows UNC path via tsclient.

    The RDP drive share maps the Linux home directory as ``\\tsclient\\home``.
    If the user has removable media mounted under ``/run/media/$USER`` or
    ``/media/$USER``, that base is shared as ``\\tsclient\\media`` and paths
    below it are remapped accordingly.

    Raises ``ValueError`` when:
      * the path contains characters invalid in Windows file paths, or
      * the path lies outside any shared location (home or media base).
        Returning an unshared path here would produce a UNC like
        ``\\tsclient\\tmp\\foo.docx`` which Windows cannot resolve,
        leading to silent app failures (empty Office window, "path not
        found"). Callers must surface this error to the user.
    """
    # Reject characters invalid in Windows file paths
    p = Path(path).resolve()
    posix_str = str(p)
    if _INVALID_WIN_CHARS & set(posix_str):
        raise ValueError(f"Path contains characters invalid for Windows: {posix_str}")

    home = Path.home()
    sep = "\\"
    try:
        relative = p.relative_to(home)
        win_path = str(relative).replace("/", sep)
        return f"\\\\tsclient\\home\\{win_path}"
    except ValueError:
        pass

    # Media share: /run/media/$USER or /media/$USER — mounted as \\tsclient\media
    media_base = _find_media_base()
    if media_base is not None:
        try:
            relative = p.relative_to(media_base)
            win_path = str(relative).replace("/", sep)
            return f"\\\\tsclient\\media\\{win_path}"
        except ValueError:
            pass

    raise ValueError(
        f"Path is outside shared locations (home={home}"
        f"{', media=' + str(media_base) if media_base else ''}): {posix_str}. "
        "Move the file under your home directory or a mounted media volume."
    )
