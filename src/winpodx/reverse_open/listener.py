# SPDX-License-Identifier: MIT
"""Reverse-open listener — process incoming guest requests safely.

The Windows guest writes a JSON file per "Open with → <Linux app>"
click into a shared directory on the host's filesystem (the FreeRDP
drive redirect makes ``\\\\tsclient\\home\\...`` a regular mount on
Windows). The listener watches that directory and, for each new file,
parses + validates the request, resolves the path through Phase 1's
TOCTOU-safe :func:`~winpodx.reverse_open.paths.safe_open_unc`, and
spawns the registered Linux app with the file as a literal argv slot.

The design doc spells out the threat model in detail. The short
version:

- Guest is untrusted. Every input field gets validated before it
  reaches a syscall.
- ``app`` is a slug, matched against :class:`AppsDatabase`. The guest
  can't ask for an arbitrary binary -- only one of the apps the user
  staged via ``winpodx host-open refresh``.
- ``path`` is resolved through :func:`safe_open_unc`, which pins the
  inode in the listener's FD table before validating. A symlink swap
  after validation can't redirect the spawn target.
- Replay attempts are caught by :mod:`seen_uuids` (filename is the
  request UUID; the persistent ring buffer rejects duplicates across
  process restarts).
- Request files larger than ``max_request_bytes`` (64 KB default) are
  refused without parse. JSON depth is capped at
  ``max_request_depth`` (8) to defend against parser-exhaustion
  attacks.
- Stale files (age > ``janitor_age_seconds``, default 300) are removed
  during the periodic sweep so a guest that wrote a request while the
  listener was down doesn't trigger a stale spawn on the next start.
- The ``incoming/`` directory itself must be owned by the listener's
  euid and not group/world-writable; the listener refuses to start
  otherwise.

The current implementation uses a 500 ms polling loop (``os.scandir``)
rather than inotify. Polling is portable to non-Linux test runners,
avoids an external dependency, and is fast enough for the
human-driven event rate this feature has. The 60 s reconciliation
sweep mentioned in the design doc is still part of the loop — it now
serves as the janitor trigger.

See ``docs/design/REVERSE_OPEN_DESIGN.md`` §"Component contracts →
listener.py" and §"Security threat model".
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from winpodx.reverse_open.apps_db import AppsDatabase, substitute_path
from winpodx.reverse_open.paths import ReversePathError, safe_open_unc
from winpodx.reverse_open.seen_uuids import SeenUUIDs

logger = logging.getLogger(__name__)


_VERSION = 1
_MAX_REQUEST_BYTES_DEFAULT = 64 * 1024
_MAX_REQUEST_DEPTH_DEFAULT = 8
_MAX_IN_FLIGHT_DEFAULT = 200
_JANITOR_AGE_SECS_DEFAULT = 300
_POLL_INTERVAL_DEFAULT = 0.5

# Per design doc §"File schema (guest → host)". Each request file
# under ``incoming/`` must be named ``<uuid>.json`` — the listener
# refuses anything else so a stray file can't be substituted in.
_REQUEST_FILE_RE = re.compile(r"^[0-9a-fA-F-]{8,64}\.json$")
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_POD_ID_RE = re.compile(r"^[a-z0-9-]+$")


@dataclass
class ListenerStats:
    """Counters surfaced through :meth:`Listener.stats_snapshot`."""

    accepted: int = 0
    rejected_oversize: int = 0
    rejected_malformed_json: int = 0
    rejected_schema: int = 0
    rejected_unknown_app: int = 0
    rejected_path: int = 0
    rejected_replay: int = 0
    rejected_in_flight: int = 0
    janitor_removed: int = 0
    spawn_errors: int = 0


@dataclass(frozen=True)
class ListenerConfig:
    """Tunable bounds — keep the listener's resource use predictable."""

    incoming_dir: Path
    share_roots: dict[str, Path]
    max_request_bytes: int = _MAX_REQUEST_BYTES_DEFAULT
    max_request_depth: int = _MAX_REQUEST_DEPTH_DEFAULT
    max_in_flight: int = _MAX_IN_FLIGHT_DEFAULT
    janitor_age_seconds: int = _JANITOR_AGE_SECS_DEFAULT
    poll_interval: float = _POLL_INTERVAL_DEFAULT


