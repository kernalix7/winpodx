# SPDX-License-Identifier: MIT
"""Background daemon for automatic pod management.

Handles:
  - Auto-suspend: pause container when no active RDP sessions for N seconds
  - Auto-resume: unpause container when an app launch is requested
  - Lock file cleanup: remove Office ~$*.* lock files after sessions end
  - Time sync: force Windows time resync after Linux host wakes from sleep
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from winpodx.core.config import Config
from winpodx.core.process import kill_session, list_active_sessions

log = logging.getLogger(__name__)


# --- Auto Suspend/Resume ---


def _run_container_cmd(
    cfg: Config,
    cmd: list[str],
    timeout: int,
    timeout_msg: str,
) -> subprocess.CompletedProcess[str] | None:
    """Run a container runtime command, returning None on exec/timeout failure.

    Thin AppImage (#357 / #363 root-cause fix, 0.6.0 item A): the container
    stack is no longer bundled, so standard PATH resolution finds the host
    runtime directly. ``host_env()`` still strips ``${APPDIR}`` from
    ``LD_LIBRARY_PATH`` so the host runtime + the host helpers it spawns
    (``systemd-run`` / ``netavark`` / ``aardvark-dns``) load HOST libs and
    not the bundled libcrypto / libssl. Outside an AppImage ``host_env()``
    is a no-op (``env=None`` -> inherit).
    """
    from winpodx.backend._hostenv import host_env

    backend = cfg.pod.backend
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=host_env())
    except FileNotFoundError:
        log.warning("Container runtime %s not found in PATH", backend)
        return None
    except subprocess.TimeoutExpired:
        log.warning(timeout_msg)
        return None


def suspend_pod(cfg: Config) -> bool:
    """Pause the Windows container to free CPU (keeps memory)."""
    if cfg.pod.backend not in ("podman", "docker"):
        return False  # the manual backend doesn't support pause
    cmd = [cfg.pod.backend, "pause", cfg.pod.container_name]
    result = _run_container_cmd(cfg, cmd, timeout=90, timeout_msg="Pod suspend timed out after 90s")
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
    result = _run_container_cmd(cfg, cmd, timeout=90, timeout_msg="Pod resume timed out after 90s")
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
    result = _run_container_cmd(cfg, cmd, timeout=30, timeout_msg="Pod inspect timed out after 30s")
    if result is None:
        return False
    if result.returncode != 0:
        log.debug("Pod inspect failed (rc=%d): %s", result.returncode, result.stderr.strip())
        return False
    return "paused" in result.stdout.lower()


def ensure_pod_awake(cfg: Config) -> None:
    """Resume pod if it's paused (called before any app launch).

    Raises ProvisionError if the pod is still paused after the resume
    attempt, so callers fail fast with a clear message instead of handing
    a paused pod to launch_app (which surfaces as a long opaque RDP timeout).
    """
    if is_pod_paused(cfg):
        log.info("Pod is paused, resuming...")
        resumed = resume_pod(cfg)
        # Brief wait for unpause to complete
        time.sleep(2)
        if not resumed or is_pod_paused(cfg):
            # Lazy import: provisioner imports daemon at module level, so a
            # top-level import here would be circular.
            from winpodx.core.provisioner import ProvisionError

            raise ProvisionError(
                f"failed to resume paused pod {cfg.pod.container_name!r}; "
                "check the container runtime logs"
            )


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
    from winpodx.core.windows_exec import WindowsExecError, run_via_transport

    try:
        result = run_via_transport(cfg, payload, description="sync-time", timeout=90)
    except WindowsExecError as e:
        log.warning("Time sync channel failure: %s", e)
        return False
    if result.rc != 0:
        log.warning("Time sync failed (rc=%d): %s", result.rc, result.stderr.strip())
        return False
    log.info("Windows time synced")
    return True


# --- Idle Monitor ---


def _apply_idle_action(cfg: Config) -> None:
    """Pause (default) or stop (#622, opt-in) the pod when the idle timeout fires.

    ``idle_action="stop"`` frees the VM's RAM (not just CPU) — only stops a
    running pod so a subsequent idle tick doesn't re-stop an already-stopped
    one, and the next app launch cold-boots. Default ``"pause"`` keeps the
    prior behaviour (freeze, RAM retained, instant resume).
    """
    if cfg.pod.idle_action == "stop":
        from winpodx.core.pod import PodState, pod_status, stop_pod

        try:
            running = pod_status(cfg).state == PodState.RUNNING
        except Exception:  # noqa: BLE001 -- degrade to "don't stop"
            running = False
        if running:
            log.info("Idle timeout reached, stopping pod (idle_action=stop)")
            stop_pod(cfg)
            cleanup_lock_files()
    elif not is_pod_paused(cfg):
        log.info("Idle timeout reached, suspending pod")
        suspend_pod(cfg)
        cleanup_lock_files()


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
    # Throttle the disk auto-grow probe: checking C: usage hits the guest
    # over /exec, so only do it every ~10 min while idle (a grow drops
    # usage well below the threshold, so it won't re-fire next tick).
    autogrow_interval = 600.0
    last_autogrow_check: float | None = None

    while not stop_event.is_set():
        sessions = list_active_sessions()

        if sessions:
            idle_since = None  # Reset idle timer
        else:
            # Disk auto-grow runs only while idle so a grow (which recreates
            # the container) never interrupts a live RemoteApp session.
            if (
                cfg.pod.disk_autogrow
                and not is_pod_paused(cfg)
                and (
                    last_autogrow_check is None
                    or time.monotonic() - last_autogrow_check >= autogrow_interval
                )
            ):
                last_autogrow_check = time.monotonic()
                try:
                    from winpodx.core.disk import maybe_autogrow

                    if maybe_autogrow(cfg):
                        # Pod was recreated -- restart the idle timer so the
                        # freshly-grown pod isn't suspended immediately.
                        idle_since = None
                        stop_event.wait(30)
                        continue
                except Exception as e:  # noqa: BLE001 -- never kill the monitor
                    log.warning("auto-grow check failed: %s", e)

            if idle_since is None:
                idle_since = time.monotonic()
                log.debug("No active sessions, starting idle timer")
            elif time.monotonic() - idle_since >= idle_timeout:
                _apply_idle_action(cfg)
                idle_since = None  # Reset after the idle action

        stop_event.wait(30)  # Check every 30 seconds, interruptible


# App icons change rarely (only when a Windows app is updated), and a full
# discovery sweep is non-trivial guest work, so check on a slow cadence.
_ICON_REFRESH_INTERVAL_SECS = 6 * 3600  # 6 hours
# Initial pass shortly after start to catch updates from a prior session.
_ICON_REFRESH_FIRST_DELAY_SECS = 300  # 5 min


def run_icon_refresh_monitor(
    cfg: Config,
    stop_event: threading.Event | None = None,
) -> None:
    """Periodically re-discover so an updated app's icon refreshes by itself.

    A Windows app update can change its icon; winpodx only re-extracted icons on
    a manual "Refresh Apps". This loop runs discovery on a slow cadence (every
    6 h, plus once ~5 min after start). The checksum gate in
    ``persist_discovered`` makes apps whose exe is unchanged a no-op, so an
    idle sweep only rewrites apps that actually changed. Gated each cycle on the
    pod being up + reachable; never wakes a paused/stopped pod. Runs on its own
    thread; never raises out (a failure just retries next cycle).
    """
    if stop_event is None:
        stop_event = threading.Event()
    log.info("Icon-refresh monitor started (every %dh)", _ICON_REFRESH_INTERVAL_SECS // 3600)
    next_run = time.monotonic() + _ICON_REFRESH_FIRST_DELAY_SECS
    while not stop_event.is_set():
        if time.monotonic() >= next_run:
            next_run = time.monotonic() + _ICON_REFRESH_INTERVAL_SECS
            try:
                _icon_refresh_once(cfg)
            except Exception as e:  # noqa: BLE001 -- never kill the monitor
                log.debug("icon-refresh pass failed (will retry): %s", e)
        stop_event.wait(60)  # interruptible; coarse enough for a 6 h cadence


# Session window-reaper cadence (#680). Poll fast enough that a closed window
# drops off "RUNNING" within a few seconds; debounce so a transient no-window
# moment (splash -> main handoff, a modal being the only top-level) can't reap a
# live app.
_WINDOW_REAP_POLL_SECS = 3.0
_WINDOW_REAP_DEBOUNCE_SECS = 6.0


def _rail_window_classes(wmctrl: str) -> set[str] | None:
    """Set of res_class tokens for mapped ``RAIL.<class>`` windows; ``None`` on
    scan error. FreeRDP RAIL windows are res_name ``RAIL`` + res_class == the
    app_name slug, so ``wmctrl -lx`` column 3 is ``RAIL.<app_name>``.
    """
    try:
        out = subprocess.run(
            [wmctrl, "-lx"], capture_output=True, text=True, timeout=4, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    classes: set[str] = set()
    for line in out.splitlines():
        parts = line.split(None, 4)  # id, desktop, wm_class, host, title
        if len(parts) >= 3 and parts[2].startswith("RAIL."):
            classes.add(parts[2][len("RAIL.") :])
    return classes


def run_session_window_reaper(
    cfg: Config,
    stop_event: threading.Event | None = None,
) -> None:
    """Reap RAIL sessions whose windows have closed (#680), from a long-lived host.

    The per-launch ``_window_reaper`` in rdp.py runs as a daemon thread inside
    the launching process, but ``winpodx app run`` returns immediately (unless
    ``--wait``) -- so for the real launch paths (menu ``.desktop`` entries,
    ``winpodx app run <app> <file>``) that thread dies at once and never fires.
    This runs in the tray (always started, long-lived) and watches ALL active
    sessions: once a session's RAIL window has appeared and then stayed gone for
    a debounce, it terminates the session via ``kill_session`` so an app that
    keeps its process resident after the window closes (Office) stops showing
    RUNNING forever and stops holding a half-stuck ``\\tsclient\\home`` redirect.

    X11 / XWayland only (needs ``wmctrl``); a clean no-op otherwise. Only reaps a
    session whose window was actually seen, so a launching / window-less session
    is never killed early.
    """
    if stop_event is None:
        stop_event = threading.Event()
    wmctrl = shutil.which("wmctrl")
    if not wmctrl:
        log.debug("session window-reaper: wmctrl absent; disabled")
        return
    log.info("Session window-reaper started")
    seen: set[str] = set()  # app_names whose RAIL window has appeared at least once
    gone_since: dict[str, float] = {}
    while not stop_event.is_set():
        try:
            live = {s.app_name for s in list_active_sessions()}
            # Forget state for sessions that have ended.
            seen.intersection_update(live)
            for name in list(gone_since):
                if name not in live:
                    gone_since.pop(name, None)

            classes = _rail_window_classes(wmctrl)
            if classes is not None:
                now = time.monotonic()
                for name in live:
                    if name in classes:
                        seen.add(name)
                        gone_since.pop(name, None)
                    elif name in seen:
                        first_gone = gone_since.setdefault(name, now)
                        if now - first_gone >= _WINDOW_REAP_DEBOUNCE_SECS:
                            log.info(
                                "session %r windows gone %.0fs after close; reaping",
                                name,
                                _WINDOW_REAP_DEBOUNCE_SECS,
                            )
                            kill_session(name)
                            seen.discard(name)
                            gone_since.pop(name, None)
        except Exception as e:  # noqa: BLE001 -- never kill the monitor
            log.debug("session window-reaper pass failed: %s", e)
        stop_event.wait(_WINDOW_REAP_POLL_SECS)


def _icon_refresh_once(cfg: Config) -> None:
    """One checksum-gated discovery sweep. Skips when the pod isn't running."""
    from winpodx.core.pod import PodState, pod_status

    if is_pod_paused(cfg):
        return
    try:
        if pod_status(cfg).state != PodState.RUNNING:
            return
    except Exception:  # noqa: BLE001 -- pod probe flaky -> skip this cycle
        return

    from winpodx.core import discovery

    # discover_apps raises if the guest agent is unreachable -> caller retries.
    apps = discovery.discover_apps(cfg)
    written = discovery.persist_discovered(apps)  # checksum gate -> changed only
    if not written:
        return

    # Re-sync the Linux desktop entries so any refreshed icon flows through, then
    # rebuild the icon cache once. install_desktop_entry is idempotent, so the
    # unchanged entries are cheap re-writes.
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.icons import refresh_icon_cache

    for info in list_available_apps():
        if getattr(info, "hidden", False):
            continue
        try:
            install_desktop_entry(info)
        except Exception:  # noqa: BLE001 -- best-effort per app
            log.debug("icon-refresh: install_desktop_entry failed for %s", info.name)
    try:
        refresh_icon_cache()
    except Exception:  # noqa: BLE001 -- cache refresh is best-effort
        log.debug("icon-refresh: icon cache refresh failed")
