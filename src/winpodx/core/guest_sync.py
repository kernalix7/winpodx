# SPDX-License-Identifier: MIT
"""Apply host-side updates to a running guest without a reinstall.

See ``docs/design/GUEST_SYNC_DESIGN.md``. Upgrading winpodx on the host
leaves the guest's ``agent.ps1`` / urlacl / rdprrap / shim stale until a
wipe-reinstall. ``/oem`` is a live bind mount of the host's ``config/oem``,
so after a host upgrade the container already has the new files; this module
delivers them into the running guest (same channel as ``pod recover-oem``,
but automated over the agent ``/exec``), re-applies the idempotent fixes,
restarts the agent, and stamps the guest version.

A guest version stamp (``C:\\winpodx\\install-state\\guest_version.json``)
records the ``(winpodx, oem_bundle)`` pair that last provisioned the guest;
``guest_sync_needed`` compares it to the host's current pair.

Guest-side ``/exec`` work -- covered by the real-Windows smoke gate, not
unit tests.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass

from winpodx.core.config import Config

log = logging.getLogger(__name__)

# Where the guest records what provisioned it.
_STAMP_PATH = r"C:\winpodx\install-state\guest_version.json"
# Container-internal HTTP port for OEM delivery (shared with recover-oem).
_OEM_HTTP_PORT = 8766
_SERVE_DIR = "/tmp/winpodx-sync"


@dataclass
class GuestVersion:
    winpodx: str
    oem_bundle: str

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, GuestVersion)
            and self.winpodx == other.winpodx
            and self.oem_bundle == other.oem_bundle
        )


def host_version() -> GuestVersion:
    """The (winpodx, oem_bundle) pair this host install ships."""
    from winpodx import __version__
    from winpodx.core.info import _bundled_oem_version

    return GuestVersion(winpodx=__version__, oem_bundle=str(_bundled_oem_version()))


# PowerShell: print the guest stamp JSON (empty string if absent).
_READ_STAMP_PS = (
    rf"if (Test-Path '{_STAMP_PATH}') {{ Get-Content -Raw '{_STAMP_PATH}' }} "
    r"else { Write-Output '' }"
)


def read_guest_version(cfg: Config, *, timeout: int = 30) -> GuestVersion | None:
    """Read the guest version stamp via ``/exec``. None when absent/unreachable."""
    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    try:
        # Agent-first: callers gate on agent health before reading, so this
        # uses the windowless /exec channel (no FreeRDP flash, no 30s RemoteApp
        # activation wait). A transitioning agent fails clean -> None.
        result = run_via_transport(
            cfg, _READ_STAMP_PS, timeout=timeout, description="guest-version"
        )
    except WindowsExecError as e:
        log.debug("guest-version read exec failed: %s", e)
        return None
    if not result.ok:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return GuestVersion(
            winpodx=str(data.get("winpodx", "")),
            oem_bundle=str(data.get("oem_bundle", "")),
        )
    except (ValueError, TypeError) as e:
        log.debug("guest-version stamp unparseable %r: %s", raw, e)
        return None


def write_guest_version(cfg: Config, ver: GuestVersion, *, timeout: int = 30) -> bool:
    """Write the guest version stamp via ``/exec``."""
    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    payload = json.dumps({"winpodx": ver.winpodx, "oem_bundle": ver.oem_bundle})
    # Base64 the JSON so quoting can't corrupt it inside the PS wrapper.
    import base64

    b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    ps = (
        r"$dir = 'C:\winpodx\install-state'; "
        r"if (-not (Test-Path $dir)) "
        r"{ New-Item -ItemType Directory -Force -Path $dir | Out-Null }; "
        f"$json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')); "
        f"Set-Content -Path '{_STAMP_PATH}' -Value $json -Encoding UTF8"
    )
    try:
        # Agent-first (windowless /exec): callers gate on agent health before
        # writing, so this avoids a FreeRDP RemoteApp flash + its 30s
        # activation timeout. The stamp is best-effort -- a transitioning
        # agent on a fresh first boot fails clean and the stamp is re-attempted
        # on the next pod start, so this is info, not a scary warning.
        result = run_via_transport(cfg, ps, timeout=timeout, description="guest-version-write")
    except WindowsExecError as e:
        log.info("guest-version stamp deferred (guest not ready); will retry next start: %s", e)
        return False
    return result.ok


def guest_sync_needed(cfg: Config) -> bool:
    """True when the guest stamp is absent or differs from the host pair."""
    guest = read_guest_version(cfg)
    if guest is None:
        return True
    return guest != host_version()


# ---- guest-mutating steps (smoke-gated) ----------------------------------

# urlacl reservation, ported from install.bat (#269). Idempotent: delete the
# overlapping 8765 reservations, then add the World-SID (WD) reservation.
_URLACL_PS = (
    "cmd /c 'netsh http delete urlacl url=http://127.0.0.1:8765/ >nul 2>&1'; "
    "cmd /c 'netsh http delete urlacl url=http://*:8765/ >nul 2>&1'; "
    "cmd /c 'netsh http delete urlacl url=http://+:8765/ >nul 2>&1'; "
    "cmd /c 'netsh http add urlacl url=http://+:8765/ sddl=D:(A;;GX;;;WD)'"
)

# The restart script that runs detached in the guest. Relaunches the agent
# the same way HKCU\Run does -- through the wscript hidden-launcher.vbs
# wrapper -- so there is NO PowerShell console flash (a bare
# `powershell.exe -WindowStyle Hidden` still flashes a window briefly). Falls
# back to hidden powershell only if the launcher is somehow missing. Kept as
# a plain multi-line source; delivered base64-encoded so its quoting can't be
# mangled by the /exec wrapper.
_LAUNCHER_VBS = r"C:\Users\Public\winpodx\launchers\hidden-launcher.vbs"
_RESTART_AGENT_SCRIPT = (
    "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" |\n"
    "  Where-Object { $_.CommandLine -like '*agent.ps1*' } |\n"
    "  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }\n"
    "Start-Sleep -Seconds 2\n"
    f"$wrap = '{_LAUNCHER_VBS}'\n"
    "if (Test-Path -LiteralPath $wrap) {\n"
    "  Start-Process wscript.exe -ArgumentList "
    '("`"$wrap`"",\'"powershell.exe"\',\'"-NoProfile"\',\'"-ExecutionPolicy"\','
    "'\"Bypass\"','\"-File\"','\"C:\\OEM\\agent.ps1\"')\n"
    "} else {\n"
    "  Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "
    "'-NoProfile','-ExecutionPolicy','Bypass','-File','C:\\OEM\\agent.ps1'\n"
    "}\n"
)


def _restart_agent_ps() -> str:
    """Build the /exec payload that stages the restart script + schedules it.

    The agent serves this very /exec, so it can't Stop-Process itself
    synchronously. We drop a restart script and a one-shot scheduled task
    (~5 s out) that does the stop+relaunch after this call returns. The task
    itself runs the restart script through the wscript hidden-launcher (or
    hidden powershell as a fallback) so the task firing causes no console
    flash either. Registered via the ScheduledTasks cmdlets so -Execute /
    -Argument are real strings (no schtasks /tr quoting hell).
    """
    import base64

    b64 = base64.b64encode(_RESTART_AGENT_SCRIPT.encode("utf-8")).decode("ascii")
    return (
        f"$s = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')); "
        r"Set-Content -Path 'C:\OEM\restart-agent.ps1' -Value $s -Encoding UTF8; "
        f"$wrap = '{_LAUNCHER_VBS}'; "
        "if (Test-Path -LiteralPath $wrap) { "
        "$exe = 'wscript.exe'; "
        '$arg = \'"\' + $wrap + \'" "powershell.exe" "-NoProfile" '
        '"-ExecutionPolicy" "Bypass" "-File" "C:\\OEM\\restart-agent.ps1"\' '
        "} else { "
        "$exe = 'powershell.exe'; "
        "$arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
        "-File C:\\OEM\\restart-agent.ps1' "
        "}; "
        "$act = New-ScheduledTaskAction -Execute $exe -Argument $arg; "
        "$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(5); "
        "Register-ScheduledTask -TaskName 'WinpodxAgentRestart' -Action $act "
        "-Trigger $trg -Force | Out-Null"
    )


def _backend_exec(
    cfg: Config, args: list[str], *, timeout: int = 60
) -> subprocess.CompletedProcess:
    """Run ``<backend> <args>`` (podman/docker). Raises on missing backend."""
    cmd = cfg.pod.backend
    if cmd not in ("podman", "docker") or not shutil.which(cmd):
        raise GuestSyncError(f"backend {cmd!r} not available for guest sync")
    return subprocess.run([cmd, *args], capture_output=True, text=True, timeout=timeout)


class GuestSyncError(Exception):
    """Raised when guest sync can't proceed (backend / delivery failure)."""


