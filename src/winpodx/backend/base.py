"""Abstract base class for Windows pod backends."""

from __future__ import annotations

import datetime
import json
import logging
import re
import subprocess
from abc import ABC, abstractmethod

from winpodx.core.config import Config

log = logging.getLogger(__name__)


# Go's `time.Time.String()` format that podman / docker emit when you
# format a timestamp via ``-f '{{.State.StartedAt}}'`` --
# ``2026-05-21 07:55:40.190529036 +0900 KST`` -- isn't ISO 8601 and
# Python's ``datetime.fromisoformat`` rejects it across the entire 3.9
# -> 3.13 range. ``--format '{{json ...}}'`` instead marshals via Go's
# json package which always emits RFC3339Nano (``"2026-05-21T07:55:40.
# 190529036+09:00"``) -- portable and stable across all podman / docker
# releases on every distro. The helpers below build the right command
# and parse the result, with one regex fallback for any case where the
# user's CLI version still emits the Go default shape.

_GO_TIME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ](?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<frac>\.\d+)?"
    r"\s*(?:Z|(?P<sign>[+-])(?P<hh>\d{2}):?(?P<mm>\d{2}))?"
)


def _parse_inspect_timestamp(raw: str) -> datetime.datetime | None:
    """Parse podman / docker ``StartedAt`` into an aware datetime.

    Handles three on-the-wire shapes we've seen in the wild:
      * ``"2026-05-21T07:55:40.190529036+09:00"`` (RFC3339Nano, quoted
        by ``{{json ...}}``)
      * ``2026-05-21T07:55:40.190529036Z`` (RFC3339Nano, bare)
      * ``2026-05-21 07:55:40.190529036 +0900 KST`` (Go's
        ``time.Time.String()`` default -- emitted by older / non-JSON
        formatted ``-f '{{.State.StartedAt}}'``)

    Returns None on any unparseable / zero-time / pre-2000 input so the
    caller treats it as "uptime unknown" rather than acting on garbage.
    """
    raw = (raw or "").strip().strip('"')
    if not raw:
        return None
    m = _GO_TIME_RE.match(raw)
    if not m:
        log.debug("inspect timestamp %r did not match parser regex", raw)
        return None
    date = m.group("date")
    time_ = m.group("time")
    frac = (m.group("frac") or "")[:7]  # microseconds-precision max
    sign = m.group("sign")
    if sign:
        hh, mm = m.group("hh"), m.group("mm")
        offset = f"{sign}{hh}:{mm}"
    else:
        offset = "+00:00"
    iso = f"{date}T{time_}{frac}{offset}"
    try:
        parsed = datetime.datetime.fromisoformat(iso)
    except ValueError:
        log.debug("inspect timestamp %r normalised to %r still failed parse", raw, iso)
        return None
    # podman emits Go zero time ``0001-01-01 00:00:00 +0000 UTC`` for
    # containers that exist but never started -- treat as unknown.
    if parsed.year < 2000:
        return None
    return parsed


def _container_uptime_secs(cli: str, name: str) -> int | None:
    """Probe ``<cli> inspect`` for container uptime, in seconds, or None.

    ``cli`` is the binary (``podman`` or ``docker``); ``name`` is the
    container name we expect (cfg.pod.container_name). We also try the
    two common compose-prefixed variants because podman-compose 1.x
    sometimes overrides the explicit ``container_name:`` with the
    project prefix -- ``is_running`` uses a regex match on ``ps`` so it
    succeeds either way, but ``inspect <name>`` is strict.

    The ``--format '{{json ...}}'`` payload always shapes the timestamp
    as RFC3339Nano even on podman / docker versions whose bare
    ``-f '{{.State.StartedAt}}'`` would emit Go's ``time.Time.String()``
    default -- saves us from having to parse the Go shape on the wire.
    """
    candidates = [name, f"winpodx_{name}", f"winpodx_{name}_1"]
    raw = ""
    last_stderr = ""
    for candidate in candidates:
        try:
            result = subprocess.run(
                [cli, "inspect", "--format", "{{json .State.StartedAt}}", candidate],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0 and result.stdout.strip():
            raw = result.stdout.strip()
            break
        last_stderr = (result.stderr or "").strip()

    if not raw:
        log.debug(
            "%s inspect StartedAt failed for all candidates %r: %s",
            cli,
            candidates,
            last_stderr,
        )
        return None
    # Strip JSON quoting if present; fall through to the regex parser
    # for either shape.
    try:
        raw = json.loads(raw)
        if not isinstance(raw, str):
            return None
    except (json.JSONDecodeError, TypeError):
        pass  # already a bare string

    started = _parse_inspect_timestamp(raw)
    if started is None:
        return None
    now = datetime.datetime.now(tz=started.tzinfo)
    return max(0, int((now - started).total_seconds()))


class Backend(ABC):
    """Interface that all pod backends must implement."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    @abstractmethod
    def start(self) -> None:
        """Start the Windows environment."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the Windows environment."""

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the Windows environment is currently running."""

    def is_paused(self) -> bool:
        """Return True if the environment is paused/suspended.

        Default: False. Container backends (podman/docker) override this
        so the CLI / GUI / tray can surface the ``PAUSED`` pod state that
        the idle monitor puts the container into. libvirt and manual
        have no equivalent primitive.
        """
        return False

    def uptime_secs(self) -> int | None:
        """Return seconds since the backend's runtime started, or None.

        Used by ``pod_status`` to distinguish a still-booting container
        (``STARTING``) from a long-running one whose Windows guest has
        gone unresponsive (``UNRESPONSIVE``). Default: None so backends
        that can't cheaply expose this fall back to legacy behaviour.
        """
        return None

    @abstractmethod
    def get_ip(self) -> str:
        """Return the IP address of the running Windows environment."""

    def wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for the Windows environment to be ready for RDP."""
        return False

    def restart(self) -> None:
        """Restart the Windows environment."""
        self.stop()
        self.start()
