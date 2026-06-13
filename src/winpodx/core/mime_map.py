# SPDX-License-Identifier: MIT
"""File-extension → MIME-type resolution for auto file-association (#545).

The MIME type of an extension is a system fact, not a winpodx opinion, so this
resolves it from the host's freedesktop shared-mime-info database
(``/usr/share/mime/globs``) — every file type the desktop knows about, not a
hand-maintained list. The guest reports the real extensions each app handles;
this maps them to the exact MIME the file manager uses for "Open with".
"""

from __future__ import annotations

import mimetypes
from functools import lru_cache
from pathlib import Path

# freedesktop shared-mime-info glob databases. The first one that exists wins;
# ``globs2`` (weighted) is preferred over plain ``globs`` where both are present.
_GLOBS_FILES: tuple[str, ...] = (
    "/usr/share/mime/globs2",
    "/usr/share/mime/globs",
    "/usr/local/share/mime/globs2",
    "/usr/local/share/mime/globs",
)


@lru_cache(maxsize=1)
def _ext_mime_db() -> dict[str, str]:
    """Build ``{".ext": "mime/type"}`` from the system shared-mime-info globs.

    Parsed once. ``globs`` lines are ``mime:*.ext``; ``globs2`` lines are
    ``weight:mime:*.ext`` (extra trailing fields ignored). Only simple
    ``*.ext`` patterns are taken — literal globs, ``*.*``, and full-name
    patterns are skipped. The highest-weight (first) entry for an extension
    wins, matching how the file manager resolves it.
    """
    db: dict[str, str] = {}
    for path in _GLOBS_FILES:
        p = Path(path)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) == 2:  # globs: mime:glob
                mime, glob = parts[0], parts[1]
            elif len(parts) >= 3 and parts[0].isdigit():  # globs2: weight:mime:glob[:...]
                mime, glob = parts[1], parts[2]
            else:
                continue
            if not glob.startswith("*."):
                continue
            stem = glob[2:]
            if not stem or any(c in stem for c in "*?["):
                continue
            db.setdefault("." + stem.lower(), mime)
        if db:
            break  # first usable globs file is the authoritative one
    return db


def mime_for_extension(ext: str) -> str | None:
    """MIME type for a file extension via the system MIME db, or None if unknown.

    Accepts ``.docx`` or ``docx``. Falls back to Python's :mod:`mimetypes`
    (``/etc/mime.types`` etc.) for anything the freedesktop globs didn't cover.
    """
    if not ext:
        return None
    e = ext.strip().lower()
    if not e.startswith("."):
        e = "." + e
    hit = _ext_mime_db().get(e)
    if hit:
        return hit
    guess, _ = mimetypes.guess_type("x" + e)
    return guess


def mimes_for_extensions(extensions: list[str]) -> list[str]:
    """Map the guest-reported extensions to MIME types (deduped, order-stable).

    Skips extensions with no known MIME. Empty in → empty out.
    """
    out: list[str] = []
    seen: set[str] = set()
    for ext in extensions:
        mime = mime_for_extension(ext)
        if mime and mime not in seen:
            seen.add(mime)
            out.append(mime)
    return out
