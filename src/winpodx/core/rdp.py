"""FreeRDP session management."""

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

# Characters invalid in Windows file paths; rejected by linux_to_unc.
_INVALID_WIN_CHARS: frozenset[str] = frozenset('*?"<>|')

# UWP AUMID: <PackageFamilyName>!<AppId>
#   PackageFamilyName := <Name>_<PublisherId>  (Name dotted, PublisherId is
#   a 13-char hash; Microsoft docs don't fix its alphabet but all observed
#   values are [a-z0-9])
#   AppId := up to 64 chars of word / dot / hyphen.
# Kept strict to block values with separators FreeRDP parses (``,``) or
# shell metacharacters a malicious discovery JSON could smuggle in.
_AUMID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}![A-Za-z0-9._-]{1,64}$")

# /wm-class and the /app name: sub-key must be a bounded, shell-safe token;
# we lowercase first so the regex only needs to cover the trimmed form.
_WM_CLASS_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _is_valid_aumid(value: str) -> bool:
    """Return True if ``value`` is a syntactically valid UWP AUMID."""
    return _AUMID_RE.fullmatch(value) is not None


def _is_safe_wm_class(value: str) -> bool:
    """Return True if ``value`` is a safe /wm-class / name: token."""
    return _WM_CLASS_RE.fullmatch(value) is not None


def _uwp_fallback_wm_class(aumid: str) -> str:
    """Derive a unique wm-class from a validated AUMID.

    Used when ``wm_class_hint`` is missing or fails ``_is_safe_wm_class``.
    A single ``winpodx-uwp`` bucket would collide pid files and make
    the Linux WM group unrelated UWP apps as one taskbar entry, so we
    slug the AUMID (already ``_is_valid_aumid``-validated) to produce
    ``winpodx-uwp-<slug>`` — unique per app, still bounded and shell-safe.
    """
    # AUMID is already validated to [A-Za-z0-9._-]+!..., so lowercasing
    # and replacing '!'/'.' with '-' keeps it inside the _WM_CLASS_RE alphabet.
    slug = aumid.lower().replace("!", "-").replace(".", "-")
    # Collapse any run of dashes introduced by the substitution.
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-_")
    candidate = f"winpodx-uwp-{slug}" if slug else "winpodx-uwp"
    # /wm-class tokens are bounded at 64 chars by _WM_CLASS_RE; truncate
    # rather than fail because the AUMID itself is legitimately long.
    if len(candidate) > 64:
        candidate = candidate[:64].rstrip("-_")
    if not _is_safe_wm_class(candidate):
        return "winpodx-uwp"
    return candidate


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
    """Find the user's removable media base directory."""
    user = os.environ.get("USER", "")

    for base in (Path("/run/media") / user, Path("/media") / user):
        if base.is_dir():
            return base

    return None


# Success-only cache; a miss is not cached so a mid-session install is picked up.
_FREERDP_CACHE: tuple[str, str] | None = None