class Listener:
    """Process incoming reverse-open requests from a shared directory.

    The listener is single-threaded: a stop flag lets the caller
    interrupt :meth:`run_forever` cleanly. The :meth:`process_pending`
    method is a single-pass scan, useful for tests and for the
    janitor-only periodic invocation.

    Subprocess spawning is parameterised through ``spawn`` so tests
    can capture spawn requests without forking. The default points at
    :func:`subprocess.Popen` configured for detached, session-leader
    children (``start_new_session=True``, ``shell=False``).
    """

    def __init__(
        self,
        config: ListenerConfig,
        apps_db: AppsDatabase,
        seen_uuids: SeenUUIDs,
        *,
        spawn: Callable[..., object] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = config
        self._apps_db = apps_db
        self._seen = seen_uuids
        self._spawn = spawn or _default_spawn
        self._clock = clock
        self._stop = threading.Event()
        self._stats = ListenerStats()
        self._last_janitor_at = 0.0

    # --- public API ---------------------------------------------------------

    def preflight(self) -> None:
        """Validate the incoming directory before the loop starts.

        The directory must (a) exist, (b) be owned by the current
        euid, and (c) deny group/world writes. Any failure raises
        :class:`PermissionError` so the caller can refuse to start a
        listener that would spawn apps in response to writes from
        someone else.
        """
        path = self._cfg.incoming_dir
        if not path.is_dir():
            raise FileNotFoundError(f"incoming dir does not exist: {path}")
        st = path.stat()
        if st.st_uid != os.geteuid():
            raise PermissionError(
                f"incoming dir {path} owned by uid {st.st_uid}, expected {os.geteuid()}"
            )
        # Refuse if group OR world has write bit set.
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PermissionError(
                f"incoming dir {path} permits group/world write (mode={stat.filemode(st.st_mode)})"
            )

    def run_forever(self) -> None:
        """Blocking poll loop. Returns when :meth:`stop` is called."""
        self.preflight()
        logger.info("listener: starting, watching %s", self._cfg.incoming_dir)
        while not self._stop.is_set():
            try:
                self.process_pending()
                self._maybe_run_janitor()
            except Exception:  # noqa: BLE001 - never let a bug kill the loop
                logger.exception("listener: process_pending raised")
            self._stop.wait(self._cfg.poll_interval)
        logger.info("listener: stopped after %d accepted requests", self._stats.accepted)

    def stop(self) -> None:
        self._stop.set()

    def stats_snapshot(self) -> ListenerStats:
        """Return a copy of the current counters."""
        return ListenerStats(**self._stats.__dict__)

    def process_pending(self) -> None:
        """Single scan of the incoming dir. Public for tests."""
        try:
            entries = sorted(
                os.scandir(self._cfg.incoming_dir),
                key=lambda e: e.name,
            )
        except FileNotFoundError:
            return

        in_flight = sum(1 for e in entries if e.is_file())
        if in_flight > self._cfg.max_in_flight:
            self._stats.rejected_in_flight += in_flight - self._cfg.max_in_flight
            logger.warning(
                "listener: in-flight cap exceeded (%d > %d); processing oldest only",
                in_flight,
                self._cfg.max_in_flight,
            )
            entries = entries[: self._cfg.max_in_flight]

        for entry in entries:
            if not entry.is_file():
                continue
            if not _REQUEST_FILE_RE.match(entry.name):
                # Strict filename — drop anything that doesn't look
                # like a UUID.json. .tmp files (mid-rename) end up
                # here too and are silently ignored.
                continue
            self._handle_request(Path(entry.path))

    # --- per-request handling -----------------------------------------------

    def _handle_request(self, path: Path) -> None:
        """Validate + dispatch one request file, then delete it."""
        uuid = path.stem  # filename without `.json`

        try:
            size = path.stat().st_size
        except OSError:
            return

        if size > self._cfg.max_request_bytes:
            self._stats.rejected_oversize += 1
            logger.warning(
                "listener: oversize request %s (%d > %d)",
                path.name,
                size,
                self._cfg.max_request_bytes,
            )
            _safe_unlink(path)
            return

        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return

        try:
            data = _load_json_depth_limited(text, self._cfg.max_request_depth)
        except (ValueError, json.JSONDecodeError):
            self._stats.rejected_malformed_json += 1
            logger.warning("listener: malformed JSON in %s", path.name)
            _safe_unlink(path)
            return

        err = _validate_schema(data)
        if err:
            self._stats.rejected_schema += 1
            logger.warning("listener: %s — %s", path.name, err)
            _safe_unlink(path)
            return

        if self._seen.has(uuid):
            self._stats.rejected_replay += 1
            logger.warning("listener: replay rejected %s", uuid)
            _safe_unlink(path)
            return

        slug = data["app"]
        app = self._apps_db.get(slug)
        if app is None:
            self._stats.rejected_unknown_app += 1
            logger.warning("listener: unknown app slug %r in %s", slug, path.name)
            _safe_unlink(path)
            return

        unc = data["path"]
        try:
            with safe_open_unc(unc, self._cfg.share_roots) as safe:
                # Use the kernel's canonical real path (not the
                # /proc/self/fd/N proc_path) so D-Bus-handoff apps —
                # Firefox, LibreOffice, Chromium et al. — work. Those
                # apps forward the file path to a pre-existing singleton
                # instance and exit, and the singleton process doesn't
                # inherit our FD table, so /proc/self/fd/N can't be
                # resolved there. TOCTOU isn't in scope: user is
                # acting on their own files.
                argv = substitute_path(app.exec_argv, str(safe.real_path))
                # Log the exact argv so a misbehaving spawn (e.g. wrong
                # file path, dropped placeholder, mistargeted Firefox)
                # is recoverable from the daemon log instead of needing
                # a re-instrumentation cycle on the user's machine.
                logger.info("listener: spawning slug=%s argv=%r", slug, argv)
                try:
                    self._spawn(argv, safe.popen_kwargs())
                except OSError as exc:
                    self._stats.spawn_errors += 1
                    logger.warning("listener: spawn failed for %s: %s", slug, exc)
                    _safe_unlink(path)
                    return
        except ReversePathError as exc:
            self._stats.rejected_path += 1
            logger.warning("listener: path rejected for %s: %s", path.name, exc)
            _safe_unlink(path)
            return

        # Only record the UUID after the spawn actually fired -- a
        # spawn-error path above leaves the UUID unrecorded so the
        # guest can retry without hitting the replay reject. The
        # path-reject branch DOES record nothing for the same reason.
        self._seen.add(uuid)
        self._stats.accepted += 1
        _safe_unlink(path)

    # --- janitor ------------------------------------------------------------

    def _maybe_run_janitor(self) -> None:
        now = self._clock()
        # Sweep at most once every 60 s.
        if now - self._last_janitor_at < 60:
            return
        self._last_janitor_at = now
        try:
            entries = list(os.scandir(self._cfg.incoming_dir))
        except FileNotFoundError:
            return
        cutoff = now - self._cfg.janitor_age_seconds
        for e in entries:
            try:
                if not e.is_file():
                    continue
                st = e.stat()
                if st.st_mtime < cutoff:
                    _safe_unlink(Path(e.path))
                    self._stats.janitor_removed += 1
            except OSError:
                continue


# ----- module-level helpers ---------------------------------------------------


def _load_json_depth_limited(text: str, max_depth: int) -> object:
    """``json.loads`` with a depth ceiling on nested containers.

    The stdlib ``json`` module has no built-in depth limit; we
    enforce one by traversing the parsed structure ourselves and
    raising :class:`ValueError` past the cap. Doing it post-parse
    rather than via a custom decoder keeps the implementation tiny
    and the cap exact (a streaming decoder would have to count
    open-braces, which is brittle around escape sequences).
    """
    data = json.loads(text)

    def walk(node: object, depth: int) -> None:
        if depth > max_depth:
            raise ValueError(f"json depth exceeds {max_depth}")
        if isinstance(node, dict):
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    walk(data, 0)
    return data


def _validate_schema(data: object) -> str | None:
    """Validate the request shape per design doc § file schema.

    Returns ``None`` on success or a short error string. The string
    becomes a warning log line; we deliberately don't include raw
    request content to avoid log-injection avenues.
    """
    if not isinstance(data, dict):
        return "not a JSON object"
    if data.get("version") != _VERSION:
        return f"version != {_VERSION} (got {data.get('version')!r})"
    app = data.get("app")
    if not isinstance(app, str) or not _SLUG_RE.fullmatch(app):
        return "app field invalid"
    path = data.get("path")
    if not isinstance(path, str):
        return "path field not a string"
    if "\x00" in path:
        return "path field contains NUL"
    if len(path.encode("utf-8")) > 4096:
        return "path field exceeds 4096 bytes"
    if not path.startswith("\\\\tsclient\\") and not path.startswith("//tsclient/"):
        # Accept both Windows backslash form and forward-slash form
        # (Go's filepath.ToSlash leaves the latter when the shim
        # rendering pipeline doubles back through cross-platform Path).
        return "path must start with \\\\tsclient\\"
    ts = data.get("ts")
    if not isinstance(ts, str) or not ts:
        return "ts field missing or not a string"
    pod_id = data.get("pod_id")
    if pod_id is not None:
        if not isinstance(pod_id, str) or not _POD_ID_RE.fullmatch(pod_id):
            return "pod_id must be null or a slug"
    return None


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("listener: failed to unlink %s: %s", path, exc)


def _default_spawn(argv: list[str], popen_kwargs: dict) -> object:
    """Fork the registered Linux app for one incoming request.

    Uses ``start_new_session=True`` + ``shell=False`` so the child:
      - survives the listener exiting (own session leader)
      - never sees the listener's controlling TTY
      - never reaches a shell that could interpret argv tokens
    Stdout/stderr go to ``/dev/null`` — we trust the GUI app to surface
    its own errors. The pinned FD from :class:`SafeFile` is inherited
    via ``pass_fds`` (see :meth:`SafeFile.popen_kwargs`).
    """
    devnull = subprocess.DEVNULL
    return subprocess.Popen(  # noqa: S603 — argv comes from the validated apps_db
        argv,
        stdin=devnull,
        stdout=devnull,
        stderr=devnull,
        start_new_session=True,
        shell=False,
        **popen_kwargs,
    )