def _serve_oem(cfg: Config) -> None:
    """Tar the container's ``/oem`` into a dedicated dir and serve it on
    :8766 (container-internal). Mirrors ``pod recover-oem``'s delivery."""
    import time

    container = cfg.pod.container_name
    check = _backend_exec(
        cfg, ["exec", container, "sh", "-c", "test -f /oem/install.bat"], timeout=10
    )
    if check.returncode != 0:
        raise GuestSyncError("/oem/install.bat missing in container; recreate the pod first")
    tar = _backend_exec(
        cfg,
        [
            "exec",
            container,
            "sh",
            "-c",
            f"rm -rf {_SERVE_DIR} && mkdir -p {_SERVE_DIR} && cd / && "
            f"tar czf {_SERVE_DIR}/oem.tar.gz oem",
        ],
        timeout=60,
    )
    if tar.returncode != 0:
        raise GuestSyncError(f"tar /oem failed: {tar.stderr.strip()}")
    # Best-effort kill any prior server, then start detached.
    _backend_exec(
        cfg,
        [
            "exec",
            container,
            "sh",
            "-c",
            f"pkill -f 'http.server {_OEM_HTTP_PORT}' 2>/dev/null; true",
        ],
        timeout=5,
    )
    time.sleep(1)
    _backend_exec(
        cfg,
        [
            "exec",
            "-d",
            container,
            "sh",
            "-c",
            f"cd {_SERVE_DIR} && nohup python3 -m http.server {_OEM_HTTP_PORT} "
            ">/tmp/sync-oem-http.log 2>&1 &",
        ],
        timeout=10,
    )
    time.sleep(2)


