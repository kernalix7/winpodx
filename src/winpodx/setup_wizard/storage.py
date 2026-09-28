# SPDX-License-Identifier: MIT
"""Qt-free storage / Windows-ISO choice validation for the setup flow (#850).

Both the CLI wizard (``cli/setup_cmd.py``) and the planned Qt setup wizard
(#655) collect a storage directory and an optional local Windows ISO from the
user. This module owns the *validation* half so both frontends share one
contract::

    validate_storage_choices(storage, iso, *, existing=None) -> StorageChoices

Raw user input (``str | os.PathLike | None``) is normalized to absolute
``pathlib.Path`` values, with blank input becoming ``None``. Invalid input
raises :class:`StorageValidationError` -- a typed, inspectable error the caller
translates to its own UX (CLI: ``SystemExit``; GUI: an inline message).

The validator is intentionally read-only: it never creates a directory, copies
an ISO, or persists configuration. The same policy that guards a hand-edited
``cfg.pod.storage_path`` (:func:`winpodx.core.config._sanitise_storage_path`)
is applied here so a user cannot route storage into a system root.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from winpodx.core.config import _sanitise_storage_path

__all__ = [
    "ExistingInstall",
    "StorageChoices",
    "StorageValidationError",
    "validate_storage_choices",
]


@dataclass(frozen=True, slots=True)
class StorageChoices:
    """Normalized storage / ISO selection handed to the installer."""

    storage: Path | None
    iso: Path | None


@dataclass(frozen=True, slots=True)
class ExistingInstall:
    """What a returning user's guest install already sits on, if anything.

    ``storage`` is the current bind-mount directory; ``named_volume`` is the
    legacy named volume. Either -- or both -- may be ``None`` when a config
    exists but its storage metadata is incomplete.
    """

    storage: Path | None = None
    named_volume: str | None = None


class StorageValidationError(Exception):
    """A rejected storage/ISO choice, keyed for the caller's UX mapping.

    ``key`` is a stable short identifier (``relative``, ``symlink``,
    ``occupied``, ``unwritable``, ``inaccessible``, ``unsafe``,
    ``existing_storage``, ``existing_iso``, ``iso_not_file``,
    ``iso_unreadable``); ``path`` is the offending normalized path when one
    exists.
    """

    def __init__(self, key: str, path: Path | None = None) -> None:
        self.key = key
        self.path = path
        super().__init__(key, path)

    def __str__(self) -> str:
        if self.path is None:
            return self.key
        return f"{self.key}: {self.path}"


def _normalise_path(raw: str | os.PathLike[str] | None) -> Path | None:
    """Coerce raw user input to an expanded absolute-ish ``Path`` or ``None``."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return Path(text).expanduser()
    except (OSError, RuntimeError) as exc:
        raise StorageValidationError("unsafe", Path(text)) from exc


def _under_process_tempdir(storage_path: Path) -> bool:
    """Return True when ``storage_path`` resolves inside the process temp root.

    The shared sanitizer hard-codes a ``/tmp`` carve-out so pytest ``tmp_path``
    fixtures validate. Environments that redirect ``TMPDIR`` elsewhere would
    otherwise see every scratch path as unsafe, so honor the runtime temp root
    as well -- without widening it to system roots.
    """
    try:
        resolved = storage_path.resolve(strict=False)
        temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
        return resolved.is_relative_to(temp_root)
    except (OSError, RuntimeError, ValueError):
        return False


def _validate_fresh_storage(storage_path: Path) -> None:
    """Validate a storage directory that will host a *new* Windows install."""
    if not storage_path.is_absolute():
        raise StorageValidationError("relative", storage_path)
    if not _sanitise_storage_path(str(storage_path)) and not _under_process_tempdir(storage_path):
        raise StorageValidationError("unsafe", storage_path)
    if storage_path.is_symlink():
        raise StorageValidationError("symlink", storage_path)

    try:
        occupied = storage_path.exists() and (
            not storage_path.is_dir() or any(storage_path.iterdir())
        )
    except OSError as exc:
        raise StorageValidationError("inaccessible", storage_path) from exc
    if occupied:
        raise StorageValidationError("occupied", storage_path)

    try:
        ancestor = storage_path
        while not ancestor.exists():
            parent = ancestor.parent
            if parent == ancestor:
                break
            ancestor = parent
        writable = ancestor.is_dir() and os.access(ancestor, os.W_OK | os.X_OK)
    except OSError as exc:
        raise StorageValidationError("inaccessible", storage_path) from exc
    if not writable:
        raise StorageValidationError("unwritable", storage_path)


def _validate_iso(iso_path: Path) -> None:
    """Validate that a local ISO exists, is a regular file, and is readable."""
    if not iso_path.is_file():
        raise StorageValidationError("iso_not_file", iso_path)
    try:
        with iso_path.open("rb"):
            pass
    except OSError as exc:
        raise StorageValidationError("iso_unreadable", iso_path) from exc


def validate_storage_choices(
    storage: str | os.PathLike[str] | None,
    iso: str | os.PathLike[str] | None,
    *,
    existing: ExistingInstall | None = None,
) -> StorageChoices:
    """Normalize and validate a storage directory and optional local ISO.

    ``existing`` marks a guest install that already exists: any *different*
    storage path or a local ISO is rejected (relocating storage is
    ``--migrate-storage``'s job, and an ISO only applies to a fresh install).
    Re-passing the current storage is allowed and skips the empty-directory
    check, since the install legitimately occupies it.
    """
    storage_path = _normalise_path(storage)
    iso_path = _normalise_path(iso)

    if existing is not None:
        existing_storage = _normalise_path(existing.storage)
        if storage_path is not None and (
            existing_storage is None or storage_path != existing_storage
        ):
            raise StorageValidationError("existing_storage", storage_path)
        if iso_path is not None:
            raise StorageValidationError("existing_iso", iso_path)
        return StorageChoices(storage=storage_path, iso=iso_path)

    if storage_path is not None:
        _validate_fresh_storage(storage_path)
    if iso_path is not None:
        _validate_iso(iso_path)
    return StorageChoices(storage=storage_path, iso=iso_path)
