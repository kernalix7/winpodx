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
    """GET /health — verifies the agent is bound and answering on 8765."""

    def _run() -> tuple[ProbeStatus, str]:
        from winpodx.core.agent import AgentClient, AgentError, AgentTimeoutError

        client = AgentClient(cfg)
        try:
            payload = client.health()
        except AgentTimeoutError as e:
            return "warn", f"agent warming up or busy: {e}"
        except AgentError as e:
            return "fail", str(e)
        version = payload.get("version", "?")
        return "ok", f"version={version}"

    status, detail, ms = _timed(_run)
    return Probe("agent_health", status, detail, ms)


def probe_agent_auth_ready(cfg: Config) -> Probe:
    """Verify host-side bearer token readiness for authenticated /exec calls."""

    def _run() -> tuple[ProbeStatus, str]:
        from winpodx.core.agent import AgentClient

        ok, detail = AgentClient(cfg).auth_ready()
        if ok:
            return "ok", "host token ready"
        return "fail", f"auth token unavailable: {detail}"

    status, detail, ms = _timed(_run)
    return Probe("agent_auth_ready", status, detail, ms)


def probe_guest_exec(cfg: Config) -> Probe:
    """POST /exec round-trip — proves the host→guest command channel works.

    Sends a trivial PowerShell payload (``Write-Output ok``) through
    ``AgentClient.exec`` and verifies rc==0 and stdout=="ok". A passing
    /health doesn't tell you the bearer auth, base64 decode, child spawn,
    stdio capture, and JSON serialization all work — this probe does.

    Skipped when the pod isn't running (no point running it just to fail);
    fails loudly when /health is fine but /exec isn't (the symptom that
    the rc:null bug from 2026-04-30 created).
    """

    def _run() -> tuple[ProbeStatus, str]:
        from winpodx.core.agent import AgentClient, AgentError, AgentTimeoutError
        from winpodx.core.pod import PodState, pod_status

        # Skip rather than fail when the pod itself is down — keeps the
        # report readable in the "pod stopped" state where every guest
        # probe would otherwise red-X.
        try:
            if pod_status(cfg).state != PodState.RUNNING:
                return "skip", "pod not running"
        except Exception:  # noqa: BLE001
            return "skip", "pod state unknown"

        client = AgentClient(cfg)
        try:
            result = client.exec("Write-Output ok\n", timeout=10.0)
        except AgentTimeoutError as e:
            return "warn", f"agent warming up or busy: {e}"
        except AgentError as e:
            return "fail", f"exec failed: {e}"
        if result.rc != 0:
            return "fail", f"rc={result.rc} stderr={result.stderr.strip()[:80]!r}"
        out = (result.stdout or "").strip()
        if out != "ok":
            return "warn", f"unexpected stdout: {out[:80]!r}"
        return "ok", "round-trip OK (rc=0, stdout='ok')"

    status, detail, ms = _timed(_run)
    return Probe("guest_exec", status, detail, ms)