def _stop_oem_server(cfg: Config) -> None:
    try:
        _backend_exec(
            cfg,
            [
                "exec",
                cfg.pod.container_name,
                "sh",
                "-c",
                f"pkill -f 'http.server {_OEM_HTTP_PORT}'",
            ],
            timeout=5,
        )
    except (subprocess.SubprocessError, GuestSyncError):
        pass


# PowerShell run in the guest: pull oem.tar.gz from the container over the
# guest's *default gateway* and extract over C:\OEM (refreshes agent.ps1,
# rdprrap, shim, rcedit, scripts).
#
# The gateway is discovered at runtime, NOT hardcoded: dockur's network mode
# decides what the guest sees -- QEMU slirp gives 10.0.2.2, but a podman
# bridge gives e.g. 10.89.0.1 (verified on @drjwhitty's host) and other
# setups give 20.20.20.1. The container's http.server (0.0.0.0:8766) is
# reachable from the guest at whatever its default route's NextHop is.
_PULL_OEM_PS = (
    r"$gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop | "
    r"Sort-Object RouteMetric | Select-Object -First 1).NextHop; "
    r"if (-not $gw) { throw 'no default gateway' }; "
    f'Invoke-WebRequest "http://${{gw}}:{_OEM_HTTP_PORT}/oem.tar.gz" '
    r"-OutFile C:\oem.tar.gz -UseBasicParsing -TimeoutSec 120; "
    r"if (-not (Test-Path C:\oem.tar.gz)) { throw 'download failed' }; "
    r"cmd /c 'cd /d C:\ && tar -xzf C:\oem.tar.gz'; "
    r"Remove-Item C:\oem.tar.gz -ErrorAction SilentlyContinue; "
    r"if (-not (Test-Path C:\OEM\agent.ps1)) { throw 'extract failed: agent.ps1 missing' }; "
    r'Write-Output "oem-refreshed via $gw"'
)


