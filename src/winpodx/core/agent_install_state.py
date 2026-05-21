# SPDX-License-Identifier: MIT
"""Host-side helpers for the agent-first guest install state.

Three groups of primitives:

1. Marker file primitives -- empty sentinel ``<step>.done`` files written
   atomically (temp + ``os.replace``).
2. ``RetryCounter`` -- wraps ``retry_counts.json`` with ACID-ish updates
   (atomic temp+rename), tolerant of missing/corrupt files.
3. Redactor + ``write_install_failure`` -- sanitize ``install_failure.json``
   payloads against the schema described in
   ``docs/design/AGENT_FIRST_INSTALL_DESIGN.md`` Section "Schemas" and the
   security threat model in the same document. The redactor strips the
   patterns enumerated in security review #3.

These primitives back the host-side parser in ``core/install_state.py`` and
the CLI surfaced by ``winpodx pod install-status`` /
``winpodx pod install-resume``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

try:  # optional dev dep; we degrade gracefully when unavailable
    import jsonschema as _jsonschema  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover - exercised by env without jsonschema
    _jsonschema = None  # type: ignore[assignment]


_LOG = logging.getLogger(__name__)

# install_failure.json required top-level fields. Mirrors the schema at
# docs/design/install_failure.schema.json. Kept as a fallback when the
# optional ``jsonschema`` package is not installed.
_INSTALL_FAILURE_REQUIRED: tuple[str, ...] = (
    "session_id",
    "failed_step",
    "phase",
    "attempt",
    "max_attempts",
    "exit_code",
    "error_class",
    "error_summary",
    "timestamp_utc",
)

_REDACTED = "<REDACTED>"
_BASE64_REDACTED = "<BASE64-REDACTED>"

# Order matters: run the more specific patterns first so that, e.g., a
# Bearer token isn't first eaten by the bare-base64 rule (which would still
# redact it but with a less informative tag).
_NET_USER_RE = re.compile(r"(net user\s+\S+\s+)\S+", re.IGNORECASE)
_AUTH_BEARER_RE = re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE)
_KV_SECRET_RE = re.compile(
    r"\b(password|token|apikey|api_key)\s*=\s*([^\s'\"&]+)",
    re.IGNORECASE,
)
# Bare base64-ish blob: 40+ chars from the base64 alphabet, optionally
# trailed by '=' padding. Word boundaries keep us from eating partial
# matches inside larger non-base64 strings.
_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])")


# ---------------------------------------------------------------------------
# Marker file primitives
# ---------------------------------------------------------------------------


def atomic_write_marker(path: Path) -> None:
    """Write an empty sentinel file at *path* atomically (temp + rename).

    Creates parent directories if missing. Safe under concurrent writers --
    each writer creates its own temp file then races to ``os.replace``;
    losers either succeed last or leave the existing file intact, but the
    final file is always empty (0 bytes) and well-formed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp_path, str(path))
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def read_marker(path: Path) -> bool:
    """Return ``True`` iff a marker file exists at *path*."""
    return path.is_file()


def list_completed_steps(state_dir: Path) -> list[str]:
    """Return sorted step names recorded in *state_dir* as ``<step>.done`` files.

    A missing or non-directory *state_dir* yields an empty list.
    """
    if not state_dir.is_dir():
        return []
    names: list[str] = []
    for entry in state_dir.iterdir():
        if entry.is_file() and entry.suffix == ".done":
            names.append(entry.name[: -len(".done")])
    names.sort()
    return names


# ---------------------------------------------------------------------------
# RetryCounter
# ---------------------------------------------------------------------------


