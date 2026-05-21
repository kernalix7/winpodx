# SPDX-License-Identifier: MIT
"""Dynamic discovery of installed Windows apps on the guest.

Invokes a guest-side PowerShell script (``scripts/windows/discover_apps.ps1``,
owned by platform-qa) which enumerates UWP/MSIX, Start Menu, and common
Win32 install locations and emits a JSON array on stdout. Results are
parsed into ``DiscoveredApp`` records and can be persisted under the
user data dir so they appear alongside bundled apps in the GUI and CLI.

Public API: ``scan(cfg)`` and ``persist(apps)`` are the explicit
entry points. Discovery is no longer auto-fired from
``provisioner.ensure_ready`` — a deliberate simplification per the
WINPODX redesign Step 3 (``feat/redesign``). Callers that want the
"discover on first install" UX (``install.sh``, the bundled
``winpodx app refresh`` post-install hook) call
``run_if_first_boot(cfg)`` explicitly.
"""

from __future__ import annotations

import base64
import binascii
import errno
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import zlib

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # Python 3.9, 3.10
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from winpodx.core.app import discovered_apps_dir as _app_discovered_apps_dir
from winpodx.core.config import Config
from winpodx.utils.paths import bundle_dir
from winpodx.utils.toml_writer import dumps as toml_dumps

log = logging.getLogger(__name__)

# Upper bound so a runaway guest enumerator can't fill the user's disk.
_MAX_APPS = 500
_MAX_ICON_BYTES = 1_048_576  # 1 MiB per icon
_MAX_NAME_LEN = 255
_MAX_PATH_LEN = 1024

# L1: hard cap on subprocess stdout so a hostile/misbehaving guest can't
# flood us into OOM. 64 MiB is ~two orders of magnitude above a normal
# discovery payload (~hundreds of KB) yet still bounded.
HARD_STDOUT_CAP = 64 * 1024 * 1024  # 64 MiB

# Bounds for PNG sanity validation (M1). Icons above these dimensions are
# refused even when the bytestream parses cleanly — a 10000x10000 icon is
# either a DoS vector or a mis-export from the guest.
_MAX_PNG_WIDTH = 1024
_MAX_PNG_HEIGHT = 1024

_VALID_SOURCES = frozenset({"uwp", "win32", "steam"})

# Matches AppInfo's _SAFE_NAME_RE contract so a discovered app loads cleanly.
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_NAME_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")

# v0.2.0: junk filter. Discovery scans Registry App Paths, Start Menu
# shortcuts, UWP packages, and shim dirs — that union surfaces a lot of
# non-apps: uninstallers, helpers, redistributables, inbox UWP plumbing
# whose display name failed to resolve. Drop these before they ever
# reach the host's persisted layout / GUI. Set
# ``WINPODX_DISCOVERY_INCLUDE_ALL=1`` in the environment to disable
# filtering for debugging.
_JUNK_NAME_PATTERNS = (
    re.compile(r"(?i)\buninstall(er)?\b"),
    re.compile(r"(?i)\bunins\b"),
    re.compile(r"(?i)\b(setup|installer|install)\b"),
    re.compile(r"(?i)\bupdater?\b"),
    re.compile(r"(?i)\bupgrade\b"),
    re.compile(r"(?i)\b(readme|release notes)\b"),
    re.compile(r"(?i)\b(license|eula)\b"),
    re.compile(r"(?i)\brepair\b"),
    re.compile(r"(?i)\bcrash"),
    re.compile(r"(?i)\bhelper\b"),
    re.compile(r"(?i)redist(ributable)?"),
    re.compile(r"(?i)bug ?report"),
    re.compile(r"(?i)report a (bug|problem)"),
    re.compile(r"(?i)send.+feedback"),
    re.compile(r"(?i)visual c\+\+"),
    re.compile(r"(?i)\bdotnet\b"),
    re.compile(r"(?i)\.net (framework|runtime|core)"),
)

# Specific Windows binaries that are dependencies / runtimes / inbox
# accessibility tools — never useful as integrated Linux apps.
_JUNK_EXE_BASENAMES = frozenset(
    {
        "unins000.exe",
        "unins001.exe",
        "uninstall.exe",
        "uninst.exe",
        "uninst000.exe",
        "setup.exe",
        "install.exe",
        "installer.exe",
        "vc_redist.x64.exe",
        "vc_redist.x86.exe",
        "vcredist_x64.exe",
        "vcredist_x86.exe",
        "dotnetfx.exe",
        "ndp48-x86-x64-allos-enu.exe",
        "crashpad_handler.exe",
        "crashreporter.exe",
        "msedge_proxy.exe",
        "msedgewebview2.exe",
        "applicationframehost.exe",
        "runtimebroker.exe",
        "narrator.exe",
        "magnify.exe",
        "osk.exe",
        "regedit.exe",
        "powershell_ise.exe",
    }
)


def _is_junk_entry(name: str, executable: str, source: str) -> bool:
    """Return True when a discovered entry should be hidden as junk.

    Filters by display-name patterns (uninstall / setup / redist / …),
    by executable basename (vcredist, crashpad_handler, …), by
    reverse-open shim path (winpodx's own Linux-app-into-Windows
    bridge — must not be re-imported as Windows apps), and — for UWP
    — by detection of unresolved PackageFamilyName fallbacks where
    the display name still looks like a dotted identifier
    (``Microsoft.AAD.BrokerPlugin``) rather than a real human label.
    """
    name_stripped = name.strip()
    if not name_stripped:
        return True

    # Reverse-open shims (#48 / v0.5.0) are Windows .exe entries
    # created by winpodx itself to surface Linux host apps in the
    # Windows "Open with…" menu — they are not Windows apps and must
    # not be re-imported as such. The check runs before the name /
    # basename filters so older guest-side scripts that didn't filter
    # them out still get caught here. The directory comes from
    # ``reverse_open.sync._GUEST_BIN_DIR`` (single source of truth)
    # via ``is_guest_shim_path`` — locally-imported to avoid a hot
    # module-level cycle.
    from winpodx.reverse_open.sync import is_guest_shim_path

    if is_guest_shim_path(executable):
        return True

    for pattern in _JUNK_NAME_PATTERNS:
        if pattern.search(name_stripped):
            return True

    # Windows paths use backslash separators, but discovery runs on Linux
    # so os.path.basename treats the whole string as one component. Split
    # on either separator manually.
    base = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if base in _JUNK_EXE_BASENAMES:
        return True

    # UWP entries whose DisplayName failed to resolve fall back to
    # PackageFamilyName (e.g. "Microsoft.AAD.BrokerPlugin"). Real
    # user-facing UWP apps almost always have spaces or non-dotted
    # names ("Calculator", "Microsoft To Do"). Allowlist the dotted
    # names we know are real apps so legitimate Microsoft.* packages
    # aren't dropped along with the junk plumbing.
    if source == "uwp" and "." in name_stripped and " " not in name_stripped:
        if name_stripped.lower() not in _UWP_DOTTED_ALLOWLIST:
            return True

    return False