def find_freerdp() -> tuple[str, str] | None:
    """Locate a FreeRDP 3+ binary on the system."""
    global _FREERDP_CACHE
    if _FREERDP_CACHE is not None:
        return _FREERDP_CACHE

    found: tuple[str, str] | None = None

    for name in ("xfreerdp3", "xfreerdp"):
        path = shutil.which(name)
        if path:
            found = (path, "xfreerdp")
            break

    if found is None:
        for name in ("sdl-freerdp3", "sdl-freerdp"):
            path = shutil.which(name)
            if path:
                found = (path, "sdl")
                break

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
    """Resolve the RDP password from askpass or config."""
    if cfg.rdp.askpass:
        parts = shlex.split(cfg.rdp.askpass)
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
    launch_uri: str | None = None,
    wm_class_hint: str | None = None,
) -> tuple[list[str], str]:
    """Build the xfreerdp command line for launching an app.

    For classic Win32 apps, pass ``app_executable`` (and optionally
    ``file_path``). For UWP/MSIX apps, pass ``launch_uri`` containing
    the AUMID (e.g. ``"Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"``);
    the builder forwards ``explorer.exe shell:AppsFolder\\<AUMID>`` as
    the RemoteApp program. ``wm_class_hint`` overrides the default
    ``/wm-class`` (exe stem) so the Linux window lines up with the
    discovered app's desktop entry rather than ``explorer``.
    """
    found = find_freerdp()
    if not found:
        raise RuntimeError("FreeRDP 3+ not found. Install xfreerdp3 or xfreerdp.")

    binary, variant = found

    # Rootless podman needs --rootless-netns to reach port-mapped containers.
    cmd: list[str] = []

    if cfg.pod.backend == "podman" and shutil.which("podman"):
        cmd += ["podman", "unshare", "--rootless-netns"]

    cmd += shlex.split(binary)

    cmd += [
        f"/v:{cfg.rdp.ip}:{cfg.rdp.port}",
        f"/u:{cfg.rdp.user}",
    ]

    if cfg.rdp.domain:
        cmd.append(f"/d:{cfg.rdp.domain}")

    cmd += [
        "+home-drive",
        "+clipboard",
        "-wallpaper",
        "/sound:sys:alsa",
        "/printer",
    ]

    # Share the media mount directory so USB storage appears under \\tsclient\media.
    media_base = _find_media_base()
    if media_base:
        cmd.append(f"/drive:media,{media_base}")

    cmd.append(f"/scale:{cfg.rdp.scale}")

    # Windows DPI scaling (0 = let Windows decide)
    if cfg.rdp.dpi > 0:
        cmd.append(f"/scale-desktop:{cfg.rdp.dpi}")

    # /p: exposes the password in /proc/pid/cmdline (same-uid-readable only);
    # /from-stdin:force is not viable under podman unshare (no tty) or GUI launches.
    password = _resolve_password(cfg)
    if password:
        cmd.append(f"/p:{password}")
        password = ""  # signal launch_app to skip stdin write

    # RemoteApp (RAIL) launch; requires fDisabledAllowList=1 set by install.bat.
    if launch_uri:
        # UWP/MSIX: launch via explorer + shell:AppsFolder\<AUMID>. The
        # AUMID must be a bare package-family!app-id token — reject
        # anything that looks like a flag payload or embeds separators
        # FreeRDP treats specially ("," splits /app sub-args).
        aumid = launch_uri.strip()
        if not _is_valid_aumid(aumid):
            raise RuntimeError(f"Invalid UWP AUMID: {aumid!r}")
        wm_class = (wm_class_hint or "").strip().lower()
        if not wm_class or not _is_safe_wm_class(wm_class):
            wm_class = _uwp_fallback_wm_class(aumid)
        app_arg = f"/app:program:explorer.exe,name:{wm_class},cmd:shell:AppsFolder\\{aumid}"
        cmd.append(app_arg)
        cmd.append(f"/wm-class:{wm_class}")
        cmd.append("+grab-keyboard")
    elif app_executable:
        from pathlib import PureWindowsPath

        stem = PureWindowsPath(app_executable).stem.lower()
        name_token = (wm_class_hint or "").strip().lower() or stem
        if not _is_safe_wm_class(name_token):
            name_token = stem
        app_arg = f"/app:program:{app_executable},name:{name_token}"
        if file_path:
            try:
                unc_path = linux_to_unc(file_path)
            except ValueError as e:
                # Convert to RuntimeError so CLI (_run_app) surfaces it to the user.
                raise RuntimeError(f"Cannot open file: {e}") from e
            app_arg += f",cmd:{unc_path}"
        cmd.append(app_arg)
        cmd.append(f"/wm-class:{name_token}")
        cmd.append("+grab-keyboard")

    # TLS for all backends: install.bat forces SecurityLayer=2 and podman unshare breaks NLA.
    cmd.append("/sec:tls")

    if cfg.rdp.ip in ("127.0.0.1", "localhost", "::1"):
        cmd.append("/cert:ignore")
    else:
        cmd.append("/cert:tofu")

    if cfg.rdp.extra_flags:
        cmd += _filter_extra_flags(cfg.rdp.extra_flags)

    return cmd, password


# Allowlist: bare toggles, value-regex, device-redirection strict set.
# Exact-match flags (no :arg payload tolerated).
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
        # /sound, /microphone, /gfx, /rfx also accept :arg forms handled below.
        "/sound",
        "/microphone",
        "/gfx",
        "/rfx",
        "/smartcard",
    }
)

