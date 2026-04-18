"""Auto-provisioning engine for first-run experience.

Handles the entire lifecycle automatically:
  1. Check dependencies → guide installation
  2. Generate config if missing
  3. Generate compose.yaml if missing
  4. Start pod if not running
  5. Wait for RDP to be available
  6. Register desktop entries if not done

The goal: user clicks an app icon → everything just works.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from winpodx.core.compose import generate_compose, generate_password
from winpodx.core.config import Config
from winpodx.core.pod import PodState, check_rdp_port, pod_status, start_pod
from winpodx.utils.paths import config_dir, data_dir

log = logging.getLogger(__name__)

# Marker written when a password rotation left the Windows password
# and the stored config out of sync (rollback failed). Presence of this
# file triggers a warning on every ensure_ready() until the user runs
# `winpodx rotate-password` manually.
_ROTATION_PENDING_MARKER = "rotation_pending"


def _rotation_marker_path() -> Path:
    return Path(config_dir()) / f".{_ROTATION_PENDING_MARKER}"


class ProvisionError(Exception):
    """Raised when auto-provisioning fails."""


def ensure_ready(cfg: Config | None = None, timeout: int = 300) -> Config:
    """Ensure everything is ready to launch a Windows app.

    Fast path: if config exists and RDP is already available, skip everything.
    Slow path: full provisioning (first run or pod is down).
    """
    # Step 1: Config
    if cfg is None:
        cfg = _ensure_config()

    # Step 1.1: Warn loudly if a previous rotation rolled back but couldn't
    # restore the Windows password. The system is in an inconsistent state:
    # config holds old password, Windows holds new one — RDP auth will fail
    # until the user runs `winpodx rotate-password` after the pod is healthy.
    _check_rotation_pending()

    # Step 1.5: Auto-rotate password if older than 1 day
    cfg = _auto_rotate_password(cfg)

    # Fast path — RDP already available, skip all checks
    if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=0.3):
        cfg = _push_oem_update_if_stale(cfg)
        return cfg

    # Slow path — full provisioning
    _check_deps()

    if cfg.pod.backend in ("podman", "docker"):
        _ensure_compose(cfg)

    from winpodx.core.daemon import ensure_pod_awake

    ensure_pod_awake(cfg)

    _ensure_pod_running(cfg, timeout)
    _install_bundled_apps_if_needed()
    _ensure_desktop_entries()
    cfg = _push_oem_update_if_stale(cfg)

    return cfg


def _push_oem_update_if_stale(cfg: Config) -> Config:
    """Trigger the in-VM oem_updater.ps1 once per winpodx version bump.

    The in-VM Scheduled Task handles AtLogOn and AtStartup, but neither fires
    on a podman pause/unpause cycle. This path guarantees Windows-side settings
    stay in sync even for VMs that live forever in a paused state between
    winpodx releases. Idempotent: ``oem_updater.ps1`` compares its own version
    marker internally, so multiple invocations are harmless.
    """
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version

        try:
            current = _pkg_version("winpodx")
        except PackageNotFoundError:
            return cfg
    except ImportError:
        return cfg

    if not current or cfg.pod.last_oem_push == current:
        return cfg

    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return cfg

    runtime = "podman" if backend == "podman" else "docker"
    cmd = [
        runtime,
        "exec",
        cfg.pod.container_name,
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        r"C:\winpodx\oem_updater.ps1",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("OEM push skipped: %s", e)
        return cfg

    if result.returncode != 0:
        log.warning(
            "oem_updater.ps1 exit=%s stderr=%s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return cfg

    cfg.pod.last_oem_push = current
    try:
        cfg.save()
    except OSError as e:
        log.warning("Failed to persist last_oem_push: %s", e)
    return cfg


def _change_windows_password(cfg: Config, new_password: str) -> bool:
    """Change Windows user password inside the container via PowerShell.

    Uses PowerShell with single-quoted password to avoid cmd.exe
    special character parsing issues (& % ! etc. in generated passwords).
    Returns True if password was changed successfully.
    """
    backend = cfg.pod.backend
    if backend not in ("podman", "docker"):
        return False

    runtime = "podman" if backend == "podman" else "docker"
    # Escape single quotes for PowerShell (double them).
    # Password alphabet never includes single quotes, but username is
    # user-supplied and must be escaped to prevent command injection.
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


def _auto_rotate_password(cfg: Config) -> Config:
    """Rotate RDP password if older than max_age.

    Changes the Windows user password inside the container first,
    then updates config and compose.yaml. No container recreation needed.
    If the container is not running, rotation is skipped (can't change
    the Windows password without a running container).
    """
    if not cfg.rdp.password:
        return cfg

    # Skip rotation if disabled or non-container backends
    if cfg.rdp.password_max_age <= 0:
        return cfg
    if cfg.pod.backend not in ("podman", "docker"):
        return cfg

    max_age_seconds = cfg.rdp.password_max_age * 86400

    # No timestamp → we cannot judge age, so skip. This is the first-launch
    # fast path: setup just baked the password into compose.yaml, Windows is
    # still booting, and the rotation subprocess (pod_status + net user)
    # would add ~100-500ms to every startup for no benefit. setup_cmd and
    # handle_rotate_password both stamp password_updated when they write a
    # new password, so the only way to hit this branch is a hand-edited
    # config — in which case "don't rotate silently" is the safe default.
    if not cfg.rdp.password_updated:
        return cfg

    # Check password age
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

    # Pod must be running to change Windows password
    status = pod_status(cfg)
    if status.state != PodState.RUNNING:
        log.debug("Pod not running, skipping password rotation")
        return cfg

    log.info("Password older than %d days, rotating...", cfg.rdp.password_max_age)

    new_password = generate_password()
    old_password = cfg.rdp.password

    # Change password inside Windows first
    if not _change_windows_password(cfg, new_password):
        log.warning("Password rotation skipped: could not change Windows password")
        return cfg

    # Windows password changed — now update config and compose to match
    cfg.rdp.password = new_password
    cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()

    try:
        cfg.save()
        generate_compose(cfg)
        log.info("Password rotated successfully")
        # Successful save clears any prior pending-rotation marker.
        _clear_rotation_pending()
    except OSError as e:
        # Config save failed but Windows already has the new password.
        # Revert the in-memory dataclass so we don't return a Config with
        # a password Windows does not accept.
        cfg.rdp.password = old_password
        log.error("Failed to save config after rotation: %s", e)

        # Try to revert Windows password to keep things in sync.
        if _change_windows_password(cfg, old_password):
            log.warning("Password rotation rolled back after config save failure")
        else:
            # Worst case: config has old password, Windows has new.
            # Persist a marker so the user is notified on next launch
            # and can recover manually once the container is reachable.
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
    """Atomically write a 0o600 marker signalling a partial rotation.
    Contents exclude the password so the marker cannot leak credentials.
    """
    import os
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

    log.info("No config found — creating default at %s", path)
    cfg = Config()
    cfg.rdp.user = "User"
    cfg.rdp.ip = "127.0.0.1"

    # Auto-detect backend
    if shutil.which("podman"):
        cfg.pod.backend = "podman"
    elif shutil.which("docker"):
        cfg.pod.backend = "docker"
    elif shutil.which("virsh"):
        cfg.pod.backend = "libvirt"
    else:
        cfg.pod.backend = "podman"  # Default, will fail with clear error

    # Auto-detect DPI
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
    # Already running and RDP available?
    if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=3):
        return

    # Check if pod is running but RDP not ready yet
    status = pod_status(cfg)
    if status.state == PodState.STOPPED:
        log.info("Starting pod (backend: %s)", cfg.pod.backend)
        start_pod(cfg)

    # Wait for RDP
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


def _install_bundled_apps_if_needed() -> None:
    """Copy bundled app definitions to user data dir if not present."""
    from winpodx.core.app import bundled_apps_dir

    src = bundled_apps_dir()
    if not src.exists():
        return

    dest = data_dir() / "apps"
    dest.mkdir(parents=True, exist_ok=True)

    for app_dir in src.iterdir():
        if app_dir.is_dir():
            target = dest / app_dir.name
            if not target.exists():
                shutil.copytree(app_dir, target)


def terminate_tracked_sessions(timeout: float = 3.0) -> int:
    """Terminate all FreeRDP processes tracked via .cproc files.

    Used before uninstall/cleanup removes the runtime directory — wiping
    the .cproc files while their processes are still alive leaves orphan
    RDP sessions and loses our only handle on them.

    Uses ``process.is_freerdp_pid`` to verify each PID really is one of
    our spawned FreeRDP clients (guards against PID reuse). Sends SIGTERM,
    waits up to ``timeout`` seconds for the process to exit, then escalates
    to SIGKILL.

    Returns the number of processes that received a signal.
    """
    import os
    import signal
    import time

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

        # Wait briefly for the process to exit before we delete its pidfile.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_freerdp_pid(sess.pid):
                break
            time.sleep(0.1)
        else:
            # Still alive — escalate.
            try:
                os.kill(sess.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    return signalled
