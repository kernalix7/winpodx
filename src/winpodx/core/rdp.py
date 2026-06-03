# SPDX-License-Identifier: MIT
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

    @property
    def pid_file(self) -> Path:
        return runtime_dir() / f"{self.app_name}.cproc"

    @property
    def stderr_log(self) -> Path:
        return runtime_dir() / f"{self.app_name}.stderr"

    @property
    def stderr_tail(self) -> bytes:
        try:
            data = self.stderr_log.read_bytes()
        except OSError:
            return b""
        return data[-2048:]

    @property
    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None


def _find_media_base() -> Path | None:
    """Find a live removable-media parent dir to expose as ``\\tsclient\\media``.

    Returns the *deepest existing* media-parent so a USB plugged in **after**
    the FreeRDP session starts still shows up on a refresh: FreeRDP's drive
    redirection passes the host directory through live (each guest enumeration
    re-reads the host fs), and xfreerdp runs in the host mount namespace (no
    ``unshare`` since #214), so a submount appearing under the redirected dir
    propagates and a guest Explorer refresh (F5) reveals it.

    The per-user dirs (``/run/media/$USER``, ``/media/$USER``) are preferred so
    the shortcut lands one level above the volume (``\\media\\<LABEL>``). They
    only exist once udisks has mounted something for this user, so we also
    accept the persistent parents (``/run/media``, ``/media``) — that catches
    the case where nothing is mounted *yet* at launch but a USB is inserted
    later (it then appears at ``\\media\\$USER\\<LABEL>`` on refresh). Returns
    None only when no media subsystem dir exists at all.
    """
    user = os.environ.get("USER", "")

    for base in (
        Path("/run/media") / user,
        Path("/media") / user,
        Path("/run/media"),
        Path("/media"),
    ):
        if base.is_dir():
            return base

    return None


def _media_redirect_base() -> Path:
    """Directory to expose as ``\\tsclient\\media`` (the guest's USB shortcut).

    install.bat always drops a ``USB`` desktop shortcut pointing at
    ``\\tsclient\\media``. If we only redirected the drive when removable
    media is mounted, clicking that shortcut with no USB plugged in failed
    with "``\\tsclient\\media`` is not accessible ... invalid address". So
    always redirect *something*: a live media parent when one exists (see
    ``_find_media_base`` — chosen so a late-inserted USB shows on refresh),
    otherwise an empty placeholder dir so the shortcut opens to an empty
    folder instead of erroring.
    """
    from winpodx.utils.paths import data_dir

    base = _find_media_base()
    if base is not None:
        return base
    placeholder = data_dir() / "media"
    try:
        placeholder.mkdir(parents=True, exist_ok=True)
    except OSError as e:  # noqa: BLE001
        log.debug("could not create media placeholder %s: %s", placeholder, e)
    return placeholder


# Success-only cache, keyed by preference; a miss is not cached so a
# mid-session install is picked up.
_FREERDP_CACHE: dict[str, tuple[str, str]] = {}
_FREERDP_MAJOR_CACHE: int | None = None


