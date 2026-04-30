"""Windows RDP password rotation.

Extracted from ``winpodx.core.provisioner`` (Track A Sprint 1 Step 2).

# Behavioral rule #6 — DO NOT route through Transport

Password rotation **must** call ``windows_exec.run_in_windows`` directly,
NOT through the Transport ABC. See ``docs/TRANSPORT_ABC.md`` rule #6:

> Password rotation requires the host to authenticate FreeRDP with the
> OLD password to set the NEW password. Routing this through a Transport
> abstraction would tempt callers to use AgentTransport, which would
> expose the new password to the agent process and to anyone who could
> read the agent's process memory.

This is enforced by code review, not by API shape. If a future patch
introduces ``transport.dispatch(cfg).exec(...)`` here, the patch must be
rejected.

# Public API

- ``maybe_rotate(cfg)`` — drives auto-rotation; returns the (possibly
  updated) Config. Replaces the inline ``_auto_rotate_password`` call in
  ``ensure_ready``.
- ``check_pending()`` — logs an error if a partial-rotation marker exists.
  Replaces the inline ``_check_rotation_pending`` call.
- ``RotationError`` — raised on unrecoverable rotation failures (defined
  for forward-compat; current paths still log+return rather than raise).
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from winpodx.core.compose import generate_compose, generate_password
from winpodx.core.config import Config
from winpodx.core.pod import PodState, pod_status
from winpodx.utils.paths import config_dir

log = logging.getLogger(__name__)

__all__ = [
    "RotationError",
    "_ROTATION_PENDING_MARKER",
    "_auto_rotate_password",
    "_change_windows_password",
    "_check_rotation_pending",
    "_clear_rotation_pending",
    "_mark_rotation_pending",
    "_rotation_marker_path",
    "check_pending",
    "maybe_rotate",
]


class RotationError(Exception):
    """Raised on unrecoverable rotation failures."""


# Marker for a partial password rotation (Windows changed, config did not).
_ROTATION_PENDING_MARKER = "rotation_pending"


def _rotation_marker_path() -> Path:
    return Path(config_dir()) / f".{_ROTATION_PENDING_MARKER}"


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

    Rule #6: this calls ``run_in_windows`` directly, never via Transport.
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


# Public API thin wrappers — ensure_ready and CLI shouldn't reach for the
# leading-underscore helpers. Implementation stays in the underscore-prefixed
# functions so existing test patches (``monkeypatch.setattr(provisioner,
# "_change_windows_password", ...)``) keep working through the provisioner
# re-export.


def maybe_rotate(cfg: Config) -> Config:
    """Driver: rotate if due, return the (possibly updated) Config."""
    return _auto_rotate_password(cfg)


def check_pending() -> None:
    """Log an error if a partial-rotation marker exists."""
    _check_rotation_pending()
