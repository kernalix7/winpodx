"""Auto-provisioning on first launch."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

from winpodx.core.compose import generate_compose
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


def _apply_via_transport(cfg: Config, payload: str, *, description: str, timeout: int = 60):
    """Run a Windows-side apply payload through the best available transport.

    Picks AgentTransport when ``agent.ps1`` /health responds, falls back
    to FreerdpTransport otherwise. Returns a ``WindowsExecResult`` so
    existing callers (the ``_apply_*`` functions below) don't need to
    change their result handling.

    Maps ``TransportError`` to ``WindowsExecError`` for the same reason —
    the legacy callers' ``except WindowsExecError`` blocks keep working.
    """
    from winpodx.core.transport import TransportError, dispatch
    from winpodx.core.windows_exec import WindowsExecError, WindowsExecResult

    transport = dispatch(cfg)
    try:
        result = transport.exec(payload, timeout=timeout, description=description)
    except TransportError as e:
        raise WindowsExecError(str(e)) from e
    return WindowsExecResult(rc=result.rc, stdout=result.stdout, stderr=result.stderr)


class ProvisionError(Exception):
    """Raised when auto-provisioning fails."""


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
    from winpodx.core.pod import recover_rdp_if_needed

    if not check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
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


def wait_for_windows_responsive(cfg: Config, timeout: int = 90) -> bool:
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

    # Stage 2 — agent /health. Best-effort: cap at 60s since the agent
    # comes up within ~10s of user logon on a healthy pod and any
    # longer wait is a sign of a real misconfig that won't resolve by
    # waiting. We return True anyway so callers proceed (FreeRDP
    # fallback handles the agent-unavailable path).
    transport = AgentTransport(cfg)
    agent_deadline = min(deadline, time.monotonic() + 60)
    while time.monotonic() < agent_deadline:
        status = transport.health()
        if status.available:
            return True
        time.sleep(5)
    log.warning(
        "wait_for_windows_responsive: RDP up but agent /health didn't "
        "answer within 60s; proceeding via FreeRDP fallback. Run "
        "`winpodx pod apply-fixes` or check C:\\winpodx\\setup.log "
        "if subsequent /exec calls keep falling back to FreeRDP."
    )
    return True


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
    ``{"backend": "skipped (libvirt/manual not supported)"}`` so the caller
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
    for name, fn in (
        ("max_sessions", _apply_max_sessions),
        ("rdp_timeouts", _apply_rdp_timeouts),
        ("oem_runtime_fixes", _apply_oem_runtime_fixes),
        ("vbs_launchers", _apply_vbs_launchers),
        ("multi_session", _apply_multi_session),
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
