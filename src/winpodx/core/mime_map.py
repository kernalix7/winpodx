# SPDX-License-Identifier: MIT
"""Standard file-extension → MIME-type table for auto file-association (#545).

The Windows guest reports the *real* extensions each discovered app handles (via
``discover_apps.ps1``'s registry scan); this maps those extensions to their
freedesktop MIME types so the host can write the ``.desktop`` ``MimeType=``.

The mapping is a universal extension→MIME constant (a ``.docx`` is always the
Office wordprocessing type regardless of which app opens it), so the host owns
it — only extensions the guest actually reported get mapped, which means no
made-up associations. Unknown extensions are skipped.
"""

from __future__ import annotations

# Extension (lowercase, leading dot) → MIME type. Conservative + standard.
_EXT_TO_MIME: dict[str, str] = {
    # Word processing
    ".doc": "application/msword",
    ".dot": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    ".docm": "application/vnd.ms-word.document.macroenabled.12",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".rtf": "application/rtf",
    # Spreadsheets
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroenabled.12",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".csv": "text/csv",
    # Presentations
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pps": "application/vnd.ms-powerpoint",
    ".ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    # Documents / text
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".xml": "application/xml",
    ".json": "application/json",
    ".htm": "text/html",
    ".html": "text/html",
    # Images
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".ico": "image/vnd.microsoft.icon",
    ".psd": "image/vnd.adobe.photoshop",
    # Audio
    ".mp3": "audio/mpeg",
    ".wav": "audio/x-wav",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    # Video
    ".mp4": "video/mp4",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".wmv": "video/x-ms-wmv",
    # Archives
    ".zip": "application/zip",
    ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar",
}


def mime_for_extension(ext: str) -> str | None:
    """MIME type for a single file extension (``.docx`` or ``docx``), or None."""
    if not ext:
        return None
    e = ext.strip().lower()
    if not e.startswith("."):
        e = "." + e
    return _EXT_TO_MIME.get(e)


def mimes_for_extensions(extensions: list[str]) -> list[str]:
    """Map the guest-reported extensions to MIME types (deduped, order-stable).

    Skips extensions we don't have a standard MIME for. Empty in → empty out.
    """
    out: list[str] = []
    seen: set[str] = set()
    for ext in extensions:
        mime = mime_for_extension(ext)
        if mime and mime not in seen:
            seen.add(mime)
            out.append(mime)
    return out
