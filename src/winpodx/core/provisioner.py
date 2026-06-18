# SPDX-License-Identifier: MIT
"""Auto-provisioning on first launch."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Callable

from winpodx.core.compose import generate_compose, generate_password
from winpodx.core.config import Config
from winpodx.core.pod import PodState, check_rdp_port, pod_status, start_pod

# Track A Sprint 1 Step 2: password rotation moved to winpodx.core.rotation.
# These re-exports preserve the public surface so existing imports
# (``from winpodx.core.provisioner import _change_windows_password``) and
# test patches (``monkeypatch.setattr(provisioner, "_check_rotation_pending",
# ...)``) keep working. The shim disappears in Step 6 (slim ensure_ready).
from winpodx.core.rotation import (  # noqa: F401  re-exports
    _ROTATION_PENDING_MARKER,
    _auto_rotate_password,
    _change_windows_password,
    _check_rotation_pending,
    _clear_rotation_pending,
    _mark_rotation_pending,
    _rotation_marker_path,
)
from winpodx.utils.paths import (  # noqa: F401  config_dir used by other helpers in this module
    bundle_dir,
    config_dir,
)

log = logging.getLogger(__name__)


def _apply_via_transport(
    cfg: Config,
    payload: str,
    *,
    description: str,
    timeout: int = 60,
    attempts: int = 2,
    backoff: float = 5.0,
):
    """Run a Windows-side apply payload through the best available transport.

    Picks AgentTransport when ``agent.ps1`` /health responds, falls back
    to FreerdpTransport otherwise. Returns a ``WindowsExecResult`` so
    existing callers (the ``_apply_*`` functions below) don't need to
    change their result handling.

    Maps ``TransportError`` to ``WindowsExecError`` for the same reason —
    the legacy callers' ``except WindowsExecError`` blocks keep working.

    Transient retry (``attempts`` × ``backoff`` s): a ``TransportError`` —
    a closed socket / a /health timeout — right after a container restart is
    common (the agent is "responsive but not yet stable" during the
    TermService cycle that rdprrap (re)activation triggers). 0.6.0 upgrade
    smoke hit exactly this: ``agent_keepalive`` failed with "Remote end closed
    connection without response" on the first try. We re-``dispatch`` each
    attempt so a recovered agent is re-picked (or a still-dead one falls to
    FreeRDP), and only surface the failure if every attempt errors. A
    ``rc != 0`` result is NOT a transport error — the payload ran and returned
    nonzero — so it is returned to the caller on the first try, unretried.
    """
    from winpodx.core.transport import TransportError, dispatch
    from winpodx.core.windows_exec import WindowsExecError, WindowsExecResult

    last_exc: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        # Re-probe transport selection each attempt: a retry after a transient
        # close should re-pick the (now-recovered) agent, or fall to FreeRDP.
        transport = dispatch(cfg)
        try:
            result = transport.exec(payload, timeout=timeout, description=description)
            return WindowsExecResult(rc=result.rc, stdout=result.stdout, stderr=result.stderr)
        except TransportError as e:
            last_exc = e
            if attempt < max(1, attempts):
                log.info(
                    "%s: transport attempt %d/%d failed (%s); retrying in %.0fs",
                    description,
                    attempt,
                    max(1, attempts),
                    e,
                    backoff,
                )
                time.sleep(backoff)
    raise WindowsExecError(str(last_exc)) from last_exc


class ProvisionError(Exception):
    """Raised when auto-provisioning fails."""


class ProvisionAgentUnavailable(ProvisionError):
    """Raised by ``finish_provisioning`` when ``require_agent=True`` but the
    in-guest agent's ``/health`` never comes up within the wait budget.

    Subclasses ``ProvisionError`` so callers that only catch the base type
    (and treat any provisioning failure as "defer to next launch") keep
    working, while migrate — the one caller that hard-gates on the agent —
    can distinguish "agent never settled" from a generic failure.
    """


def ensure_ready(cfg: Config | None = None, timeout: int = 300) -> Config:
    """Ensure everything is ready to launch a Windows app."""
    if cfg is None:
        cfg = _ensure_config()

    _check_rotation_pending()
    cfg = _auto_rotate_password(cfg)

    # v0.2.2 (post-rollback Sprint 3): self-heal removed.
    #
    # Previously this block re-applied 4 registry/service payloads
    # (max_sessions, rdp_timeouts, OEM, multi-session) via FreeRDP
    # RemoteApp on every ensure_ready, gated by a stamp that required
    # ALL FOUR to succeed. A single transient FreeRDP failure (rc=131
    # during install.bat's TermService restart, etc.) prevented the
    # stamp from being written and the host kept retrying on every app
    # launch — kernalix7 reported this as "PowerShell 창이 계속 깜빡거리는"
    # symptom on 2026-04-30.
    #
    # New rule: install.bat applies all OEM state at first boot; the
    # host does NOT redo that work on subsequent launches. If a user
    # upgrades the winpodx CLI without recreating the container, they
    # invoke `winpodx pod apply-fixes` (CLI) or click "Apply Windows
    # Fixes" (GUI Tools page) — both still call apply_windows_runtime_fixes
    # below, which surfaces per-step success/failure to the caller.
    if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=0.3):
        return cfg

    _check_deps()

    if cfg.pod.backend in ("podman", "docker"):
        _ensure_compose(cfg)

    from winpodx.core.daemon import ensure_pod_awake

    ensure_pod_awake(cfg)

    _ensure_pod_running(cfg, timeout)
    # Bug B: after host suspend / long idle the pod can be running but RDP
    # itself is dead while VNC is fine. Probe and try to revive TermService
    # before handing the cfg to the caller — the alternative is the FreeRDP
    # launch failing with a connection-refused that the user has to debug.
    #
    # Two-stage recovery (cheap before expensive):
    #   1. Agent-driven TermService cycle (try_recover_rdp). Costs ~5-30 s
    #      and keeps the container + Windows uptime; the right fix when the
    #      stall is just a wedged RDP listener.
    #   2. Whole-container restart via recover_rdp_if_needed. Costs ~30 s
    #      pod restart and resets Windows uptime; the fallback when the
    #      agent isn't reachable or stage 1 didn't bring RDP back.
    if not check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
        # Skip recovery while install.sh is mid-flight. The
        # ``[3/4]`` / ``[4/4]`` install phases legitimately have RDP
        # down for minutes while Sysprep / the OEM reboot pass runs;
        # restarting the container during that window blows away the
        # in-progress install.
        from winpodx.desktop.tray_spawn import _install_in_progress

        if _install_in_progress():
            log.info(
                "ensure_ready: install.sh in progress; skipping RDP recovery, "
                "letting wait-ready phases drive readiness."
            )
        else:
            from winpodx.core.pod import recover_rdp_if_needed
            from winpodx.core.pod.recovery import try_recover_rdp

            result = try_recover_rdp(cfg)
            log.info(
                "ensure_ready: agent recovery returned action=%s success=%s",
                result.action.value,
                result.success,
            )
            if not result.success:
                recover_rdp_if_needed(cfg)

    # Discovery is no longer auto-fired here (Step 3 of the redesign).
    # The "populate the menu on first boot" UX is owned by install.sh
    # (which runs `winpodx app refresh` post-install) and the GUI's
    # Refresh button — both call ``core.discovery.scan`` + ``persist``
    # explicitly. ``ensure_ready`` stays cheap and side-effect-free.
    _ensure_desktop_entries()

    return cfg


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

    from winpodx.core.windows_exec import WindowsExecError

    try:
        result = _apply_via_transport(cfg, payload, description="apply-max-sessions")
    except WindowsExecError as e:
        log.warning("max_sessions: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("max_sessions: rc=%d stderr=%s", result.rc, result.stderr.strip())
        raise RuntimeError(f"max_sessions apply failed (rc={result.rc}): {result.stderr.strip()}")
    log.info("max_sessions: %s", result.stdout.strip())


def wait_for_windows_responsive(cfg: Config, timeout: int = 300) -> bool:
    """Poll until the Windows guest is ready to accept commands.

    Two-stage readiness probe:

    Stage 1 (RDP port). The TermService listener comes up before user
    logon, so an open RDP port means Windows itself is alive (Sysprep
    done, kernel + services booted). Required.

    Stage 2 (agent /health). agent.ps1 binds 8765 only after the
    autologon User logs in and HKCU\\Run fires the wscript wrapper.
    Preferred — it tells the host that subsequent /exec calls will
    succeed without falling back to FreeRDP RemoteApp.

    The function used to require BOTH stages (no fallback). That
    deadlocked install.sh's wait-ready phase 3 when agent.ps1 didn't
    come up for any reason: HKCU\\Run mis-registered, autologon mid-
    cycle, agent token mismatch, port-mapping blip — kernalix7 sat at
    `[3/3] Waiting for Windows activation` for 30+ minutes 2026-05-02
    on a fresh install where the desktop was visible via VNC. Now:
    after RDP is open, we wait up to ``min(timeout, 60s)`` for /health
    to come up. If it does, return True with the agent path live. If
    it doesn't, return True anyway (Windows IS responsive — host code
    can fall back to FreeRDP RemoteApp via ``transport.dispatch``) and
    log a warning so apply-fixes / discovery surface what happened.
    Only return False if RDP itself never opens.
    """
    from winpodx.core.transport.agent import AgentTransport

    deadline = time.monotonic() + max(1, int(timeout))

    # Stage 1 — RDP port. Required.
    while time.monotonic() < deadline:
        if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
            break
        time.sleep(2)
    else:
        return False

    # Stage 2 — agent /health. Best-effort.
    #
    # Cap was 60s pre-v0.4.0; bumped to 180s after kernalix7's 2026-05-04
    # smoke tests on opensuse Tumbleweed showed install.bat OEM v22's
    # rdprrap-extract / installer / activate / launcher-stage / agent-
    # spawn chain regularly exceeded the old 60s budget on first boot
    # (especially on slower disks / colder caches), causing /health to
    # not-yet-answer when wait-ready phase 3 timed out. install.sh then
    # ran migrate, migrate fell back to FreerdpTransport via dispatch(),
    # FreeRDP opened a new RDP session, and that session kick killed
    # install.bat mid-stage -- because rdprrap wasn't loaded yet (its
    # ServiceDll patch isn't live until install.bat's tail TermService
    # cycle runs), so single-session enforcement was still in effect.
    #
    # Honor the caller's full timeout for the agent wait, not a hardcoded
    # 180s cap. On a slow first boot (cold cache, slow disk, a long ISO
    # download that pushes the whole install late) install.bat's rdprrap-
    # extract / installer / activate / launcher-stage / agent-spawn chain
    # plus autologon regularly exceed 180s before /health answers --
    # @kernalix7 saw exactly this on opensuse Tumbleweed (agent up, but only
    # after ~4min), which surfaced a scary "agent didn't answer within 180s"
    # WARN + made migrate/discovery skip prematurely. Callers that want a
    # generous wait already pass timeout=600 (10min); a genuinely broken
    # agent is still bounded by that, and we return True regardless so
    # callers proceed (migrate/discovery have their own agent gates +
    # FreeRDP fallback). The only hard requirement remains RDP (stage 1).
    transport = AgentTransport(cfg)
    while time.monotonic() < deadline:
        status = transport.health()
        if status.available:
            return True
        time.sleep(5)
    waited = int(timeout)
    log.warning(
        "wait_for_windows_responsive: RDP up but agent /health didn't "
        "answer within %ds; proceeding without agent. Migrate's apply "
        "chain will skip via its own agent gate; check "
        "C:\\winpodx\\setup.log via VNC if /health remains down.",
        waited,
    )
    return True


def _wait_for_agent_ready(
    cfg: Config,
    transport,
    *,
    need_consecutive: int = 1,
    poll: float = 2.0,
    on_progress: Callable[[str, str], None] | None = None,
    stage: str = "agent",
) -> bool:
    """Block until the guest agent ``/health`` is (stably) up, gated on pod liveness.

    Returns ``True`` once ``/health`` answers ``need_consecutive`` times in a
    row. Returns ``False`` only when the pod is no longer ``RUNNING`` — i.e.
    recovery is genuinely impossible, because without a running pod the
    in-guest ``WinpodxAgentKeepAlive`` watchdog can't relaunch a dead agent.

    There is deliberately **no time cap**. As long as the pod runs, the
    keepalive task relaunches a crashed agent every ~60 s, so we wait for it
    rather than giving up on an arbitrary timer — the wait is bounded by a
    *real signal* (pod liveness), the same philosophy as the wget-ETA dynamic
    deadline in ``pod wait-ready`` (#126). A slow or flapping agent on a live
    pod is something to wait out, not a failure; only a stopped pod ends it.
    """
    consecutive = 0
    waited = 0.0
    while consecutive < need_consecutive:
        if pod_status(cfg).state != PodState.RUNNING:
            return False
        if transport.health().available:
            consecutive += 1
            if consecutive >= need_consecutive:
                return True
        else:
            consecutive = 0
            if on_progress is not None:
                on_progress(
                    stage,
                    f"guest agent not up yet ({int(waited)}s); pod is running, "
                    "waiting for the keepalive watchdog to revive it…",
                )
            log.info(
                "%s: agent /health down but pod running (%ds); waiting for keepalive revival",
                stage,
                int(waited),
            )
        time.sleep(poll)
        waited += poll
    return True


def finish_provisioning(
    cfg: Config,
    *,
    wait_timeout: int = 3600,
    require_agent: bool = False,
    with_reverse_open: bool = True,
    with_discovery: bool = True,
    retries: int = 2,
    on_progress: Callable[[str, str], None] | None = None,
    wait_fn: Callable[[Config, int], bool] | None = None,
) -> dict[str, Any]:
    """Run the post-pod-running provisioning chain in one place (0.6.0 item B).

    This is the single source of truth for the ``wait-ready → agent-settle →
    apply-fixes → discovery → reverse-open`` sequence that previously lived,
    with subtly diverging ordering and gating, in ``install.sh``,
    ``setup_cmd._run_full_provision``, ``migrate``, and ``pending.resume``.
    It sits AFTER ``ensure_ready`` (which owns compose generation + pod
    bring-up + RDP recovery); ``finish_provisioning`` only consolidates the
    work each of those callers did once the pod was already running.

    Every stage is parameter-gated so the four callers can request exactly
    the behaviour they had before:

    * ``wait_timeout`` — seconds for ``wait_for_windows_responsive``.
      install.sh / setup_cmd use 3600; pending.resume uses 300.
    * ``require_agent`` — when True, hard-gate on the agent ``/health`` and
      raise :class:`ProvisionAgentUnavailable` if it never answers (migrate's
      behaviour — it refuses to fall back to FreeRDP RemoteApp because a
      fresh-boot FreeRDP connect can kick install.bat's autologon session).
      When False, do a best-effort settle poll (install.sh's behaviour:
      30 attempts × 2 s) and proceed silently regardless — this lets
      setup_cmd's auto-provision use the chain without crashing on a slow
      first boot.
    * ``with_discovery`` — run the shared discovery path (discover_apps +
      persist_discovered + _register_desktop_entries) with ``retries``
      attempts and exponential backoff. Defaults to 2 (0.6.0 item M): the
      agent keep-alive (#359) keeps the guest agent reliably up, so the old
      fixed 6× loop install.sh used is overkill — 1-2 attempts suffice and
      a fresh install finishes faster.
    * ``with_reverse_open`` — run the host-open listener-start + manifest
      refresh, gated additionally on ``cfg.reverse_open.enabled``.
    * ``on_progress(stage, detail)`` — optional callback so a GUI can mirror
      the same stages onto a progress bar without re-running the chain.
    * ``wait_fn(cfg, timeout) -> bool`` — optional override for the wait-ready
      stage. ``None`` (migrate / pending / GUI) runs the silent
      :func:`wait_for_windows_responsive`. The CLI ``provision`` command and
      the interactive setup wizard inject the rich log-streaming wait
      (``cli/pod._wait_ready`` — the ``[1/4]…[4/4]`` checkpoints, the
      self-erasing download/boot line, AND the wget-ETA dynamic deadline
      extension for slow links, #126) so a fresh install isn't a silent
      multi-minute hang. Injection (not import) keeps ``core`` cli-free. Must
      return True when the guest is ready, False on timeout/failure.

    When ``require_agent`` is True the function also exports
    ``WINPODX_REQUIRE_AGENT=1`` for the duration of the apply-fixes + discovery
    stages, so the env-honouring guest-side callers (``core.discovery``,
    ``migrate``'s apply transport) refuse the FreeRDP RemoteApp fallback and
    raise ``agent_unavailable`` rather than racing FreeRDP into install.bat's
    autologon session (#271 / agent-first install). Without that propagation
    the ``require_agent`` flag would gate only the one-shot settle re-probe and
    discovery would still fall back to FreeRDP — the regression that shipped in
    the first cut of item B.

    Returns a results dict whose keys are the stage names and whose values
    are short human-readable status strings, so callers can log / surface
    them:
    ``{"wait_ready": "ok", "agent_settle": "ok"|"skipped"|"not-up",
    "apply_fixes": {...per-helper...}, "discovery": "12 apps"|"skipped",
    "reverse_open": "ok"|"skipped"|"failed: ..."}``.
    """
    results: dict[str, Any] = {}

    def _progress(stage: str, detail: str) -> None:
        if on_progress is not None:
            try:
                on_progress(stage, detail)
            except Exception:  # noqa: BLE001 — progress is best-effort, never fatal
                log.debug("finish_provisioning on_progress(%s) raised", stage, exc_info=True)

    # Backend gate: the manual backend has no Windows-side runtime apply,
    # discovery channel, or reverse-open. Return early with a marker so the
    # caller knows nothing was attempted (mirrors apply_windows_runtime_fixes).
    if cfg.pod.backend not in ("podman", "docker"):
        results["backend"] = f"skipped (backend={cfg.pod.backend} not supported)"
        _progress("backend", results["backend"])
        return results

    # --- Stage 1: wait-ready ------------------------------------------------
    _progress("wait_ready", f"up to {wait_timeout}s")
    if wait_fn is not None:
        # Rich (log-streaming) wait supplied by the caller — it prints its own
        # [1/4]..[4/4] + live progress line, so we don't echo a redundant
        # completion detail.
        ready = wait_fn(cfg, wait_timeout)
    else:
        ready = wait_for_windows_responsive(cfg, timeout=wait_timeout)
    results["wait_ready"] = "ok" if ready else "timeout"
    if wait_fn is None:
        _progress("wait_ready", results["wait_ready"])
    if not ready:
        # RDP never opened. Nothing downstream can succeed; let the caller
        # decide whether to defer (pending machinery) or surface the timeout.
        return results

    # --- Stage 2: agent settle ---------------------------------------------
    from winpodx.core.transport.agent import AgentTransport

    transport = AgentTransport(cfg)
    if require_agent:
        # Hard gate (migrate's behaviour). ``wait_for_windows_responsive``
        # already waited up to ``wait_timeout`` for /health and returned True
        # regardless. But a *single* OK probe can be the agent momentarily up
        # mid-TermService-cycle (rdprrap (re)activation restarts the service);
        # it dies again right after, and the apply burst then hits a closed
        # socket — the "agent_keepalive: Remote end closed connection" seen
        # during 0.6.0 upgrade smoke. Require a few CONSECUTIVE OK probes so we
        # proceed only once the agent has actually stabilised, not on the first
        # flicker. (The per-apply transient retry in _apply_via_transport is
        # the second layer of defence for a stall that lands after this gate.)
        #
        # Wait with NO time cap — gated only on pod liveness — for 3 consecutive
        # /health OK. We do NOT give up on a timer: as long as the pod runs the
        # keepalive watchdog will bring the agent back, so settling "slowly but
        # reliably" beats deferring the whole install on an arbitrary deadline.
        # Only a stopped pod (no watchdog) ends the wait. See _wait_for_agent_ready.
        if _wait_for_agent_ready(
            cfg,
            transport,
            need_consecutive=3,
            poll=2.0,
            on_progress=on_progress,
            stage="agent_settle",
        ):
            results["agent_settle"] = "ok"
            _progress("agent_settle", "ok (stable)")
        else:
            results["agent_settle"] = "not-up"
            _progress("agent_settle", "pod stopped before the agent came up")
            raise ProvisionAgentUnavailable(
                "Guest agent /health never came up and the pod is no longer "
                "running, so the keepalive watchdog can't revive it. Check "
                "`winpodx pod status` and C:\\winpodx\\setup.log via VNC."
            )
    else:
        # Soft settle poll (install.sh's behaviour): 30 attempts × 2 s. Silent
        # if it never lands — apply-fixes / discovery have their own agent
        # gates + FreeRDP fallback, so we proceed either way.
        _progress("agent_settle", "best-effort poll (up to 60s)")
        settled = False
        for _ in range(30):
            if transport.health().available:
                settled = True
                break
            time.sleep(2)
        results["agent_settle"] = "ok" if settled else "not-up (proceeding)"
        _progress("agent_settle", results["agent_settle"])

    # When the caller demands agent-first, export WINPODX_REQUIRE_AGENT for
    # the apply + discovery stages so the env-honouring guest-side code
    # (core.discovery, migrate's apply transport) refuses the FreeRDP fallback
    # and defers instead of racing FreeRDP into install.bat's autologon
    # session (#271). Saved + restored so we don't leak the override into the
    # caller's environment. ``require_agent=False`` leaves the env untouched —
    # FreeRDP fallback stays allowed (install.sh's old soft behaviour).
    _prev_require_agent = os.environ.get("WINPODX_REQUIRE_AGENT")
    if require_agent:
        os.environ["WINPODX_REQUIRE_AGENT"] = "1"
    try:
        # --- Stage 3: apply-fixes ------------------------------------------
        # No gate — apply_windows_runtime_fixes is idempotent and surfaces its
        # own per-helper success/failure map.
        _progress("apply_fixes", "applying Windows-side runtime fixes")
        apply_results = apply_windows_runtime_fixes(cfg)
        results["apply_fixes"] = apply_results
        _progress("apply_fixes", ", ".join(f"{k}: {v}" for k, v in apply_results.items()))

        # --- Stage 4: discovery --------------------------------------------
        if with_discovery:
            _progress("discovery", f"scanning guest (up to {retries} attempts)")
            try:
                count = _run_discovery_with_retry(
                    cfg, retries=retries, require_agent=require_agent, on_progress=_progress
                )
                results["discovery"] = f"{count} apps"
                _progress("discovery", results["discovery"])
            except ProvisionAgentUnavailable:
                # require_agent + agent never came up for discovery: defer
                # cleanly (don't record as a generic failure). The caller maps
                # this to the pending machinery / exit 5, matching install.sh's
                # old WINPODX_REQUIRE_AGENT=1 -> "deferred" behaviour (#271).
                results["discovery"] = "deferred (agent not up)"
                _progress("discovery", results["discovery"])
                raise
            except Exception as e:  # noqa: BLE001 — best-effort; pending machinery is the net
                results["discovery"] = f"failed: {e}"
                _progress("discovery", results["discovery"])
        else:
            results["discovery"] = "skipped"
            _progress("discovery", "skipped")
    finally:
        # Restore the caller's WINPODX_REQUIRE_AGENT (unset if it wasn't set).
        if _prev_require_agent is None:
            os.environ.pop("WINPODX_REQUIRE_AGENT", None)
        else:
            os.environ["WINPODX_REQUIRE_AGENT"] = _prev_require_agent

    # --- Stage 5: reverse-open ---------------------------------------------
    if with_reverse_open and getattr(cfg.reverse_open, "enabled", False):
        _progress("reverse_open", "starting listener + pushing manifest")
        try:
            _run_reverse_open(cfg)
            results["reverse_open"] = "ok"
        except Exception as e:  # noqa: BLE001 — best-effort
            results["reverse_open"] = f"failed: {e}"
        _progress("reverse_open", results["reverse_open"])
    else:
        results["reverse_open"] = "skipped"
        _progress("reverse_open", "skipped")

    return results


def _run_discovery_with_retry(
    cfg: Config,
    *,
    retries: int,
    require_agent: bool = False,
    on_progress: Callable[[str, str], None] | None = None,
) -> int:
    """Shared discovery path: discover_apps → persist_discovered → register.

    This is the ONE discovery entrypoint the unified chain uses (0.6.0 item
    B). It is the more-complete of the two historical forms: it not only
    scans + persists but also installs the XDG .desktop entries AND prunes
    stale ones + refreshes the icon cache (via ``cli.app._register_desktop_
    entries``). install.sh's old ``app refresh`` and pending.resume's
    ``discover_apps + persist + register`` both collapse onto this.

    Retries up to ``retries`` attempts with exponential backoff (2, 4, 8…s,
    capped at 30 s) to ride out the agent's transient "responsive but not
    yet stable" window right after install.bat's final TermService cycle —
    install.sh used a fixed 6× / 10 s loop; the backoff here is gentler at
    the start and bounded above.

    ``require_agent`` mirrors the chain-level flag: with ``WINPODX_REQUIRE_AGENT
    =1`` exported (which ``finish_provisioning`` does for the agent-first
    install path), ``discover_apps`` raises ``DiscoveryError(kind="agent_
    unavailable")`` instead of falling back to FreeRDP. When every attempt ends
    that way AND ``require_agent`` is set, we raise :class:`ProvisionAgent
    Unavailable` so the caller defers cleanly to the pending machinery (#271)
    rather than surfacing a generic discovery failure. Any other error (or the
    non-require-agent case) raises the last exception as before.

    The ``cli.app`` imports are lazy + local: discovery wiring (persist +
    desktop-entry registration) lives on the CLI side historically, and a
    module-level import here would invert the core→cli layering. The same
    lazy-import pattern is used by ``utils.pending.resume``.
    """
    from winpodx.cli.app import _register_desktop_entries
    from winpodx.core.discovery import DiscoveryError, discover_apps, persist_discovered
    from winpodx.core.transport.agent import AgentTransport

    transport = AgentTransport(cfg)
    last_exc: Exception | None = None
    attempt = 0
    while True:
        try:
            apps = discover_apps(cfg, timeout=180)
            persist_discovered(apps)
            if apps:
                _register_desktop_entries(apps)
            return len(apps)
        except Exception as e:  # noqa: BLE001 — retry on any discovery failure
            last_exc = e
            agent_unavailable = (
                isinstance(e, DiscoveryError) and getattr(e, "kind", "") == "agent_unavailable"
            )
            # Agent-first discovery, agent not up yet: this isn't a failure to
            # retry-then-give-up on — it's a "wait for the agent". Block (no
            # time cap, pod-gated) until the keepalive watchdog revives it, then
            # retry discovery. This does NOT consume the bounded retry budget;
            # we only fall through to defer if the POD stops (recovery
            # impossible). "Slow but reliable" — never punt a fresh install to
            # `app refresh` just because the agent lagged a minute.
            if require_agent and agent_unavailable:
                if _wait_for_agent_ready(
                    cfg,
                    transport,
                    need_consecutive=1,
                    poll=10.0,
                    on_progress=on_progress,
                    stage="discovery",
                ):
                    continue  # agent answered — retry discovery (not a spent attempt)
                break  # pod stopped — can't recover here; defer below
            # Any other (non-agent) transient error: bounded exponential backoff.
            attempt += 1
            if attempt < max(1, retries):
                backoff = min(30, 2**attempt)
                if on_progress is not None:
                    on_progress(
                        "discovery",
                        f"attempt {attempt} deferred ({e}); retrying in {backoff}s",
                    )
                log.info(
                    "discovery attempt %d deferred (%s); retrying in %ds",
                    attempt,
                    e,
                    backoff,
                )
                time.sleep(backoff)
                continue
            break
    assert last_exc is not None  # loop always sets it before falling through
    # Agent-first install: a persistent agent_unavailable is a "defer", not a
    # hard failure — let the caller route it to the pending machinery (#271).
    if (
        require_agent
        and isinstance(last_exc, DiscoveryError)
        and getattr(last_exc, "kind", "") == "agent_unavailable"
    ):
        raise ProvisionAgentUnavailable(
            f"discovery requires the guest agent (WINPODX_REQUIRE_AGENT=1) but it "
            f"never came up after {retries} attempts: {last_exc}"
        ) from last_exc
    raise last_exc


def _run_reverse_open(cfg: Config) -> None:
    """Shared reverse-open path: start the listener, then push the manifest.

    Mirrors install.sh's ``host-open start-listener`` + ``host-open refresh``
    and setup_cmd's ``_cmd_refresh`` call. Both CLI handlers take an
    ``argparse.Namespace``; we build the minimal shapes they read. Imports
    are lazy + local for the same core→cli layering reason as discovery.

    The listener start is best-effort (it activates on the next ``pod
    start`` if it doesn't come up now); a refresh failure propagates so the
    caller records ``reverse_open: failed``.
    """
    import argparse

    from winpodx.cli.host_open import _cmd_refresh, _cmd_start_listener

    try:
        _cmd_start_listener(argparse.Namespace(json=False))
    except Exception as e:  # noqa: BLE001 — listener is best-effort
        log.warning("reverse-open listener did not start: %s", e)

    _cmd_refresh(argparse.Namespace(include_nodisplay=False, skip_icons=False, json=False))


def _apply_multi_session(cfg: Config) -> None:
    """Ensure rdprrap multi-session is enabled — auto-activate if not.

    Multi-session is core winpodx functionality (the dialog "Select a
    session to reconnect to" appears on every multi-app launch without
    it). Running it as a probe-only step (PR #77) was a safety measure
    against the inline-activation hang that mid-apply rdprrap-conf
    --enable caused: TermService restart killed the agent's session
    while the host was still awaiting /exec, /exec timed out, and the
    pod needed a full restart to recover.

    PR #80 made activation safe at runtime by spawning rdprrap-
    activate.ps1 *detached* via wscript+hidden-launcher.vbs. The /exec
    response returns before TermService cycles, so the host never
    blocks on a dying agent. Combined with the .activation_status
    marker for idempotency — already-enabled pods become a no-op,
    no disruption — this step now self-heals: if marker says enabled,
    return; otherwise queue the detached activator.

    Cost: when activation is needed, the user's RDP sessions briefly
    disconnect (~10 s) while TermService cycles. After reconnect, the
    marker reads ``enabled`` and subsequent applies are no-ops. This
    is the same one-time cost the OEM-time path pays, just deferred
    to migration time for users on pre-OEM-v15 builds.

    Depends on the vbs_launchers step running first (which stages
    rdprrap-activate.ps1 + hidden-launcher.vbs into Public dir).
    apply_windows_runtime_fixes orders the chain accordingly.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    target_dir = "C:\\Users\\Public\\winpodx\\launchers"
    activate_ps1 = f"{target_dir}\\rdprrap-activate.ps1"
    hidden_vbs = f"{target_dir}\\hidden-launcher.vbs"

    payload_lines = [
        '$marker = "C:\\winpodx\\rdprrap\\.activation_status"',
        '$logPath = "C:\\winpodx\\rdprrap\\install.log"',
        f'$activate = "{activate_ps1}"',
        f'$hidden = "{hidden_vbs}"',
        # Idempotent: if the marker says enabled, skip everything. The
        # vast majority of apply-fixes calls hit this path — running on
        # an already-healthy pod produces no /exec round-trips beyond
        # the one we're already in, no disconnect, no churn.
        "if (Test-Path -LiteralPath $marker) {",
        "    $status = Get-Content -LiteralPath $marker -ErrorAction SilentlyContinue"
        " | Select-Object -First 1",
        "    if ($status -eq 'enabled') {",
        "        Write-Output 'rdprrap status: enabled (no-op)'",
        "        exit 0",
        "    }",
        "}",
        # Belt-and-suspenders: even when the marker says non-enabled
        # (or is missing), check ServiceDll directly. If TermService is
        # already pointing at termwrap.dll, rdprrap is patched and
        # multi-session is live — reactivating would needlessly cycle
        # TermService, kill the agent's RDP session, and leave the agent
        # dead until the user opens an app to refire HKCU\Run. Stamp the
        # marker so subsequent apply-fixes calls hit the fast path
        # above without re-checking the registry every time.
        # (kernalix7 hit this 2026-05-02: marker = installer-failed
        # from a partial OEM-time apply, but ServiceDll had successfully
        # been patched at OEM time and multi-session worked. apply-fixes
        # fired a redundant activation, killed the agent, and the agent
        # stayed dead because the user wasn't connecting to apps.)
        "$svcDll = (Get-ItemProperty"
        " -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\TermService\\Parameters'"
        " -Name ServiceDll -ErrorAction SilentlyContinue).ServiceDll",
        "if ($svcDll -match 'termwrap') {",
        "    Set-Content -LiteralPath $marker -Value 'enabled' -Force"
        " -ErrorAction SilentlyContinue",
        '    Write-Output ("rdprrap status: enabled'
        " (ServiceDll=$svcDll; marker reconciled to 'enabled' from previous state)\")",
        "    exit 0",
        "}",
        # Need activation. Confirm the activator + VBS wrapper are staged.
        # vbs_launchers (which runs before this step in the apply chain)
        # pushes both. If they're missing, the user is on a pod older
        # than OEM v17 AND skipped vbs_launchers — surface that clearly
        # rather than silently failing.
        "if (-not (Test-Path -LiteralPath $activate)) {",
        "    Write-Output 'rdprrap-activate.ps1 not staged"
        " (vbs_launchers must run first); skipping activation'",
        "    exit 0",
        "}",
        "if (-not (Test-Path -LiteralPath $hidden)) {",
        "    Write-Output 'hidden-launcher.vbs not staged"
        " (vbs_launchers must run first); skipping activation'",
        "    exit 0",
        "}",
        # Spawn rdprrap-activate.ps1 detached via wscript so this /exec
        # response returns before TermService cycle kills the agent's
        # user session. -Detached makes the script wait 2s (giving us
        # time to land the response at the host) before doing the
        # installer + service restart work. After completion the
        # marker flips to 'enabled'; subsequent apply-fixes calls are
        # no-ops via the marker check above.
        "$startArgs = @($hidden, 'powershell.exe', '-NoProfile',",
        "         '-ExecutionPolicy', 'Bypass', '-File', $activate, '-Detached')",
        "Start-Process wscript.exe -ArgumentList $startArgs | Out-Null",
        "$prev = if (Test-Path -LiteralPath $marker) {",
        "    Get-Content -LiteralPath $marker -ErrorAction SilentlyContinue"
        " | Select-Object -First 1",
        "} else { 'never activated' }",
        'Write-Output ("rdprrap status: $prev -> activation queued")',
        "Write-Output 'note: RDP sessions will briefly disconnect (~10s)"
        " while TermService restarts. Reconnect to restore.'",
        # On non-enabled states with an existing log, tail it so the
        # apply-fixes output has root-cause context for the previous
        # failure (so users can compare before/after activation).
        "if (Test-Path -LiteralPath $logPath) {",
        "    Write-Output ''",
        "    Write-Output '--- install.log tail (pre-activation) ---'",
        "    Get-Content -LiteralPath $logPath -Tail 20 -ErrorAction SilentlyContinue",
        "    Write-Output '--- end install.log ---'",
        "}",
        "exit 0",
    ]
    payload = "\n".join(payload_lines) + "\n"

    from winpodx.core.windows_exec import WindowsExecError

    try:
        result = _apply_via_transport(cfg, payload, description="probe-multi-session")
    except WindowsExecError as e:
        log.warning("multi_session: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("multi_session: rc=%d stderr=%s", result.rc, result.stderr.strip())
        return
    log.info("multi_session: %s", result.stdout.strip())


def apply_windows_runtime_fixes(cfg: Config) -> dict[str, str]:
    """Public entry point: run all idempotent Windows-side runtime applies.

    Used by the standalone ``winpodx pod apply-fixes`` CLI command, the
    GUI Tools-page button, and v0.1.9.3+ migrate (which always invokes
    this regardless of version comparison so users on a "already current"
    marker still receive fixes that landed in patch releases).

    Returns a per-helper result map: ``{helper_name: "ok" | "failed: ..."}``
    so the caller can render success/failure rows. Backend gating returns
    ``{"backend": "skipped (manual not supported)"}`` so the caller
    knows nothing was attempted.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return {"backend": f"skipped (backend={cfg.pod.backend} not supported)"}

    results: dict[str, str] = {}
    # Order matters: vbs_launchers stages rdprrap-activate.ps1 +
    # hidden-launcher.vbs that multi_session needs to spawn detached
    # activation. multi_session is a no-op when rdprrap is already
    # enabled (idempotent via marker), so the only-on-first-migration
    # disconnect cost is paid exactly once per pod.
    # agent_keepalive runs LAST, after multi_session. multi_session may
    # queue a detached rdprrap activation that cycles TermService and
    # transiently drops the agent's session; registering (and kicking)
    # the keep-alive task afterwards means its 1-minute repetition is the
    # backstop that brings the agent back once that cycle settles.
    for name, fn in (
        ("max_sessions", _apply_max_sessions),
        ("rdp_timeouts", _apply_rdp_timeouts),
        ("oem_runtime_fixes", _apply_oem_runtime_fixes),
        ("vbs_launchers", _apply_vbs_launchers),
        ("multi_session", _apply_multi_session),
        ("agent_keepalive", _apply_agent_keepalive),
        # media_monitor LAST: it reuses the hidden-launcher.vbs that
        # vbs_launchers stages, and it's a non-critical convenience (USB
        # drive-letter mapping) so a failure here shouldn't precede the
        # session-keeping fixes above.
        ("media_monitor", _apply_media_monitor),
    ):
        try:
            fn(cfg)
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

    from winpodx.core.windows_exec import WindowsExecError

    try:
        result = _apply_via_transport(cfg, payload, description="apply-oem")
    except WindowsExecError as e:
        log.warning("oem_runtime_fixes: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("oem_runtime_fixes: rc=%d stderr=%s", result.rc, result.stderr.strip())
        raise RuntimeError(
            f"oem_runtime_fixes apply failed (rc={result.rc}): {result.stderr.strip()}"
        )
    log.info("oem_runtime_fixes: %s", result.stdout.strip())


def _apply_vbs_launchers(cfg: Config) -> None:
    """Push hidden-launcher.vbs / launch_uwp.{vbs,ps1} / agent-respawn.ps1
    + update HKCU\\Run + auto-respawn the running agent under the new
    wrapper so existing pods stop flashing PowerShell windows on agent
    autostart and UWP launches — without needing a user logout or pod
    restart.

    Migration path for users on v0.3.0-RTM1 / OEM v12 / v13. Fresh installs
    from OEM v14+ already have the files staged via install.bat; this step
    is then a re-write + re-respawn no-op. Targets
    ``C:\\Users\\Public\\winpodx\\launchers\\`` because Public is
    universally writable for Authenticated Users (the agent runs as User
    and can't write to ``C:\\OEM\\``, which is SYSTEM-owned).

    The respawn fires as a detached wscript invocation at the end of the
    payload. ``agent-respawn.ps1`` waits ~3s (giving this /exec response
    time to land), kills the old agent process, waits for port 8765 to
    free, then starts a fresh agent under wscript+hidden-launcher.vbs.

    Idempotent — re-running rewrites files, refreshes the registry
    value, and triggers another respawn cycle. UWP launch path is picked
    up immediately by the host's next ``rdp.build_rdp_command`` call.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    oem_root = bundle_dir() / "config" / "oem"
    files = (
        "hidden-launcher.vbs",
        "launch_uwp.vbs",
        "launch_uwp.ps1",
        "agent-respawn.ps1",
        # agent-keepalive.ps1 is staged here so the keep-alive scheduled
        # task (_apply_agent_keepalive, the chain step after this one) can
        # point at it on existing pods without a container recreate. The
        # task copies it to C:\winpodx for the persistent run location.
        "agent-keepalive.ps1",
        # rdprrap-activate.ps1 is staged here so `winpodx pod multi-
        # session enable` can activate rdprrap on existing pods without
        # forcing a container recreate. See cli.pod._multi_session.
        "rdprrap-activate.ps1",
    )
    sources: dict[str, str] = {}
    for fname in files:
        path = oem_root / fname
        if not path.is_file():
            raise RuntimeError(f"vbs_launchers source missing: {path}")
        try:
            sources[fname] = path.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"cannot read {path}: {e}") from e

    # Build a single PS payload that writes all three files + updates
    # HKCU\Run in one /exec round-trip. Each file body is base64-encoded
    # in transit so embedded quotes / newlines / unicode survive the
    # PowerShell here-string boundary cleanly.
    import base64 as _b64

    target_dir = "C:\\Users\\Public\\winpodx\\launchers"
    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$dir = '{target_dir}'",
        "if (-not (Test-Path $dir)) { [void](New-Item -ItemType Directory -Force -Path $dir) }",
    ]
    for fname, body in sources.items():
        b64 = _b64.b64encode(body.encode("utf-8")).decode("ascii")
        target = f"{target_dir}\\{fname}"
        lines.append(f"$bytes = [Convert]::FromBase64String('{b64}')")
        lines.append(f"[IO.File]::WriteAllBytes('{target}', $bytes)")
    # HKCU\Run\WinpodxAgent — point at the new VBS launcher so the next
    # user session logon stops flashing a PS console.
    reg_value = (
        f'wscript.exe "{target_dir}\\hidden-launcher.vbs" '
        '"powershell.exe" "-NoProfile" "-ExecutionPolicy" "Bypass" '
        '"-File" "C:\\OEM\\agent.ps1"'
    ).replace("'", "''")
    # HKCU\Run\WinpodxMedia — same wrapper-fix as WinpodxAgent. Pre-OEM-
    # v19 install.bat registered media_monitor.ps1 with bare
    # `-WindowStyle Hidden`; under multi-session each new RDP logon re-
    # fires HKCU\Run, briefly allocating a console for every app launch
    # (kernalix7 reported 2026-05-02: "검정 콘솔이 잠깐 뜨고 글씨는 안보여 ...
    # 앱 실행하고 나면" — the conhost flash before SW_HIDE applies).
    media_reg_value = (
        f'wscript.exe "{target_dir}\\hidden-launcher.vbs" '
        '"powershell.exe" "-NoProfile" "-ExecutionPolicy" "Bypass" '
        '"-File" "C:\\winpodx\\media_monitor.ps1"'
    ).replace("'", "''")
    lines.extend(
        [
            "$runKey = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'",
            "if (-not (Test-Path $runKey)) { [void](New-Item -Path $runKey -Force) }",
            f"Set-ItemProperty -Path $runKey -Name 'WinpodxAgent' -Value '{reg_value}'",
            # Only rewrite WinpodxMedia if the old (unwrapped) entry exists —
            # avoids creating a stale entry on pods where install.bat skipped
            # the media_monitor staging (warned + omitted the reg add).
            "$cur = (Get-ItemProperty -Path $runKey -Name 'WinpodxMedia' "
            "-ErrorAction SilentlyContinue).WinpodxMedia",
            "if ($cur) {",
            f"    Set-ItemProperty -Path $runKey -Name 'WinpodxMedia' -Value '{media_reg_value}'",
            "}",
        ]
    )
    # Auto-respawn the running agent under the new wscript wrapper so the
    # autostart-flash fix takes effect without requiring a user logout or
    # `winpodx pod restart`. The respawn script waits ~3s before killing
    # the old agent — long enough for THIS /exec response to land at the
    # host. Spawned hidden via wscript+hidden-launcher.vbs.
    respawn_args_ps = (
        "@(",
        f"        '{target_dir}\\hidden-launcher.vbs',",
        "        'powershell.exe',",
        "        '-NoProfile',",
        "        '-ExecutionPolicy', 'Bypass',",
        f"        '-File', '{target_dir}\\agent-respawn.ps1'",
        "    )",
    )
    lines.extend(
        [
            "$respawnArgs = " + "\n    ".join(respawn_args_ps),
            "Start-Process wscript.exe -ArgumentList $respawnArgs | Out-Null",
            "Write-Output 'vbs_launchers applied + agent respawn queued'",
        ]
    )
    payload = "\n".join(lines) + "\n"

    from winpodx.core.windows_exec import WindowsExecError

    try:
        result = _apply_via_transport(cfg, payload, description="apply-vbs-launchers")
    except WindowsExecError as e:
        raise RuntimeError(f"vbs_launchers apply failed: {e}") from e
    if result.rc != 0:
        raise RuntimeError(f"vbs_launchers apply failed (rc={result.rc}): {result.stderr.strip()}")
    log.info("vbs_launchers: %s", result.stdout.strip())


def _apply_media_monitor(cfg: Config) -> None:
    """Deliver media_monitor.ps1 to the guest + register/start it, via the agent.

    media_monitor maps host USB volumes (``\\tsclient\\media\\<LABEL>``) to
    guest drive letters. It is delivered at RUNTIME here -- never via
    install.bat / the OEM bundle -- because adding a file to ``C:\\OEM``
    re-triggers the intermittent Defender/rdprrap first-boot deadlock that
    hangs the install (#613/#638). This pushes the script to ``C:\\winpodx``,
    (re)registers the ``WinpodxMedia`` HKCU\\Run entry under the hidden-launcher
    wrapper so every interactive logon (full desktop + each RemoteApp session)
    starts one instance, and kicks one now so the current session maps drives
    without waiting for the next logon. A per-session mutex in the script makes
    the duplicate launch exit cleanly. Idempotent.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    src = bundle_dir() / "scripts" / "windows" / "media_monitor.ps1"
    if not src.is_file():
        raise RuntimeError(f"media_monitor source missing: {src}")
    try:
        body = src.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"cannot read {src}: {e}") from e

    import base64 as _b64

    b64 = _b64.b64encode(body.encode("utf-8")).decode("ascii")
    launcher = "C:\\Users\\Public\\winpodx\\launchers\\hidden-launcher.vbs"
    target = "C:\\winpodx\\media_monitor.ps1"
    media_reg_value = (
        f'wscript.exe "{launcher}" '
        '"powershell.exe" "-NoProfile" "-ExecutionPolicy" "Bypass" '
        f'"-File" "{target}"'
    ).replace("'", "''")
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "if (-not (Test-Path 'C:\\winpodx')) "
        "{ [void](New-Item -ItemType Directory -Force -Path 'C:\\winpodx') }",
        f"$bytes = [Convert]::FromBase64String('{b64}')",
        f"[IO.File]::WriteAllBytes('{target}', $bytes)",
        "$runKey = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'",
        "if (-not (Test-Path $runKey)) { [void](New-Item -Path $runKey -Force) }",
        f"Set-ItemProperty -Path $runKey -Name 'WinpodxMedia' -Value '{media_reg_value}'",
        # Kick one instance now so the current session maps drives immediately;
        # the per-session mutex makes a later (next-logon) duplicate exit.
        f"if (Test-Path -LiteralPath '{launcher}') {{",
        f"    Start-Process wscript.exe -ArgumentList "
        f"@('{launcher}','powershell.exe','-NoProfile','-ExecutionPolicy',"
        f"'Bypass','-File','{target}') | Out-Null",
        "} else {",
        f"    Start-Process powershell.exe -ArgumentList "
        f"@('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden',"
        f"'-File','{target}') | Out-Null",
        "}",
        "Write-Output 'media_monitor delivered + started'",
    ]
    payload = "\n".join(lines) + "\n"

    from winpodx.core.windows_exec import WindowsExecError

    try:
        result = _apply_via_transport(cfg, payload, description="apply-media-monitor")
    except WindowsExecError as e:
        raise RuntimeError(f"media_monitor apply failed: {e}") from e
    if result.rc != 0:
        raise RuntimeError(f"media_monitor apply failed (rc={result.rc}): {result.stderr.strip()}")
    log.info("media_monitor: %s", result.stdout.strip())


def _apply_agent_keepalive(cfg: Config) -> None:
    """Register (idempotently) the WinpodxAgentKeepAlive scheduled task.

    The guest agent's only autostart is an HKCU\\Run entry that fires once
    per interactive logon. When the autologon session is torn down (RDP
    single-session enforcement on a FreeRDP connect before rdprrap
    multi-session is active, or a TermService cycle during rdprrap
    (re)activation) the agent dies with the session and HKCU\\Run does not
    re-fire -- the agent stays dead until the pod reboots. This task is the
    persistent watchdog: it runs ``agent-keepalive.ps1`` AtLogOn and every
    1 minute, which (re)launches the agent only when no agent.ps1 process
    is running (never kills a healthy one).

    Principal is the INTERACTIVE autologon user, NOT SYSTEM / S4U: the
    agent's /exec runs PowerShell that callers expect in the user context
    (Start Menu / per-user app discovery, per-user reverse-open HKCU
    registration). See config/oem/agent-keepalive.ps1 for the full
    reasoning. A user-context task covers crash-but-alive + re-logon; the
    session-kick-with-no-relogon case is prevented by keeping rdprrap
    activation idempotent (_apply_multi_session) so the kick doesn't
    happen.

    Depends on vbs_launchers having staged agent-keepalive.ps1 +
    hidden-launcher.vbs into the Public launchers dir first;
    apply_windows_runtime_fixes orders the chain accordingly. Idempotent:
    Register-ScheduledTask -Force rewrites the task on every apply-fixes.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    target_dir = "C:\\Users\\Public\\winpodx\\launchers"
    staged_ka = f"{target_dir}\\agent-keepalive.ps1"
    run_ka = "C:\\winpodx\\agent-keepalive.ps1"
    hidden_vbs = f"{target_dir}\\hidden-launcher.vbs"

    payload_lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$staged = '{staged_ka}'",
        f"$ka = '{run_ka}'",
        f"$wrap = '{hidden_vbs}'",
        # vbs_launchers (runs before this step) stages agent-keepalive.ps1
        # into the Public dir; copy it to C:\winpodx for the persistent run
        # location (survives the C:\OEM wipe on classic VMs, same as
        # power-monitor.ps1). Surface clearly if vbs_launchers was skipped.
        "if (-not (Test-Path -LiteralPath $staged)) {",
        "    Write-Output 'agent-keepalive.ps1 not staged"
        " (vbs_launchers must run first); skipping keep-alive task'",
        "    exit 0",
        "}",
        "if (-not (Test-Path -LiteralPath 'C:\\winpodx')) {",
        "    [void](New-Item -ItemType Directory -Force -Path 'C:\\winpodx')",
        "}",
        "Copy-Item -LiteralPath $staged -Destination $ka -Force",
        # Build the action: run the keep-alive through the windowless
        # wscript wrapper so the 1-minute wakeups never flash a console.
        # Fall back to hidden powershell only if the wrapper is missing.
        "if (Test-Path -LiteralPath $wrap) {",
        "    $exe = 'wscript.exe'",
        '    $arg = \'"\' + $wrap + \'" "powershell.exe" "-NoProfile"'
        ' "-ExecutionPolicy" "Bypass" "-File" "\' + $ka + \'"\'',
        "} else {",
        "    $exe = 'powershell.exe'",
        "    $arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"' + $ka + '\"'",
        "}",
        "$act = New-ScheduledTaskAction -Execute $exe -Argument $arg",
        # AtLogOn brings the agent back after a re-logon; the 1-minute
        # repetition (indefinite) brings a crashed-but-session-alive agent
        # back within ~1 min and is the backstop after a multi_session
        # TermService cycle settles.
        "$tLogon = New-ScheduledTaskTrigger -AtLogOn",
        "$tRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date)"
        " -RepetitionInterval (New-TimeSpan -Minutes 1)",
        # Interactive autologon user principal (NOT SYSTEM): keeps HKCU /
        # Start Menu context intact for discovery + reverse-open.
        '$me = "$env:USERDOMAIN\\$env:USERNAME"',
        "$prin = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited",
        "$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries"
        " -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew"
        " -ExecutionTimeLimit (New-TimeSpan -Minutes 2)",
        "Register-ScheduledTask -TaskName 'WinpodxAgentKeepAlive'"
        " -Action $act -Trigger @($tLogon,$tRepeat) -Principal $prin"
        " -Settings $set -Force | Out-Null",
        # Kick it once now so a currently-dead agent comes back immediately
        # rather than waiting up to a minute for the first repetition.
        "Start-ScheduledTask -TaskName 'WinpodxAgentKeepAlive' -ErrorAction SilentlyContinue",
        "Write-Output ('agent_keepalive: WinpodxAgentKeepAlive registered for ' + $me)",
        "exit 0",
    ]
    payload = "\n".join(payload_lines) + "\n"

    from winpodx.core.windows_exec import WindowsExecError

    try:
        result = _apply_via_transport(cfg, payload, description="apply-agent-keepalive")
    except WindowsExecError as e:
        log.warning("agent_keepalive: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("agent_keepalive: rc=%d stderr=%s", result.rc, result.stderr.strip())
        raise RuntimeError(
            f"agent_keepalive apply failed (rc={result.rc}): {result.stderr.strip()}"
        )
    log.info("agent_keepalive: %s", result.stdout.strip())


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

    from winpodx.core.windows_exec import WindowsExecError

    try:
        result = _apply_via_transport(cfg, payload, description="apply-rdp-timeouts")
    except WindowsExecError as e:
        log.warning("rdp_timeouts: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("rdp_timeouts: rc=%d stderr=%s", result.rc, result.stderr.strip())
        raise RuntimeError(f"rdp_timeouts apply failed (rc={result.rc}): {result.stderr.strip()}")
    log.info("rdp_timeouts: %s", result.stdout.strip())


def _ensure_config() -> Config:
    """Load config, or create a default one if none exists."""
    path = Config.path()
    if path.exists():
        return Config.load()

    log.info("No config found, creating default at %s", path)
    cfg = Config()
    cfg.rdp.user = "WPX-User"
    cfg.rdp.ip = "127.0.0.1"
    cfg.rdp.password = generate_password()
    cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()

    if shutil.which("podman"):
        cfg.pod.backend = "podman"
    elif shutil.which("docker"):
        cfg.pod.backend = "docker"
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
