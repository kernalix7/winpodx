"""Dynamic discovery of installed Windows apps on the guest.

Invokes a guest-side PowerShell script (``scripts/windows/discover_apps.ps1``,
owned by platform-qa) which enumerates UWP/MSIX, Start Menu, and common
Win32 install locations and emits a JSON array on stdout. Results are
parsed into ``DiscoveredApp`` records and can be persisted under the
user data dir so they appear alongside bundled apps in the GUI and CLI.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import shutil
import subprocess
import sys
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

_VALID_SOURCES = frozenset({"uwp", "win32", "steam"})

# Matches AppInfo's _SAFE_NAME_RE contract so a discovered app loads cleanly.
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_NAME_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


class DiscoveryError(RuntimeError):
    """Raised when guest-side discovery cannot complete.

    Covers: unsupported backend (libvirt/manual), container runtime
    missing, script copy/exec failure, timeout, malformed JSON, or
    the pod not being running.
    """


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
    candidates = [
        Path(__file__).resolve().parent.parent.parent.parent
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
        f"Discovery requires a container backend (podman/docker); got {backend!r}."
    )


def discover_apps(cfg: Config, timeout: int = 120) -> list[DiscoveredApp]:
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
        raise DiscoveryError(f"{runtime!r} not found on PATH; install {cfg.pod.backend} first.")

    script = _ps_script_path()
    if not script.exists():
        raise DiscoveryError(f"Discovery script not found: {script}")

    container = cfg.pod.container_name
    guest_path = "C:/winpodx-discover.ps1"

    try:
        subprocess.run(  # noqa: S603
            [runtime, "cp", str(script), f"{container}:{guest_path}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as e:
        raise DiscoveryError(f"{runtime!r} binary vanished: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise DiscoveryError("Timed out copying discovery script to guest.") from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise DiscoveryError(
            f"Failed to copy discovery script (is the pod running?): {stderr}"
        ) from e

    try:
        result = subprocess.run(  # noqa: S603
            [
                runtime,
                "exec",
                container,
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                guest_path.replace("/", "\\"),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise DiscoveryError(f"{runtime!r} binary vanished mid-run: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise DiscoveryError(f"Discovery timed out after {timeout}s on guest.") from e

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise DiscoveryError(f"Discovery script failed (rc={result.returncode}): {stderr}")

    return _parse_discovery_output(result.stdout)


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
        raise DiscoveryError(f"Malformed discovery JSON: {e}; head={snippet!r}") from e

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise DiscoveryError(f"Discovery JSON must be an array, got {type(raw).__name__}.")

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

        toml_path = app_dir / "app.toml"
        try:
            toml_path.write_text(_render_app_toml(app), encoding="utf-8")
        except OSError as e:
            log.warning("Could not write %s: %s", toml_path, e)
            continue

        if app.icon_bytes:
            icon_ext = _sniff_icon_ext(app.icon_bytes)
            if icon_ext:
                try:
                    (app_dir / f"icon.{icon_ext}").write_bytes(app.icon_bytes)
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