def probe_guest_summary(cfg: Config) -> Probe:
    """Single /exec call that returns a JSON snapshot of guest state.

    Reports Windows version, uptime, current RDP user, active session
    count, and C: free space in one row. Cheap enough to include in the
    default check (~150ms typical) and answers the most common
    "what's going on inside Windows" question without requiring the
    user to open a remote desktop.
    """

    def _run() -> tuple[ProbeStatus, str]:
        import json

        from winpodx.core.agent import AgentClient, AgentError, AgentTimeoutError
        from winpodx.core.pod import PodState, pod_status

        try:
            if pod_status(cfg).state != PodState.RUNNING:
                return "skip", "pod not running"
        except Exception:  # noqa: BLE001
            return "skip", "pod state unknown"

        # Single PowerShell payload — emit one JSON object so the host
        # parses it in one shot. Keep the script short; agent.ps1 logs
        # the payload hash for forensics, and longer scripts inflate
        # /exec latency.
        script = (
            "$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue;"
            "$cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue;"
            "$disk = Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID='C:'\""
            " -ErrorAction SilentlyContinue;"
            "$sessions = (query session 2>$null | Where-Object { $_ -match 'rdp' }"
            " | Measure-Object).Count;"
            "$obj = @{"
            "  os = if ($os) { $os.Caption.Trim() } else { '?' };"
            "  build = if ($os) { $os.BuildNumber } else { '?' };"
            "  uptime_h = if ($os) {"
            "    [math]::Round((New-TimeSpan -Start $os.LastBootUpTime"
            " -End (Get-Date)).TotalHours, 1)"
            "  } else { 0 };"
            "  user = if ($cs) { $cs.UserName } else { '' };"
            "  c_free_gb = if ($disk) {"
            "    [math]::Round($disk.FreeSpace / 1GB, 1)"
            "  } else { 0 };"
            "  sessions = [int]$sessions;"
            "};"
            "$obj | ConvertTo-Json -Compress"
        )

        client = AgentClient(cfg)
        try:
            result = client.exec(script, timeout=15.0)
        except AgentTimeoutError as e:
            return "warn", f"agent warming up or busy: {e}"
        except AgentError as e:
            return "fail", f"exec failed: {e}"
        if result.rc != 0:
            return "warn", f"rc={result.rc} stderr={result.stderr.strip()[:80]!r}"
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as e:
            return "warn", f"non-JSON stdout: {e}"

        os_name = payload.get("os", "?")
        build = payload.get("build", "?")
        uptime_h = payload.get("uptime_h", 0)
        user = payload.get("user") or "(no user)"
        sessions = payload.get("sessions", 0)
        c_free = payload.get("c_free_gb", 0)
        detail = (
            f"{os_name} build={build} up={uptime_h}h user={user!r} "
            f"sessions={sessions} C:={c_free}GiB free"
        )
        return "ok", detail

    status, detail, ms = _timed(_run)
    return Probe("guest_summary", status, detail, ms)


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
    probe_agent_auth_ready,
    probe_guest_exec,  # round-trip test — proves /exec works, not just /health
    probe_guest_summary,  # in-guest snapshot (Windows version / uptime / sessions / disk)
    probe_oem_version,
    probe_password_age,
    probe_apps_discovered,
    probe_disk_free,
)


def _skip_probe(name: str, detail: str) -> Probe:
    return Probe(name, "skip", detail, 0)


def run_all(cfg: Config) -> list[Probe]:
    """Run every probe and return results in deterministic order."""
    out: list[Probe] = []
    agent_health: Probe | None = None
    agent_auth_ready: Probe | None = None

    for probe_fn in PROBES:
        if probe_fn is probe_agent_auth_ready and agent_health is not None:
            if agent_health.status != "ok":
                agent_auth_ready = _skip_probe(
                    "agent_auth_ready",
                    f"agent not ready: {agent_health.detail}",
                )
                out.append(agent_auth_ready)
                continue

        if probe_fn in (probe_guest_exec, probe_guest_summary):
            if agent_health is not None and agent_health.status != "ok":
                out.append(_skip_probe(probe_fn.__name__.replace("probe_", ""), "agent not ready"))
                continue
            if agent_auth_ready is not None and agent_auth_ready.status != "ok":
                out.append(
                    _skip_probe(
                        probe_fn.__name__.replace("probe_", ""),
                        f"agent auth not ready: {agent_auth_ready.detail}",
                    )
                )
                continue

        probe = probe_fn(cfg)
        out.append(probe)
        if probe.name == "agent_health":
            agent_health = probe
        elif probe.name == "agent_auth_ready":
            agent_auth_ready = probe

    return out


def overall(probes: Iterable[Probe]) -> ProbeStatus:
    """Aggregate verdict: any fail → fail; any warn → warn; otherwise ok."""
    seen_warn = False
    for p in probes:
        if p.status == "fail":
            return "fail"
        if p.status == "warn":
            seen_warn = True
    return "warn" if seen_warn else "ok"