# Known-good UWP package names whose DisplayName resolves to a dotted
# identifier rather than a human label (because the manifest's display
# name uses an ms-resource: indirection that PowerShell can't resolve in
# a non-interactive session). Comparison is lowercase.
_UWP_DOTTED_ALLOWLIST = frozenset(
    n.lower()
    for n in (
        "Microsoft.WindowsCalculator",
        "Microsoft.WindowsTerminal",
        "Microsoft.Paint",
        "Microsoft.ScreenSketch",
        "Microsoft.WindowsCamera",
        "Microsoft.WindowsAlarms",
        "Microsoft.WindowsMaps",
        "Microsoft.WindowsSoundRecorder",
        "Microsoft.WindowsNotepad",
        "Microsoft.MicrosoftStickyNotes",
        "Microsoft.MSPaint",
        "Microsoft.GetHelp",
        "Microsoft.YourPhone",
        "Microsoft.Todos",
        "windows.immersivecontrolpanel",
    )
)


class DiscoveryError(RuntimeError):
    """Raised when guest-side discovery cannot complete.

    Covers: unsupported backend (libvirt/manual), container runtime
    missing, script copy/exec failure, timeout, malformed JSON, or
    the pod not being running.

    The optional ``kind`` keyword attaches a machine-readable tag drawn
    from the canonical set so CLI/GUI callers can pattern-match without
    parsing the human-facing message. Canonical kinds:

    - ``pod_not_running`` — transport literally couldn't reach the pod
      (TCP refused, connect timeout, agent unavailable). Pod is down
      or starting.
    - ``session_disconnected`` — the FreeRDP RemoteApp session was
      terminated by the guest mid-call (LOGOFF_BY_USER, RPC_INITIATED_
      DISCONNECT, etc.). The pod IS running; what failed is the
      session winpodx tried to use. Common causes: multi-session
      mid-activation (TermService cycle kicks the call), the autologon
      user briefly logging off, or single-session-already-in-use when
      rdprrap isn't active. Retry usually works.
    - ``script_missing``
    - ``script_failed``
    - ``bad_json``
    - ``truncated``
    - ``timeout``
    - ``unsupported_backend``
    """

    def __init__(self, msg: str = "", *, kind: str = "") -> None:
        super().__init__(msg)
        self.kind = kind


@dataclass
class DiscoveredApp:
    name: str
    full_name: str
    executable: str
    description: str = ""  # one-line tooltip / .desktop Comment field
    args: str = ""
    source: str = "win32"  # uwp | win32 | steam
    wm_class_hint: str = ""
    launch_uri: str = ""  # UWP AUMID (shell:AppsFolder\<AUMID>) when source == "uwp"
    icon_bytes: bytes = field(default=b"", repr=False)
    # Filled by persist_discovered() after the on-disk layout is
    # materialised. Empty on freshly-parsed instances.
    slug: str = ""  # sanitized app name slug (same as `name` after persist)
    icon_path: str = ""  # absolute path to persisted PNG/SVG, or ""
    # Hybrid filter classification — set by persist_discovered() based on
    # the curated allowlist / noise denylist + any prior user override.
    hidden: bool = False
    essential: bool = False


# --- Hybrid filter: essentials allowlist + noise denylist -----------------
#
# Auto-discovery surfaces 40-50 entries on a stock Windows 11 install — many
# are system shims (LicenseManagerShellExt, WindowsPackageManagerServer,
# DesktopPackageMetadata, …) that no user wants in their app menu, and a few
# OS staples (File Explorer, Settings) don't appear at all because they
# aren't enumerated as Start Menu .lnk files. The hybrid filter:
#
#   1. ESSENTIAL_APPS — guarantees the staples are present (synthesizes a
#      stub when the scan missed them).
#   2. NOISE_PATTERNS — auto-flags known system shims as ``hidden=True`` so
#      the GUI grid filters them by default. Users can unhide via the GUI.
#   3. User overrides win — if a prior app.toml has explicit ``hidden=true``
#      / ``hidden=false``, that decision persists across the next scan.

# Curated essentials. Each entry's `name` becomes the slug and must satisfy
# _SAFE_NAME_RE (lowercase, hyphens). `launch_uri` for UWP triggers the
# shell:AppsFolder\<AUMID> launcher path; `executable` is the FreeRDP
# RemoteApp program (explorer.exe is the standard launcher for UWP shells).
ESSENTIAL_APPS: tuple[dict[str, str], ...] = (
    {
        "name": "file-explorer",
        "full_name": "File Explorer",
        "executable": "C:\\Windows\\explorer.exe",
        # explorer.exe launched without arguments tries to take over as
        # the user shell — RemoteApp shows nothing. shell:MyComputerFolder
        # opens the "This PC" view as a normal explorer window instead.
        "args": "shell:MyComputerFolder",
        "description": "Browse files and folders on the Windows guest",
        "wm_class_hint": "explorer",
        "source": "win32",
    },
    {
        "name": "calculator",
        "full_name": "Calculator",
        "executable": "explorer.exe",
        "launch_uri": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
        "description": "Calculator app from the Windows guest",
        "wm_class_hint": "calculator",
        "source": "uwp",
    },
    {
        "name": "settings",
        "full_name": "Settings",
        "executable": "explorer.exe",
        "launch_uri": (
            "windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel"
        ),
        "description": "Open the Windows guest's Settings panel",
        "wm_class_hint": "settings",
        "source": "uwp",
    },
)

