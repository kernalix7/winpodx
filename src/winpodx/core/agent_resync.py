# SPDX-License-Identifier: MIT
"""Self-heal the guest agent's bearer token when it drifts from the host (#615).

The guest agent reads its bearer token once at boot from a baked copy of
``C:\\OEM\\agent_token.txt`` and caches it in memory. If the host token
(``~/.config/winpodx/agent_token.txt``) and that baked copy ever diverge,
every authenticated endpoint returns HTTP 401 and stays broken until the guest
copy is refreshed *and* the agent restarted — the agent can't fix itself
because the channel that would carry a new token (its own ``/exec``) is the
very one rejecting auth.

:func:`resync_token` closes the loop over the **FreeRDP RemoteApp** channel,
which authenticates with the Windows account password (not the bearer token)
and so still works while the agent is 401. It rewrites
``C:\\OEM\\agent_token.txt`` with the current host token and respawns
``agent.ps1`` so the listener re-reads it, then verifies an authenticated
``/exec`` round-trip now succeeds.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from winpodx.utils.agent_token import ensure_agent_token, stage_token_to_oem

log = logging.getLogger(__name__)

# agent-respawn.ps1 + hidden-launcher.vbs are staged here by the vbs-launchers
# step (apply-fixes / migrate). agent-respawn.ps1 is also in the OEM bundle as
# a secondary location for guests that predate the Public launchers dir.
_LAUNCHER_DIR = "C:\\Users\\Public\\winpodx\\launchers"
_OEM_DIR = "C:\\OEM"


def _resync_payload(token: str) -> str:
    """Build the guest PowerShell that rewrites the token file and respawns.

    ``token`` is a hex string from ``secrets.token_hex`` (``[0-9a-f]{64}``), so
    single-quoting it is injection-safe.
    """
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$tok = '{token}'",
            # Overwrite the baked OEM copy the agent reads at boot. ascii + no
            # trailing newline so it matches the host write byte-for-byte.
            "Set-Content -Path 'C:\\OEM\\agent_token.txt' -Value $tok -NoNewline -Encoding ascii",
            # Respawn the agent so it re-reads the refreshed token. Detached via
            # wscript+hidden-launcher (no console flash); agent-respawn waits
            # ~3s, kills the stale agent, then starts a fresh one. Try the Public
            # launchers dir first, then the OEM bundle copy.
            f"$hidden = '{_LAUNCHER_DIR}\\hidden-launcher.vbs'",
            "$respawn = $null",
            f"foreach ($cand in @('{_LAUNCHER_DIR}\\agent-respawn.ps1', "
            f"'{_OEM_DIR}\\agent-respawn.ps1')) {{",
            "    if (Test-Path -LiteralPath $cand) { $respawn = $cand; break }",
            "}",
            "if ($respawn -and (Test-Path -LiteralPath $hidden)) {",
            "    Start-Process wscript.exe -ArgumentList @($hidden,"
            "'powershell.exe','-NoProfile','-ExecutionPolicy','Bypass',"
            "'-File',$respawn) | Out-Null",
            "    Write-Output 'resynced+respawn'",
            "} else {",
            # No launcher: the token is written, but a fresh agent won't start
            # until the next HKCU\\Run logon. Report so the caller can advise.
            "    Write-Output 'resynced+norespawn'",
            "}",
        ]
    )


def resync_token(cfg, *, verify_timeout: float = 25.0) -> tuple[bool, str]:
    """Push the current host token to the guest over FreeRDP and respawn the agent.

    Returns ``(ok, detail)``. ``ok`` is True only when a post-resync
    authenticated ``/exec`` round-trip succeeds. Best-effort: never raises —
    a missing FreeRDP binary, a down pod, or a guest without the respawn
    launcher all surface as ``(False, <reason>)``.
    """
    from winpodx.core.agent import AgentClient, AgentError
    from winpodx.core.windows_exec import WindowsExecError, run_in_windows

    token = ensure_agent_token()

    # Refresh the live /oem mount too, so a future first boot / install.bat
    # re-run bakes the correct token. Non-fatal — the guest push below is the
    # part that actually heals the running agent.
    try:
        from winpodx.core.pod.compose import _find_oem_dir

        stage_token_to_oem(Path(_find_oem_dir()))
    except Exception as e:  # noqa: BLE001
        log.warning("resync-token: could not refresh OEM-mount token (%s)", e)

    # Deliver over FreeRDP explicitly — the agent is the thing that's 401, so
    # run_via_transport (which prefers the agent) would just hit the same wall.
    try:
        result = run_in_windows(cfg, _resync_payload(token), description="resync-token", timeout=60)
    except WindowsExecError as e:
        return False, f"FreeRDP push failed: {e}"
    if result.rc != 0:
        return (
            False,
            f"guest token rewrite failed (rc={result.rc}): {result.stderr.strip()[:120]!r}",
        )

    marker = (result.stdout or "").strip()
    if marker == "resynced+norespawn":
        return (
            False,
            "token written to the guest, but the respawn launcher is missing — "
            "run `winpodx guest apply-fixes`, or log the Windows session out and "
            "back in, to restart the agent with the new token.",
        )

    # agent-respawn sleeps ~3s then restarts the listener; poll an authed
    # round-trip until it answers or the deadline passes.
    waited = 0.0
    step = 2.0
    last = "no verify attempt"
    while waited < verify_timeout:
        time.sleep(step)
        waited += step
        try:
            # Fresh client each poll so no stale cached state survives.
            r = AgentClient(cfg).exec("Write-Output ok\n", timeout=15.0)
            if r.rc == 0 and (r.stdout or "").strip() == "ok":
                return True, f"token resynced; authenticated /exec OK after ~{waited:.0f}s"
            last = f"rc={r.rc}"
        except AgentError as e:
            last = str(e)
    return (
        False,
        f"token pushed + agent respawned, but still not authenticated "
        f"after {verify_timeout:.0f}s ({last})",
    )
