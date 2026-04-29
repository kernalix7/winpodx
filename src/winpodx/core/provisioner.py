"""Auto-provisioning on first launch."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from winpodx.core.compose import generate_compose, generate_password
from winpodx.core.config import Config
from winpodx.core.pod import PodState, check_rdp_port, pod_status, start_pod
from winpodx.utils.paths import config_dir

log = logging.getLogger(__name__)

# Marker for a partial password rotation (Windows changed, config did not).
_ROTATION_PENDING_MARKER = "rotation_pending"


def _rotation_marker_path() -> Path:
    return Path(config_dir()) / f".{_ROTATION_PENDING_MARKER}"


class ProvisionError(Exception):
    """Raised when auto-provisioning fails."""


def ensure_ready(cfg: Config | None = None, timeout: int = 300) -> Config:
    """Ensure everything is ready to launch a Windows app."""
    if cfg is None:
        cfg = _ensure_config()

    _check_rotation_pending()
    cfg = _auto_rotate_password(cfg)

    # v0.1.9.2: probe pod state once and run idempotent runtime fixes BEFORE
    # the RDP-port early-return. install.bat changes only land on first boot
    # of a new container; without this block, existing 0.1.x guests would
    # never pick up OEM v7/v8 changes (NIC power-save, TermService failure
    # recovery, RDP timeouts, max_sessions sync) until they recreated their
    # container. Each apply is idempotent — `Set-ItemProperty -Force` is a
    # no-op when the value already matches — so running them on every
    # ensure_ready is cheap (~1.5s overhead) and self-healing.
    #
    # v0.2.0.1: gate the warm-pod path on `check_rdp_port` so we don't
    # fire FreeRDP RemoteApp at a container that's `RUNNING` but whose
    # Windows VM is still booting. Without the gate, each apply hits
    # ERRCONNECT_ACTIVATION_TIMEOUT (rc=131) or ERRCONNECT_CONNECT_TRANSPORT_FAILED
    # (rc=147, connection reset) after up to 60s, and the cascade
    # (3×60s) surfaces as a Launch Error dialog when the user tries
    # to start an app right after `winpodx pod restart`.
    if (
        cfg.pod.backend in ("podman", "docker")
        and pod_status(cfg).state == PodState.RUNNING
        and check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0)
    ):
        _self_heal_apply(cfg)

    if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=0.3):
        return cfg

    _check_deps()

    if cfg.pod.backend in ("podman", "docker"):
        _ensure_compose(cfg)

    from winpodx.core.daemon import ensure_pod_awake

    ensure_pod_awake(cfg)

    _ensure_pod_running(cfg, timeout)
    # Re-apply once more after starting (cold-pod path); same idempotency
    # guarantees mean this only does work the first time after a fresh
    # start. The earlier branch handles the warm-pod case.
    _self_heal_apply(cfg)
    # Bug B: after host suspend / long idle the pod can be running but RDP
    # itself is dead while VNC is fine. Probe and try to revive TermService
    # before handing the cfg to the caller — the alternative is the FreeRDP
    # launch failing with a connection-refused that the user has to debug.
    from winpodx.core.pod import recover_rdp_if_needed

    if not check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
        recover_rdp_if_needed(cfg)

    # v0.1.9: bundled profile set was removed; auto-discover on first boot
    # so the user's menu is populated without them having to know about
    # `winpodx app refresh`.
    _auto_discover_if_empty(cfg)
    _ensure_desktop_entries()

    return cfg


def _change_windows_password(cfg: Config, new_password: str) -> bool:
    """Change the Windows user account password via FreeRDP RemoteApp.

    Uses the CURRENT cfg.password to authenticate FreeRDP, then runs
    ``net user <User> <new>`` inside Windows. On success, the caller
    updates cfg.password. The existing rotation rollback marker
    (``_ROTATION_PENDING_MARKER``) handles the partial-failure window
    where the host saved the new password to disk but the guest didn't
    accept it — on next ensure_ready the marker is detected and the
    cfg.password is reverted to whatever Windows actually accepts.

    v0.1.9.5: was on the broken `podman exec ... powershell.exe` path
    which silently failed for every release back to 0.1.0. Migrated to
    the FreeRDP RemoteApp channel along with all the other Windows-
    side commands.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return False

    user = cfg.rdp.user.replace("'", "''")
    pw = new_password.replace("'", "''")
    payload = f"& net user '{user}' '{pw}' | Out-Null\nWrite-Output 'password set'\n"

    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(cfg, payload, description="rotate-password", timeout=45)
    except WindowsExecError as e:
        log.warning("Password change channel failure: %s", e)
        return False
    if result.rc != 0:
        log.warning("Password change failed (rc=%d): %s", result.rc, result.stderr.strip())
        return False
    return True