# Slugs in this set never get auto-hidden, even if a NOISE_PATTERN matches.
# (No essentials should match a NOISE_PATTERN today, but keep the rule
# explicit so future denylist edits can't accidentally hide a staple.)
_ESSENTIAL_SLUGS = frozenset(e["name"] for e in ESSENTIAL_APPS)

# Slug regexes that get auto-hidden. Lowercase + hyphens because that's the
# slug shape (sanitized name). Patterns are intentionally narrow to avoid
# catching legitimate apps; a false positive can be reverted with the GUI's
# Show toggle, but a false negative (visible noise) is what users complain
# about, so we err toward narrow patterns and let users hide more if they
# want. Add new entries here when a noisy app surfaces in the wild.
import re as _re  # noqa: E402 — local alias keeps the patterns readable inline

NOISE_PATTERNS: tuple[_re.Pattern[str], ...] = tuple(
    _re.compile(p, _re.IGNORECASE)
    for p in (
        # System shims and COM/IPC servers — never user-facing.
        r"^license-?manager-?shell-?ext$",
        r".*shell-?ext$",
        r".*-com-server$",
        r".*-package-manager-server.*",
        r"^widgets-?platform-?runtime$",
        r"^desktop-?package-?metadata$",
        r"^microsoft-?store-?server$",
        r"^microsoft-?r-?contacts-?import-?tool$",
        # Internet Explorer is end-of-life and shouldn't be promoted.
        r"^internet-explorer$",
        r"^diagnostics-utility-for-internet-explorer$",
    )
)


def _matches_noise(slug: str) -> bool:
    """True when `slug` matches any NOISE_PATTERN. Whitelist always wins."""
    if slug in _ESSENTIAL_SLUGS:
        return False
    return any(p.match(slug) for p in NOISE_PATTERNS)


def _existing_hidden_override(app_dir: Path) -> bool | None:
    """Return the user's prior `hidden` override if app.toml exists.

    None means "no prior decision" — let the auto-classifier decide. True
    or False means the user explicitly set it via the GUI Hide/Show
    action and we must preserve their choice across rediscovery.
    """
    toml_path = app_dir / "app.toml"
    if not toml_path.exists():
        return None
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        return None
    if "hidden" not in data:
        return None
    val = data.get("hidden")
    return bool(val) if isinstance(val, bool) else None


def _merge_essentials(scanned: list[DiscoveredApp]) -> list[DiscoveredApp]:
    """Add ESSENTIAL_APPS stubs for any essential not already in `scanned`.

    Match is by slug — if discovery already produced (e.g.) `file-explorer`,
    the existing entry wins (it has the real icon bytes). Synthesized stubs
    have no icon; the GUI falls back to a default icon glyph for them.
    """
    seen_slugs = {a.name for a in scanned}
    out = list(scanned)
    for spec in ESSENTIAL_APPS:
        slug = spec["name"]
        if slug in seen_slugs:
            # Mark the existing entry as essential so it's flagged in
            # the GUI / can never be hidden by the noise denylist.
            for app in out:
                if app.name == slug:
                    app.essential = True
                    break
            continue
        out.append(
            DiscoveredApp(
                name=slug,
                full_name=spec.get("full_name", slug),
                executable=spec.get("executable", ""),
                description=spec.get("description", ""),
                args=spec.get("args", ""),
                source=spec.get("source", "win32"),
                wm_class_hint=spec.get("wm_class_hint", ""),
                launch_uri=spec.get("launch_uri", ""),
                essential=True,
            )
        )
    return out


def discovered_apps_dir() -> Path:
    """Path under which persisted discovered apps are stored.

    Thin alias over ``winpodx.core.app.discovered_apps_dir`` so callers
    of ``core.discovery`` don't need a second import. Kept separate
    from bundled and user-authored entries so a rediscovery run can
    safely clear/replace only discovered entries.
    """
    return _app_discovered_apps_dir()


def _ps_script_path() -> Path:
    """Locate ``scripts/windows/discover_apps.ps1`` via :func:`bundle_dir`."""
    return bundle_dir() / "scripts" / "windows" / "discover_apps.ps1"


def _runtime_for(backend: str) -> str:
    if backend == "podman":
        return "podman"
    if backend == "docker":
        return "docker"
    raise DiscoveryError(
        f"Discovery requires a container backend (podman/docker); got {backend!r}.",
        kind="unsupported_backend",
    )


