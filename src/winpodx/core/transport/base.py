"""Transport ABC + result types for the host->guest command channel.

See ``docs/TRANSPORT_ABC.md`` for the full contract. This module defines
the abstract surface every concrete transport (FreeRDP RemoteApp, HTTP
agent, future channels) must implement, plus the result/exception
hierarchy.

Spec version is declared on this module; concrete implementations may
import and assert against it so a contract drift fails fast at import
time rather than at runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

SPEC_VERSION = 1


@dataclass(frozen=True)
class ExecResult:
    """Result of a one-shot script execution.

    rc/stdout/stderr come from the script itself, not the transport.
    Transport-level failures (channel down, auth, timeout) raise
    TransportError subclasses instead of returning a bad ExecResult.
    """

    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


@dataclass(frozen=True)
class HealthStatus:
    """Result of a transport-level health probe.

    ``available`` is the only field every transport must populate; the
    rest is best-effort optional metadata.
    """

    available: bool
    version: Optional[str] = None
    detail: Optional[str] = None


class TransportError(RuntimeError):
    """Base for any transport-level failure (channel down, auth, malformed
    response). Distinct from a script-level non-zero rc, which lives
    inside ExecResult."""


class TransportUnavailable(TransportError):
    """Transport is not reachable (connection refused, no FreeRDP binary,
    no agent /health response, etc). Caller may fall back to another
    transport via dispatch()."""


class TransportAuthError(TransportError):
    """Transport reachable but rejected our auth (agent 401/403, FreeRDP
    bad password). Do NOT silently fall back to another transport — auth
    failures usually mean config drift, not channel state."""


class TransportTimeoutError(TransportError):
    """Server accepted the request but didn't finish in time."""


class Transport(ABC):
    """Abstract host->guest command channel.

    Implementations: FreerdpTransport (slow, always-on after RDP works),
    AgentTransport (fast, available after install.bat finishes).

    Callers should declare intent in business terms (apply fix X, run
    discovery, etc.) via higher-level modules; this layer just runs
    PowerShell.
    """

    name: str  # human-readable name, e.g. "freerdp" or "agent"

    @abstractmethod
    def health(self) -> HealthStatus:
        """Cheap probe (~50ms-2s budget) used by dispatch() to pick a
        transport. MUST NOT raise on transient failures — return
        ``HealthStatus(available=False, detail=str(e))`` instead. May
        raise on configuration errors (missing FreeRDP binary etc) so
        the caller surfaces the problem rather than silently falling
        back."""

    @abstractmethod
    def exec(
        self,
        script: str,
        *,
        timeout: int = 60,
        description: str = "winpodx-exec",
    ) -> ExecResult:
        """Run ``script`` as PowerShell on the guest.

        ``description`` is a short human-readable label for log lines and
        for FreeRDP RemoteApp's task name. Caller is trusted; do not
        inject characters that break the underlying channel.

        Raises:
            TransportUnavailable: channel down (caller may fall back).
            TransportAuthError: auth rejected (do NOT fall back).
            TransportTimeoutError: server-side timeout.
            TransportError: any other channel-level failure.

        Returns ExecResult even when script's rc != 0 — that's a
        script-level result, not a transport-level error.
        """

    @abstractmethod
    def stream(
        self,
        script: str,
        on_progress: Callable[[str], None],
        *,
        timeout: int = 600,
        description: str = "winpodx-stream",
    ) -> ExecResult:
        """Run ``script`` and call ``on_progress(line)`` for each progress
        line the script emits via the agreed channel:

        * FreerdpTransport: tail the progress file written via
          ``Write-WinpodxProgress`` (existing protocol in windows_exec.py).
        * AgentTransport: SSE feed from /apply/{step}, /discover, etc.

        Returns the final ExecResult after the stream closes. Same error
        contract as exec().
        """
