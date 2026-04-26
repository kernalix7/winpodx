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
    if cfg.pod.backend in ("podman", "docker") and pod_status(cfg).state == PodState.RUNNING:
        _apply_max_sessions(cfg)
        _apply_rdp_timeouts(cfg)
        _apply_oem_runtime_fixes(cfg)

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
    _apply_max_sessions(cfg)
    _apply_rdp_timeouts(cfg)
    _apply_oem_runtime_fixes(cfg)
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
    """Change Windows user password inside the container via PowerShell."""
    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return False

    runtime = "podman" if backend == "podman" else "docker"
    # Escape single quotes in username to prevent PowerShell injection.
    user = cfg.rdp.user.replace("'", "''")
    pw = new_password.replace("'", "''")
    ps_cmd = f"net user '{user}' '{pw}'"
    cmd = [
        runtime,
        "exec",
        cfg.pod.container_name,
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        ps_cmd,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("Failed to exec password change: %s", e)
        return False

    if result.returncode == 0:
        return True

    log.warning(
        "Password change failed (rc=%d): %s",
        result.returncode,
        result.stderr.strip(),
    )
    return False


def _apply_max_sessions(cfg: Config) -> None:
    """Sync the guest's MaxInstanceCount registry value with cfg.pod.max_sessions.

    Reads the current value first and only rewrites + restarts TermService
    when the value differs, so active RemoteApp sessions aren't dropped
    every time ensure_ready() runs.

    Only runs for container backends (podman/docker). libvirt + manual
    backends are a v0.2.0 guest-agent concern.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    # Clamped at __post_init__; re-assert defensively so the integer
    # interpolated into the PS command is always within [1, 50].
    desired = max(1, min(50, int(cfg.pod.max_sessions)))
    runtime = "podman" if cfg.pod.backend == "podman" else "docker"
    ps_exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    reg_path = r"HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server"

    read_cmd = [
        runtime,
        "exec",
        cfg.pod.container_name,
        ps_exe,
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        f"(Get-ItemProperty '{reg_path}' -Name MaxInstanceCount "
        f"-ErrorAction SilentlyContinue).MaxInstanceCount",
    ]
    try:
        read_result = subprocess.run(read_cmd, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("max_sessions: failed to read current value: %s", e)
        return

    current: int | None
    stdout = read_result.stdout.strip()
    try:
        current = int(stdout) if stdout else None
    except ValueError:
        current = None

    if current == desired:
        log.debug("max_sessions: already %d on guest, no apply needed", desired)
        return

    apply_script = (
        f"$p = '{reg_path}'; "
        f"Set-ItemProperty -Path $p -Name MaxInstanceCount -Value {desired} "
        f"-Type DWord -Force; "
        f"Set-ItemProperty -Path $p -Name fSingleSessionPerUser -Value 0 "
        f"-Type DWord -Force; "
        f"Restart-Service -Force TermService"
    )
    apply_cmd = [
        runtime,
        "exec",
        cfg.pod.container_name,
        ps_exe,
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        apply_script,
    ]
    try:
        result = subprocess.run(apply_cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("max_sessions: apply failed: %s", e)
        return

    if result.returncode == 0:
        log.info(
            "max_sessions: guest registry updated %s -> %d (TermService restarted)",
            current if current is not None else "<unset>",
            desired,
        )
    else:
        log.warning(
            "max_sessions: apply rc=%d stderr=%s",
            result.returncode,
            result.stderr.strip(),
        )


def _apply_oem_runtime_fixes(cfg: Config) -> None:
    """v0.1.9.2: bring existing 0.1.x guests up to OEM v7+ baseline at runtime.

    install.bat only runs at dockur's unattended first boot. Users who
    installed under OEM v6 (winpodx 0.1.6) don't get the v7 NIC
    power-save off + TermService failure-recovery actions, and v8 RDP
    timeouts (those are covered by ``_apply_rdp_timeouts``). Without
    this helper they'd have to recreate the container to get any
    Windows-side fix shipped after their first install.

    Stays idempotent — Set-NetAdapterPowerManagement / sc.exe failure
    are no-op when state already matches. Failure is non-fatal:
    log warning + return so a flaky exec doesn't block ensure_ready.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    runtime = "podman" if cfg.pod.backend == "podman" else "docker"
    ps_exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    apply_script = (
        # v0.1.9 OEM v7: stop Windows from putting the virtual NIC to sleep.
        "$ErrorActionPreference = 'SilentlyContinue'; "
        "Get-NetAdapter | Where-Object { $_.Status -ne 'Disabled' } | "
        "Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false; "
        # v0.1.9 OEM v7: TermService recovery actions (3 attempts at 5s, 24h reset).
        # `sc.exe failure` is non-PowerShell but we can shell to it.
        "& sc.exe failure TermService reset= 86400 "
        "actions= restart/5000/restart/5000/restart/5000 | Out-Null"
    )
    cmd = [
        runtime,
        "exec",
        cfg.pod.container_name,
        ps_exe,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        apply_script,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("oem_runtime_fixes: apply failed: %s", e)
        return
    if result.returncode != 0:
        log.warning(
            "oem_runtime_fixes: rc=%d stderr=%s",
            result.returncode,
            result.stderr.strip(),
        )


def _apply_rdp_timeouts(cfg: Config) -> None:
    """v0.1.9.1: disable RDP idle/disconnect/connection timeouts + enable keep-alive.

    Without this Windows will drop active RemoteApp sessions after its
    default timeouts (1h idle), and NAT/firewall keep-alive cleanup can
    kill the underlying TCP. Idempotent: writes the same key set every
    provision; reg add is no-op when value already matches.

    Mirrors the OEM v8 install.bat changes for guests that were
    provisioned under an older OEM version (so users don't have to
    recreate their container to pick this up).
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return

    runtime = "podman" if cfg.pod.backend == "podman" else "docker"
    ps_exe = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    apply_script = (
        # Machine policy keys (override per-user / per-WinStation defaults).
        r"$mp = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'; "
        r"if (-not (Test-Path $mp)) { New-Item -Path $mp -Force | Out-Null }; "
        r"Set-ItemProperty -Path $mp -Name MaxIdleTime -Value 0 -Type DWord -Force; "
        r"Set-ItemProperty -Path $mp -Name MaxDisconnectionTime -Value 0 -Type DWord -Force; "
        r"Set-ItemProperty -Path $mp -Name MaxConnectionTime -Value 0 -Type DWord -Force; "
        r"Set-ItemProperty -Path $mp -Name KeepAliveEnable -Value 1 -Type DWord -Force; "
        r"Set-ItemProperty -Path $mp -Name KeepAliveInterval -Value 1 -Type DWord -Force; "
        # Per-WinStation keys (the actual TermService consults these).
        r"$ws = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'; "
        r"Set-ItemProperty -Path $ws -Name MaxIdleTime -Value 0 -Type DWord -Force; "
        r"Set-ItemProperty -Path $ws -Name MaxDisconnectionTime -Value 0 -Type DWord -Force; "
        r"Set-ItemProperty -Path $ws -Name MaxConnectionTime -Value 0 -Type DWord -Force; "
        r"Set-ItemProperty -Path $ws -Name KeepAliveTimeout -Value 1 -Type DWord -Force"
    )
    cmd = [
        runtime,
        "exec",
        cfg.pod.container_name,
        ps_exe,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        apply_script,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("rdp_timeouts: apply failed: %s", e)
        return
    if result.returncode != 0:
        log.warning(
            "rdp_timeouts: rc=%d stderr=%s",
            result.returncode,
            result.stderr.strip(),
        )


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