def _wait_for_transport_ready(
    cfg: Config,
    *,
    max_wait_sec: int = 30,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """Block until at least one transport (agent /health or RDP port)
    answers, or ``max_wait_sec`` elapses. install.sh runs migrate
    immediately after wait-ready; if migrate's apply chain cycled
    TermService (multi-session activation) the agent's session may
    still be respawning by the time discovery fires. Without this
    gate, refresh hits a freshly-killed agent + a freshly-bound RDP
    listener and falls into a cascade of channel-failure errors that
    abort discovery on what is otherwise a perfectly-good pod.
    """
    import socket
    import time as _time

    from winpodx.core.transport import dispatch as _dispatch

    deadline = _time.monotonic() + max_wait_sec
    while _time.monotonic() < deadline:
        try:
            t = _dispatch(cfg)
        except Exception:  # noqa: BLE001 — dispatch is best-effort here
            t = None
        if t is not None and getattr(t, "name", None) == "agent":
            return  # agent transport is the preferred path; ready
        # Agent not up — RDP fallback is fine if the port answers.
        try:
            with socket.create_connection((cfg.rdp.ip, cfg.rdp.port), timeout=1.0):
                return
        except OSError:
            pass
        if progress_callback:
            try:
                progress_callback("Waiting for guest transport...")
            except Exception:  # noqa: BLE001 — progress is decorative
                pass
        _time.sleep(2)
    # Fall through: caller will hit the underlying transport error which
    # is more informative than anything we'd raise here.


def _looks_suspiciously_empty(apps: list[DiscoveredApp]) -> bool:
    """Heuristic for "we caught Windows mid-boot" — discovery returned
    an oddly small set with no UWP entries. Stock Win11 always ships
    Calculator / Settings / Terminal as UWP packages, so a UWP count
    of 0 is a reliable signal that AppXSvc was still warming up. Used
    as a retry gate, not a hard error — if the second pass returns
    the same shape, that's the real state and we accept it.
    """
    total = len(apps)
    if total == 0:
        return True
    # Most stock Win11 installs return 15+ entries even before user
    # apps are added. Below that is a strong signal of partial result.
    if total < 5:
        return True
    uwp = sum(1 for a in apps if getattr(a, "source", "") == "uwp")
    return uwp == 0


def _classify_channel_error(exc: Exception) -> str:
    """Map a transport / FreeRDP error message to a ``DiscoveryError`` ``kind``.

    Pre-this-helper the classifier was a one-liner: any error whose
    message contained ``"no result file"`` or ``"auth"`` was tagged
    ``pod_not_running``. That over-fired on session-side failures
    where the pod was very much running -- e.g., FreeRDP RemoteApp
    succeeded at the connection layer but the guest's RDP session
    terminated mid-call (``ERRINFO_LOGOFF_BY_USER`` /
    ``ERRINFO_RPC_INITIATED_DISCONNECT``, both of which produce a
    "no result file written" outer message because the script never
    finished writing). The GUI then surfaced a "Pod Not Running"
    dialog on a perfectly running pod (kernalix7 hit this 2026-05-03
    after a fresh install).

    The new logic separates three states:

    1. ``pod_not_running`` — connection refused / connect transport
       failed / agent unavailable. Pod is genuinely down or still
       booting.
    2. ``session_disconnected`` — FreeRDP RemoteApp connected but
       the session terminated (LOGOFF_BY_USER, RPC_INITIATED_DISCONNECT,
       common during multi-session mid-activation). Retry usually
       works.
    3. ``script_failed`` — anything else (script crash, malformed
       output, etc.).
    """
    msg = str(exc).lower()
    pod_down_signals = (
        "connection refused",
        "errconnect_connect_transport_failed",
        "errconnect_activation_timeout",
        "transport_read_layer",
        "connection reset",
        "transport failed",
        "unavailable",  # agent-transport flag for /health miss
    )
    if any(s in msg for s in pod_down_signals):
        return "pod_not_running"
    session_disconnect_signals = (
        "errinfo_logoff_by_user",
        "errinfo_rpc_initiated_disconnect",
        "errinfo_remote_by_user",
        "errinfo_logoff_by_admin",
        "0x0001000c",  # LOGOFF_BY_USER hex
        "0x00010001",  # RPC_INITIATED_DISCONNECT hex
    )
    if any(s in msg for s in session_disconnect_signals):
        return "session_disconnected"
    # Auth failure is its own thing -- pod's running but credentials don't
    # match. Caller can route this to the sync-password rescue path.
    if "auth" in msg or "logon_failure" in msg or "0xc000006d" in msg:
        return "pod_not_running"
    return "script_failed"


def discover_apps(
    cfg: Config,
    timeout: int = 180,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> list[DiscoveredApp]:
    """Run guest-side discovery and return the parsed apps.

    Copies ``discover_apps.ps1`` into the running Windows container and
    executes it via ``{runtime} exec``. The script is expected to print
    a JSON array of objects shaped as::

        [{"name","path","args","source","wm_class_hint",
          "launch_uri","icon_b64"}, ...]

    and may include ``"_truncated": true`` as its last element to
    indicate the guest clipped its own output.

    Default ``timeout`` bumped 120 → 180 to absorb the script's
    first-boot readiness gate (up to 60 s waiting for AppXSvc + Start
    Menu .lnks before enumeration starts).

    First-boot races (Sysprep just finished, AppX still deploying,
    Start Menu indexer mid-propagation) used to produce empty / partial
    results stochastically — kernalix7 reported (2026-05-02) menu
    populating one install and missing UWP entries the next on
    identical configs. Three layers now collapse the variance:

      1. Guest-side readiness gate (``discover_apps.ps1``) waits for
         AppXSvc Running + ProgramData Start Menu .lnk count > 0,
         requiring 3 consecutive stable 1 s samples before enumerating.
      2. Host-side transport readiness (this function, just below)
         waits for agent /health or RDP port to answer before invoking
         the script — covers the migrate-apply-chain race where
         TermService just cycled and the agent is mid-respawn.
      3. Host-side retry-on-empty (this function, after first run)
         retries once if the result is suspiciously empty (< 5 apps
         or 0 UWP — both impossible on a stock Win11 install).

    Raises:
        DiscoveryError: backend unsupported, runtime missing, pod not
            running, script copy/exec failure, timeout, or malformed
            output.
    """
    runtime = _runtime_for(cfg.pod.backend)

    if shutil.which(runtime) is None:
        raise DiscoveryError(
            f"{runtime!r} not found on PATH; install {cfg.pod.backend} first.",
            kind="pod_not_running",
        )

    script = _ps_script_path()
    if not script.exists():
        raise DiscoveryError(f"Discovery script not found: {script}", kind="script_missing")

    # v0.1.9.5: was on the broken `podman exec -i ... powershell -Command -`
    # path (kernalix7's machine showed rc=127 "powershell.exe not found in
    # $PATH" because podman exec only reaches the dockur Linux container,
    # not the Windows VM inside). Migrated to ``windows_exec.run_in_windows``
    # alongside the rest of the host->Windows command paths.
    try:
        script_body = script.read_text(encoding="utf-8")
    except OSError as e:
        raise DiscoveryError(
            f"Cannot read discovery script {script}: {e}", kind="script_missing"
        ) from e

    # Wait for at least one transport to answer before invoking the
    # script. Covers the install.sh race where migrate's apply chain
    # just cycled TermService (multi-session activation) and the
    # agent is mid-respawn — without this, discovery fires into a
    # freshly-killed agent and bails with channel-failure cascade.
    _wait_for_transport_ready(cfg, max_wait_sec=30, progress_callback=progress_callback)

    # Prefer the HTTP agent transport when it answers /health — the
    # discovery script regularly takes 15-25s and the legacy FreeRDP
    # RemoteApp channel hits a hard 30s wall (kernalix7 saw the timeout
    # on 2026-05-01 with a stock Win11 install). The agent's /exec has a
    # default 60s timeout and exposes the script's stdout directly via
    # JSON so we don't have to round-trip a result file. Falls back to
    # FreeRDP automatically if /health doesn't answer.
    from winpodx.core.transport import TransportError, dispatch
    from winpodx.core.windows_exec import WindowsExecError

    def _run_once() -> list[DiscoveredApp]:
        try:
            transport = dispatch(cfg)
        except Exception as e:  # noqa: BLE001 — never let dispatch take down the run
            log.debug("transport dispatch failed, falling back to FreeRDP: %s", e)
            transport = None

        if transport is not None and transport.name == "agent":
            if progress_callback:
                try:
                    progress_callback("Connecting to guest agent...")
                except Exception:  # noqa: BLE001 — progress is decorative
                    pass
            try:
                tresult = transport.exec(script_body, timeout=timeout, description="discover-apps")
            except TransportError as e:
                raise DiscoveryError(
                    f"Discovery channel failure: {e}", kind=_classify_channel_error(e)
                ) from e
            if tresult.rc != 0:
                raise DiscoveryError(
                    f"Discovery script failed (rc={tresult.rc}): {tresult.stderr.strip()}",
                    kind="script_failed",
                )
            return _parse_discovery_output(tresult.stdout)

        # Agent unreachable — fall back to FreeRDP RemoteApp. Slower (30s
        # cap) but always available once the pod is up and RDP works.
        #
        # v0.4.0 (post-rc1): WINPODX_REQUIRE_AGENT=1 (set by install.sh during
        # post-install discovery) skips FreeRDP fallback. Why: a fresh-boot
        # FreeRDP login from the host while install.bat is still running
        # KICKS the autologon User session (rdprrap multi-session isn't
        # patched yet, so single-session enforcement is in effect). install.bat
        # runs as a child of that session and dies mid-stage -- agent never
        # gets staged, setup.log never written, /health stays down forever.
        # kernalix7 hit this on every fresh-install smoke test 2026-05-02
        # through 2026-05-04. Gating refresh on agent-up means the first
        # install completes cleanly: install.bat finishes, agent comes up,
        # then a manual `winpodx app refresh` (or pending-resume on next
        # launch) populates the menu via the agent.
        import os

        if os.environ.get("WINPODX_REQUIRE_AGENT") == "1":
            raise DiscoveryError(
                "agent transport required (WINPODX_REQUIRE_AGENT=1) but "
                "guest agent isn't up yet; skipping FreeRDP fallback to "
                "avoid kicking install.bat's autologon session. "
                "Re-run `winpodx app refresh` once the agent comes up.",
                kind="agent_unavailable",
            )

        from winpodx.core.windows_exec import run_in_windows

        try:
            result = run_in_windows(
                cfg,
                script_body,
                description="discover-apps",
                timeout=timeout,
                progress_callback=progress_callback,
            )
        except WindowsExecError as e:
            raise DiscoveryError(
                f"Discovery channel failure: {e}", kind=_classify_channel_error(e)
            ) from e

        if result.rc != 0:
            raise DiscoveryError(
                f"Discovery script failed (rc={result.rc}): {result.stderr.strip()}",
                kind="script_failed",
            )

        return _parse_discovery_output(result.stdout)

    apps = _run_once()
    if _looks_suspiciously_empty(apps):
        # Caught Windows mid-boot. Wait briefly and rerun — by now AppX
        # deployment / Start Menu indexer should have settled. We only
        # retry once: if the second pass also looks empty, it's the
        # real state (e.g., a stripped-down image) and we accept it
        # rather than spin forever.
        log.info(
            "discovery looks suspiciously empty (%d total, %d uwp); retrying once",
            len(apps),
            sum(1 for a in apps if getattr(a, "source", "") == "uwp"),
        )
        if progress_callback:
            try:
                progress_callback("Initial scan looked partial; retrying...")
            except Exception:  # noqa: BLE001
                pass
        import time as _time

        _time.sleep(8)
        retry_apps = _run_once()
        # Pick whichever pass returned more — never regress.
        if len(retry_apps) > len(apps):
            return retry_apps
    return apps


def _run_bounded(
    cmd: list[str],
    *,
    timeout: int,
    runtime: str,
    stdin_bytes: bytes | None = None,
) -> tuple[bytes, bytes, int]:
    """Run ``cmd`` and return ``(stdout, stderr, returncode)`` with a hard cap.

    Spawns the process via ``Popen`` and drains stdout / stderr via
    ``os.read`` with an accumulated byte cap of ``HARD_STDOUT_CAP``
    (L1 audit). If the cap is exceeded the process is killed and a
    ``DiscoveryError(kind="truncated")`` is raised. If the process
    does not finish within ``timeout`` seconds it is killed and a
    ``DiscoveryError(kind="timeout")`` is raised.

    When ``stdin_bytes`` is provided the bytes are written to the
    child's stdin and the pipe is closed before draining begins
    (Bug A fix: ``powershell -Command -`` reads the script body from
    stdin so the host doesn't need to ``podman cp`` into the Windows
    VM's virtual disk).

    Returns raw bytes so the caller decodes exactly once with an
    error-replace codec — this avoids UTF-8 decode exceptions on
    malformed guest output.
    """
    popen_kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": False,
    }
    if stdin_bytes is not None:
        popen_kwargs["stdin"] = subprocess.PIPE

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603
    except FileNotFoundError as e:
        raise DiscoveryError(
            f"{runtime!r} binary vanished mid-run: {e}", kind="pod_not_running"
        ) from e

    if stdin_bytes is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_bytes)
        except (BrokenPipeError, OSError) as err:
            log.debug("discovery: stdin write failed (%s); continuing", err)
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_bytes = 0
    stderr_bytes = 0

    # Readers drain stdout/stderr in background threads so a full pipe
    # buffer can't deadlock the child. Each reader enforces the cap
    # independently and sets an event if tripped.
    cap_tripped = threading.Event()

    def _drain(fd: int, sink: list[bytes], counter_name: str) -> None:
        nonlocal stdout_bytes, stderr_bytes
        try:
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except OSError as err:
                    if err.errno == errno.EBADF:
                        return
                    raise
                if not chunk:
                    return
                sink.append(chunk)
                if counter_name == "stdout":
                    stdout_bytes += len(chunk)
                    if stdout_bytes > HARD_STDOUT_CAP:
                        cap_tripped.set()
                        return
                else:
                    stderr_bytes += len(chunk)
                    # stderr is also capped (symmetry + cheap guard).
                    if stderr_bytes > HARD_STDOUT_CAP:
                        cap_tripped.set()
                        return
        except Exception:
            # Reader threads must never propagate — surface via cap_tripped
            # + returncode on the main path.
            log.debug("discovery: reader thread crashed", exc_info=True)

    assert proc.stdout is not None and proc.stderr is not None
    t_out = threading.Thread(
        target=_drain, args=(proc.stdout.fileno(), stdout_chunks, "stdout"), daemon=True
    )
    t_err = threading.Thread(
        target=_drain, args=(proc.stderr.fileno(), stderr_chunks, "stderr"), daemon=True
    )
    t_out.start()
    t_err.start()

    deadline = time.monotonic() + max(1, int(timeout))
    killed_for_cap = False
    killed_for_timeout = False
    while True:
        if cap_tripped.is_set():
            killed_for_cap = True
            proc.kill()
            break
        if proc.poll() is not None:
            break
        if time.monotonic() >= deadline:
            killed_for_timeout = True
            proc.kill()
            break
        time.sleep(0.05)

    # Wait for process + drain threads to finish so we don't leak FDs.
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    t_out.join(timeout=2)
    t_err.join(timeout=2)

    if killed_for_cap:
        raise DiscoveryError(
            f"Discovery stdout exceeded {HARD_STDOUT_CAP} bytes; aborted.",
            kind="truncated",
        )
    if killed_for_timeout:
        raise DiscoveryError(f"Discovery timed out after {timeout}s on guest.", kind="timeout")

    return b"".join(stdout_chunks), b"".join(stderr_chunks), proc.returncode