class RetryCounter:
    """Atomic per-step retry counter persisted at *path* as JSON.

    A missing file is equivalent to all-zero counts. A corrupt JSON file is
    logged at WARNING level and treated as empty (we do not destroy the
    file in case the user wants to inspect it).
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, int]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            _LOG.warning("retry_counts: read failed for %s: %s", self._path, exc)
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            _LOG.warning("retry_counts: corrupt JSON in %s: %s", self._path, exc)
            return {}
        if not isinstance(data, dict):
            _LOG.warning("retry_counts: unexpected top-level type in %s", self._path)
            return {}
        out: dict[str, int] = {}
        for key, value in data.items():
            if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool):
                out[key] = value
        return out

    def _save(self, counts: dict[str, int]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent),
            prefix=f".{self._path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(counts, fh, sort_keys=True, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, str(self._path))
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

    def get(self, step: str) -> int:
        return self._load().get(step, 0)

    def increment(self, step: str) -> int:
        counts = self._load()
        counts[step] = counts.get(step, 0) + 1
        new = counts[step]
        self._save(counts)
        return new

    def reset(self, step: str) -> None:
        counts = self._load()
        if step in counts:
            counts[step] = 0
            self._save(counts)

    def all(self) -> dict[str, int]:
        return self._load()


# ---------------------------------------------------------------------------
# Redactor + install_failure.json writer
# ---------------------------------------------------------------------------


def redact_log_line(line: str) -> str:
    """Strip secrets from *line* per security review #3.

    Patterns redacted (in order):

    1. ``net user <user> <pw>`` argv -> ``net user <user> <REDACTED>``
    2. ``Authorization: Bearer <token>`` -> ``Authorization: Bearer <REDACTED>``
    3. ``password=``/``token=``/``apikey=``/``api_key=`` (case-insensitive)
       up to next whitespace or quote -> ``<KEY>=<REDACTED>``
    4. Bare base64-ish blobs of 40+ chars -> ``<BASE64-REDACTED>``

    Non-string input is coerced to ``str`` (caller's contract -- this is a
    last line of defence and we should not crash logging on a stray int).
    """
    if not isinstance(line, str):
        line = str(line)
    if not line:
        return line
    out = _NET_USER_RE.sub(rf"\1{_REDACTED}", line)
    out = _AUTH_BEARER_RE.sub(rf"\1{_REDACTED}", out)
    out = _KV_SECRET_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", out)
    out = _BASE64_RE.sub(_BASE64_REDACTED, out)
    return out


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy *payload* with every string value passed through ``redact_log_line``.

    Walks nested dicts and lists. Non-string scalars (int/float/bool/None)
    pass through untouched. The input dict is not mutated.
    """

    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            return redact_log_line(value)
        if isinstance(value, dict):
            return {k: _walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_walk(item) for item in value]
        if isinstance(value, tuple):
            return tuple(_walk(item) for item in value)
        return value

    walked = _walk(payload)
    if not isinstance(walked, dict):  # pragma: no cover - defensive
        raise TypeError("redact_payload requires a dict at the top level")
    return walked


def _validate_install_failure(payload: dict[str, Any], schema_path: Path | None) -> None:
    """Raise ``ValueError`` if *payload* fails install_failure schema validation.

    Uses ``jsonschema`` if available and *schema_path* is provided; otherwise
    falls back to checking that all required top-level fields are present.
    """
    if not isinstance(payload, dict):
        raise ValueError("install_failure payload must be a dict")
    if schema_path is not None and _jsonschema is not None:
        try:
            schema_text = schema_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"install_failure schema unreadable: {exc}") from exc
        try:
            schema = json.loads(schema_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"install_failure schema is not valid JSON: {exc}") from exc
        try:
            _jsonschema.validate(instance=payload, schema=schema)
        except _jsonschema.ValidationError as exc:  # type: ignore[attr-defined]
            raise ValueError(f"install_failure schema validation failed: {exc.message}") from exc
        return
    missing = [field for field in _INSTALL_FAILURE_REQUIRED if field not in payload]
    if missing:
        raise ValueError("install_failure payload missing required field(s): " + ", ".join(missing))


def write_install_failure(
    path: Path,
    payload: dict[str, Any],
    schema_path: Path | None = None,
) -> None:
    """Write a sanitized ``install_failure.json`` to *path* atomically.

    Validation order (security review #3): validate first, redact second,
    then write. Validation failures raise ``ValueError`` *before* anything
    is written so a malformed payload never lands on disk.

    If *schema_path* is given and the optional ``jsonschema`` dev dep is
    installed, full schema validation runs; otherwise a minimal
    "required-fields-present" check is used. ``jsonschema`` is intentionally
    not a base runtime dependency.
    """
    _validate_install_failure(payload, schema_path)
    sanitized = redact_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(sanitized, fh, sort_keys=True, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, str(path))
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
