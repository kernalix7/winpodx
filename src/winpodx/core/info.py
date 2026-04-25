"""Aggregate system/display/deps/pod/config snapshot for `winpodx info` + GUI Info page.

The CLI handler and the Qt main_window Info page both consume the same
``gather_info(cfg)`` output so the two surfaces stay in sync without
duplicating subprocess probes.

Every external probe is hard-bounded so a sick pod can't make this
function block longer than ~10s total on the slow path.
"""

from __future__ import annotations

import logging
import platform
import subprocess
from pathlib import Path
from typing import Any

from winpodx import __version__
from winpodx.core.config import Config, check_session_budget
from winpodx.core.pod import PodState, check_rdp_port, pod_status

log = logging.getLogger(__name__)

# Probe timeout budget: each network/subprocess call is bounded so the
# whole gather completes in a finite time even when the pod is half-dead.
_PORT_PROBE_TIMEOUT = 2.0
_INSPECT_TIMEOUT = 5.0


def _read_text_file(path: Path, *, max_bytes: int = 4096) -> str | None:
    """Best-effort small-text read; returns None on any failure."""
    try:
        with path.open("rb") as fh:
            data = fh.read(max_bytes + 1)
    except (FileNotFoundError, OSError):
        return None
    if len(data) > max_bytes:
        return None
    try:
        return data.decode("utf-8", errors="replace").strip()
    except UnicodeDecodeError:
        return None


def _bundled_oem_version() -> str:
    """Read the OEM bundle version marker shipped with this winpodx install.

    Searches the same locations as `compose._find_oem_dir`.
    """
    candidates = [
        Path(__file__).parent.parent.parent.parent / "config" / "oem" / "oem_version.txt",
        Path.home() / ".local" / "bin" / "winpodx-app" / "config" / "oem" / "oem_version.txt",
    ]
    for path in candidates:
        text = _read_text_file(path, max_bytes=64)
        if text:
            return text
    return "(unknown)"


def _bundled_rdprrap_version() -> str:
    """Read the bundled rdprrap version pin file."""
    candidates = [
        Path(__file__).parent.parent.parent.parent / "config" / "oem" / "rdprrap_version.txt",
        Path.home() / ".local" / "bin" / "winpodx-app" / "config" / "oem" / "rdprrap_version.txt",
    ]
    for path in candidates:
        text = _read_text_file(path, max_bytes=128)
        if text:
            # Pin file may have version on first line + sha256 on second; keep just the version.
            return text.splitlines()[0].strip()
    return "(unknown)"


def _read_os_release() -> dict[str, str]:
    """Parse /etc/os-release; returns empty dict on failure."""
    text = _read_text_file(Path("/etc/os-release"), max_bytes=8192)
    if not text:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, _, raw = line.partition("=")
        val = raw.strip().strip('"').strip("'")
        out[key.strip()] = val
    return out


def _system_section() -> dict[str, str]:
    osr = _read_os_release()
    distro_id = osr.get("ID", "")
    distro_ver = osr.get("VERSION", osr.get("VERSION_ID", ""))
    distro = f"{distro_id} {distro_ver}".strip() or "(unknown)"
    return {
        "winpodx": __version__,
        "oem_bundle": _bundled_oem_version(),
        "rdprrap": _bundled_rdprrap_version(),
        "distro": distro,
        "kernel": platform.release() or "(unknown)",
    }


def _display_section() -> dict[str, str]:
    from winpodx.display.detector import display_info
    from winpodx.display.scaling import detect_raw_scale, detect_scale_factor

    info = display_info()
    return {
        "session_type": info.get("session_type", "(unknown)"),
        "desktop_environment": info.get("desktop_environment", "(unknown)"),
        "wayland_freerdp": str(info.get("wayland_freerdp", False)),
        "raw_scale": f"{detect_raw_scale():.2f}",
        "rdp_scale": f"{detect_scale_factor()}%",
    }


def _dependencies_section() -> dict[str, dict[str, str]]:
    from winpodx.utils.deps import check_all

    out: dict[str, dict[str, str]] = {}
    for name, dep in check_all().items():
        out[name] = {
            "found": "true" if dep.found else "false",
            "path": dep.path or "",
        }
    return out


def _container_uptime(cfg: Config) -> str:
    """Best-effort `podman inspect` for container start time. Returns '' on any failure."""
    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return ""
    runtime = "podman" if backend == "podman" else "docker"
    cmd = [
        runtime,
        "inspect",
        "--format",
        "{{.State.StartedAt}}",
        cfg.pod.container_name,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_INSPECT_TIMEOUT)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _active_session_count() -> int:
    """Count winpodx-tracked .cproc sessions; 0 on any failure."""
    try:
        from winpodx.core.process import list_active_sessions

        return len(list_active_sessions())
    except Exception as e:  # noqa: BLE001 — defensive; never crash info gather
        log.debug("active session count failed: %s", e)
        return 0


def _pod_section(cfg: Config) -> dict[str, Any]:
    try:
        status = pod_status(cfg)
        state = status.state.value if isinstance(status.state, PodState) else str(status.state)
    except Exception as e:  # noqa: BLE001
        log.debug("pod_status failed: %s", e)
        state = "unknown"

    rdp_ok = check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=_PORT_PROBE_TIMEOUT)
    vnc_ok = check_rdp_port(cfg.rdp.ip, cfg.pod.vnc_port, timeout=_PORT_PROBE_TIMEOUT)

    return {
        "state": state,
        "uptime": _container_uptime(cfg),
        "rdp_port": cfg.rdp.port,
        "rdp_reachable": rdp_ok,
        "vnc_port": cfg.pod.vnc_port,
        "vnc_reachable": vnc_ok,
        "active_sessions": _active_session_count(),
    }


def _config_section(cfg: Config) -> dict[str, Any]:
    return {
        "path": str(Config.path()),
        "backend": cfg.pod.backend,
        "ip": cfg.rdp.ip,
        "port": cfg.rdp.port,
        "user": cfg.rdp.user,
        "scale": cfg.rdp.scale,
        "idle_timeout": cfg.pod.idle_timeout,
        "max_sessions": cfg.pod.max_sessions,
        "ram_gb": cfg.pod.ram_gb,
        "budget_warning": check_session_budget(cfg) or "",
    }


def gather_info(cfg: Config) -> dict[str, Any]:
    """Return a 5-section snapshot consumed by both the CLI and GUI Info surfaces.

    Sections: ``system``, ``display``, ``dependencies``, ``pod``, ``config``.
    Total wall time bounded by per-probe timeouts; on a healthy pod this
    completes in < 1 s, on a sick pod < 10 s.
    """
    return {
        "system": _system_section(),
        "display": _display_section(),
        "dependencies": _dependencies_section(),
        "pod": _pod_section(cfg),
        "config": _config_section(cfg),
    }