def _parse_discovery_output(stdout: str) -> list[DiscoveredApp]:
    """Parse the JSON array emitted by ``discover_apps.ps1``."""
    text = stdout.strip()
    if not text:
        return []

    # PowerShell may prepend a UTF-8 BOM to Out-String/ConvertTo-Json output.
    if text.startswith("﻿"):
        text = text[1:]

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        snippet = text[:200].replace("\n", " ")
        raise DiscoveryError(
            f"Malformed discovery JSON: {e}; head={snippet!r}", kind="bad_json"
        ) from e

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise DiscoveryError(
            f"Discovery JSON must be an array, got {type(raw).__name__}.", kind="bad_json"
        )

    apps: list[DiscoveredApp] = []
    truncated = False
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        if entry.get("_truncated") is True:
            truncated = True
            continue
        parsed = _entry_to_discovered(entry)
        if parsed is not None:
            apps.append(parsed)
        if len(apps) >= _MAX_APPS:
            truncated = True
            break

    if truncated:
        log.warning(
            "Discovery output was truncated (>%d apps or guest-side clip); showing the first %d.",
            _MAX_APPS,
            len(apps),
        )
    return apps


def _entry_to_discovered(entry: dict[str, Any]) -> DiscoveredApp | None:
    """Validate and convert one JSON entry into a ``DiscoveredApp``."""
    raw_name = entry.get("name")
    path = entry.get("path")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    if not isinstance(path, str) or not path.strip():
        return None
    if len(raw_name) > _MAX_NAME_LEN or len(path) > _MAX_PATH_LEN:
        return None

    source = entry.get("source", "win32")
    if source not in _VALID_SOURCES:
        source = "win32"

    args = entry.get("args", "")
    if not isinstance(args, str) or len(args) > _MAX_PATH_LEN:
        args = ""

    description = entry.get("description", "")
    if not isinstance(description, str):
        description = ""
    # Bound the description so a hostile guest can't fill the .desktop
    # Comment field with megabytes of garbage.
    description = description.strip()[:512]

    wm_class_hint = entry.get("wm_class_hint", "")
    if not isinstance(wm_class_hint, str) or len(wm_class_hint) > _MAX_NAME_LEN:
        wm_class_hint = ""

    launch_uri = entry.get("launch_uri", "")
    if not isinstance(launch_uri, str) or len(launch_uri) > _MAX_PATH_LEN:
        launch_uri = ""

    # UWP entries must have a launch_uri (AUMID); otherwise they are unlaunchable.
    if source == "uwp" and not launch_uri:
        return None

    # v0.2.0: drop common Windows junk (uninstallers, helpers, redistributables,
    # unresolved UWP package fallbacks). Bypass with WINPODX_DISCOVERY_INCLUDE_ALL=1.
    if not os.environ.get("WINPODX_DISCOVERY_INCLUDE_ALL") and _is_junk_entry(
        raw_name, path, source
    ):
        return None

    icon_b64 = entry.get("icon_b64", "")
    icon_bytes = b""
    if isinstance(icon_b64, str) and icon_b64:
        try:
            icon_bytes = base64.b64decode(icon_b64, validate=True)
        except (binascii.Error, ValueError):
            icon_bytes = b""
        if len(icon_bytes) > _MAX_ICON_BYTES:
            icon_bytes = b""

    slug = _slugify_name(raw_name)
    if not slug:
        return None

    return DiscoveredApp(
        name=slug,
        full_name=raw_name.strip(),
        executable=path.strip(),
        description=description,
        args=args,
        source=source,
        wm_class_hint=wm_class_hint,
        launch_uri=launch_uri,
        icon_bytes=icon_bytes,
    )


