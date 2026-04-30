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
import sys
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from winpodx.core.app import discovered_apps_dir as _app_discovered_apps_dir
from winpodx.core.config import Config
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
    by executable basename (vcredist, crashpad_handler, …), and — for
    UWP — by detection of unresolved PackageFamilyName fallbacks where
    the display name still looks like a dotted identifier
    (``Microsoft.AAD.BrokerPlugin``) rather than a real human label.
    """
    name_stripped = name.strip()
    if not name_stripped:
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
    # names ("Calculator", "Microsoft To Do").
    if source == "uwp" and "." in name_stripped and " " not in name_stripped:
        return True

    return False


class DiscoveryError(RuntimeError):
    """Raised when guest-side discovery cannot complete.

    Covers: unsupported backend (libvirt/manual), container runtime
    missing, script copy/exec failure, timeout, malformed JSON, or
    the pod not being running.

    The optional ``kind`` keyword attaches a machine-readable tag drawn
    from the canonical set so CLI/GUI callers can pattern-match without
    parsing the human-facing message. Canonical kinds:

    - ``pod_not_running``
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
    args: str = ""
    source: str = "win32"  # uwp | win32 | steam
    wm_class_hint: str = ""
    launch_uri: str = ""  # UWP AUMID (shell:AppsFolder\<AUMID>) when source == "uwp"
    icon_bytes: bytes = field(default=b"", repr=False)
    # Filled by persist_discovered() after the on-disk layout is
    # materialised. Empty on freshly-parsed instances.
    slug: str = ""  # sanitized app name slug (same as `name` after persist)
    icon_path: str = ""  # absolute path to persisted PNG/SVG, or ""


def discovered_apps_dir() -> Path:
    """Path under which persisted discovered apps are stored.

    Thin alias over ``winpodx.core.app.discovered_apps_dir`` so callers
    of ``core.discovery`` don't need a second import. Kept separate
    from bundled and user-authored entries so a rediscovery run can
    safely clear/replace only discovered entries.
    """
    return _app_discovered_apps_dir()


def _ps_script_path() -> Path:
    """Locate ``scripts/windows/discover_apps.ps1``.

    Checks repo layout first, then wheel / user install prefixes. Uses
    the same search order as ``winpodx.core.app.bundled_apps_dir`` so
    behaviour is consistent across install modes.
    """
    # __file__ is at <root>/src/winpodx/core/discovery/__init__.py — five
    # `.parent` hops reach <root>, where `scripts/windows/` lives. The previous
    # four-hop version landed at <root>/src/, producing "<root>/src/scripts/..."
    # which never exists in any layout (repo, wheel, or .local/bin install).
    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent.parent
        / "scripts"
        / "windows"
        / "discover_apps.ps1",
        Path(sys.prefix) / "share" / "winpodx" / "scripts" / "windows" / "discover_apps.ps1",
        Path.home() / ".local" / "share" / "winpodx" / "scripts" / "windows" / "discover_apps.ps1",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _runtime_for(backend: str) -> str:
    if backend == "podman":
        return "podman"
    if backend == "docker":
        return "docker"
    raise DiscoveryError(
        f"Discovery requires a container backend (podman/docker); got {backend!r}.",
        kind="unsupported_backend",
    )


def discover_apps(
    cfg: Config,
    timeout: int = 120,
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

    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(
            cfg,
            script_body,
            description="discover-apps",
            timeout=timeout,
            progress_callback=progress_callback,
        )
    except WindowsExecError as e:
        msg = str(e).lower()
        # FreeRDP failed to connect at the channel level — most often
        # because the pod is stopped (no RDP listener) or the auth
        # password drifted from cfg. Surface as pod_not_running so cli
        # routes to exit code 2 + the helpful "run `winpodx pod start
        # --wait` first" hint.
        kind = "pod_not_running" if "no result file" in msg or "auth" in msg else "script_failed"
        raise DiscoveryError(f"Discovery channel failure: {e}", kind=kind) from e

    if result.rc != 0:
        raise DiscoveryError(
            f"Discovery script failed (rc={result.rc}): {result.stderr.strip()}",
            kind="script_failed",
        )

    return _parse_discovery_output(result.stdout)


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

    written: list[Path] = []
    seen: set[str] = set()
    for app in apps:
        if app.name in seen:
            continue
        if not _SAFE_NAME_RE.match(app.name):
            continue
        seen.add(app.name)

        app_dir = root / app.name
        if replace and app_dir.exists():
            _safe_rmtree(app_dir, root)
        app_dir.mkdir(parents=True, exist_ok=True)

        # Stamp the contract fields cli/gui callers rely on (I2). slug
        # mirrors `name` post-sanitisation; icon_path is filled below
        # only when a valid image is written.
        app.slug = app.name
        app.icon_path = ""

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
    if app.source:
        data["source"] = app.source
    if app.wm_class_hint:
        data["wm_class_hint"] = app.wm_class_hint
    if app.launch_uri:
        data["launch_uri"] = app.launch_uri
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