def _write_stamp_with_retry(cfg: Config, *, attempts: int = 4, wait: float = 5.0) -> bool:
    """Write the guest stamp, retrying while the agent settles.

    rdprrap re-activation in the preceding fix step briefly drops the RDP
    session + agent, so the first stamp write can hit an unreachable guest.
    Retry a few times (the agent rebinds within seconds) before giving up.
    """
    import time

    for i in range(attempts):
        if write_guest_version(cfg, host_version()):
            return True
        if i < attempts - 1:
            log.info("stamp write attempt %d/%d failed; waiting for agent", i + 1, attempts)
            time.sleep(wait)
    return False


def sync_guest(cfg: Config, *, force: bool = False) -> dict[str, str]:
    """Push the host's current guest artifacts into the running guest.

    Returns a per-step result map (``"ok"`` / ``"failed: ..."`` / ``"skipped"``)
    for CLI/GUI rendering. Raises :class:`GuestSyncError` only on a
    precondition failure (wrong backend, ``/oem`` missing). Each step is
    idempotent; the version stamp is written only when delivery + fixes
    succeed so an interrupted run re-syncs on the next trigger.
    """
    from winpodx.core.provisioner import apply_windows_runtime_fixes
    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    # All three guest-mutating calls below (OEM pull, urlacl, agent restart)
    # run while the agent is still up -- maybe_autosync gates on agent health,
    # and the restart's /exec returns before the scheduled task kills the
    # agent. Route them through the windowless agent channel, NOT FreeRDP
    # run_in_windows: the latter pops a visible RemoteApp/PowerShell window for
    # each call, which is exactly the console-flash this is meant to avoid (it
    # only surfaced once guest-sync first actually fired, on a 0.5.8 -> 0.5.9
    # upgrade). Falls back to FreeRDP only if the agent is unreachable at
    # dispatch (e.g. manual `--force` on a dead agent).

    if cfg.pod.backend not in ("podman", "docker"):
        return {"backend": f"skipped (backend={cfg.pod.backend} not supported)"}

    if not force and not guest_sync_needed(cfg):
        return {"sync": "skipped (guest already current)"}

    results: dict[str, str] = {}

    # 1. Deliver refreshed /oem into the guest.
    try:
        _serve_oem(cfg)
        pull = run_via_transport(cfg, _PULL_OEM_PS, timeout=180, description="guest-sync-oem")
        if not pull.ok:
            raise GuestSyncError(
                f"guest OEM pull failed: {pull.stderr.strip() or pull.stdout.strip()}"
            )
        results["oem_delivery"] = "ok"
    except (GuestSyncError, WindowsExecError) as e:
        results["oem_delivery"] = f"failed: {e}"
        _stop_oem_server(cfg)
        return results  # nothing downstream is meaningful without the new files
    finally:
        _stop_oem_server(cfg)

    # 2. urlacl reservation (#269).
    try:
        r = run_via_transport(cfg, _URLACL_PS, timeout=60, description="guest-sync-urlacl")
        results["urlacl"] = "ok" if r.ok else f"failed: rc={r.rc}"
    except WindowsExecError as e:
        results["urlacl"] = f"failed: {e}"

    # 3. Idempotent registry / runtime fixes + rdprrap re-activation.
    for name, res in apply_windows_runtime_fixes(cfg).items():
        results[f"fix:{name}"] = res

    # 4. Stamp the guest -- BEFORE the agent restart. The restart kills the
    # agent we're /exec-ing through (a ~5s scheduled task), so the stamp
    # write must land while the agent is still up; doing it after races the
    # restart and times out on the RDP fallback. The preceding multi_session
    # / rdprrap re-activation also briefly disrupts the RDP session + agent,
    # so retry a few times to let it settle before giving up.
    delivery_ok = results.get("oem_delivery") == "ok"
    fixes_ok = all(not v.startswith("failed") for k, v in results.items() if k.startswith("fix:"))
    if delivery_ok and fixes_ok:
        results["stamp"] = "ok" if _write_stamp_with_retry(cfg) else "failed: stamp write"
    else:
        results["stamp"] = "skipped (a step failed; will retry next sync)"

    # 5. Restart the agent LAST (one-shot scheduled task; fire-and-forget) so
    # it picks up the refreshed agent.ps1 + urlacl. Nothing else runs over
    # /exec after this.
    try:
        r = run_via_transport(
            cfg, _restart_agent_ps(), timeout=60, description="guest-sync-agent-restart"
        )
        results["agent_restart"] = "ok" if r.ok else f"failed: rc={r.rc}"
    except WindowsExecError as e:
        results["agent_restart"] = f"failed: {e}"

    # 6. Wait for the agent to answer /health again before returning. The
    # restart above is fire-and-forget over a scheduled task, and the
    # preceding apply chain cycles TermService -- so without this the caller's
    # downstream work (migrate apply, app discovery, reverse-open) races the
    # relaunch, finds the agent unreachable, and degrades to a pending-resume.
    # Poll generously; pending-resume stays the backstop if the relaunch is
    # unusually slow (e.g. a session teardown).
    if results.get("agent_restart") == "ok":
        results["agent_back"] = "ok" if _wait_agent_back(cfg) else "timeout (still settling)"

    return results