def _slugify_name(raw: str) -> str:
    """Convert a display name into a safe AppInfo ``name`` slug.

    Lowercases, replaces runs of unsafe chars with ``-``, trims leading
    and trailing dashes and underscores, and bounds the length.
    """
    slug = _NAME_SANITIZE_RE.sub("-", raw.strip().lower()).strip("-_")
    if not slug:
        return ""
    if len(slug) > 64:
        slug = slug[:64].rstrip("-_")
    if not _SAFE_NAME_RE.match(slug):
        return ""
    return slug


def persist_discovered(
    apps: list[DiscoveredApp],
    target_dir: Path | None = None,
    replace: bool = True,
    *,
    add_essentials: bool = True,
) -> list[Path]:
    """Write discovered apps as ``<dir>/<name>/{app.toml, icon.*}`` entries.

    Args:
        apps: Output of ``discover_apps``.
        target_dir: Destination root. Defaults to ``discovered_apps_dir()``.
        replace: If True, removes any existing per-app subdir before
            writing. Bundled / user-authored entries are untouched —
            only the discovered tree is affected.

    Returns:
        List of ``app.toml`` paths written (skipped entries are
        omitted).
    """
    root = target_dir if target_dir is not None else discovered_apps_dir()
    root.mkdir(parents=True, exist_ok=True)

    # Self-heal: earlier winpodx builds (before this fix) could re-import
    # reverse-open shims as Windows apps on discovery, leaving polluted
    # entries under discovered/. Sweep them now so this run cleans up
    # after the bug — users don't need a manual `rm -rf` step.
    _purge_reverse_open_entries(root)

    # Hybrid filter step — guarantee essentials are present (synthesizing
    # stubs when the scan missed them) before we touch disk. Tests can
    # opt out via ``add_essentials=False`` to keep their fixtures
    # isolated from the curated list.
    if add_essentials:
        apps = _merge_essentials(apps)

    written: list[Path] = []
    seen: set[str] = set()
    for app in apps:
        if app.name in seen:
            continue
        if not _SAFE_NAME_RE.match(app.name):
            continue
        seen.add(app.name)

        app_dir = root / app.name

        # Read the user's prior hidden override BEFORE the rmtree below
        # nukes it; the override (None / True / False) decides what gets
        # stamped into the new app.toml so the user's choice is sticky
        # across rediscovery sweeps.
        prior_override = _existing_hidden_override(app_dir)

        if replace and app_dir.exists():
            _safe_rmtree(app_dir, root)
        app_dir.mkdir(parents=True, exist_ok=True)

        # Stamp the contract fields cli/gui callers rely on (I2). slug
        # mirrors `name` post-sanitisation; icon_path is filled below
        # only when a valid image is written.
        app.slug = app.name
        app.icon_path = ""

        # Resolve hidden state in priority order: explicit user override
        # (None means no override) > essentials (never auto-hidden) >
        # noise denylist (auto-hide). Result lands in app.toml so
        # AppInfo.hidden reflects it on next load.
        if prior_override is not None:
            app.hidden = prior_override
        elif app.essential:
            app.hidden = False
        else:
            app.hidden = _matches_noise(app.name)

        toml_path = app_dir / "app.toml"
        try:
            toml_path.write_text(_render_app_toml(app), encoding="utf-8")
        except OSError as e:
            log.warning("Could not write %s: %s", toml_path, e)
            continue

        if app.icon_bytes:
            icon_ext = _sniff_icon_ext(app.icon_bytes)
            icon_ok = True
            if icon_ext == "png" and not _validate_png_bytes(app.icon_bytes):
                # M1: malformed / oversized / CRC-broken PNG from a
                # compromised guest must not abort the run. Skip this
                # app's icon but keep the entry.
                log.warning(
                    "Rejecting malformed PNG icon for %s; writing entry without icon.", app.name
                )
                icon_ok = False
            if icon_ext and icon_ok:
                icon_target = app_dir / f"icon.{icon_ext}"
                try:
                    icon_target.write_bytes(app.icon_bytes)
                    app.icon_path = str(icon_target.resolve())
                except OSError as e:
                    log.warning("Could not write icon for %s: %s", app.name, e)

        written.append(toml_path)

    return written


