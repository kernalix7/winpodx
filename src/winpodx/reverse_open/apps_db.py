# SPDX-License-Identifier: MIT
"""Load the staged ``apps.json`` manifest into an in-memory lookup table.

The listener (``listener.py``) consults this database on every incoming
guest request: it needs to (a) verify the requested ``app`` slug is one
the user actually has registered, (b) recover the pre-validated argv to
spawn, and (c) confirm the icon path. Doing the JSON parse + validation
once at process start saves us from re-validating per request, and the
type-checked :class:`AppEntry` shape makes the spawn site impossible to
accidentally call with a string argv (the original Phase 1 review's
biggest landmine).

The database is intentionally immutable after load. ``refresh()`` from
the CLI writes a new manifest on disk; the listener must be signalled
(SIGUSR1 in lifecycle.py) to re-load. Doing it that way avoids a race
where the disk file is mid-write while the listener tries to read.

See ``docs/design/REVERSE_OPEN_DESIGN.md`` §"File schema (host →
guest)" + §"Component contracts → listener.py" — the apps.json shape
must stay in sync with what ``cli/host_open._app_to_dict`` writes.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")

# Field codes the spec defines as path placeholders on the Exec= line.
# When the guest hands us a path, exactly ONE of these tokens (if
# present) gets the substituted value; the rest are silently dropped
# at substitution time. discovery.py already strips the always-unused
# codes (%c %k %i etc.), so anything left here is either a placeholder
# or a literal argv string.
_PATH_PLACEHOLDERS = frozenset({"%f", "%u", "%F", "%U"})


@dataclass(frozen=True)
class AppEntry:
    """A single entry from the manifest, validated at load time."""

    slug: str
    name: str
    comment: str
    exec_argv: list[str]
    icon_name: str
    mime_types: list[str]
    desktop_file: str
    is_default_for: list[str] = field(default_factory=list)


class AppsDatabase:
    """In-memory lookup of registered host apps by slug.

    Constructed via :meth:`load` from a manifest path. Use
    :meth:`get` to fetch a validated :class:`AppEntry`; returns
    ``None`` if the slug isn't present. The listener treats a ``None``
    return as a permanent reject (no arbitrary app names).
    """

    def __init__(self, entries: dict[str, AppEntry], generated_at: str):
        self._entries = entries
        self._generated_at = generated_at

    @property
    def generated_at(self) -> str:
        return self._generated_at

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, slug: str) -> bool:
        return slug in self._entries

    def get(self, slug: str) -> AppEntry | None:
        return self._entries.get(slug)

    def slugs(self) -> list[str]:
        return sorted(self._entries)

    @classmethod
    def empty(cls) -> AppsDatabase:
        """Return an empty database — used when the manifest is missing."""
        return cls({}, "")

    @classmethod
    def load(cls, manifest_path: Path) -> AppsDatabase:
        """Load and validate the manifest at ``manifest_path``.

        Missing file or malformed JSON returns an empty database with
        a logged warning — the listener stays up and rejects every
        incoming request rather than crashing. This is a defensive
        choice: a corrupt manifest is recoverable by re-running
        ``winpodx host-open refresh``, but a crashed listener would
        require manual intervention.
        """
        if not manifest_path.is_file():
            log.info("apps_db: manifest not found at %s", manifest_path)
            return cls.empty()
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("apps_db: cannot parse %s: %s", manifest_path, exc)
            return cls.empty()
        if not isinstance(data, dict):
            log.warning("apps_db: top-level is not an object in %s", manifest_path)
            return cls.empty()
        if data.get("version") != 1:
            log.warning(
                "apps_db: unsupported manifest version %r in %s",
                data.get("version"),
                manifest_path,
            )
            return cls.empty()

        generated_at = str(data.get("generated_at", ""))
        entries: dict[str, AppEntry] = {}
        for raw in data.get("apps", []) or []:
            entry = _validate_entry(raw)
            if entry is None:
                continue
            entries[entry.slug] = entry
        return cls(entries, generated_at)


def _validate_entry(raw: object) -> AppEntry | None:
    """Validate one ``apps`` array element. Returns ``None`` on failure."""
    if not isinstance(raw, dict):
        return None
    slug = raw.get("slug")
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    exec_argv = raw.get("exec_argv")
    if not isinstance(exec_argv, list) or not exec_argv:
        return None
    if not all(isinstance(tok, str) for tok in exec_argv):
        return None
    icon_name = raw.get("icon_name") or ""
    if not isinstance(icon_name, str):
        return None
    mime_types = raw.get("mime_types") or []
    if not isinstance(mime_types, list) or not all(isinstance(m, str) for m in mime_types):
        return None
    desktop_file = raw.get("desktop_file") or ""
    if not isinstance(desktop_file, str):
        return None
    is_default_for = raw.get("is_default_for") or []
    if not isinstance(is_default_for, list) or not all(isinstance(m, str) for m in is_default_for):
        return None
    comment = raw.get("comment") or ""
    if not isinstance(comment, str):
        return None
    return AppEntry(
        slug=slug,
        name=name.strip(),
        comment=comment,
        exec_argv=list(exec_argv),
        icon_name=icon_name,
        mime_types=list(mime_types),
        desktop_file=desktop_file,
        is_default_for=list(is_default_for),
    )


def substitute_path(exec_argv: list[str], path: str) -> list[str]:
    """Substitute the first path placeholder in argv with ``path``.

    Walks the argv tokens in order. The first whole-token placeholder
    (``%f`` / ``%u`` / ``%F`` / ``%U``) is replaced with ``path`` as a
    single argv slot — never re-shelled, never split. Subsequent
    placeholders are dropped. If no placeholder is present, ``path``
    is appended as a final argv slot.

    Returns a fresh list; the input is not mutated.
    """
    out: list[str] = []
    substituted = False
    for tok in exec_argv:
        if tok in _PATH_PLACEHOLDERS:
            if not substituted:
                out.append(path)
                substituted = True
            # Subsequent placeholders silently dropped — see
            # design doc § "argv substitution semantics".
            continue
        out.append(tok)
    if not substituted:
        out.append(path)
    return out
