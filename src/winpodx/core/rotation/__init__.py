# SPDX-License-Identifier: MIT
"""Windows RDP password rotation.

Extracted from ``winpodx.core.provisioner`` (Track A Sprint 1 Step 2).

# Transport selection (rule #6 superseded 2026-05-07)

Rotation now prefers ``AgentTransport`` and falls back to
``run_in_windows`` (FreeRDP RemoteApp). The historical prohibition on
using Transport for rotation (TRANSPORT_ABC.md rule #6) was based on
two arguments that no longer hold:

1. "Need OLD password to authenticate FreeRDP" — AgentTransport
   authenticates with a bearer token, not the user password.
2. "Don't expose new password to agent process memory" — both
   transports expose the new password equally via PowerShell argv and
   ``net user`` argv. The agent path adds an in-memory HTTP request
   buffer; the FreeRDP path writes a script file under
   ``~/.local/share/winpodx/windows-exec/``. Neither is strictly
   safer than the other.

The agent-first preference fixes a real cachyos bug where xfreerdp3's
broken bidirectional drive redirect caused the FreeRDP-only path to
time out at 45s. See ``docs/TRANSPORT_ABC.md`` for the full rationale.

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

from winpodx.core.compose import generate_compose, generate_compose_to, generate_password
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
    "rotate_password",
]


class RotationError(Exception):
    """Raised on unrecoverable rotation failures."""


# Marker for a partial password rotation (Windows changed, config did not).
_ROTATION_PENDING_MARKER = "rotation_pending"


def _rotation_marker_path() -> Path:
    return Path(config_dir()) / f".{_ROTATION_PENDING_MARKER}"


def _verify_windows_password(cfg: Config, password: str) -> bool:
    """Return whether Windows accepts ``password`` for the configured user."""
    if cfg.pod.backend not in ("podman", "docker"):
        return False

    user = cfg.rdp.user.replace("'", "''")
    escaped = password.replace("'", "''")
    # PSCredential construction does not authenticate. Validate against the
    # local account database and close the returned token immediately.
    payload = (
        "$signature = @'\n"
        "using System;\n"
        "using System.Runtime.InteropServices;\n"
        "public static class WinPodxLogon {\n"
        '    [DllImport("advapi32.dll", EntryPoint="LogonUserW", '
        "CharSet=CharSet.Unicode, SetLastError=true)]\n"
        "    public static extern bool LogonUser(string user, string domain, "
        "string password, int logonType, int logonProvider, out IntPtr token);\n"
        '    [DllImport("kernel32.dll", SetLastError=true)]\n'
        "    public static extern bool CloseHandle(IntPtr handle);\n"
        "}\n"
        "'@\n"
        "Add-Type -TypeDefinition $signature\n"
        "$LOGON32_LOGON_NETWORK = 3\n"
        "$LOGON32_PROVIDER_DEFAULT = 0\n"
        "$token = [IntPtr]::Zero\n"
        "$ok = $false\n"
        "try {\n"
        "    $ok = [WinPodxLogon]::LogonUser(\n"
        f"        '{user}', '.', '{escaped}',\n"
        "        $LOGON32_LOGON_NETWORK, $LOGON32_PROVIDER_DEFAULT, [ref]$token)\n"
        "} finally {\n"
        "    if ($token -ne [IntPtr]::Zero) {\n"
        "        [WinPodxLogon]::CloseHandle($token) | Out-Null\n"
        "    }\n"
        "}\n"
        "if (-not $ok) { exit 1 }\n"
        "Write-Output 'password accepted'\n"
    )

    from winpodx.core.transport import dispatch
    from winpodx.core.transport.base import TransportAuthError, TransportError, TransportUnavailable
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        transport = dispatch(cfg, prefer="agent")
    except TransportUnavailable:
        original_password = cfg.rdp.password
        cfg.rdp.password = password
        try:
            result = run_in_windows(cfg, payload, description="verify-password", timeout=30)
        except WindowsExecError as exc:
            raise RotationError("Password verification outcome is unknown") from exc
        finally:
            cfg.rdp.password = original_password
    except TransportAuthError:
        return False
    else:
        try:
            result = transport.exec(payload, description="verify-password", timeout=30)
        except (TransportAuthError, TransportError) as exc:
            raise RotationError("Password verification outcome is unknown") from exc

    if result.rc == 124:
        raise RotationError("Password verification timed out; outcome is unknown")
    return result.rc == 0 and "password accepted" in result.stdout


def _change_windows_password(cfg: Config, new_password: str) -> bool:
    """Change the Windows user account password.

    Tries AgentTransport first (fast HTTP, bypasses xfreerdp3's broken
    bidirectional drive redirect on cachyos). If the agent isn't
    reachable, falls back to ``run_in_windows`` (FreeRDP RemoteApp).
    Auth failures on the agent are NOT followed by FreeRDP fallback —
    those indicate config drift, not transient channel state.

    Runs ``net user <User> <new>`` on the guest. On success the caller
    updates cfg.password. The existing rotation rollback marker
    (``_ROTATION_PENDING_MARKER``) handles the partial-failure window
    where the host saved the new password to disk but the guest didn't
    accept it.
    """
    if cfg.pod.backend not in ("podman", "docker"):
        return False

    user = cfg.rdp.user.replace("'", "''")
    pw = new_password.replace("'", "''")
    # ``net user`` writes errors to stderr but exits with a non-zero code.
    # Without checking $LASTEXITCODE the script continues to Write-Output,
    # so the agent reported rc=0 even when the user did not exist or the
    # password change failed (#569).
    payload = (
        f"& net user '{user}' '{pw}' | Out-Null\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        "Write-Output 'password set'\n"
    )

    from winpodx.core.transport import dispatch
    from winpodx.core.transport.base import TransportAuthError, TransportError, TransportUnavailable
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    try:
        transport = dispatch(cfg, prefer="agent")
    except TransportUnavailable:
        log.info("Agent unavailable for password rotation; falling back to FreeRDP")
        try:
            result = run_in_windows(cfg, payload, description="rotate-password", timeout=120)
        except WindowsExecError as e:
            raise RotationError(
                "Password change channel failed after submission; outcome is unknown"
            ) from e
    except TransportAuthError as e:
        log.warning("Password change auth failure: %s", e)
        return False
    else:
        try:
            result = transport.exec(payload, description="rotate-password", timeout=90)
        except TransportAuthError as e:
            log.warning("Password change auth failure: %s", e)
            return False
        except TransportError as e:
            raise RotationError(
                "Password change channel failed after submission; outcome is unknown"
            ) from e

    if result.rc == 124:
        raise RotationError("Password change timed out; outcome is unknown")
    if result.rc != 0:
        log.warning("Password change failed (rc=%d): %s", result.rc, result.stderr.strip())
        return False
    return True


def _auto_rotate_password(cfg: Config) -> Config:
    """Rotate RDP password if older than max_age."""
    if _rotation_marker_path().exists():
        recovered = _recover_pending_rotation(cfg)
        if recovered is None:
            raise RotationError(
                "Pending password rotation remains unresolved; restore the "
                "last known-good guest password manually before retrying"
            )
        cfg = recovered

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

    if not rotate_password(cfg, generate_password()):
        log.warning("Password rotation skipped: could not change Windows password")

    return cfg


def _prepare_rotation_compose(cfg: Config, new_password: str, updated_at: str) -> Path | None:
    compose_path = Path(config_dir()) / "compose.yaml"
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=compose_path.parent,
        prefix=".compose-rotate-",
        suffix=".tmp",
    )
    os.close(fd)
    candidate = Path(tmp_path)
    old_password = cfg.rdp.password
    old_updated = cfg.rdp.password_updated
    cfg.rdp.password = new_password
    cfg.rdp.password_updated = updated_at
    try:
        generate_compose_to(cfg, candidate)
    except KeyboardInterrupt:
        candidate.unlink(missing_ok=True)
        raise
    except OSError as e:
        log.error("Could not prepare password rotation compose file: %s", e)
        candidate.unlink(missing_ok=True)
        return None
    finally:
        cfg.rdp.password = old_password
        cfg.rdp.password_updated = old_updated
    return candidate


def _restore_password_rotation(cfg: Config, old_password: str, old_updated: str) -> bool:
    try:
        guest_restored = _change_windows_password(cfg, old_password)
    except RotationError as e:
        log.error("Password rollback channel outcome is unknown: %s", e)
        return False
    if not guest_restored:
        log.error("Password rollback was rejected by the guest")
        return False

    cfg.rdp.password = old_password
    cfg.rdp.password_updated = old_updated
    restored = True
    try:
        cfg.save()
    except OSError as e:
        restored = False
        log.error("Password rollback restored the guest but not config: %s", e)
    try:
        generate_compose(cfg)
    except OSError as e:
        restored = False
        log.error("Password rollback restored the guest but not compose: %s", e)
    if restored and _clear_rotation_pending():
        log.warning("Password rotation rolled back after persistence failure")
        return True
    return False


def rotate_password(cfg: Config, new_password: str) -> bool:
    """Rotate guest, config, and compose credentials as one transaction."""
    marker = _rotation_marker_path()
    if marker.exists():
        raise RotationError(
            f"Unresolved pending password rotation at {marker}; run "
            "`winpodx app run desktop` to trigger automatic recovery, or restore "
            "the last known-good password manually if recovery cannot converge"
        )
    old_password = cfg.rdp.password
    old_updated = cfg.rdp.password_updated
    updated_at = datetime.now(timezone.utc).isoformat()
    candidate = _prepare_rotation_compose(cfg, new_password, updated_at)
    if candidate is None:
        return False

    compose_path = Path(config_dir()) / "compose.yaml"
    try:
        if not _mark_rotation_pending(old_password, new_password):
            return False
        try:
            changed = _change_windows_password(cfg, new_password)
        except KeyboardInterrupt:
            log.error("Password rotation interrupted; guest outcome is unknown")
            raise
        if not changed:
            _clear_rotation_pending()
            return False
        try:
            verified = _verify_windows_password(cfg, new_password)
        except RotationError:
            raise
        if not verified:
            if not _restore_password_rotation(cfg, old_password, old_updated):
                raise RotationError(
                    "Candidate password was not accepted and rollback is incomplete"
                )
            return False

        cfg.rdp.password = new_password
        cfg.rdp.password_updated = updated_at
        try:
            cfg.save()
            os.replace(candidate, compose_path)
        except KeyboardInterrupt:
            if not _restore_password_rotation(cfg, old_password, old_updated):
                log.error("Password rotation interrupted and rollback is incomplete")
            raise
        except OSError as e:
            log.error("Failed to persist password rotation: %s", e)
            if _restore_password_rotation(cfg, old_password, old_updated):
                return False
            raise RotationError("Password rotation rollback is incomplete") from e

        if not _clear_rotation_pending():
            raise RotationError("Password rotated, but pending marker cleanup failed")
        log.info("Password rotated successfully")
        return True
    finally:
        candidate.unlink(missing_ok=True)


def _mark_rotation_pending(old_password: str, new_password: str) -> bool:
    """Atomically write a 0o600 marker signalling a partial rotation."""
    marker = _rotation_marker_path()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=marker.parent, prefix=".winpodx-rot-", suffix=".tmp")
        tmp = Path(tmp_path)
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(f"{old_password}\n{new_password}\n".encode())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, marker)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError as e:
        log.error("Failed to write rotation marker: %s", e)
        return False
    return True


def _clear_rotation_pending() -> bool:
    marker = _rotation_marker_path()
    try:
        marker.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Could not remove rotation marker: %s", e)
        return False
    return True


def _check_rotation_pending() -> None:
    marker = _rotation_marker_path()
    if marker.exists():
        log.error(
            "Pending password rotation detected (%s). "
            "Readiness will attempt automatic recovery before RDP connects.",
            marker,
        )


# Public API thin wrappers — ensure_ready and CLI shouldn't reach for the
# leading-underscore helpers. Implementation stays in the underscore-prefixed
# functions so existing test patches (``monkeypatch.setattr(provisioner,
# "_change_windows_password", ...)``) keep working through the provisioner
# re-export.


def _recover_pending_rotation(cfg: Config) -> Config | None:
    """Converge an interrupted rotation before any new credential is generated."""
    marker = _rotation_marker_path()
    try:
        lines = marker.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) != 2:
        return None
    old_password, candidate = lines
    try:
        candidate_valid = _verify_windows_password(cfg, candidate)
    except RotationError:
        candidate_valid = False
    if candidate_valid:
        recovered_password = candidate
    else:
        try:
            if not _verify_windows_password(cfg, old_password):
                return None
        except RotationError:
            return None
        recovered_password = old_password
    cfg.rdp.password = recovered_password
    cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()
    try:
        cfg.save()
        generate_compose(cfg)
    except OSError:
        return None
    if not _clear_rotation_pending():
        return None
    return cfg


def maybe_rotate(cfg: Config) -> Config:
    """Driver: rotate if due, return the (possibly updated) Config."""
    return _auto_rotate_password(cfg)


def check_pending() -> None:
    """Log an error if a partial-rotation marker exists."""
    _check_rotation_pending()