def _render_app_toml(app: DiscoveredApp) -> str:
    """Render a ``DiscoveredApp`` into the AppInfo-loadable TOML shape."""
    data: dict[str, Any] = {
        "name": app.name,
        "full_name": app.full_name,
        "executable": app.executable,
        "categories": [],
        "mime_types": [],
    }
    if app.args:
        data["args"] = app.args
    if app.description:
        data["description"] = app.description
    if app.source:
        data["source"] = app.source
    if app.wm_class_hint:
        data["wm_class_hint"] = app.wm_class_hint
    if app.launch_uri:
        data["launch_uri"] = app.launch_uri
    # Always emit hidden / essential when set so AppInfo.load_app picks
    # them up. Keep the keys absent on the default-False side to keep
    # toml diffs minimal for the common case (visible, non-essential).
    if app.hidden:
        data["hidden"] = True
    if app.essential:
        data["essential"] = True
    return toml_dumps(data)


def _sniff_icon_ext(data: bytes) -> str:
    """Return ``'png'``, ``'svg'``, or ``''`` based on the icon header."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    head = data[:256].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        return "svg"
    return ""


def _validate_png_bytes(data: bytes) -> bool:
    """Return True only if ``data`` is a structurally valid PNG (M1).

    The 8-byte magic check in ``_sniff_icon_ext`` is sufficient to
    *classify* bytes as PNG but not to trust them as a PNG — a guest
    can trivially prepend the magic to arbitrary garbage. This helper
    runs a second-pass validation before the bytes reach disk /
    hicolor cache:

    1. If PySide6 is available, use ``QImage.loadFromData`` and reject
       anything that lands on ``QImage.isNull()``.
    2. Otherwise fall back to a stdlib chunk walker that verifies the
       PNG magic, requires ``IHDR`` as the first chunk with sane
       dimensions (``0 < w,h <= _MAX_PNG_WIDTH/_MAX_PNG_HEIGHT``),
       validates every chunk CRC, and requires the stream to terminate
       with ``IEND``.

    Never raises — on any exception we return ``False`` so the caller
    can simply skip the icon.
    """
    if not data or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False

    # v0.2.0.10: only use QImage on the main thread. From a Qt worker
    # thread (e.g. the GUI Refresh button's _DiscoveryWorker), creating
    # a QImage races with libgallium / Mesa state owned by the main
    # GUI thread and segfaults the whole process — observed on
    # openSUSE Tumbleweed Wayland 2026-04-27. The stdlib walker is
    # strict enough on its own (CRC + dimension caps + IEND
    # terminator), so off-main-thread callers get a slightly slower
    # but crash-free path.
    if threading.current_thread() is not threading.main_thread():
        return _validate_png_stdlib(data)

    # Fast path: PySide6 is already a GUI-team dependency, and QImage
    # is strict about truncated/corrupt streams.
    try:
        from PySide6.QtGui import QImage  # type: ignore[import-not-found]
    except ImportError:
        QImage = None  # type: ignore[assignment]
    except Exception:  # noqa: BLE001 — Qt init can fail headless; fall through.
        QImage = None  # type: ignore[assignment]

    if QImage is not None:
        try:
            img = QImage()
            if not img.loadFromData(data):
                return False
            if img.isNull():
                return False
            if img.width() <= 0 or img.height() <= 0:
                return False
            if img.width() > _MAX_PNG_WIDTH or img.height() > _MAX_PNG_HEIGHT:
                return False
            return True
        except Exception:  # noqa: BLE001 — headless Qt edge cases.
            log.debug("discovery: QImage validation crashed; falling back", exc_info=True)
            # Fall through to the stdlib walker.

    return _validate_png_stdlib(data)


def _validate_png_stdlib(data: bytes) -> bool:
    """Stdlib-only PNG chunk walker (fallback for ``_validate_png_bytes``)."""
    try:
        if len(data) < 8 + 12:  # magic + at least one chunk header+CRC
            return False
        pos = 8  # past the magic
        saw_ihdr = False
        saw_iend = False
        width = 0
        height = 0

        while pos < len(data):
            if pos + 8 > len(data):
                return False
            length = struct.unpack(">I", data[pos : pos + 4])[0]
            chunk_type = data[pos + 4 : pos + 8]
            if length > len(data):  # obviously bogus length prefix
                return False
            data_start = pos + 8
            data_end = data_start + length
            crc_end = data_end + 4
            if crc_end > len(data):
                return False

            declared_crc = struct.unpack(">I", data[data_end:crc_end])[0]
            actual_crc = zlib.crc32(data[pos + 4 : data_end]) & 0xFFFFFFFF
            if declared_crc != actual_crc:
                return False

            if not saw_ihdr:
                if chunk_type != b"IHDR" or length != 13:
                    return False
                width = struct.unpack(">I", data[data_start : data_start + 4])[0]
                height = struct.unpack(">I", data[data_start + 4 : data_start + 8])[0]
                if width == 0 or height == 0:
                    return False
                if width > _MAX_PNG_WIDTH or height > _MAX_PNG_HEIGHT:
                    return False
                saw_ihdr = True
            elif chunk_type == b"IEND":
                if length != 0:
                    return False
                saw_iend = True
                pos = crc_end
                break

            pos = crc_end

        return saw_ihdr and saw_iend
    except (struct.error, ValueError, IndexError):
        return False


def _purge_reverse_open_entries(root: Path) -> None:
    """Remove any existing per-app subdir whose app.toml points at a reverse-open shim.

    Heals the pre-fix state where the discovery scan returned the
    winpodx-installed reverse-open .exe shims as Windows apps. The
    canonical shim directory is owned by ``reverse_open.sync`` and
    queried via ``is_guest_shim_path`` so this sweep stays in sync
    with the install layout if it ever moves.

    Failure is non-fatal: a broken TOML or unreadable file is skipped,
    and the next discovery refresh retries.
    """
    from winpodx.reverse_open.sync import is_guest_shim_path

    if not root.exists():
        return
    for child in root.iterdir():
        if not child.is_dir():
            continue
        toml_path = child / "app.toml"
        if not toml_path.is_file():
            continue
        try:
            data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if is_guest_shim_path(str(data.get("executable", ""))):
            log.info("Purging stale reverse-open entry from discovered/: %s", child.name)
            _safe_rmtree(child, root)


def _safe_rmtree(path: Path, root: Path) -> None:
    """Remove ``path`` if and only if it sits inside ``root``.

    Guards against a crafted ``name`` resolving outside the discovered
    tree (e.g. via a pre-existing symlink at the target location).
    """
    try:
        resolved = path.resolve(strict=False)
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError):
        return
    if not resolved.is_relative_to(root_resolved):
        log.warning("Refusing to remove %s: escapes discovered root %s", resolved, root_resolved)
        return
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path, ignore_errors=True)


def scan(
    cfg: Config,
    timeout: int = 120,
    *,
    progress_callback: Callable[[str], None] | None = None,
) -> list[DiscoveredApp]:
    """Run guest-side discovery and return the parsed apps.

    Thin wrapper over ``discover_apps`` so callers (CLI ``app refresh``,
    GUI Refresh button, post-install hook) speak the redesigned public
    API name. The underlying enumeration, junk filtering, and bounds
    checks are unchanged from ``discover_apps``.
    """
    return discover_apps(cfg, timeout=timeout, progress_callback=progress_callback)


def persist(
    apps: list[DiscoveredApp],
    target_dir: Path | None = None,
    *,
    replace: bool = True,
) -> list[Path]:
    """Persist discovered apps under ``~/.local/share/winpodx/apps/discovered/``.

    Thin wrapper over ``persist_discovered``. Returns the list of
    written ``app.toml`` paths.
    """
    return persist_discovered(apps, target_dir=target_dir, replace=replace)


def run_if_first_boot(cfg: Config) -> None:
    """Run discovery once when the persisted tree is empty (first boot).

    Moved out of ``provisioner.ensure_ready`` (Step 3 of the redesign):
    discovery is now explicit-only. Callers wanting the legacy "auto-
    populate on first install" behaviour invoke this once at install
    time (``install.sh`` -> ``winpodx app refresh``). ``ensure_ready``
    no longer fires this path on every probe of the menu state.

    Failure is non-fatal: a busy or still-booting guest skips the run
    and the user can re-trigger discovery via the GUI Refresh button or
    ``winpodx app refresh``.
    """
    try:
        discovered_dir = discovered_apps_dir()
        if discovered_dir.exists() and any(discovered_dir.iterdir()):
            return  # already populated; user-triggered refresh stays in their hands.

        log.info("First boot detected; auto-running discovery to populate the app menu...")
        # v0.2.0.3: discovery uses the FreeRDP RemoteApp channel; on
        # first pod boot Windows VM may still be initialising inside
        # QEMU even though the RDP TCP port is open. Skip the run if
        # the guest is not yet activation-responsive.
        from winpodx.core.provisioner import wait_for_windows_responsive

        if not wait_for_windows_responsive(cfg, timeout=180):
            log.info("Windows guest still booting; deferring discovery to a later run.")
            return
        apps = scan(cfg)
        persist(apps)
        log.info("First-boot discovery wrote %d app(s) to %s", len(apps), discovered_dir)
    except Exception as e:  # noqa: BLE001
        log.warning(
            "First-boot discovery failed (non-fatal — run `winpodx app refresh` to retry): %s",
            e,
        )