# <flag>:<value> pairs validated by per-flag fullmatch regex.
_SIMPLE_VALUE_FLAGS: dict[str, re.Pattern[str]] = {
    # Display / scaling: small positive integers only.
    "/scale": re.compile(r"[1-9][0-9]{0,3}"),
    "/scale-desktop": re.compile(r"[1-9][0-9]{0,3}"),
    "/scale-device": re.compile(r"[1-9][0-9]{0,3}"),
    "/size": re.compile(r"[1-9][0-9]{1,4}x[1-9][0-9]{1,4}"),
    "/w": re.compile(r"[1-9][0-9]{1,4}"),
    "/h": re.compile(r"[1-9][0-9]{1,4}"),
    "/bpp": re.compile(r"(8|15|16|24|32)"),
    # Documented FreeRDP keywords only.
    "/network": re.compile(r"(modem|broadband|broadband-low|broadband-high|wan|lan|auto)"),
    "/codec": re.compile(r"[a-zA-Z0-9_-]{1,32}"),
    # /sound:sys:alsa and similar; bounded identifier pairs, no file paths.
    "/sound": re.compile(r"[a-zA-Z0-9_-]{1,16}(:[a-zA-Z0-9_-]{1,16}){0,3}"),
    "/microphone": re.compile(r"[a-zA-Z0-9_-]{1,16}(:[a-zA-Z0-9_-]{1,16}){0,3}"),
    "/gfx": re.compile(r"[a-zA-Z0-9_,:+-]{1,64}"),
    "/rfx": re.compile(r"[a-zA-Z0-9_-]{1,32}"),
    # Documented log levels only; rejects wildcard scopes like TRACE:FOO.
    "/log-level": re.compile(r"(OFF|FATAL|ERROR|WARN|INFO|DEBUG|TRACE)", re.IGNORECASE),
}

# Device-redirection allowlist; empty set rejects all :value forms.
_STRICT_PATTERN_FLAGS: dict[str, frozenset[str]] = {
    "/drive": frozenset({"home", "media"}),
    "/usb": frozenset({"auto"}),
    "/serial": frozenset(),
    "/parallel": frozenset(),
    "/smartcard": frozenset(),
}


def _validate_flag(part: str) -> bool:
    """Return True if part is an allowed FreeRDP flag token."""
    if part in _BARE_FLAGS:
        return True

    if ":" not in part:
        return False
    flag, _, value = part.partition(":")

    if flag in _STRICT_PATTERN_FLAGS:
        allowed = _STRICT_PATTERN_FLAGS[flag]
        # Reject separators used to smuggle host paths (e.g. /drive:x,/etc).
        if "," in value or "/" in value or "\\" in value:
            return False
        return value in allowed

    pattern = _SIMPLE_VALUE_FLAGS.get(flag)
    if pattern is None:
        return False
    return pattern.fullmatch(value) is not None


def _filter_extra_flags(flags_str: str) -> list[str]:
    """Filter extra_flags to only allow safe FreeRDP switches."""
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

    # Must be a live freerdp/xfreerdp process (guard against PID reuse).
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
    """Wait for process exit, drain stderr, and clean up the PID file."""
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
    launch_uri: str | None = None,
    wm_class_hint: str | None = None,
) -> RDPSession:
    """Launch a Windows app via RDP and return the session handle.

    Pass ``launch_uri`` for UWP/MSIX apps (takes precedence over
    ``app_executable``) and ``wm_class_hint`` to override the default
    ``/wm-class`` token (needed for UWP so the Linux window doesn't
    come up labelled ``explorer``).
    """
    import threading

    if launch_uri:
        hint = (wm_class_hint or "").strip().lower()
        if hint and _is_safe_wm_class(hint):
            app_name = hint
        else:
            # Derive a per-app slug so two UWP apps with invalid hints
            # don't collide on the same pid_file. If the AUMID itself
            # is malformed, build_rdp_command will reject it shortly;
            # for pid-file purposes just fall back to the bare bucket.
            aumid = launch_uri.strip()
            app_name = _uwp_fallback_wm_class(aumid) if _is_valid_aumid(aumid) else "winpodx-uwp"
    elif app_executable:
        from pathlib import PureWindowsPath

        app_name = PureWindowsPath(app_executable).stem.lower()
    else:
        app_name = "desktop"

    existing = _find_existing_session(app_name)
    if existing is not None:
        return existing

    cmd, password = build_rdp_command(
        cfg,
        app_executable=app_executable,
        file_path=file_path,
        launch_uri=launch_uri,
        wm_class_hint=wm_class_hint,
    )

    log.info("Launching RDP: %s", " ".join(cmd))

    # Acquire PID file lock before launching to prevent race conditions.
    session = RDPSession(app_name=app_name)
    session.pid_file.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = session.pid_file.open("w")
    try:
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fd.close()
        existing = _find_existing_session(app_name)
        if existing is not None:
            return existing
        raise RuntimeError(f"Could not acquire lock for {app_name}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        session.process = proc

        lock_fd.write(str(proc.pid))
        lock_fd.flush()
    except Exception:
        session.pid_file.unlink(missing_ok=True)
        raise
    finally:
        lock_fd.close()

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
    """Convert a Linux file path to a Windows UNC path via tsclient."""
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

    # Media share mounted as \\tsclient\media.
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
