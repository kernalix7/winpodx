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
from winpodx.utils.paths import config_dir  # noqa: F401  used by other helpers in this module

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

    Readiness signal (Sprint 4 of feat/redesign): **agent /health responds**.
    The agent.ps1 listener only binds AFTER:
      1. install.bat first-boot OEM stage completes (so rdprrap is
         installed + TermService restarted),
      2. autologon User session opens (HKCU\\Run fires agent.ps1),
      3. agent.ps1 reads ``C:\\OEM\\agent_token.txt`` (delivered via the
         OEM bind mount at setup time) and binds 127.0.0.1:8765.

    All three are necessary for FreeRDP RemoteApp to work without
    "Another user is signed in" / single-session conflict dialogs, so
    /health responding is the unambiguous "Windows is ready" signal.

    Probe is HTTP-only — **no FreeRDP RemoteApp**, so no PowerShell-
    window flashes during the polling loop. Previous design (FreeRDP
    "Write-Output 'ping'" every 3s) was the source of the
    "PowerShell 창 폭주" symptom kernalix7 reported on 2026-04-30.

    Returns True once /health answers, False at timeout. Caller decides
    whether to skip / retry / surface to user.
    """
    from winpodx.core.transport.agent import AgentTransport

    deadline = time.monotonic() + max(1, int(timeout))

    # First wait for the RDP port to come up — cheap TCP probe, no PS
    # flash, just confirms the container's QEMU forwarders are alive.
    while time.monotonic() < deadline:
        if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=1.0):
            break
        time.sleep(2)
    else:
        return False

    # Now poll agent /health. Each probe is a localhost HTTP roundtrip
    # (~50ms when up, ~2s timeout when not) — invisible to the user, no
    # FreeRDP RemoteApp, no PS-window flash. The agent only answers
    # /health AFTER install.bat is fully done and the listener is
    # bound; so a positive answer is the clean "ready" signal.
    transport = AgentTransport(cfg)
    while time.monotonic() < deadline:
        status = transport.health()
        if status.available:
            return True
        # 5s spacing — much longer than the 3s of the old FreeRDP loop
        # since /health is cheap and agent.ps1 binding is bursty
        # (happens once when Wait-Token unblocks).
        if time.monotonic() < deadline - 5:
            time.sleep(5)
        else:
            break
    return False


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

    from winpodx.core.windows_exec import WindowsExecError

    try:
        result = _apply_via_transport(cfg, payload, description="apply-multi-session")
    except WindowsExecError as e:
        log.warning("multi_session: channel failure: %s", e)
        raise
    if result.rc != 0:
        log.warning("multi_session: rc=%d stderr=%s", result.rc, result.stderr.strip())
        # Non-fatal — log and continue. rdprrap not being patched
        # doesn't break winpodx, just means each app share a session.
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
    for name, fn in (
        ("max_sessions", _apply_max_sessions),
        ("rdp_timeouts", _apply_rdp_timeouts),
        ("oem_runtime_fixes", _apply_oem_runtime_fixes),
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
