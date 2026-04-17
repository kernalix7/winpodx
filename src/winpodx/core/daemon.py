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


def suspend_pod(cfg: Config) -> bool:
    """Pause the Windows container to free CPU (keeps memory)."""
    backend = cfg.pod.backend
    container = cfg.pod.container_name

    if backend == "podman":
        cmd = ["podman", "pause", container]
    elif backend == "docker":
        cmd = ["docker", "pause", container]
    else:
        return False  # libvirt/manual don't support pause

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        log.warning("Container runtime %s not found in PATH", backend)
        return False
    except subprocess.TimeoutExpired:
        log.warning("Pod suspend timed out after 30s")
        return False
    if result.returncode == 0:
        log.info("Pod suspended (paused)")
        return True
    log.warning(
        "Pod suspend failed (rc=%d): %s",
        result.returncode,
        result.stderr.strip(),
    )
    return False


def resume_pod(cfg: Config) -> bool:
    """Unpause a suspended container."""
    backend = cfg.pod.backend
    container = cfg.pod.container_name

    if backend == "podman":
        cmd = ["podman", "unpause", container]
    elif backend == "docker":
        cmd = ["docker", "unpause", container]
    else:
        return False

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        log.warning("Container runtime %s not found in PATH", backend)
        return False
    except subprocess.TimeoutExpired:
        log.warning("Pod resume timed out after 30s")
        return False
    if result.returncode == 0:
        log.info("Pod resumed (unpaused)")
        return True
    log.warning(
        "Pod resume failed (rc=%d): %s",
        result.returncode,
        result.stderr.strip(),
    )
    return False


def is_pod_paused(cfg: Config) -> bool:
    """Check if the container is in paused state."""
    backend = cfg.pod.backend
    container = cfg.pod.container_name

    if backend == "podman":
        cmd = ["podman", "inspect", "--format", "{{.State.Status}}", container]
    elif backend == "docker":
        cmd = ["docker", "inspect", "--format", "{{.State.Status}}", container]
    else:
        return False

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        log.warning("Container runtime %s not found in PATH", backend)
        return False
    except subprocess.TimeoutExpired:
        log.warning("Pod inspect timed out after 10s")
        return False
    if result.returncode != 0:
        log.debug(
            "Pod inspect failed (rc=%d): %s",
            result.returncode,
            result.stderr.strip(),
        )
        return False
    return "paused" in result.stdout.lower()


def ensure_pod_awake(cfg: Config) -> None:
    """Resume pod if it's paused — called before any app launch."""
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
    """Force Windows time resync via RDP command execution.

    Uses podman/docker exec to run w32tm inside the container.
    """
    backend = cfg.pod.backend
    container = cfg.pod.container_name

    if backend not in ("podman", "docker"):
        return False

    runtime = "podman" if backend == "podman" else "docker"
    cmd = [
        runtime,
        "exec",
        container,
        "cmd",
        "/c",
        "w32tm /resync /force",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode == 0:
        log.info("Windows time synced")
        return True

    log.warning("Time sync failed: %s", result.stderr.strip())
    return False


# --- Idle Monitor ---


def run_idle_monitor(
    cfg: Config,
    stop_event: threading.Event | None = None,
) -> None:
    """Monitor for idle sessions and auto-suspend.

    This runs in a loop — intended to be called from a background thread
    or the tray application. Pass a threading.Event as stop_event to
    allow graceful shutdown.
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