def _wait_agent_back(cfg: Config, *, timeout: int = 180, interval: float = 5.0) -> bool:
    """Poll the agent ``/health`` until it answers after a restart, or time out.

    Returns True once the agent is reachable again. Bounded so a guest that
    won't come back doesn't hang the caller forever -- the caller treats a
    timeout as "deferred" and relies on the next pod start / pending-resume.
    """
    import time

    from winpodx.core.agent import AgentClient

    client = AgentClient(cfg)
    deadline = time.monotonic() + max(15, timeout)
    while time.monotonic() < deadline:
        try:
            client.health()
            return True
        except Exception:  # noqa: BLE001 -- agent transports raise varied types
            time.sleep(interval)
    return False


def maybe_autosync(cfg: Config) -> bool:
    """Trigger hook: sync the guest when the host is newer. Returns True when
    a sync ran. Honours ``pod.guest_autosync``; safe no-op when current.

    Crucially, this only auto-syncs when a stamp is **present and older**. An
    *absent* stamp is treated as "fresh install or pre-stamp pod": we record
    the current version but do NOT sync. Auto-syncing on a fresh install
    would fire mid-first-boot and the agent restart races install.bat's own
    agent bring-up, leaving the agent down (observed on a fresh opensuse
    install). A genuinely stale pre-stamp pod can be refreshed once with
    ``winpodx pod sync-guest --force``; after that its stamp drives future
    auto-syncs.
    """
    if not getattr(cfg.pod, "guest_autosync", True):
        return False
    if cfg.pod.backend not in ("podman", "docker"):
        return False

    # Everything below talks to the guest over the agent (read/write stamp,
    # sync). Confirm the agent is up FIRST -- not to hide failures, but
    # because the alternative (letting read/write fall back to FreeRDP
    # RemoteApp) reports the WRONG error: a misleading
    # `ERRCONNECT_ACTIVATION_TIMEOUT` FreeRDP stacktrace instead of the real
    # condition. If the agent isn't reachable we surface THAT, clearly, and
    # defer -- the stamp/sync retries on the next start.
    try:
        from winpodx.core.agent import AgentClient

        AgentClient(cfg).health()
    except Exception as e:  # noqa: BLE001
        log.warning(
            "guest agent not reachable (%s); deferring guest version "
            "stamp/sync to the next pod start",
            e,
        )
        return False

    guest = read_guest_version(cfg)
    if guest is None:
        # No stamp -> fresh install / pre-stamp. Record version, never sync.
        if write_guest_version(cfg, host_version()):
            log.info("guest has no version stamp; recorded %s without syncing", host_version())
        return False
    if guest == host_version():
        return False

    log.info("guest %s older than host %s; auto-syncing", guest, host_version())
    try:
        sync_guest(cfg)
    except GuestSyncError as e:
        log.warning("guest auto-sync skipped: %s", e)
        return False
    return True