def _apply_max_sessions(cfg: Config) -> None:
    """Sync the guest's MaxInstanceCount with cfg.pod.max_sessions.

    Idempotent — if the registry already matches, the wrapper short-
    circuits and skips the TermService restart so active sessions don't
    drop. Runs via FreeRDP RemoteApp (see ``windows_exec.run_in_windows``)
    because podman exec can't reach the Windows VM inside the dockur
    Linux container.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    desired = max(1, min(50, int(cfg.pod.max_sessions)))
    # v0.1.9.5: do NOT call `Restart-Service -Force TermService` here.
    # The whole reason this script is running is that we're already inside
    # an RDP session served by that very TermService — restarting it kills
    # the session, the wrapper never gets to write its result file, and the
    # host sees ERRINFO_RPC_INITIATED_DISCONNECT (kernalix7 saw exactly
    # this on 2026-04-26). Registry write alone is enough; TermService
    # picks up MaxInstanceCount on its next natural cycle (next pod boot
    # or next manual `winpodx pod restart`). Idempotent so repeated runs
    # eventually converge.
    # v0.2.1: MaxInstanceCount lives under \WinStations\RDP-Tcp — NOT
    # at Terminal Server root. Previous releases wrote the value to
    # the wrong subkey, which Windows silently ignored, so changing
    # cfg.max_sessions had no effect (only install.bat's initial cap
    # at OEM time was authoritative). Now both keys are written:
    # WinStations\RDP-Tcp\MaxInstanceCount (the one Windows actually
    # reads) and Terminal Server\fSingleSessionPerUser (single-user
    # gate, separate key, lives at Terminal Server root).
    payload = (
        "$pTs   = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server'\n"
        "$pTcp  = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\"
        "Terminal Server\\WinStations\\RDP-Tcp'\n"
        f"$desired = {desired}\n"
        "$current = (Get-ItemProperty $pTcp -Name MaxInstanceCount "
        "-ErrorAction SilentlyContinue).MaxInstanceCount\n"
        "if ($current -eq $desired) {\n"
        '    Write-Output "max_sessions already $desired"\n'
        "    return\n"
        "}\n"
        "Set-ItemProperty -Path $pTcp -Name MaxInstanceCount "
        "-Value $desired -Type DWord -Force\n"
        "Set-ItemProperty -Path $pTs  -Name fSingleSessionPerUser "
        "-Value 0 -Type DWord -Force\n"
        'Write-Output "max_sessions: $current -> $desired '
        '(takes effect on next TermService restart)"\n'
    )

    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(cfg, payload, description="apply-max-sessions")
    except WindowsExecError as e:
        log.warning("max_sessions: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("max_sessions: rc=%d stderr=%s", result.rc, result.stderr.strip())
        raise RuntimeError(f"max_sessions apply failed (rc={result.rc}): {result.stderr.strip()}")
    log.info("max_sessions: %s", result.stdout.strip())


def wait_for_windows_responsive(cfg: Config, timeout: int = 90) -> bool:
    """Poll until the Windows guest can actually accept FreeRDP RemoteApp.

    Container ``RUNNING`` state is not enough — dockur boots the Linux
    container in seconds but the Windows VM inside QEMU needs another
    30-90s before its RDP listener accepts activation. Firing an apply
    inside that window means every call hits one of:

    - ``ERRCONNECT_CONNECT_TRANSPORT_FAILED`` (rc=147, connection reset
      by peer — RDP socket open but server not initialized)
    - ``ERRCONNECT_ACTIVATION_TIMEOUT`` (rc=131, FreeRDP connected but
      activation phase didn't complete in time)

    This helper waits for the RDP port first, then fires repeated tiny
    no-op probes (`Write-Output 'ping'`) until either one succeeds or
    the overall timeout expires. v0.2.0.6 added the retry loop after
    v0.2.0.5 shipped a one-shot probe — on a still-booting guest the
    first probe always hits rc=147 connection-reset and the entire
    wait collapsed in <1s regardless of the timeout the caller passed.
    Returns True once a probe succeeds, False if the guest is still
    booting at ``timeout`` seconds — caller decides whether to skip /
    retry / surface to user.
    """
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    deadline = time.monotonic() + max(1, int(timeout))

    # v0.2.2.2: poll the OEM-install-done sentinel BEFORE firing any
    # FreeRDP RemoteApp probe. install.bat enables RDP early (line
    # ~13) but only activates rdprrap multi-session at the end (line
    # ~239); probing FreeRDP in between makes Windows surface the
    # "Another user is signed in" dialog inside the guest.
    while time.monotonic() < deadline:
        if _oem_install_done(cfg):
            break
        time.sleep(3)
    else:
        return False

    while time.monotonic() < deadline:
        if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
            break
        time.sleep(2)
    else:
        return False

    # Port is open — keep firing the activation probe until one succeeds
    # or the deadline expires. On first boot the guest can refuse FreeRDP
    # for several minutes (Windows still in mid-Sysprep / OEM apply) even
    # though the RDP listener answers TCP, so a one-shot probe here is
    # the wrong primitive — the user already passed `timeout` to express
    # "try this hard, for this long".
    while time.monotonic() < deadline:
        per_probe_budget = max(5, min(20, int(deadline - time.monotonic())))
        try:
            result = run_in_windows(
                cfg,
                "Write-Output 'ping'\n",
                description="responsive-probe",
                timeout=per_probe_budget,
            )
            if result.rc == 0:
                return True
        except WindowsExecError:
            # Transient — Windows likely still booting / Sysprep running.
            pass
        # Pace retries so we don't pin a CPU spinning FreeRDP processes.
        if time.monotonic() < deadline - 3:
            time.sleep(3)
        else:
            break
    return False


_APPLIES_STAMP_FILENAME = ".applies_stamp"


# v0.2.2.2: install.bat completion gate.
#
# Between RDP-port-up (line 12-18 of install.bat) and the TermService
# restart that activates rdprrap (line 239-240), Windows enforces
# single-session-per-user. Any host-fired FreeRDP RemoteApp attempt in
# that window surfaces the "Another user is signed in" dialog visibly
# inside the guest (kernalix7 reported on 2026-04-29 during a fresh
# install — the dialog flashes repeatedly as wait_for_windows_responsive
# probes every 3s).
#
# Detection priority:
#   1. Cache hit for this (container, started_at).
#   2. dockur prints "Windows started successfully" once QEMU has fully
#      booted Windows; install.bat then runs as part of OOBE. Once we see
#      that message AND _OEM_DONE_DOCKUR_BUFFER_SECONDS have passed, the
#      OEM stage is overwhelmingly likely done (install.bat is bounded
#      to ~1-3 min).
#   3. Time-based fallback at _OEM_DONE_FALLBACK_AGE_SECONDS for the
#      pathological case where dockur logs aren't readable at all.
#
# install.bat's own `echo [winpodx] Post-install configuration complete`
# does NOT appear in `<runtime> logs`: dockur exposes its own status
# messages and QEMU's serial output, not Windows console output (which
# only the user sees via VNC at :8006).
_OEM_DOCKUR_READY_SENTINEL = "Windows started successfully"
_OEM_DONE_DOCKUR_BUFFER_SECONDS = 120
_OEM_DONE_FALLBACK_AGE_SECONDS = 180
_OEM_DONE_CACHE: dict[str, str] = {}  # container_name -> known-good started_at


def _container_started_at(cfg: Config) -> str:
    """Read the container's StartedAt timestamp via {runtime} inspect.

    Used as part of the self-heal stamp so a pod restart invalidates
    the previous apply (TermService / NIC settings need to land again
    after a reboot of the Windows VM). Returns an empty string when the
    backend isn't podman/docker or inspect fails — caller treats empty
    as "no stamp" and runs the apply.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return ""
    try:
        proc = subprocess.run(  # noqa: S603
            [
                cfg.pod.backend,
                "inspect",
                "--format",
                "{{.State.StartedAt}}",
                cfg.pod.container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _oem_install_done(cfg: Config) -> bool:
    """Return True when install.bat's first-boot OEM stage has finished.

    Until install.bat reaches its TermService restart line (which
    activates rdprrap multi-session), Windows enforces single-session-
    per-user. Any host-fired FreeRDP RemoteApp in that window surfaces
    the "Another user is signed in" dialog visibly to the user.

    Detection priority (see module-level constants for the rationale):
      1. Cache hit for this (container, started_at).
      2. dockur's "Windows started successfully" appears in container
         logs AND _OEM_DONE_DOCKUR_BUFFER_SECONDS have elapsed since
         the container started — by then install.bat is almost
         certainly done (it's bounded to a few minutes).
      3. Time-based fallback at _OEM_DONE_FALLBACK_AGE_SECONDS for
         the pathological case where dockur logs are unreadable.

    Returns True for non-dockur backends (libvirt / manual) since the
    race only exists when dockur autologons a User session before host
    FreeRDP runs.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return True

    started = _container_started_at(cfg)
    if not started:
        log.debug("oem_install_done: container_started_at empty — pod not inspectable")
        return False

    name = cfg.pod.container_name
    if _OEM_DONE_CACHE.get(name) == started:
        return True

    age_seconds: float | None = None
    try:
        from datetime import datetime

        started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        now_dt = datetime.now(started_dt.tzinfo)
        age_seconds = (now_dt - started_dt).total_seconds()
    except (ValueError, TypeError, AttributeError) as e:
        log.debug("oem_install_done: failed to parse started_at %r: %s", started, e)

    # Path 2: dockur sentinel + buffer.
    try:
        proc = subprocess.run(  # noqa: S603
            [cfg.pod.backend, "logs", "--tail", "200", name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        haystack = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if _OEM_DOCKUR_READY_SENTINEL in haystack:
            if age_seconds is None or age_seconds >= _OEM_DONE_DOCKUR_BUFFER_SECONDS:
                log.info(
                    "oem_install_done: dockur ready + %.0fs buffer — gate open",
                    age_seconds or 0,
                )
                _OEM_DONE_CACHE[name] = started
                return True
            log.debug(
                "oem_install_done: dockur ready but buffer not elapsed (%.0fs/%ds)",
                age_seconds, _OEM_DONE_DOCKUR_BUFFER_SECONDS,
            )
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        log.debug("oem_install_done: container logs read failed: %s", e)

    # Path 3: pure time-based fallback.
    if age_seconds is not None and age_seconds >= _OEM_DONE_FALLBACK_AGE_SECONDS:
        log.info(
            "oem_install_done: time-based fallback after %.0fs — gate open",
            age_seconds,
        )
        _OEM_DONE_CACHE[name] = started
        return True

    return False


def _applies_stamp_path() -> Path:
    return Path(config_dir()) / _APPLIES_STAMP_FILENAME


def _self_heal_already_done(cfg: Config) -> bool:
    """Return True when the self-heal apply has already succeeded for this
    (winpodx version, pod start instance). Avoids a 3-FreeRDP-RemoteApp
    PowerShell flash on every single app launch — the apply payloads are
    idempotent on the registry side, but their visible effect (briefly
    appearing PS windows) was hitting users every time they clicked an app.
    """
    from winpodx import __version__

    started = _container_started_at(cfg)
    if not started:
        return False
    expected = f"{__version__}:{started}"
    try:
        return _applies_stamp_path().read_text(encoding="utf-8").strip() == expected
    except (FileNotFoundError, OSError):
        return False


def _record_self_heal_done(cfg: Config) -> None:
    """Stamp ``<winpodx_version>:<container_started_at>`` once all three
    self-heal applies have succeeded. The next ensure_ready short-circuits
    until either the pod is restarted (started_at changes) or winpodx is
    upgraded (__version__ changes)."""
    from winpodx import __version__

    started = _container_started_at(cfg)
    if not started:
        return
    try:
        path = _applies_stamp_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{__version__}:{started}\n", encoding="utf-8")
    except OSError as e:
        log.debug("could not write self-heal stamp: %s", e)


# v0.2.2.2: agent-token staging stamp. Independent of the self-heal stamp
# because the trigger is different — this one invalidates on token rotation
# (host-side mtime changes) AND on container restart, while self-heal
# invalidates on winpodx version bump AND container restart.
_AGENT_TOKEN_STAMP_FILENAME = ".agent_token_stamp"


def _agent_token_stamp_path() -> Path:
    return Path(config_dir()) / _AGENT_TOKEN_STAMP_FILENAME


def _agent_token_already_staged(cfg: Config) -> bool:
    """Return True when the host's agent token has already been pushed
    into ``C:\\OEM\\agent_token.txt`` for the current container start.

    Stamp content is ``<container_started_at>:<host_token_mtime>`` so a
    pod restart (Windows VM reboot drops C:\\OEM contents on a fresh
    container) or a token rotation forces a re-push.
    """
    started = _container_started_at(cfg)
    if not started:
        return False
    from winpodx.utils.agent_token import token_path

    try:
        token_mtime = int(token_path().stat().st_mtime)
    except OSError:
        return False
    expected = f"{started}:{token_mtime}"
    try:
        return _agent_token_stamp_path().read_text(encoding="utf-8").strip() == expected
    except (FileNotFoundError, OSError):
        return False


def _record_agent_token_staged(cfg: Config) -> None:
    """Write the agent-token stamp once the push has succeeded."""
    started = _container_started_at(cfg)
    if not started:
        return
    from winpodx.utils.agent_token import token_path

    try:
        token_mtime = int(token_path().stat().st_mtime)
    except OSError:
        return
    try:
        path = _agent_token_stamp_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{started}:{token_mtime}\n", encoding="utf-8")
    except OSError as e:
        log.debug("could not write agent-token stamp: %s", e)


def _ensure_agent_token_in_guest(cfg: Config) -> None:
    r"""Push the host's agent token into the Windows guest at
    ``C:\OEM\agent_token.txt``.

    install.bat tries to copy the same file from ``\\tsclient\home`` at
    OOBE time, but no FreeRDP session exists yet at install — the OEM
    stage runs before any RDP connect — so that copy fails silently
    every time. Without this push, ``agent.ps1`` sits in its
    ``Wait-Token`` polling loop forever, the HTTP listener never binds,
    and every host -> guest call falls back to slow FreeRDP RemoteApp.

    This helper runs from inside ``_self_heal_apply`` (which fires from
    ensure_ready on warm + cold paths). At that point the pod is
    RDP-reachable, and ``run_in_windows`` uses ``+home-drive`` so
    ``\\tsclient\home`` is visible inside Windows for the duration of
    the call. The token is staged via a Copy-Item against that share.

    Idempotent via ``_agent_token_already_staged`` — runs at most once
    per (container start, host-token mtime) tuple.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return
    from winpodx.utils.agent_token import token_path

    if not token_path().exists():
        return  # Setup hasn't run, or token was deleted; nothing to push.

    if _agent_token_already_staged(cfg):
        return

    # The token contents never appear in the script source: PowerShell
    # reads them from \\tsclient\home inside Windows. The host-side
    # script body is therefore safe to log / leak; only the redirected
    # share carries the secret, and that path is already used by
    # run_in_windows for every other privileged exec.
    payload = (
        "$ErrorActionPreference = 'Stop'\n"
        "$src = '\\\\tsclient\\home\\.config\\winpodx\\agent_token.txt'\n"
        "$dst = 'C:\\OEM\\agent_token.txt'\n"
        "if (-not (Test-Path $src)) {\n"
        '    throw "source $src not visible from inside Windows '
        '(home-drive not forwarded?)"\n'
        "}\n"
        "New-Item -ItemType Directory -Path 'C:\\OEM' -Force | Out-Null\n"
        "Copy-Item -Path $src -Destination $dst -Force\n"
        "Write-Output 'agent token staged'\n"
    )

    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(
            cfg, payload, description="stage-agent-token", timeout=30
        )
    except WindowsExecError as e:
        log.warning(
            "agent_token_stage: channel failure (will retry next ensure_ready): %s",
            e,
        )
        return
    if result.rc != 0:
        log.warning(
            "agent_token_stage: rc=%d stderr=%s",
            result.rc,
            result.stderr.strip(),
        )
        return
    log.info("agent token staged into guest C:\\OEM\\agent_token.txt")
    _record_agent_token_staged(cfg)


def _apply_multi_session(cfg: Config) -> None:
    """v0.2.0.9: enable rdprrap multi-session by default.

    Without this, the 2nd FreeRDP RemoteApp launch against the same
    Windows account triggers a "Select a session to reconnect to"
    dialog (Windows refuses concurrent sessions per user by default)
    instead of giving the user an independent app window. rdprrap
    patches termsrv.dll so each connection becomes its own session.

    Idempotent — rdprrap-conf --enable is a no-op when already enabled.
    Tolerates rdprrap-conf missing (e.g. older OEM builds) by treating
    the apply as a successful skip rather than a hard failure, since
    the rest of the self-heal block is more important than this UX
    nicety.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    candidates = [
        r"C:\OEM\rdprrap\rdprrap-conf.exe",
        r"C:\OEM\rdprrap-conf.exe",
        r"C:\Program Files\rdprrap\rdprrap-conf.exe",
    ]
    payload_lines = ["$rdprrap = $null"]
    for path in candidates:
        payload_lines.append(
            f"if (-not $rdprrap -and (Test-Path '{path}')) {{ $rdprrap = '{path}' }}"
        )
    payload_lines += [
        "if (-not $rdprrap) {",
        "    Write-Output 'rdprrap-conf not found; multi-session left disabled'",
        "    exit 0",  # treat missing rdprrap as best-effort skip, not failure
        "}",
        "& $rdprrap --enable | Out-Null",
        "Write-Output 'multi-session enabled'",
        "exit 0",
    ]
    payload = "\n".join(payload_lines) + "\n"

    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(cfg, payload, description="apply-multi-session")
    except WindowsExecError as e:
        log.warning("multi_session: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("multi_session: rc=%d stderr=%s", result.rc, result.stderr.strip())
        # Non-fatal — log and continue. rdprrap not being patched
        # doesn't break winpodx, just means each app share a session.
        return
    log.info("multi_session: %s", result.stdout.strip())


def _self_heal_apply(cfg: Config) -> None:
    """Run the four idempotent runtime applies in self-healing mode.

    Distinct from ``apply_windows_runtime_fixes`` which is called from
    the explicit ``winpodx pod apply-fixes`` CLI / GUI button: that one
    must surface failures in its result map. Self-healing mode is fired
    from ``ensure_ready`` on every app launch, so any failure must be
    swallowed — the user is trying to launch an app, not run an apply,
    and a transient ERRCONNECT_ACTIVATION_TIMEOUT (Windows still
    booting) must not cascade into a Launch Error dialog. The next
    ensure_ready call picks up wherever this one left off.

    v0.2.0.8: short-circuit when a stamp already records that all three
    applies succeeded for the current (winpodx version, container
    StartedAt) tuple. Without this, every single app launch fires three
    FreeRDP RemoteApp PowerShell windows — even though `-WindowStyle
    Hidden` makes them tiny, they still flash visibly on every click,
    which kernalix7 reported as "powershell 계속 깜빡깜빡 뜬다" on
    2026-04-27. The stamp invalidates on pod restart (so TermService /
    NIC settings re-apply after a Windows reboot) and on winpodx
    upgrade (so a patch-version bump still gets a chance to apply
    new payloads).
    """
    # v0.2.2.2: don't fire any FreeRDP RemoteApp until install.bat has
    # finished. Between RDP-port-up and rdprrap-active the guest
    # rejects concurrent sessions visibly via the "Another user is
    # signed in" dialog. Skip silently and let the next ensure_ready
    # retry once the OEM stage completes.
    if not _oem_install_done(cfg):
        log.debug("self_heal_apply deferred: OEM install not yet complete")
        return

    # v0.2.2.2: stage the agent token into the guest before probing
    # the agent. This call has its own per-(container, token-mtime)
    # stamp so it doesn't fire FreeRDP every launch — but it must
    # run before the self-heal short-circuit below, since the
    # self-heal stamp doesn't track token state.
    _ensure_agent_token_in_guest(cfg)

    if _self_heal_already_done(cfg):
        return

    # v0.2.2: route each apply through the HTTP guest agent first,
    # falling back to the FreeRDP RemoteApp PowerShell payload when
    # the agent isn't available (older containers without agent.ps1,
    # fresh installs where the Task Scheduler trigger hasn't fired
    # yet, or the agent is being upgraded). The fallback path is
    # exactly what v0.2.1 used — same registry payloads, same
    # outcomes — just the channel differs. Apply payloads land
    # ~50× faster via the agent (~50ms HTTP vs ~5-10s FreeRDP per
    # call) and crucially don't flash a PowerShell window.
    from winpodx.core.agent import AgentError, run_apply_via_agent_or_freerdp
    from winpodx.core.windows_exec import WindowsExecError

    applies = (
        ("max_sessions", _apply_max_sessions),
        ("rdp_timeouts", _apply_rdp_timeouts),
        ("oem_runtime_fixes", _apply_oem_runtime_fixes),
        ("multi_session", _apply_multi_session),
    )
    succeeded = 0
    for name, fn in applies:
        try:
            run_apply_via_agent_or_freerdp(cfg, name, fn)
            succeeded += 1
        except WindowsExecError as e:
            log.warning(
                "%s: channel failure during self-heal (will retry next ensure_ready): %s",
                name,
                e,
            )
            return  # Don't waste retries on a still-booting guest.
        except AgentError as e:
            log.warning("%s: agent reported failure: %s", name, e)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: self-heal apply failed: %s", name, e)

    if succeeded == len(applies):
        _record_self_heal_done(cfg)


def apply_windows_runtime_fixes(cfg: Config) -> dict[str, str]:
    """Public entry point: run all idempotent Windows-side runtime applies.

    Used by the standalone ``winpodx pod apply-fixes`` CLI command, the
    GUI Tools-page button, and v0.1.9.3+ migrate (which always invokes
    this regardless of version comparison so users on a "already current"
    marker still receive fixes that landed in patch releases).

    Returns a per-helper result map: ``{helper_name: "ok" | "failed: ..."}``
    so the caller can render success/failure rows. Backend gating returns
    ``{"backend": "skipped (libvirt/manual not supported)"}`` so the caller
    knows nothing was attempted.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return {"backend": f"skipped (backend={cfg.pod.backend} not supported)"}

    # v0.2.2.2: defer if install.bat hasn't finished yet — same dialog
    # race as _self_heal_apply. Surfaces a "deferred" status so the
    # caller (CLI apply-fixes / GUI button / pending.resume) can
    # report the skip and retry later instead of triggering the
    # "Another user is signed in" dialog.
    if not _oem_install_done(cfg):
        return {
            "oem": "deferred (install.bat still running — will run on next attempt)"
        }

    # v0.2.2: route through the HTTP guest agent when available with
    # FreeRDP RemoteApp fallback. Same as _self_heal_apply but reports
    # per-step status to the caller (CLI / GUI render the result map).
    from winpodx.core.agent import run_apply_via_agent_or_freerdp

    results: dict[str, str] = {}
    for name, fn in (
        ("max_sessions", _apply_max_sessions),
        ("rdp_timeouts", _apply_rdp_timeouts),
        ("oem_runtime_fixes", _apply_oem_runtime_fixes),
        ("multi_session", _apply_multi_session),
    ):
        try:
            run_apply_via_agent_or_freerdp(cfg, name, fn)
            results[name] = "ok"
        except Exception as e:  # noqa: BLE001
            results[name] = f"failed: {e}"
    return results


def _apply_oem_runtime_fixes(cfg: Config) -> None:
    """OEM v7 baseline (NIC power-save, TermService failure recovery) at runtime.

    install.bat only runs at dockur's unattended first boot, so existing
    0.1.6 / 0.1.7 / 0.1.8 / 0.1.9 / 0.1.9.x guests never picked up the v7
    fixes shipped after their initial install. This pushes them via
    FreeRDP RemoteApp so users don't have to recreate the container.

    Idempotent — Set-NetAdapterPowerManagement / sc.exe failure are
    no-ops when state already matches.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    # NIC power-save off is preventive for physical adapters; virtual NICs
    # (virtio, e1000) often don't expose the AllowComputerToTurnOffDevice
    # parameter at all (kernalix7 saw "A parameter cannot be found ...").
    # Two fixes: (1) the parameter expects the enum 'Disabled'/'Enabled',
    # not $true/$false, and (2) wrap in try/catch since virtual adapters
    # are common in our deployment and the cmdlet shape varies. Skip
    # silently when not supported — sc.exe TermService recovery is the
    # part that actually matters for the dockur VM.
    payload = (
        "$ErrorActionPreference = 'Continue'\n"
        "try {\n"
        "    Get-NetAdapter -ErrorAction Stop | "
        "Where-Object { $_.Status -ne 'Disabled' } | ForEach-Object {\n"
        "        try {\n"
        "            Set-NetAdapterPowerManagement -Name $_.Name "
        "-AllowComputerToTurnOffDevice 'Disabled' -ErrorAction Stop\n"
        "        } catch {\n"
        "            # Virtual NICs lack this parameter — that's fine.\n"
        "        }\n"
        "    }\n"
        "} catch {\n"
        "    # No NetAdapter module / API not available — skip preventive NIC fix.\n"
        "}\n"
        "& sc.exe failure TermService reset= 86400 "
        "actions= restart/5000/restart/5000/restart/5000 | Out-Null\n"
        "Write-Output 'oem v7 baseline applied'\n"
    )

    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(cfg, payload, description="apply-oem")
    except WindowsExecError as e:
        log.warning("oem_runtime_fixes: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("oem_runtime_fixes: rc=%d stderr=%s", result.rc, result.stderr.strip())
        raise RuntimeError(
            f"oem_runtime_fixes apply failed (rc={result.rc}): {result.stderr.strip()}"
        )
    log.info("oem_runtime_fixes: %s", result.stdout.strip())


def _apply_rdp_timeouts(cfg: Config) -> None:
    """Disable RDP idle/disconnect/connection timeouts + enable keep-alive.

    Without this Windows drops active RemoteApp sessions after the 1h
    default idle, and NAT/firewall idle-cleanup can kill the underlying
    TCP. Idempotent: ``Set-ItemProperty -Force`` with the same value is
    a no-op. Mirrors install.bat OEM v8 for guests provisioned under
    older OEM versions.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    payload = (
        # Machine policy (overrides per-user defaults).
        "$mp = 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\Terminal Services'\n"
        "if (-not (Test-Path $mp)) { New-Item -Path $mp -Force | Out-Null }\n"
        "Set-ItemProperty -Path $mp -Name MaxIdleTime -Value 0 -Type DWord -Force\n"
        "Set-ItemProperty -Path $mp -Name MaxDisconnectionTime -Value 30000 "
        "-Type DWord -Force\n"
        "Set-ItemProperty -Path $mp -Name MaxConnectionTime -Value 0 "
        "-Type DWord -Force\n"
        "Set-ItemProperty -Path $mp -Name KeepAliveEnable -Value 1 -Type DWord -Force\n"
        "Set-ItemProperty -Path $mp -Name KeepAliveInterval -Value 1 -Type DWord -Force\n"
        # Per-WinStation (TermService actually consults these).
        "$ws = 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Terminal Server\\"
        "WinStations\\RDP-Tcp'\n"
        "Set-ItemProperty -Path $ws -Name MaxIdleTime -Value 0 -Type DWord -Force\n"
        "Set-ItemProperty -Path $ws -Name MaxDisconnectionTime -Value 30000 "
        "-Type DWord -Force\n"
        "Set-ItemProperty -Path $ws -Name MaxConnectionTime -Value 0 "
        "-Type DWord -Force\n"
        "Set-ItemProperty -Path $ws -Name KeepAliveTimeout -Value 1 -Type DWord -Force\n"
        "Write-Output 'rdp_timeouts applied'\n"
    )

    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(cfg, payload, description="apply-rdp-timeouts")
    except WindowsExecError as e:
        log.warning("rdp_timeouts: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("rdp_timeouts: rc=%d stderr=%s", result.rc, result.stderr.strip())
        raise RuntimeError(f"rdp_timeouts apply failed (rc={result.rc}): {result.stderr.strip()}")
    log.info("rdp_timeouts: %s", result.stdout.strip())


def _auto_rotate_password(cfg: Config) -> Config:
    """Rotate RDP password if older than max_age."""
    if not cfg.rdp.password:
        return cfg

    if cfg.rdp.password_max_age <= 0:
        return cfg
    if cfg.pod.backend not in ("podman", "docker"):
        return cfg

    max_age_seconds = cfg.rdp.password_max_age * 86400

    # No timestamp means we cannot judge age, so skip rather than rotate silently.
    if not cfg.rdp.password_updated:
        return cfg

    try:
        updated = datetime.fromisoformat(cfg.rdp.password_updated)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated
        if age.total_seconds() < max_age_seconds:
            return cfg
    except (ValueError, TypeError) as e:
        log.warning("Invalid password_updated timestamp: %s", e)
        return cfg

    status = pod_status(cfg)
    if status.state != PodState.RUNNING:
        log.debug("Pod not running, skipping password rotation")
        return cfg

    log.info("Password older than %d days, rotating...", cfg.rdp.password_max_age)

    new_password = generate_password()
    old_password = cfg.rdp.password

    if not _change_windows_password(cfg, new_password):
        log.warning("Password rotation skipped: could not change Windows password")
        return cfg

    cfg.rdp.password = new_password
    cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()

    try:
        cfg.save()
        generate_compose(cfg)
        log.info("Password rotated successfully")
        _clear_rotation_pending()
    except OSError as e:
        # Config save failed but Windows already has the new password.
        cfg.rdp.password = old_password
        log.error("Failed to save config after rotation: %s", e)

        if _change_windows_password(cfg, old_password):
            log.warning("Password rotation rolled back after config save failure")
        else:
            # Worst case: config holds old password, Windows holds new.
            _mark_rotation_pending(old_password, new_password)
            log.error(
                "CRITICAL: password rotation partially applied. "
                "Windows now uses the new password, but it could not be "
                "saved to config and could not be reverted. RDP "
                "authentication will fail until you run "
                "`winpodx rotate-password` once the container is healthy."
            )

    return cfg


def _mark_rotation_pending(old_password: str, new_password: str) -> None:
    """Atomically write a 0o600 marker signalling a partial rotation."""
    import tempfile

    marker = _rotation_marker_path()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=marker.parent, prefix=".winpodx-rot-", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, b"pending\n")
            os.close(fd)
            os.rename(tmp_path, marker)
        except Exception:
            os.close(fd)
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as e:
        log.error("Failed to write rotation marker: %s", e)


def _clear_rotation_pending() -> None:
    marker = _rotation_marker_path()
    try:
        marker.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Could not remove rotation marker: %s", e)


def _check_rotation_pending() -> None:
    marker = _rotation_marker_path()
    if marker.exists():
        log.error(
            "Pending password rotation detected (%s). "
            "Run `winpodx rotate-password` once the container is "
            "running to bring config and Windows back in sync.",
            marker,
        )


def _ensure_config() -> Config:
    """Load config, or create a default one if none exists."""
    path = Config.path()
    if path.exists():
        return Config.load()

    log.info("No config found, creating default at %s", path)
    cfg = Config()
    cfg.rdp.user = "User"
    cfg.rdp.ip = "127.0.0.1"

    if shutil.which("podman"):
        cfg.pod.backend = "podman"
    elif shutil.which("docker"):
        cfg.pod.backend = "docker"
    elif shutil.which("virsh"):
        cfg.pod.backend = "libvirt"
    else:
        cfg.pod.backend = "podman"  # Default, will fail with clear error

    try:
        from winpodx.display.scaling import detect_scale_factor

        cfg.rdp.scale = detect_scale_factor()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass

    cfg.save()
    log.info("Default config created: backend=%s", cfg.pod.backend)
    return cfg


def _check_deps() -> None:
    """Check critical dependencies and raise if missing."""
    from winpodx.core.rdp import find_freerdp

    if find_freerdp() is None:
        raise ProvisionError(
            "FreeRDP 3+ not found.\n"
            "Install with: sudo zypper install freerdp\n"
            "Or: sudo apt install freerdp2-x11"
        )


def _ensure_compose(cfg: Config) -> None:
    """Generate compose.yaml if it doesn't exist."""
    compose_path = config_dir() / "compose.yaml"
    if compose_path.exists():
        return

    log.info("Generating compose.yaml")
    generate_compose(cfg)


def _ensure_pod_running(cfg: Config, timeout: int = 300) -> None:
    """Start the pod if not running, wait for RDP to be available."""
    if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=3):
        return

    status = pod_status(cfg)
    if status.state == PodState.STOPPED:
        log.info("Starting pod (backend: %s)", cfg.pod.backend)
        start_pod(cfg)

    log.info("Waiting for RDP at %s:%d ...", cfg.rdp.ip, cfg.rdp.port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=3):
            log.info("RDP is ready")
            return
        time.sleep(5)

    raise ProvisionError(
        f"Timeout ({timeout}s) waiting for RDP at "
        f"{cfg.rdp.ip}:{cfg.rdp.port}.\n"
        f"Troubleshooting:\n"
        f"  1. Check container: {cfg.pod.backend} logs {cfg.pod.container_name}\n"
        f"  2. Check status: winpodx pod status\n"
        f"  3. Common causes: out of disk, OOM, KVM not available"
    )


def _ensure_desktop_entries() -> None:
    """Register all app definitions as desktop entries if not already done."""
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.icons import install_winpodx_icon, update_icon_cache
    from winpodx.utils.paths import applications_dir

    install_winpodx_icon()

    apps = list_available_apps()
    app_dir = applications_dir()

    installed = False
    for app_info in apps:
        desktop_file = app_dir / f"winpodx-{app_info.name}.desktop"
        if not desktop_file.exists():
            install_desktop_entry(app_info)
            log.info("Registered desktop entry: %s", app_info.full_name)
            installed = True

    if installed:
        update_icon_cache()


def _auto_discover_if_empty(cfg: Config) -> None:
    """Fire `winpodx app refresh` once when the discovered tree is empty.

    v0.1.9 dropped the 14 bundled profiles, so on first pod boot the
    user's app menu is empty until discovery runs. We trigger it here —
    after the pod is reachable and TermService recovery has had a chance
    — so the menu populates without the user having to know about
    `winpodx app refresh`. Failure is non-fatal: the user-clicked app
    launch continues regardless and the next ensure_ready will retry.
    """
    try:
        from winpodx.core.app import discovered_apps_dir
        from winpodx.core.discovery import discover_apps, persist_discovered

        discovered_dir = discovered_apps_dir()
        if discovered_dir.exists() and any(discovered_dir.iterdir()):
            return  # already discovered before; user-triggered refresh stays in their hands.

        log.info("First boot detected; auto-running discovery to populate the app menu...")
        # v0.2.0.3: discovery uses the same FreeRDP RemoteApp channel as
        # the apply path; on first pod boot Windows VM may still be
        # booting inside QEMU even though ensure_ready already passed
        # check_rdp_port (port open != activation-ready). Wait for a
        # responsive guest before scanning, otherwise rc=147 connection
        # reset and the user's first install ends with an empty menu.
        if not wait_for_windows_responsive(cfg, timeout=180):
            log.info("Windows guest still booting; deferring auto-discovery to a later run.")
            return
        apps = discover_apps(cfg)
        persist_discovered(apps)
        log.info("Auto-discovery wrote %d app(s) to %s", len(apps), discovered_dir)
    except Exception as e:  # noqa: BLE001
        # Discovery failure must not block app launch. The user can retry
        # manually via `winpodx app refresh` or the GUI Refresh button.
        log.warning("Auto-discovery failed (non-fatal — run `winpodx app refresh` to retry): %s", e)


def terminate_tracked_sessions(timeout: float = 3.0) -> int:
    """Terminate all FreeRDP processes tracked via .cproc files."""
    import signal

    from winpodx.core.process import is_freerdp_pid, list_active_sessions

    sessions = list_active_sessions()
    signalled = 0
    for sess in sessions:
        if not is_freerdp_pid(sess.pid):
            continue
        try:
            os.kill(sess.pid, signal.SIGTERM)
            signalled += 1
        except (ProcessLookupError, PermissionError) as e:
            log.debug("Could not SIGTERM %s (pid %d): %s", sess.app_name, sess.pid, e)
            continue

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_freerdp_pid(sess.pid):
                break
            time.sleep(0.1)
        else:
            # Still alive; escalate to SIGKILL.
            try:
                os.kill(sess.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    return signalled
