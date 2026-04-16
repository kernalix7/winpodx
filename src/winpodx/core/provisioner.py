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

from winpodx.core.config import Config
from winpodx.core.pod import PodState, check_rdp_port, pod_status, start_pod
from winpodx.utils.paths import config_dir, data_dir

log = logging.getLogger(__name__)


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

    # Step 1.5: Auto-rotate password if older than 1 day
    cfg = _auto_rotate_password(cfg)

    # Fast path — RDP already available, skip all checks
    if check_rdp_port(cfg.rdp.ip, cfg.rdp.port, timeout=0.3):
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
    # Single quotes in PowerShell treat all characters as literal.
    # Our password alphabet (ascii + digits + !@#$%&*) never includes
    # single quotes, so this is safe.
    ps_cmd = f"net user '{cfg.rdp.user}' '{new_password}'"
    cmd = [
        runtime,
        "exec",
        "winpodx-windows",
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
    from datetime import datetime, timezone

    if not cfg.rdp.password:
        return cfg

    # Skip rotation if disabled or non-container backends
    if cfg.rdp.password_max_age <= 0:
        return cfg
    if cfg.pod.backend not in ("podman", "docker"):
        return cfg

    max_age_seconds = cfg.rdp.password_max_age * 86400

    # Check password age
    if cfg.rdp.password_updated:
        try:
            updated = datetime.fromisoformat(cfg.rdp.password_updated)
            age = datetime.now(timezone.utc) - updated
            if age.total_seconds() < max_age_seconds:
                return cfg
        except ValueError as e:
            log.warning("Invalid password_updated timestamp: %s", e)

    # Pod must be running to change Windows password
    from winpodx.core.pod import PodState, pod_status

    status = pod_status(cfg)
    if status.state != PodState.RUNNING:
        log.debug("Pod not running, skipping password rotation")
        return cfg

    log.info("Password older than %d days, rotating...", cfg.rdp.password_max_age)

    from winpodx.cli.setup_cmd import _generate_compose, _generate_password

    new_password = _generate_password()
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
        _generate_compose(cfg)
        log.info("Password rotated successfully")
    except OSError as e:
        # Config save failed but Windows already has the new password.
        # Try to revert Windows password to keep things in sync.
        log.error("Failed to save config after rotation: %s", e)
        _change_windows_password(cfg, old_password)

    return cfg


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
    from winpodx.cli.setup_cmd import _generate_compose

    _generate_compose(cfg)


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
        f"  1. Check container: {cfg.pod.backend} logs winpodx-windows\n"
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