def freerdp_major_version() -> int:
    """Return the FreeRDP major version (2 or 3), or 3 on detection failure.

    Result is cached in ``_FREERDP_MAJOR_CACHE``. We default to 3 when
    the version probe can't run cleanly because: (a) winpodx targets
    FreeRDP 3+ anyway, (b) the combined ``/app:program:X,name:Y,cmd:Z``
    syntax is FreeRDP 3-only and is what most users hit, (c) on
    FreeRDP 2 hosts the user gets the (now-rare) Microsoft Store
    fallback symptom rather than a silent crash.

    The branching matters because FreeRDP 3 made ``/app:`` parse its
    value as ``<key>:<value>,...`` rather than as a bare path, so
    ``/app:C:\\Path\\app.exe`` (FreeRDP 2 syntax) is rejected with
    ``Unexpected keyword`` at the ``C:`` prefix. The combined form is
    the only ``/app:`` form FreeRDP 3 accepts. Conversely, FreeRDP 2
    parses the entire combined string as a literal path and lands on
    the Store fallback (#158).
    """
    global _FREERDP_MAJOR_CACHE
    if _FREERDP_MAJOR_CACHE is not None:
        return _FREERDP_MAJOR_CACHE

    found = find_freerdp()
    if found is None:
        _FREERDP_MAJOR_CACHE = 3  # safest default; later code paths gate on this
        return _FREERDP_MAJOR_CACHE

    path, _kind = found
    # ``flatpak run ...`` returns the inner FreeRDP version too; the
    # caller passes the whole launcher string so shlex it.
    cmd = shlex.split(path) + ["--version"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        _FREERDP_MAJOR_CACHE = 3
        return _FREERDP_MAJOR_CACHE
    # FreeRDP --version prints "This is FreeRDP version <major>.<minor>.<patch>"
    # on stdout. Be forgiving about stream choice and surrounding text.
    blob = (result.stdout or "") + (result.stderr or "")
    match = re.search(r"FreeRDP version\s+(\d+)\.", blob)
    if not match:
        _FREERDP_MAJOR_CACHE = 3
        return _FREERDP_MAJOR_CACHE
    try:
        _FREERDP_MAJOR_CACHE = int(match.group(1))
    except ValueError:
        _FREERDP_MAJOR_CACHE = 3
    return _FREERDP_MAJOR_CACHE


def _find_native_freerdp() -> tuple[str, str] | None:
    """First available native FreeRDP binary.

    ``xfreerdp`` first: it is the only client with working RAIL.
    ``sdl-freerdp`` has none (FreeRDP #9078) and ``wlfreerdp`` is
    deprecated with broken RAIL repaint, so neither is a Wayland
    substitute -- pure-Wayland needs XWayland. SDL stays as a
    full-desktop-only fallback.
    """
    probes: tuple[tuple[tuple[str, ...], str], ...] = (
        (("xfreerdp3", "xfreerdp"), "xfreerdp"),
        (("sdl-freerdp3", "sdl-freerdp"), "sdl"),
    )
    for names, kind in probes:
        for name in names:
            path = shutil.which(name)
            if path:
                return (path, kind)
    return None


# Flatpak `run` options for com.freerdp.FreeRDP. Two things have to be right or
# the client silently degrades inside the sandbox:
#   * --command=xfreerdp — the app's DEFAULT command is the SDL client, which
#     has NO RAIL (FreeRDP #9078); without this, a RemoteApp launch opens the
#     full Windows desktop / login screen instead of a single app window. We
#     force the X11 xfreerdp binary, the only one with working RAIL.
#   * the sockets / device / network / filesystem holes every winpodx RDP flag
#     needs, so clipboard / sound / printer / display / drive-redirection
#     (\\tsclient\home + \\tsclient\media) and the localhost RDP connection all
#     work the same as a native client:
#       /v:127.0.0.1     -> --share=network
#       RAIL + +clipboard -> --socket=x11 (RAIL needs X11/XWayland) + --socket=wayland
#       /sound           -> --socket=pulseaudio
#       /printer         -> --socket=cups
#       /scale + display -> --device=dri
#       /drive:media + \\tsclient\home -> --filesystem=home + the media mount roots
_FLATPAK_RUN_OPTS = (
    "--command=xfreerdp "
    "--share=network "
    "--socket=x11 --socket=wayland "
    "--socket=pulseaudio "
    "--socket=cups "
    "--device=dri "
    "--filesystem=home "
    "--filesystem=/run/media --filesystem=/media --filesystem=/mnt"
)
_FLATPAK_FREERDP_CMD = f"flatpak run {_FLATPAK_RUN_OPTS} com.freerdp.FreeRDP"


def _find_flatpak_freerdp() -> tuple[str, str] | None:
    """The Flatpak FreeRDP (``com.freerdp.FreeRDP``) if installed.

    Returns the full ``flatpak run`` invocation that forces the RAIL-capable
    ``xfreerdp`` binary and opens the sandbox holes winpodx's RDP flags need
    (clipboard / sound / printer / display / drive redirection / network) — so
    the Flatpak behaves like a native client, not a degraded full-desktop one.
    """
    try:
        result = subprocess.run(
            ["flatpak", "list", "--app", "--columns=application"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and "com.freerdp.FreeRDP" in result.stdout:
            return (_FLATPAK_FREERDP_CMD, "flatpak")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def find_freerdp(prefer: str = "auto") -> tuple[str, str] | None:
    """Locate a FreeRDP 3+ client, honouring a source preference.

    ``prefer``:
      * ``"auto"`` (default) — **prefer the Flatpak ``com.freerdp.FreeRDP``**,
        fall back to the native ``xfreerdp`` only when the Flatpak is absent.
        The Flatpak ships a self-contained FreeRDP 3+ (no host package skew);
        its earlier RAIL multi-display rough edges are handled by
        ``cfg.rdp.multimon = "span"`` (see ``build_rdp_command``).
      * ``"native"`` — force the native client first, Flatpak only as fallback
        (for hosts where the Flatpak sandbox is a problem).
      * ``"flatpak"`` — force the Flatpak (fall back to native only if the
        Flatpak isn't installed); set via ``cfg.rdp.freerdp_source``.

    Override per install via ``cfg.rdp.freerdp_source``. Returns ``(path_or_cmd,
    kind)`` or ``None``. Success is cached per-preference; a miss is not cached
    so a mid-session install is still picked up.
    """
    pref = prefer if prefer in ("auto", "native", "flatpak") else "auto"
    cached = _FREERDP_CACHE.get(pref)
    if cached is not None:
        return cached

    if pref == "native":
        # Forced native: try it first, fall back to the Flatpak if absent.
        found = _find_native_freerdp() or _find_flatpak_freerdp()
    else:
        # auto + flatpak: prefer the Flatpak client (self-contained FreeRDP 3+,
        # no host package skew), native only as fallback. The RAIL multi-display
        # rough edges that previously made native the safer default are handled
        # by cfg.rdp.multimon = "span" (see build_rdp_command), so the Flatpak
        # is viable as the preferred client again.
        found = _find_flatpak_freerdp() or _find_native_freerdp()

    if found is not None:
        _FREERDP_CACHE[pref] = found
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
    default_args: str | None = None,
    extra_args: str = "",
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
    found = find_freerdp(prefer=getattr(cfg.rdp, "freerdp_source", "auto"))
    if not found:
        raise RuntimeError("FreeRDP 3+ not found. Install xfreerdp3 or xfreerdp.")

    binary, variant = found

    # FreeRDP runs directly on the host. Rootless podman publishes the
    # container's 3389 to the host's 127.0.0.1:<rdp_port> via pasta /
    # slirp4netns, which is reachable from the host loopback without
    # entering the container's net namespace. The legacy `podman unshare
    # --rootless-netns` wrap put FreeRDP *inside* the container's net ns
    # where the host-side publish is invisible -- which broke the launch
    # on modern podman + pasta (Ubuntu/Kubuntu 26.04 default). See #214.
    cmd: list[str] = shlex.split(binary)

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
        "/dynamic-resolution",
    ]

    # If an App launches, remove default dynamic-resolution flag
    if (app_executable or launch_uri) and "/dynamic-resolution" in cmd:
        cmd.remove("/dynamic-resolution")

    # Multi-monitor RAIL: without a desktop big enough to cover every host
    # monitor, a RAIL window dragged onto a second monitor lands at coords
    # outside the (single-monitor) session desktop, so input/repaint desync
    # and clicks miss. "/span" sizes the session desktop to the bounding box
    # of all host monitors -- one wide rectangle, NO per-monitor
    # MonitorDefArray (which rdprrap can't handle, so "/multimon" kills input
    # entirely). Scoped to RAIL app launches; the full-desktop path keeps
    # /dynamic-resolution instead. cfg.rdp.multimon "off" disables it for
    # non-rectangular layouts.
    if app_executable or launch_uri:
        _multimon = getattr(cfg.rdp, "multimon", "span")
        if _multimon == "span":
            cmd.append("/span")
        elif _multimon == "multimon":
            cmd.append("/multimon")

    # Share a directory as \\tsclient\media so the guest's USB desktop
    # shortcut always resolves: the real removable-media base when mounted,
    # else an empty placeholder (so clicking USB with nothing plugged in
    # opens an empty folder instead of erroring "invalid address").
    cmd.append(f"/drive:media,{_media_redirect_base()}")

    cmd.append(f"/scale:{cfg.rdp.scale}")

    # Windows DPI scaling (0 = let Windows decide)
    if cfg.rdp.dpi > 0:
        cmd.append(f"/scale-desktop:{cfg.rdp.dpi}")

    # /p: exposes the password in /proc/pid/cmdline (same-uid-readable only);
    # /from-stdin:force is not viable under GUI launches (no controlling tty).
    password = _resolve_password(cfg)
    if password:
        cmd.append(f"/p:{password}")
        password = ""  # signal launch_app to skip stdin write

    # RemoteApp (RAIL) launch; requires fDisabledAllowList=1 set by install.bat.
    if launch_uri:
        # UWP/MSIX: launch via the hidden VBS wrapper rather than
        # `explorer.exe shell:AppsFolder\<AUMID>`. The legacy explorer.exe
        # path triggers a brief explorer RemoteApp window before the UWP
        # frame appears — that's the "PowerShell-like flash" users see on
        # Calculator / Settings / Terminal. wscript.exe is GUI-subsystem
        # (no console) and launch_uwp.vbs activates the AUMID via
        # IApplicationActivationManager directly, so the UWP frame is the
        # only window the RemoteApp client paints.
        #
        # The AUMID must be a bare package-family!app-id token — reject
        # anything that looks like a flag payload or embeds separators
        # FreeRDP treats specially ("," splits /app sub-args).
        aumid = launch_uri.strip()
        if not _is_valid_aumid(aumid):
            raise RuntimeError(f"Invalid UWP AUMID: {aumid!r}")
        wm_class = (wm_class_hint or "").strip().lower()
        if not wm_class or not _is_safe_wm_class(wm_class):
            wm_class = _uwp_fallback_wm_class(aumid)
        app_arg = (
            f"/app:program:wscript.exe,name:{wm_class},"
            f"cmd:C:\\Users\\Public\\winpodx\\launchers\\launch_uwp.vbs {aumid}"
        )
        cmd.append(app_arg)
        cmd.append(f"/wm-class:{wm_class}")
        cmd.append("+grab-keyboard")
    elif app_executable:
        from pathlib import PureWindowsPath

        stem = PureWindowsPath(app_executable).stem.lower()
        name_token = (wm_class_hint or "").strip().lower() or stem
        if not _is_safe_wm_class(name_token):
            name_token = stem
        # FreeRDP's RemoteApp syntax is incompatible between major
        # versions and we have to branch:
        #
        # - FreeRDP 3.x parses ``/app:`` as ``<key>:<value>,...`` —
        #   bare ``/app:C:\Path\app.exe`` is rejected with
        #   ``Unexpected keyword`` at the ``C:`` prefix (the parser
        #   reads ``C`` as an unknown sub-key). The COMBINED form
        #   ``/app:program:PATH,name:NAME,cmd:CMD`` is the only
        #   accepted shape.
        # - FreeRDP 2.11.x (still apt default on Ubuntu 22.04 LTS)
        #   parses the combined string as a literal program path and
        #   fails to launch — Windows shell handler then falls back
        #   to Microsoft Store for unknown app names (#158, reported
        #   by @poetman, verified with manual xfreerdp invocations).
        #   FreeRDP 2 only accepts SEPARATE flags ``/app:PATH``,
        #   ``/app-name:NAME``, ``/app-cmd:CMD``.
        #
        # ``freerdp_major_version()`` caches the probe so this branch
        # only spawns the version-check subprocess once per process.
        if freerdp_major_version() >= 3:
            # FreeRDP 3: combined sub-arg form. Comma in ``default_args``
            # would collide with FreeRDP's sub-arg separator, so it is
            # sanitised to spaces. ``cmd:`` accepts a UNC path (file
            # open) or a CLI string (Explorer ``shell:Desktop``).
            #
            # ``app_executable`` is interpolated into the same ``/app:``
            # arg, so a comma in the path would inject a spurious sub-key
            # (same hazard ``default_args`` is sanitised for below). Strip
            # commas to spaces here too before building the combined arg.
            program_token = app_executable.replace(",", " ")
            app_arg = f"/app:program:{program_token},name:{name_token}"
            if file_path:
                try:
                    unc_path = linux_to_unc(file_path)
                except ValueError as e:
                    raise RuntimeError(f"Cannot open file: {e}") from e
                app_arg += f",cmd:{unc_path}"
            elif default_args:
                sanitized = default_args.replace(",", " ")
                app_arg += f",cmd:{sanitized}"
            cmd.append(app_arg)
        else:
            # FreeRDP 2: separate flags. Commas inside ``/app-cmd:``
            # are safe because each flag is its own argv entry.
            cmd.append(f"/app:{app_executable}")
            cmd.append(f"/app-name:{name_token}")
            if file_path:
                try:
                    unc_path = linux_to_unc(file_path)
                except ValueError as e:
                    raise RuntimeError(f"Cannot open file: {e}") from e
                cmd.append(f"/app-cmd:{unc_path}")
            elif default_args:
                cmd.append(f"/app-cmd:{default_args}")
        cmd.append(f"/wm-class:{name_token}")
        cmd.append("+grab-keyboard")

    # TLS for all backends. install.bat forces SecurityLayer=2 on the
    # guest side; matching /sec:tls on the client avoids the NLA path
    # that would otherwise need credentials cached in CredSSP.
    cmd.append("/sec:tls")

    if cfg.rdp.ip in ("127.0.0.1", "localhost", "::1"):
        cmd.append("/cert:ignore")
    else:
        cmd.append("/cert:tofu")

    if cfg.rdp.extra_flags:
        cmd += _filter_extra_flags(cfg.rdp.extra_flags)

    # Per-launch override (CLI --extra-args / GUI per-launch). Appended AFTER
    # the global extra_flags so a per-launch flag wins over a global default
    # when FreeRDP ties on duplicate flags. Goes through the same allowlist
    # so callers can't smuggle unsafe flags via this path.
    if extra_args:
        cmd += _filter_extra_flags(extra_args)

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
        # ---- codec / graphics toggles (#126 diagnosis, 2026-05-06/07) ----
        # FreeRDP 3.x splits codec flags into BOOL (`+/-foo` toggles) vs
        # OPTIONAL/REQUIRED (`/foo:value` only). xiyeming's first test of
        # `--extra-args="-gfx-h264"` failed at FreeRDP's own cmdline
        # parser ("Unexpected keyword") because `gfx-h264` is OPTIONAL,
        # not BOOL — bare `+/-gfx-h264` is invalid syntax regardless of
        # whether the build is experimental. Same for `nsc`, `jpeg`,
        # `avc444`. The workaround xiyeming actually needs is
        # `/gfx:RFX` (force RemoteFX, skip H.264 negotiation entirely),
        # which already passes through the existing `/gfx` value-regex
        # in _SIMPLE_VALUE_FLAGS. Only the genuine BOOL toggles stay
        # in the bare allowlist.
        #
        # #380 (notnotno, FreeRDP 3.26): `progressive`, `thin-client`, and
        # `small-cache` are the SAME case as `gfx-h264` — they are `/gfx:`
        # sub-options, NOT bare `+/-` toggles. The earlier fix removed
        # `gfx-h264` but missed these siblings, so `+gfx-progressive` etc.
        # passed our allowlist only to be rejected by xfreerdp's own parser.
        # Removed; use `/gfx:progressive:on|off`, `/gfx:thin-client:on|off`,
        # `/gfx:small-cache:on|off` instead (the `/gfx` value-regex accepts
        # all three).
        # Visual / desktop toggles (already in default cmd; expose for override).
        "+wallpaper",
        "-wallpaper",
        "+themes",
        "-themes",
        # #380: `window-position` takes coordinates (`/window-position:<x>x<y>`),
        # it is not a bare toggle — moved to _SIMPLE_VALUE_FLAGS below.
        "+decorations",
        "-decorations",
        # Input grab / mouse-keyboard policy.
        "+grab-keyboard",
        "-grab-keyboard",
        "+grab-mouse",
        "-grab-mouse",
        "+mouse-relative",
        "-mouse-relative",
        # Connection robustness.
        "+async-update",
        "-async-update",
        "+async-channels",
        "-async-channels",
        "+auto-reconnect",
        "-auto-reconnect",
        # Cache toggles (off-by-default usually saves bandwidth at cost of CPU).
        "+bitmap-cache",
        "-bitmap-cache",
        "+offscreen-cache",
        "-offscreen-cache",
        "+glyph-cache",
        "-glyph-cache",
        # Multi-monitor / repaint experiments (RAIL window-move corruption,
        # 2026-05-30). Bare forms; the value forms (/multimon:force, /gdi:sw,
        # /smart-sizing:WxH, /monitors:0,1) live in _SIMPLE_VALUE_FLAGS below.
        # `/multimon` advertises the full host monitor layout to the session so
        # a RAIL window dragged between monitors stays inside a geometry the
        # guest knows; `/smart-sizing` rescales on the client to fight blur
        # across mixed-DPI monitors.
        "/multimon",
        "/span",
        "/smart-sizing",
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
    # Multi-monitor / repaint experiments (RAIL window-move corruption).
    "/multimon": re.compile(r"force"),  # /multimon:force
    "/gdi": re.compile(r"(sw|hw)"),  # software vs hardware GDI repaint
    "/monitors": re.compile(r"[0-9]{1,2}(,[0-9]{1,2}){0,7}"),  # /monitors:0,1
    "/smart-sizing": re.compile(r"[1-9][0-9]{1,4}x[1-9][0-9]{1,4}"),  # /smart-sizing:WxH
    "/window-position": re.compile(r"[0-9]{1,5}x[0-9]{1,5}"),  # /window-position:<x>x<y> (#380)
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
    """Wait for process exit and clean up the PID file."""
    proc = session.process
    if proc is None:
        return
    try:
        proc.wait()
    except (OSError, ValueError):
        pass
    finally:
        session.pid_file.unlink(missing_ok=True)


def _read_stderr_log(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        return f"(could not read stderr log {path}: {exc})"


def _raise_if_exited_immediately(session: RDPSession) -> None:
    """Surface FreeRDP clients that die immediately after a successful spawn."""
    import time

    proc = session.process
    if proc is None:
        return

    time.sleep(0.5)
    if proc.poll() is None:
        return

    stderr_content = _read_stderr_log(session.stderr_log)
    if not stderr_content:
        stderr_content = "(stderr log was empty)"
    raise RuntimeError(
        f"FreeRDP exited immediately with rc={proc.returncode}. Stderr:\n{stderr_content}"
    )


_SESSION_STATE_PS = (
    "if (Get-Process LogonUI -ErrorAction SilentlyContinue) { 'LOCKED' } "
    "elseif (Get-Process explorer -ErrorAction SilentlyContinue) { 'READY' } "
    "else { 'NOSHELL' }"
)


def _wait_session_interactive(cfg: Config, *, timeout: int = 20) -> bool:
    """Best-effort wait until the guest console is logged in + unlocked (#332).

    Polls the **agent only** (never FreeRDP -- a FreeRDP probe would itself
    flash a RAIL window). `LogonUI.exe` running means the logon / lock screen
    is up; `explorer.exe` with no LogonUI means the desktop is interactive.

    Returns True once READY is confirmed. Returns False (and the caller
    proceeds anyway -- this only *reduces* the race, never blocks a launch)
    when the agent is unreachable or the wait times out.
    """
    try:
        from winpodx.core.agent import AgentClient, AgentError
    except Exception:  # noqa: BLE001
        return False
    client = AgentClient(cfg)
    try:
        client.health()
    except Exception:  # noqa: BLE001 -- agent down: can't gate, let caller proceed
        return False
    import time as _time

    deadline = _time.monotonic() + max(1, timeout)
    state = ""
    while _time.monotonic() < deadline:
        try:
            state = (client.exec(_SESSION_STATE_PS, timeout=10).stdout or "").strip()
        except AgentError:
            return False
        if state == "READY":
            return True
        _time.sleep(2)
    log.warning(
        "guest session not confirmed interactive within %ds (state=%r); launching RemoteApp anyway",
        timeout,
        state,
    )
    return False


def launch_app(
    cfg: Config,
    app_executable: str | None = None,
    file_path: str | None = None,
    launch_uri: str | None = None,
    wm_class_hint: str | None = None,
    default_args: str | None = None,
    extra_args: str = "",
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
        default_args=default_args,
        extra_args=extra_args,
    )

    # See find_freerdp(): only xfreerdp has working RAIL, and it needs $DISPLAY.
    is_remoteapp = launch_uri is not None or app_executable is not None
    found = find_freerdp(prefer=getattr(cfg.rdp, "freerdp_source", "auto"))
    kind = found[1] if found else ""
    if is_remoteapp and kind == "xfreerdp" and not os.environ.get("DISPLAY"):
        raise RuntimeError(
            "RemoteApp requires xfreerdp, which needs an X display. "
            "On Wayland, enable XWayland (e.g. your compositor's built-in "
            "support, or xwayland-satellite for niri/river) and ensure "
            "$DISPLAY is set."
        )

    # #332: don't fire the RemoteApp (RAIL) connection while the guest
    # session is still at the logon / lock screen. dockur's autologon
    # session can briefly re-spawn (see rdprrap-activate.ps1), and if the
    # RAIL window is created during that transition FreeRDP paints the stale
    # logon framebuffer and never repaints the app -> "app launched but the
    # screen shows the login background", + `xf_Pointer: Invalid appWindow`
    # spam. Wait (best-effort, agent-only) until the desktop is interactive.
    if is_remoteapp and cfg.pod.backend in ("podman", "docker"):
        _wait_session_interactive(cfg)

    log.info("Launching RDP: %s", " ".join(cmd))

    # Acquire PID file lock before launching to prevent race conditions.
    #
    # Open WITHOUT O_TRUNC: a plain "w" open empties the file before the
    # real PID is written below, so a concurrent reader (process.py's
    # list_active_sessions / the idle monitor) could see an empty lock
    # file between flock and the write and unlink the live session's lock
    # as "corrupt". We hold the empty (or stale) contents until the PID is
    # known, then ftruncate+write it as the single mutation under flock.
    import fcntl

    session = RDPSession(app_name=app_name)
    session.pid_file.parent.mkdir(parents=True, exist_ok=True)
    lock_raw = os.open(session.pid_file, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_raw, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_raw)
        existing = _find_existing_session(app_name)
        if existing is not None:
            return existing
        raise RuntimeError(f"Could not acquire lock for {app_name}")

    # stderr=PIPE would SIGPIPE-kill the detached client once the CLI
    # parent exits; log to a file so the session outlives us.
    #
    # start_new_session=True puts the client in its own process group (PGID ==
    # this PID) and session. The FreeRDP client is actually a tree -- the
    # Flatpak path is `flatpak run` -> bwrap -> xfreerdp -- and a SIGTERM to
    # just the leader may not propagate through the nested sandbox to the real
    # xfreerdp. Owning the group lets kill_session() signal the whole tree at
    # once (os.killpg), so Terminate reliably ends the session. It also cleanly
    # detaches the client from the CLI's session (it must outlive us).
    err_log = session.stderr_log.open("wb")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err_log,
            start_new_session=True,
        )
        err_log.close()

        session.process = proc

        # Write the PID as the only mutation: truncate then write in one
        # shot so a reader never observes a partially written / empty file.
        pid_bytes = str(proc.pid).encode()
        os.ftruncate(lock_raw, 0)
        os.lseek(lock_raw, 0, os.SEEK_SET)
        os.write(lock_raw, pid_bytes)
        os.fsync(lock_raw)
    except Exception:
        session.pid_file.unlink(missing_ok=True)
        raise
    finally:
        os.close(lock_raw)

    t = threading.Thread(
        target=_reaper_thread,
        args=(session,),
        daemon=True,
    )
    t.start()

    _raise_if_exited_immediately(session)

    return session


def launch_desktop(cfg: Config, *, extra_args: str = "") -> RDPSession:
    """Launch a full Windows desktop RDP session (no RemoteApp)."""
    return launch_app(cfg, app_executable=None, file_path=None, extra_args=extra_args)


def linux_to_unc(path: str) -> str:
    """Convert a Linux file path to a Windows UNC path via tsclient."""
    p = Path(path).resolve()
    posix_str = str(p)
    if _INVALID_WIN_CHARS & set(posix_str):
        raise ValueError(f"Path contains characters invalid for Windows: {posix_str}")

    # Resolve $HOME (and the media base) too, not just the file (#418). On
    # Fedora Atomic / Silverblue / Kinoite, /home is a symlink to /var/home, so
    # `Path(path).resolve()` yields /var/home/me/... while a bare `Path.home()`
    # stays /home/me — the prefix check then wrongly reports the file as
    # "outside shared locations". Resolving both sides makes the comparison
    # symlink-agnostic (and is a no-op on normal layouts).
    home = Path.home().resolve()
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
        media_base = media_base.resolve()
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
