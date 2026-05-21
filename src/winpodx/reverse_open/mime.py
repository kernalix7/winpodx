# SPDX-License-Identifier: MIT
"""MIME type → Windows file-extension mapping (#48, design decision #1).

The reverse-open layer needs to ask, for every Linux ``.desktop`` we
plan to register on the Windows guest: *which file extensions does
this app handle?* The Linux ``.desktop`` lists MIME types
(``MimeType=text/xml;application/xml``), but the Windows registry
keys under ``HKCU\\Software\\Classes\\<.ext>\\OpenWithProgids`` are
extension-keyed, not MIME-keyed. So we have to translate.

Hybrid strategy (per ``docs/design/REVERSE_OPEN_DESIGN.md`` §
"Resolved design decisions" #1):

1. **Curated table** (``CURATED_MIME_EXT``) for the top ~80
   unambiguous mappings — text/plain → ``.txt``, application/pdf →
   ``.pdf``, image/png → ``.png``, … This is fast, deterministic,
   and gives us editorial control over which extension wins for
   types that *technically* have multiple registered globs but
   where one is overwhelmingly the convention on Windows
   (text/html → ``.html``, not ``.htm``).
2. **Generated fallback** for the long tail. Tries ``xdg.Mime``
   from pyxdg first; if pyxdg isn't installed (it's a soft dep),
   falls back to a stdlib ``xml.etree`` parse of
   ``/usr/share/mime/packages/freedesktop.org.xml`` — the same
   shared MIME-info database every Linux DE uses.

Ambiguous types: ``image/*``, ``application/octet-stream``, and a
handful of others legitimately match many extensions. Per design
decision #1, :func:`mime_to_extensions` returns the *first* one
only — this is what we register the app under in the Windows
registry. The GUI's "More types…" expander uses
:func:`mime_to_all_extensions` to surface the full set so the user
can opt in to additional registrations by hand.

Both functions are total — they never raise on bad input
(non-string, empty, unknown MIME). They return ``[]`` and the
caller skips that .desktop entry. Defensive on input by design:
this module sits behind a config-file read path, and a malformed
MIME string in a third-party .desktop file should not crash the
refresh.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

__all__ = [
    "CURATED_MIME_EXT",
    "mime_to_all_extensions",
    "mime_to_extensions",
]

log = logging.getLogger(__name__)


# Curated mappings: top ~80 unambiguous MIME types on a typical
# Linux desktop. Order of extensions in each list matters — the
# first is the canonical Windows extension, used for the primary
# registry registration; the rest are surfaced via
# :func:`mime_to_all_extensions` for the GUI's "More types…" UI.
CURATED_MIME_EXT: dict[str, list[str]] = {
    # --- text/* ---
    "text/plain": [".txt"],
    "text/xml": [".xml"],
    "text/html": [".html", ".htm"],
    "text/css": [".css"],
    "text/csv": [".csv"],
    "text/markdown": [".md", ".markdown"],
    "text/x-markdown": [".md"],
    "text/javascript": [".js"],
    "text/x-python": [".py"],
    "text/x-c": [".c"],
    "text/x-c++": [".cpp", ".cc", ".cxx"],
    "text/x-csrc": [".c"],
    "text/x-chdr": [".h"],
    "text/x-java": [".java"],
    "text/x-go": [".go"],
    "text/x-rust": [".rs"],
    "text/x-shellscript": [".sh"],
    "text/x-script.python": [".py"],
    "text/tab-separated-values": [".tsv"],
    "text/rtf": [".rtf"],
    "text/calendar": [".ics"],
    "text/vcard": [".vcf"],
    # --- application/* (documents) ---
    "application/json": [".json"],
    "application/xml": [".xml"],
    "application/pdf": [".pdf"],
    "application/rtf": [".rtf"],
    "application/msword": [".doc"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
    "application/vnd.ms-excel": [".xls"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
    "application/vnd.ms-powerpoint": [".ppt"],
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
    "application/vnd.oasis.opendocument.text": [".odt"],
    "application/vnd.oasis.opendocument.spreadsheet": [".ods"],
    "application/vnd.oasis.opendocument.presentation": [".odp"],
    "application/epub+zip": [".epub"],
    # --- application/* (archives) ---
    "application/zip": [".zip"],
    "application/x-tar": [".tar"],
    "application/gzip": [".gz"],
    "application/x-gzip": [".gz"],
    "application/x-bzip2": [".bz2"],
    "application/x-xz": [".xz"],
    "application/x-7z-compressed": [".7z"],
    "application/vnd.rar": [".rar"],
    "application/x-rar-compressed": [".rar"],
    "application/x-iso9660-image": [".iso"],
    # --- application/* (code / config) ---
    "application/javascript": [".js"],
    "application/x-yaml": [".yaml", ".yml"],
    "application/yaml": [".yaml", ".yml"],
    "application/toml": [".toml"],
    "application/sql": [".sql"],
    "application/x-sh": [".sh"],
    "application/x-shellscript": [".sh"],
    "application/x-perl": [".pl"],
    "application/x-php": [".php"],
    "application/x-ruby": [".rb"],
    # --- image/* ---
    "image/png": [".png"],
    "image/jpeg": [".jpg", ".jpeg"],
    "image/gif": [".gif"],
    "image/bmp": [".bmp"],
    "image/webp": [".webp"],
    "image/svg+xml": [".svg"],
    "image/tiff": [".tiff", ".tif"],
    "image/x-icon": [".ico"],
    "image/vnd.microsoft.icon": [".ico"],
    "image/heic": [".heic"],
    "image/heif": [".heif"],
    "image/avif": [".avif"],
    "image/x-xcf": [".xcf"],
    "image/vnd.adobe.photoshop": [".psd"],
    # --- audio/* ---
    "audio/mpeg": [".mp3"],
    "audio/wav": [".wav"],
    "audio/x-wav": [".wav"],
    "audio/ogg": [".ogg", ".oga"],
    "audio/flac": [".flac"],
    "audio/x-flac": [".flac"],
    "audio/x-m4a": [".m4a"],
    "audio/mp4": [".m4a"],
    "audio/aac": [".aac"],
    "audio/opus": [".opus"],
    "audio/midi": [".mid", ".midi"],
    # --- video/* ---
    "video/mp4": [".mp4"],
    "video/x-matroska": [".mkv"],
    "video/webm": [".webm"],
    "video/quicktime": [".mov"],
    "video/x-msvideo": [".avi"],
    "video/mpeg": [".mpg", ".mpeg"],
    "video/x-flv": [".flv"],
    "video/3gpp": [".3gp"],
    # --- font/* ---
    "font/ttf": [".ttf"],
    "font/otf": [".otf"],
    "font/woff": [".woff"],
    "font/woff2": [".woff2"],
    "application/font-woff": [".woff"],
    "application/x-font-ttf": [".ttf"],
}


# Sentinel for "we tried to import xdg.Mime once, doesn't matter the
# outcome — don't re-log the soft-dep miss." Keyed on a module-level
# single-shot flag rather than functools.lru_cache so the import is
# attempted only once per process.
_XDG_IMPORT_TRIED = False
_XDG_MIME_MODULE = None  # type: ignore[var-annotated]

# Standard freedesktop.org shared-MIME-info database location.
# Distros that ship the database elsewhere are exotic; we fall through
# to empty-list rather than probe a long path list.
_FDO_MIME_XML = Path("/usr/share/mime/packages/freedesktop.org.xml")
_FDO_NS = "http://www.freedesktop.org/standards/shared-mime-info"


def _try_import_xdg() -> Optional[object]:
    """Attempt to import ``xdg.Mime`` exactly once per process.

    pyxdg is a soft dependency — winpodx itself doesn't list it
    (see ``pyproject.toml``); it's used only for this best-effort
    fallback. Returns the module if available, ``None`` otherwise.
    Logs the soft-dep miss at INFO once so support has a breadcrumb
    if a user reports "no extensions found for MIME X".
    """
    global _XDG_IMPORT_TRIED, _XDG_MIME_MODULE
    if _XDG_IMPORT_TRIED:
        return _XDG_MIME_MODULE
    _XDG_IMPORT_TRIED = True
    try:
        from xdg import Mime as _Mime  # type: ignore[import-not-found]

        _XDG_MIME_MODULE = _Mime
        return _Mime
    except ImportError:
        log.info(
            "pyxdg not installed; reverse_open.mime falls back to "
            "stdlib XML parsing of %s for non-curated MIME types",
            _FDO_MIME_XML,
        )
        return None


def _normalise(mime_type: object) -> Optional[str]:
    """Defensive input normalisation.

    Returns a lowercased, whitespace-stripped MIME string, or
    ``None`` if the input isn't a usable string. Empty after
    stripping is also ``None``.
    """
    if not isinstance(mime_type, str):
        return None
    cleaned = mime_type.strip().lower()
    if not cleaned:
        return None
    return cleaned


def _lookup_via_xdg(mime_type: str) -> list[str]:
    """Generated fallback: ask pyxdg's ``xdg.Mime`` for known globs.

    Returns extensions in the order pyxdg yields them (typically
    parse order from the database, which tends to match
    freedesktop.org.xml's source order). Returns ``[]`` if pyxdg
    isn't installed or doesn't know the type.
    """
    mod = _try_import_xdg()
    if mod is None:
        return []
    try:
        # xdg.Mime exposes .lookup(name) -> MIMEtype with a .aliases
        # set; the globs DB is parsed lazily on first lookup. The
        # public attribute we need is .extensions(), which a few
        # pyxdg releases expose, but older releases don't — so we
        # fall through to direct globs2 access.
        mt = mod.lookup(mime_type)  # type: ignore[attr-defined]
        if mt is None:
            return []
        # Try the (newer) explicit method first.
        if hasattr(mt, "extensions"):
            try:
                exts = list(mt.extensions())  # type: ignore[call-arg]
                return [e if e.startswith(".") else f".{e}" for e in exts if e]
            except Exception:  # pragma: no cover - defensive
                pass
        # Older pyxdg releases — read the parsed globs table directly.
        globs = getattr(mod, "globs", None)
        if globs is None:
            return []
        # globs.allglobs is dict[MIMEtype, list[(weight, glob, flags)]]
        all_globs = getattr(globs, "allglobs", None)
        if not isinstance(all_globs, dict):
            return []
        entries = all_globs.get(mt, [])
        out: list[str] = []
        for entry in entries:
            # entry is (weight, glob, flags) or just glob — be liberal.
            glob = entry[1] if isinstance(entry, tuple) and len(entry) >= 2 else entry
            if not isinstance(glob, str):
                continue
            # We only handle the simple "*.ext" form. Anything else
            # (literal filenames like "Makefile", or "*.tar.gz" which
            # has no single Windows extension) is skipped.
            if glob.startswith("*.") and "/" not in glob and "?" not in glob and "[" not in glob:
                ext = glob[1:]  # "*.txt" → ".txt"
                if "." in ext[1:]:
                    # multi-dot like ".tar.gz" — Windows registers
                    # the *last* component, ".gz". Skip; let the
                    # curated table handle these explicitly.
                    continue
                out.append(ext.lower())
        return out
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("xdg.Mime lookup for %r failed: %s", mime_type, exc)
        return []


def _lookup_via_stdlib_xml(mime_type: str) -> list[str]:
    """Stdlib fallback: parse freedesktop.org.xml directly.

    Used when pyxdg isn't installed. The shared-MIME-info package is
    on every Linux desktop with a working DE — if the file is
    missing, we just return ``[]``.

    We only return simple ``*.ext`` globs and skip multi-dot
    patterns (``*.tar.gz``) and literal filenames (``Makefile``)
    for the same reason as the xdg path: there's no single Windows
    extension that corresponds to those.
    """
    if not _FDO_MIME_XML.is_file():
        return []
    try:
        tree = ET.parse(_FDO_MIME_XML)
    except (ET.ParseError, OSError) as exc:  # pragma: no cover - defensive
        log.debug("Failed to parse %s: %s", _FDO_MIME_XML, exc)
        return []
    root = tree.getroot()
    ns = {"mi": _FDO_NS}
    out: list[str] = []
    for mt in root.findall("mi:mime-type", ns):
        type_attr = mt.get("type", "").lower()
        if type_attr == mime_type:
            for glob in mt.findall("mi:glob", ns):
                pattern = glob.get("pattern", "")
                if (
                    pattern.startswith("*.")
                    and "/" not in pattern
                    and "?" not in pattern
                    and "[" not in pattern
                ):
                    ext = pattern[1:]  # "*.txt" → ".txt"
                    if "." in ext[1:]:
                        # multi-dot: skip, see _lookup_via_xdg note.
                        continue
                    out.append(ext.lower())
            # Also walk aliases — sometimes the queried type is an
            # alias of another type whose globs we want. The
            # database lists this as <alias type="..."/> *under*
            # the canonical type, so a forward scan would miss it.
            # Cheap one-pass solution: keep going to other entries
            # and check their <alias> children too.
        for alias in mt.findall("mi:alias", ns):
            if alias.get("type", "").lower() == mime_type:
                for glob in mt.findall("mi:glob", ns):
                    pattern = glob.get("pattern", "")
                    if (
                        pattern.startswith("*.")
                        and "/" not in pattern
                        and "?" not in pattern
                        and "[" not in pattern
                    ):
                        ext = pattern[1:]
                        if "." in ext[1:]:
                            continue
                        out.append(ext.lower())
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    deduped: list[str] = []
    for ext in out:
        if ext not in seen:
            seen.add(ext)
            deduped.append(ext)
    return deduped


def _lookup_generated(mime_type: str) -> list[str]:
    """Combined generated fallback: pyxdg first, stdlib second."""
    via_xdg = _lookup_via_xdg(mime_type)
    if via_xdg:
        return via_xdg
    return _lookup_via_stdlib_xml(mime_type)


def mime_to_extensions(mime_type: str) -> list[str]:
    """Return the canonical Windows extension(s) for ``mime_type``.

    Tries the curated table first; on miss, falls back to the
    generated lookup (pyxdg / stdlib XML). For genuinely ambiguous
    types where the fallback returns multiple extensions, returns
    the **first one only** — that's what the Windows registry
    registration uses. Callers needing the full set (the GUI's
    "More types…" expander) should call :func:`mime_to_all_extensions`.

    Total function: returns ``[]`` for any input it can't resolve
    (non-string, empty, unknown MIME, missing database). Never
    raises.
    """
    normalised = _normalise(mime_type)
    if normalised is None:
        return []
    curated = CURATED_MIME_EXT.get(normalised)
    if curated:
        # Return the first one only per design decision #1.
        return [curated[0]]
    generated = _lookup_generated(normalised)
    if generated:
        return [generated[0]]
    return []


def mime_to_all_extensions(mime_type: str) -> list[str]:
    """Return *all* known Windows extensions for ``mime_type``.

    Used by the GUI's "More types…" detail to let the user opt in
    to registering the app under additional extensions beyond the
    canonical one chosen by :func:`mime_to_extensions`. Order of
    the returned list is preserved from the source (curated entry
    order, or pyxdg / freedesktop.org.xml parse order).

    Total function with the same defensive contract as
    :func:`mime_to_extensions`: returns ``[]`` on any failure.
    """
    normalised = _normalise(mime_type)
    if normalised is None:
        return []
    curated = CURATED_MIME_EXT.get(normalised)
    if curated:
        return list(curated)
    return _lookup_generated(normalised)
