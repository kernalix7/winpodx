"""FreeRDP session management.

Wraps xfreerdp3/xfreerdp to launch and manage RDP sessions
for individual Windows applications.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from winpodx.core.config import Config
from winpodx.utils.paths import runtime_dir

log = logging.getLogger(__name__)


@dataclass
class RDPSession:
    app_name: str
    process: subprocess.Popen | None = None

    @property
    def pid_file(self) -> Path:
        return runtime_dir() / f"{self.app_name}.cproc"

    @property
    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None


def find_freerdp() -> tuple[str, str] | None:
    """Locate a FreeRDP 3+ binary on the system.

    Search order: xfreerdp3 → xfreerdp → sdl-freerdp3 → flatpak
    Note: wlfreerdp is deprecated upstream; xfreerdp works on Wayland via XWayland.

    Returns (binary_path, variant) where variant is one of:
      'xfreerdp', 'sdl', 'flatpak'.
    """
    # xfreerdp works on both X11 and Wayland (via XWayland)
    for name in ("xfreerdp3", "xfreerdp"):
        path = shutil.which(name)
        if path:
            return (path, "xfreerdp")

    # SDL client — native X11/Wayland support
    for name in ("sdl-freerdp3", "sdl-freerdp"):
        path = shutil.which(name)
        if path:
            return (path, "sdl")

    # Flatpak fallback
    try:
        result = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and "com.freerdp.FreeRDP" in result.stdout:
            return ("flatpak run com.freerdp.FreeRDP", "flatpak")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return None


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

    # Display & performance
    cmd += [
        "+home-drive",
        "+clipboard",
        "-wallpaper",
    ]

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

    # Launch specific app via RemoteApp (FreeRDP 3.x syntax)
    if app_executable:
        from pathlib import PureWindowsPath

        stem = PureWindowsPath(app_executable).stem.lower()
        app_arg = f"/app:program:{app_executable},name:{stem}"
        if file_path:
            unc_path = linux_to_unc(file_path)
            app_arg += f",cmd:{unc_path}"
        cmd.append(app_arg)
        cmd.append(f"/wm-class:{stem}")

    # TLS auth: NLA/Kerberos fails inside podman unshare namespace
    # (krb5_parse_name EAGAIN), so use TLS-level authentication.
    if cfg.pod.backend == "podman":
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


# Allowlist of safe FreeRDP flag prefixes
_SAFE_FLAG_PREFIXES = (
    "/scale:",
    "/scale-desktop:",
    "/scale-device:",
    "/size:",
    "/w:",
    "/h:",
    "/sound",
    "/microphone",
    "/gfx",
    "/rfx",
    "/bpp:",
    "/network:",
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
    "/codec:",
    "/compression",
    "+compression",
    "-compression",
    "+gestures",
    "-gestures",
    "/log-level:",
    "/smartcard",
    "/usb:",
    "/printer",
    "/drive:",
    "/serial:",
    "/parallel:",
)


def _filter_extra_flags(flags_str: str) -> list[str]:
    """Filter extra_flags to only allow safe FreeRDP switches."""
    parts = shlex.split(flags_str)
    safe: list[str] = []
    for part in parts:
        lower = part.lower()
        if any(lower.startswith(prefix.lower()) for prefix in _SAFE_FLAG_PREFIXES):
            safe.append(part)
        else:
            log.warning("Blocked unsafe extra_flag: %s", part)
    return safe


def _find_existing_session(app_name: str) -> RDPSession | None:
    """Check if an RDP session for this app is already running."""
    pid_file = runtime_dir() / f"{app_name}.cproc"
    if not pid_file.exists():
        return None

    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return None

    # Verify PID is alive (signal 0 = just check existence)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        pid_file.unlink(missing_ok=True)
        return None

    # Verify it's actually an RDP-related process
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().lower()
        if b"freerdp" not in cmdline and b"winpodx" not in cmdline:
            pid_file.unlink(missing_ok=True)
            return None
    except OSError:
        pid_file.unlink(missing_ok=True)
        return None

    log.info(
        "Reusing existing RDP session for %s (pid %d)",
        app_name,
        pid,
    )
    return RDPSession(app_name=app_name)


def _reaper_thread(proc: subprocess.Popen, pid_file: Path) -> None:
    """Wait for process exit and clean up PID file."""
    proc.wait()
    pid_file.unlink(missing_ok=True)


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

    # Password is embedded in the command via /p: flag
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
    finally:
        lock_fd.close()

    # Reaper thread to clean up zombie and PID file
    t = threading.Thread(
        target=_reaper_thread,
        args=(proc, session.pid_file),
        daemon=True,
    )
    t.start()

    return session


def launch_desktop(cfg: Config) -> RDPSession:
    """Launch a full Windows desktop RDP session (no RemoteApp)."""
    return launch_app(cfg, app_executable=None, file_path=None)


def linux_to_unc(path: str) -> str:
    """Convert a Linux file path to a Windows UNC path via tsclient.

    The RDP drive share maps the Linux home directory as \\tsclient\\home.
    Validates the path contains no characters invalid in Windows paths.
    """
    # Reject characters invalid in Windows file paths
    _INVALID_WIN_CHARS = set('*?"<>|')
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
        # File outside home — use absolute path
        win_path = str(p).lstrip("/").replace("/", sep)
        return f"\\\\tsclient\\{win_path}"
