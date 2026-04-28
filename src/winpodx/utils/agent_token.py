"""Agent token helper — generate and persist the shared 32-byte hex secret.

The token is stored at ~/.config/winpodx/agent_token.txt with mode 0600.
The Windows guest reads the same file through the \\tsclient\\home RDP
home-drive redirection and copies it to C:\\OEM\\agent_token.txt so the
HTTP agent (agent.ps1) can read it locally without touching the share on
every request.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from winpodx.utils.paths import config_dir

_TOKEN_FILENAME = "agent_token.txt"


def token_path() -> Path:
    """Return the canonical path for the agent token file."""
    return config_dir() / _TOKEN_FILENAME


def ensure_agent_token() -> str:
    """Return the agent token, generating and writing it on first call.

    The file is created with mode 0600 (owner read/write only).
    If the file already exists and is non-empty its contents are returned
    unchanged, so this function is idempotent.
    """
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        token = path.read_text(encoding="ascii").strip()
        if token:
            return token

    token = secrets.token_hex(32)
    # Write atomically: open a temp file beside the target, then rename.
    tmp_path = path.parent / (path.name + ".tmp")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (token + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp_path), str(path))
    # Ensure permissions even if the file already existed with wrong perms.
    os.chmod(str(path), 0o600)
    return token
