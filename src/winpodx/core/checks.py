"""Live health probes — `winpodx check` + GUI status panel.

Each probe runs in bounded time and returns a Probe(name, status, detail,
duration_ms). Status is one of: ``ok`` / ``warn`` / ``fail`` / ``skip``.
Probes never raise — exceptions are caught and turned into ``fail`` so
``run_all`` always returns a complete report even when one probe blows up.

Distinct from ``core.info.gather_info`` (static snapshot for `winpodx info`):
checks is the fast/repeatable health view that the GUI polls and `winpodx
check` summarizes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Literal

from winpodx.core.config import Config

log = logging.getLogger(__name__)

ProbeStatus = Literal["ok", "warn", "fail", "skip"]


@dataclass(frozen=True)
class Probe:
    name: str
    status: ProbeStatus
    detail: str
    duration_ms: int

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"


def _timed(fn: Callable[[], tuple[ProbeStatus, str]]) -> tuple[ProbeStatus, str, int]:
    t0 = time.monotonic()
    try:
        status, detail = fn()
    except Exception as e:  # noqa: BLE001 — probes must not raise
        log.debug("probe raised", exc_info=True)
        status, detail = "fail", f"unexpected error: {type(e).__name__}: {e}"
    return status, detail, int((time.monotonic() - t0) * 1000)


def probe_pod_running(cfg: Config) -> Probe:
    def _run() -> tuple[ProbeStatus, str]:
        from winpodx.core.pod import PodState, pod_status

        s = pod_status(cfg)
        if s.state == PodState.RUNNING:
            return "ok", f"running (ip={s.ip or 'n/a'})"
        if s.state == PodState.STARTING:
            return "warn", "starting"
        return "fail", f"state={s.state.value}"

    status, detail, ms = _timed(_run)
    return Probe("pod_running", status, detail, ms)


def probe_rdp_port(cfg: Config) -> Probe:
    def _run() -> tuple[ProbeStatus, str]:
        from winpodx.core.pod import check_rdp_port

        ok = check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=2.0)
        target = f"{cfg.rdp.ip}:{cfg.rdp.port}"
        return ("ok", f"{target} reachable") if ok else ("fail", f"{target} not reachable")

    status, detail, ms = _timed(_run)
    return Probe("rdp_port", status, detail, ms)


def probe_agent_health(cfg: Config) -> Probe:
    def _run() -> tuple[ProbeStatus, str]:
        from winpodx.core.agent import AgentClient, AgentError

        client = AgentClient(cfg)
        try:
            payload = client.health()
        except AgentError as e:
            return "fail", str(e)
        version = payload.get("version", "?")
        return "ok", f"version={version}"

    status, detail, ms = _timed(_run)
    return Probe("agent_health", status, detail, ms)


def probe_oem_version(cfg: Config) -> Probe:
    def _run() -> tuple[ProbeStatus, str]:
        from winpodx.core.info import _bundled_oem_version

        bundled = _bundled_oem_version()
        if bundled == "(unknown)":
            return "warn", "bundled OEM version marker missing"
        return "ok", f"bundle={bundled}"

    status, detail, ms = _timed(_run)
    return Probe("oem_version", status, detail, ms)


def probe_password_age(cfg: Config) -> Probe:
    def _run() -> tuple[ProbeStatus, str]:
        if cfg.rdp.password_max_age <= 0:
            return "skip", "rotation disabled"
        if not cfg.rdp.password_updated:
            return "warn", "no password_updated timestamp"
        try:
            updated = datetime.fromisoformat(cfg.rdp.password_updated)
        except (ValueError, TypeError) as e:
            return "warn", f"unparseable timestamp: {e}"
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - updated).total_seconds() / 86400
        days_left = cfg.rdp.password_max_age - int(age_days)
        if days_left < 0:
            return "warn", f"overdue by {-days_left}d (max_age={cfg.rdp.password_max_age}d)"
        return "ok", f"{days_left}d remaining (max_age={cfg.rdp.password_max_age}d)"

    status, detail, ms = _timed(_run)
    return Probe("password_age", status, detail, ms)


def probe_apps_discovered(_cfg: Config | None = None) -> Probe:
    def _run() -> tuple[ProbeStatus, str]:
        from winpodx.core.discovery import discovered_apps_dir

        d = discovered_apps_dir()
        if not d.exists():
            return "warn", "no apps directory yet — run `winpodx app refresh`"
        # Apps live as <slug>/app.toml subdirs (not flat JSON files), so count
        # directories that contain an app.toml manifest rather than every entry.
        count = sum(1 for sub in d.iterdir() if sub.is_dir() and (sub / "app.toml").is_file())
        if count == 0:
            return "warn", "0 apps — run `winpodx app refresh`"
        return "ok", f"{count} app(s) in {d}"

    status, detail, ms = _timed(_run)
    return Probe("apps_discovered", status, detail, ms)


def probe_disk_free(_cfg: Config) -> Probe:
    def _run() -> tuple[ProbeStatus, str]:
        import shutil

        target = Path.home() / ".config" / "winpodx"
        target = target if target.exists() else Path.home()
        try:
            usage = shutil.disk_usage(target)
        except OSError as e:
            return "fail", f"disk_usage({target}) failed: {e}"
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        if free_gb < 2:
            return "fail", f"{free_gb:.1f}/{total_gb:.0f} GiB free"
        if free_gb < 10:
            return "warn", f"{free_gb:.1f}/{total_gb:.0f} GiB free"
        return "ok", f"{free_gb:.1f}/{total_gb:.0f} GiB free"

    status, detail, ms = _timed(_run)
    return Probe("disk_free", status, detail, ms)


PROBES: tuple[Callable[[Config], Probe], ...] = (
    probe_pod_running,
    probe_rdp_port,
    probe_agent_health,
    probe_oem_version,
    probe_password_age,
    probe_apps_discovered,
    probe_disk_free,
)


def run_all(cfg: Config) -> list[Probe]:
    """Run every probe and return results in deterministic order."""
    return [p(cfg) for p in PROBES]


def overall(probes: Iterable[Probe]) -> ProbeStatus:
    """Aggregate verdict: any fail → fail; any warn → warn; otherwise ok."""
    seen_warn = False
    for p in probes:
        if p.status == "fail":
            return "fail"
        if p.status == "warn":
            seen_warn = True
    return "warn" if seen_warn else "ok"
