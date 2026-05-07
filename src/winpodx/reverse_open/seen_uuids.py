"""Persistent ring buffer of processed UUIDs (replay defence).

The reverse-open listener processes a guest-supplied JSON request,
spawns the matching Linux app, then deletes the request file. Without
extra state, a malicious guest can re-submit the *same* UUID after
deletion and trigger a second spawn — classic replay.

Delete-after-process alone is not sufficient: it only handles the
crash-mid-process case (listener died before the spawn returned), where
the file's continued existence on restart is the signal "this was never
processed". For deliberate replay, we need a memory of UUIDs the
listener has already finished — and that memory must outlive listener
restarts (otherwise the attacker just waits for a restart).

This module provides that memory.

Design:

- Bounded persistent ring buffer; default capacity 1000 UUIDs which,
  at human-paced right-click rates, covers many days. Capacity is
  configurable per-instance (the listener accepts no flood-rate that
  would saturate 1000 inside 300s anyway — that's the janitor window).
- On-disk format: one UUID per line, oldest at the top, newest at the
  bottom. Plain text so it's human-debuggable; the file isn't a hot
  path so we don't need a binary format.
- Atomic update: write to ``<path>.tmp`` then ``os.replace``. Truncated
  writes during crashes never leave the file half-written — either the
  pre-write state survives, or the new state is fully committed.
- Mode 0600 (per-user secret-ish; the file content reveals shim
  request UUIDs which leak some launch-rate info to anyone who can
  read the user's files, but at that point they have everything else
  too — still, lock it down by default).
- ``add`` is the only mutator. ``has`` reads the in-memory cache that
  is populated once at ``__init__`` time; subsequent calls are O(1).
- Defensive load: malformed entries on disk (anything that doesn't
  parse as a UUID) are silently dropped at init. We don't treat a
  hand-edited / corrupted file as fatal — the worst case is one stale
  UUID slips through, but the file's integrity is best-effort
  anyway. (We could checksum it; not worth the complexity for a
  cache-of-tombstones whose accidental loss is non-fatal.)
- Empty / malformed inputs to ``add`` are rejected with ``ValueError``
  *before* any write — we don't want garbage tombstones polluting the
  on-disk file.

The class is NOT thread-safe. The listener is single-threaded by
design (one inotify loop, one dispatcher); a future multi-threaded
listener would need to wrap ``add`` with a lock.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import deque
from pathlib import Path
from typing import Deque, Optional, Set

from winpodx.utils.paths import data_dir

__all__ = ["SeenUUIDs", "default_seen_uuids_path"]

log = logging.getLogger(__name__)

#: Default ring-buffer size. Covers many days of human-paced launches;
#: the listener has a 200-file in-flight cap and a 300s janitor anyway,
#: so a flood that saturates 1000 entries inside the buffer's coverage
#: window already triggers the DoS guard upstream.
DEFAULT_MAX_SIZE = 1000


def default_seen_uuids_path() -> Path:
    """``~/.local/share/winpodx/reverse-open/.seen-uuids``.

    The dot-prefix marks it as a hidden cache-ish file (file managers
    skip it by default) — it is not a config the user is expected to
    edit. ``listener.py`` will pass this exact path at construction
    time; the function is exposed so tests and any future CLI
    inspector can find the file without re-deriving the path.
    """
    return data_dir() / "reverse-open" / ".seen-uuids"


def _is_valid_uuid(candidate: str) -> bool:
    """Return whether ``candidate`` parses as a UUID in canonical form.

    Used both to validate ``add()`` input and to drop malformed entries
    from a corrupted on-disk file. Any UUID variant (v1/v4/v7/...) is
    accepted; the listener generates v7 today but we don't want to
    couple the ring-buffer to that choice.
    """
    if not isinstance(candidate, str):
        return False
    candidate = candidate.strip()
    if not candidate:
        return False
    try:
        uuid.UUID(candidate)
    except (ValueError, AttributeError):
        return False
    return True


class SeenUUIDs:
    """Persistent FIFO ring buffer of processed request UUIDs.

    Loaded once at construction; subsequent reads (``has``) are
    O(1) against an in-memory set. Each :meth:`add` rewrites the file
    atomically (temp file + ``os.replace``) so a crashed listener
    leaves either the pre-add state or the post-add state on disk —
    never partial.

    Parameters
    ----------
    path:
        File holding the persisted ring. Created (along with parent
        directories) on first :meth:`add`. Defaults to
        :func:`default_seen_uuids_path`.
    max_size:
        Maximum entries retained. Older entries are dropped FIFO when
        the buffer would overflow. Must be ``>= 1``.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        max_size: int = DEFAULT_MAX_SIZE,
    ) -> None:
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size!r}")
        self._path: Path = Path(path) if path is not None else default_seen_uuids_path()
        self._max_size: int = max_size
        # ``deque`` for cheap O(1) append + O(1) left-pop on overflow;
        # ``set`` mirrors it for O(1) ``has``. The two are kept in
        # lockstep — every mutation goes through ``add`` which updates
        # both then persists. Construction below populates them from
        # whatever is on disk (if anything).
        self._order: Deque[str] = deque(maxlen=max_size)
        self._index: Set[str] = set()
        self._load()

    # ---- public API --------------------------------------------------

    @property
    def path(self) -> Path:
        """On-disk path the ring is persisted to."""
        return self._path

    @property
    def max_size(self) -> int:
        """Configured ring-buffer capacity."""
        return self._max_size

    def __len__(self) -> int:
        return len(self._order)

    def has(self, candidate: str) -> bool:
        """Return whether ``candidate`` was previously :meth:`add`-ed.

        O(1). Returns ``False`` for any non-string input or unknown
        UUID; never raises (the listener calls this on every request
        and a raise here would DoS the dispatch loop).
        """
        if not isinstance(candidate, str):
            return False
        return candidate in self._index

    def add(self, candidate: str) -> None:
        """Record ``candidate`` as processed; persist the ring atomically.

        Validates input — rejects non-strings, empties, and anything
        that doesn't parse as a UUID. Idempotent on re-add (no
        duplicate written, no reorder; the existing entry retains its
        FIFO position).

        Raises
        ------
        ValueError
            If ``candidate`` isn't a valid UUID string. The on-disk
            file is unchanged in this case.
        """
        if not _is_valid_uuid(candidate):
            raise ValueError(f"not a valid UUID: {candidate!r}")
        if candidate in self._index:
            # Already remembered. Keep its existing position so a
            # malicious replay can't refresh the tombstone's age and
            # push older legitimate UUIDs out of the ring.
            return
        # ``deque(maxlen=N).append`` evicts the leftmost element when
        # the deque is full; capture it so we can drop it from the
        # mirror set too.
        evicted: Optional[str] = self._order[0] if len(self._order) >= self._max_size else None
        self._order.append(candidate)
        self._index.add(candidate)
        if evicted is not None and evicted not in self._order:
            # ``in self._order`` covers the unlikely case where the
            # evicted UUID happened to also be the new one (caught
            # earlier by the duplicate check, but defensive).
            self._index.discard(evicted)
        self._persist()

    # ---- internals ---------------------------------------------------

    def _load(self) -> None:
        """Populate ``_order`` / ``_index`` from disk, if the file exists.

        Malformed lines are silently dropped — see module docstring.
        Missing file or unreadable file are non-fatal: we start with
        an empty ring and the first :meth:`add` will create the file.
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as e:
            log.warning("seen-uuids: cannot read %s: %s; starting empty", self._path, e)
            return

        valid: list[str] = []
        for line in raw.splitlines():
            entry = line.strip()
            if not entry:
                continue
            if not _is_valid_uuid(entry):
                # Dropped silently per design — corrupted on-disk
                # entries shouldn't crash the listener.
                continue
            valid.append(entry)

        # If the file held more than ``max_size`` entries (operator
        # shrank max_size between runs), keep the newest ones — those
        # are the most recent and matter most for replay defence.
        if len(valid) > self._max_size:
            valid = valid[-self._max_size :]

        for entry in valid:
            if entry in self._index:
                # Duplicate on disk (hand-edited file); skip.
                continue
            self._order.append(entry)
            self._index.add(entry)

    def _persist(self) -> None:
        """Atomically rewrite the on-disk ring file with mode 0600.

        Strategy: ensure the parent dir exists (mode 0700 — the file
        lives next to other reverse-open state files), write to
        ``<path>.tmp`` with the FD opened ``O_WRONLY|O_CREAT|O_TRUNC``
        and mode 0600, ``fsync`` (best-effort), then ``os.replace``
        onto the target. ``os.replace`` is atomic on POSIX same-FS.

        Failure to write is logged at WARNING and re-raised — the
        caller (``add``) propagates so the listener can decide whether
        to refuse the request. (Better to bail than to spawn an app
        without persisting the tombstone, since that would re-open
        the replay window after the next restart.)
        """
        parent = self._path.parent
        try:
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as e:
            log.warning("seen-uuids: cannot create parent %s: %s", parent, e)
            raise

        tmp_path = self._path.with_name(self._path.name + ".tmp")
        # Open with explicit mode 0600 so the file is locked down even
        # on first creation. ``Path.write_text`` honours umask, which
        # would surface group/world-readable bits on permissive umasks
        # (0022). Using os.open + os.fdopen sidesteps that.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(tmp_path), flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                # Newest at the bottom, one per line. Trailing newline
                # so concatenations / hand-edits behave normally.
                for entry in self._order:
                    fh.write(entry)
                    fh.write("\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # fsync can fail on some FS / mounts (e.g. tmpfs
                    # in some configs); the atomic rename below still
                    # gives us crash-consistency at the OS-cache level.
                    pass
        except OSError as e:
            log.warning("seen-uuids: write to %s failed: %s", tmp_path, e)
            # Clean up the partial temp file so a future _persist
            # doesn't trip over leftover state.
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

        # Re-assert mode 0600 in case an interposing umask or ACL
        # changed it between open() and now (paranoia; on Linux the
        # mode passed to os.open is final modulo umask, which we
        # already account for via the explicit mode arg).
        try:
            os.chmod(tmp_path, 0o600)
        except OSError as e:
            log.debug("seen-uuids: chmod 0600 on %s failed: %s", tmp_path, e)

        try:
            os.replace(str(tmp_path), str(self._path))
        except OSError as e:
            log.warning("seen-uuids: atomic replace %s -> %s failed: %s", tmp_path, self._path, e)
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise
