"""MIME type registration for Windows applications."""

from __future__ import annotations

import configparser
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from winpodx.core.app import AppInfo
from winpodx.utils.paths import applications_dir, config_dir

log = logging.getLogger(__name__)


def register_mime_types(app: AppInfo) -> None:
    """Register MIME type associations for a Windows app."""
    desktop_file = applications_dir() / f"winpodx-{app.name}.desktop"
    if not desktop_file.exists():
        return

    for mime in app.mime_types:
        try:
            result = subprocess.run(
                ["xdg-mime", "default", desktop_file.name, mime],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                log.warning(
                    "xdg-mime register %s failed: %s",
                    mime,
                    result.stderr.strip(),
                )
        except FileNotFoundError:
            log.debug("xdg-mime not found, skipping MIME registration")
            break


def unregister_mime_types(app: AppInfo) -> None:
    """Remove MIME associations for a Windows app, preserving other handlers."""
    mimeapps = config_dir().parent / "mimeapps.list"
    if not mimeapps.exists():
        return

    desktop_name = f"winpodx-{app.name}.desktop"

    # strict=False tolerates duplicate sections; '=' delimiter avoids ':' in MIME types.
    parser = configparser.RawConfigParser(strict=False, delimiters=("=",))
    # Preserve case - MIME types and desktop filenames are case-sensitive.
    parser.optionxform = str  # type: ignore[assignment,method-assign]

    try:
        parser.read(mimeapps, encoding="utf-8")
    except configparser.Error as e:
        log.warning("Failed to parse %s: %s", mimeapps, e)
        return

    changed = False
    for section in parser.sections():
        # Iterate over a snapshot - mutating section while walking it.
        for key in list(parser[section].keys()):
            raw = parser.get(section, key)
            entries = [e for e in raw.split(";") if e]
            kept = [e for e in entries if e != desktop_name]
            if len(kept) == len(entries):
                continue

            changed = True
            if not kept:
                parser.remove_option(section, key)
            else:
                # Freedesktop convention: values end with trailing ';'.
                parser.set(section, key, ";".join(kept) + ";")

    if not changed:
        return

    _atomic_write_configparser(mimeapps, parser)


def _atomic_write_configparser(path: Path, parser: configparser.RawConfigParser) -> None:
    """Write a RawConfigParser to path atomically via tempfile + rename."""
    # Same-dir tempfile keeps os.replace atomic; delete=False since we rename.
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        # space_around_delimiters=False mirrors xdg-mime's output format.
        parser.write(tmp, space_around_delimiters=False)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
