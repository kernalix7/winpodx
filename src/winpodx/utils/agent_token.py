"""Host-side bearer token for the guest HTTP agent.

The same token is read by:
- ``AgentClient._token()`` on the host (this file's location)
- ``agent.ps1`` inside Windows (read from ``C:\\OEM\\agent_token.txt``,
  staged at setup time by copying from here into the OEM bind mount).

See docs/AGENT_V2_DESIGN.md for the full delivery flow.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from winpodx.utils.paths import config_dir

_TOKEN_FILENAME = "agent_token.txt"
_TOKEN_BYTES = 32


def token_path() -> Path:
    """Return the host-side token file path: ~/.config/winpodx/agent_token.txt."""
    return config_dir() / _TOKEN_FILENAME


def _atomic_write_token(path: Path, token: str) -> None:
    """Write *token* to *path* atomically with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".agent-token-", suffix=".tmp")
    fd_closed = False
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, token.encode("ascii"))
        os.fsync(fd)
        os.close(fd)
        fd_closed = True
        os.replace(tmp_path, str(path))
    except Exception:
        if not fd_closed:
            os.close(fd)
        Path(tmp_path).unlink(missing_ok=True)
        raise


def ensure_agent_token() -> str:
    """Generate or load the host-side agent token.

    Returns the token as a hex string. Creates the file with mode 0600 if
    missing. If the file exists with a wrong mode, re-applies 0600 in place.
    Idempotent: subsequent calls return the same token.
    """
    path = token_path()
    if path.exists():
        existing = path.read_text(encoding="ascii").strip()
        # Re-apply 0600 in case it was created with wrong perms previously.
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if existing:
            return existing
        # Empty file: regenerate.

    token = secrets.token_hex(_TOKEN_BYTES)
    _atomic_write_token(path, token)
    return token


def stage_token_to_oem(oem_dir: Path | str) -> Path:
    """Copy the host-side token into *oem_dir/agent_token.txt* with mode 0600.

    Returns the destination path. dockur copies /oem/* into C:\\OEM\\ at
    first boot; agent.ps1 reads C:\\OEM\\agent_token.txt to bind its
    listener with bearer auth.
    """
    token = ensure_agent_token()
    dest = Path(oem_dir) / _TOKEN_FILENAME
    _atomic_write_token(dest, token)
    return dest
