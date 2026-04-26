"""Background daemon for automatic pod management.

Handles:
  - Auto-suspend: pause container when no active RDP sessions for N seconds
  - Auto-resume: unpause container when an app launch is requested
  - Lock file cleanup: remove Office ~$*.* lock files after sessions end
  - Time sync: force Windows time resync after Linux host wakes from sleep
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

from winpodx.core.config import Config
from winpodx.core.process import list_active_sessions

log = logging.getLogger(__name__)


# --- Auto Suspend/Resume ---


def _run_container_cmd(
    cfg: Config,
    cmd: list[str],
    timeout: int,
    timeout_msg: str,
) -> subprocess.CompletedProcess[str] | None:
    """Run a container runtime command, returning None on exec/timeout failure."""
    backend = cfg.pod.backend
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        log.warning("Container runtime %s not found in PATH", backend)
        return None
    except subprocess.TimeoutExpired:
        log.warning(timeout_msg)
        return None


def suspend_pod(cfg: Config) -> bool:
    """Pause the Windows container to free CPU (keeps memory)."""
    if cfg.pod.backend not in ("podman", "docker"):
        return False  # libvirt/manual don't support pause
    cmd = [cfg.pod.backend, "pause", cfg.pod.container_name]
    result = _run_container_cmd(cfg, cmd, timeout=30, timeout_msg="Pod suspend timed out after 30s")
    if result is None:
        return False
    if result.returncode == 0:
        log.info("Pod suspended (paused)")
        return True
    log.warning("Pod suspend failed (rc=%d): %s", result.returncode, result.stderr.strip())
    return False


def resume_pod(cfg: Config) -> bool:
    """Unpause a suspended container."""
    if cfg.pod.backend not in ("podman", "docker"):
        return False
    cmd = [cfg.pod.backend, "unpause", cfg.pod.container_name]
    result = _run_container_cmd(cfg, cmd, timeout=30, timeout_msg="Pod resume timed out after 30s")
    if result is None:
        return False
    if result.returncode == 0:
        log.info("Pod resumed (unpaused)")
        return True
    log.warning("Pod resume failed (rc=%d): %s", result.returncode, result.stderr.strip())
    return False


def is_pod_paused(cfg: Config) -> bool:
    """Check if the container is in paused state."""
    if cfg.pod.backend not in ("podman", "docker"):
        return False
    cmd = [cfg.pod.backend, "inspect", "--format", "{{.State.Status}}", cfg.pod.container_name]
    result = _run_container_cmd(cfg, cmd, timeout=10, timeout_msg="Pod inspect timed out after 10s")
    if result is None:
        return False
    if result.returncode != 0:
        log.debug("Pod inspect failed (rc=%d): %s", result.returncode, result.stderr.strip())
        return False
    return "paused" in result.stdout.lower()


def ensure_pod_awake(cfg: Config) -> None:
    """Resume pod if it's paused (called before any app launch)."""
    if is_pod_paused(cfg):
        log.info("Pod is paused, resuming...")
        resume_pod(cfg)
        # Brief wait for unpause to complete
        time.sleep(2)


# --- Lock File Cleanup ---

LOCK_PATTERNS = [
    "~$*.docx",
    "~$*.doc",
    "~$*.xlsx",
    "~$*.xls",
    "~$*.pptx",
    "~$*.ppt",
    "~$*.onetoc2",
]


def cleanup_lock_files(search_dirs: list[Path] | None = None) -> list[Path]:
    """Remove Office lock files (~$*.*) from common document directories.

    Returns list of removed files.
    """
    if search_dirs is None:
        home = Path.home()
        search_dirs = [
            home / "Documents",
            home / "Desktop",
            home / "Downloads",
        ]

    removed: list[Path] = []
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for pattern in LOCK_PATTERNS:
            for lock_file in search_dir.rglob(pattern):
                if lock_file.is_symlink():
                    continue  # Never follow symlinks
                if lock_file.is_file():
                    try:
                        # Only remove small files (lock files are < 1KB)
                        if lock_file.stat().st_size < 1024:
                            lock_file.unlink()
                            removed.append(lock_file)
                            log.info("Removed lock file: %s", lock_file)
                    except OSError:
                        pass
    return removed


# --- Time Sync ---


def sync_windows_time(cfg: Config) -> bool:
    """Force Windows time resync via FreeRDP RemoteApp PowerShell.

    v0.1.9.5: was on the broken `podman exec ... cmd /c w32tm` path —
    podman exec only reaches the Linux container, not the Windows VM
    inside, so this never actually ran. Migrated to
    ``windows_exec.run_in_windows`` along with the rest of the Windows-
    side helpers.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return False

    payload = "& w32tm /resync /force | Out-Null\nWrite-Output 'time synced'\n"
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        result = run_in_windows(cfg, payload, description="sync-time", timeout=30)
    except WindowsExecError as e:
        log.warning("Time sync channel failure: %s", e)
        return False
    if result.rc != 0:
        log.warning("Time sync failed (rc=%d): %s", result.rc, result.stderr.strip())
        return False
    log.info("Windows time synced")
    return True


# --- Idle Monitor ---


def run_idle_monitor(
    cfg: Config,
    stop_event: threading.Event | None = None,
) -> None:
    """Monitor for idle sessions and auto-suspend.

    Runs in a loop; intended to be called from a background thread or tray app.
    Pass a threading.Event as stop_event to allow graceful shutdown.
    """
    if stop_event is None:
        stop_event = threading.Event()

    idle_timeout = cfg.pod.idle_timeout
    if idle_timeout <= 0:
        log.info("Idle monitor disabled (timeout=0)")
        return

    log.info("Idle monitor started (timeout=%ds)", idle_timeout)
    idle_since: float | None = None

    while not stop_event.is_set():
        sessions = list_active_sessions()

        if sessions:
            idle_since = None  # Reset idle timer
        else:
            if idle_since is None:
                idle_since = time.monotonic()
                log.debug("No active sessions, starting idle timer")
            elif time.monotonic() - idle_since >= idle_timeout:
                if not is_pod_paused(cfg):
                    log.info("Idle timeout reached, suspending pod")
                    suspend_pod(cfg)
                    # Cleanup lock files after suspend
                    cleanup_lock_files()
                idle_since = None  # Reset after suspend

        stop_event.wait(30)  # Check every 30 seconds, interruptible
