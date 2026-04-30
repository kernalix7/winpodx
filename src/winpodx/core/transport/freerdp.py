"""FreerdpTransport — Transport over the FreeRDP RemoteApp channel.

Wraps ``winpodx.core.windows_exec.run_in_windows`` (the existing PS-via-
RemoteApp helper). The wrapped helper already handles the script
harness, the ``\\tsclient\\home`` redirection, the result-file JSON, and
the ``Write-WinpodxProgress`` file-tail streaming protocol — this class
adapts its result + exception types to the Transport contract.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from winpodx.core.config import Config
from winpodx.core.pod.health import check_rdp_port
from winpodx.core.rdp import find_freerdp
from winpodx.core.transport.base import (
    SPEC_VERSION,
    ExecResult,
    HealthStatus,
    Transport,
    TransportAuthError,
    TransportError,
    TransportTimeoutError,
    TransportUnavailable,
)
from winpodx.core.windows_exec import WindowsExecError, run_in_windows

assert SPEC_VERSION == 1, "FreerdpTransport built against Transport spec v1"

log = logging.getLogger(__name__)

_HEALTH_PROBE_TIMEOUT = 2.0


class FreerdpTransport(Transport):
    """Transport implementation over FreeRDP RemoteApp.

    Per the spec, ``health()`` returns ``available=False`` for transient
    failures (RDP port refused) but raises for configuration errors
    (FreeRDP binary missing) so the caller surfaces them rather than
    silently falling back.
    """

    name = "freerdp"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def health(self) -> HealthStatus:
        # Configuration error — raise per spec rule "may raise on
        # configuration errors (missing FreeRDP binary)".
        found = find_freerdp()
        if found is None:
            raise TransportUnavailable("FreeRDP binary not found on $PATH")
        binary, _flavor = found

        # Transient state (RDP port closed during boot, etc) — return
        # available=False instead of raising so dispatch() stays simple.
        try:
            up = check_rdp_port(self.cfg.rdp.ip, self.cfg.rdp.port, timeout=_HEALTH_PROBE_TIMEOUT)
        except Exception as e:  # noqa: BLE001 — health() must never raise on transient state
            return HealthStatus(
                available=False,
                detail=f"RDP port probe failed: {e}",
            )

        if not up:
            return HealthStatus(
                available=False,
                detail=f"RDP port {self.cfg.rdp.ip}:{self.cfg.rdp.port} not accepting connections",
            )
        return HealthStatus(
            available=True,
            detail=f"FreeRDP at {binary}, RDP {self.cfg.rdp.ip}:{self.cfg.rdp.port} reachable",
        )

    def exec(
        self,
        script: str,
        *,
        timeout: int = 60,
        description: str = "winpodx-exec",
    ) -> ExecResult:
        try:
            result = run_in_windows(
                self.cfg,
                script,
                timeout=timeout,
                description=description,
            )
        except WindowsExecError as e:
            raise self._map_exec_error(e) from e
        return ExecResult(rc=result.rc, stdout=result.stdout, stderr=result.stderr)

    def stream(
        self,
        script: str,
        on_progress: Callable[[str], None],
        *,
        timeout: int = 600,
        description: str = "winpodx-stream",
    ) -> ExecResult:
        try:
            result = run_in_windows(
                self.cfg,
                script,
                timeout=timeout,
                description=description,
                progress_callback=on_progress,
            )
        except WindowsExecError as e:
            raise self._map_exec_error(e) from e
        return ExecResult(rc=result.rc, stdout=result.stdout, stderr=result.stderr)

    @staticmethod
    def _map_exec_error(err: WindowsExecError) -> TransportError:
        """Map a WindowsExecError to the matching TransportError subclass.

        windows_exec.run_in_windows raises a single error type but
        embeds the failure mode in the message. The classifier here is
        deliberately loose — false negatives just degrade to a generic
        TransportUnavailable, which is the safe default for the
        dispatcher.
        """
        msg = str(err)
        lower = msg.lower()
        if "timed out" in lower or "timeout" in lower:
            return TransportTimeoutError(msg)
        if "password" in lower or "auth" in lower or "logon" in lower:
            return TransportAuthError(msg)
        if "freerdp not found" in lower or "freerdp binary vanished" in lower:
            return TransportUnavailable(msg)
        # No-result-file is the "RDP couldn't connect / +home-drive
        # broken / generic channel failure" bucket. Treat as
        # Unavailable so dispatch() may fall back if appropriate.
        if "no result file" in lower:
            return TransportUnavailable(msg)
        return TransportError(msg)
