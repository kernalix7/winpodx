# SPDX-License-Identifier: MIT
"""Host-locale detection + IANA-to-Windows timezone translation.

Used by the compose generator (and, later, the GUI/CLI wizards) to
populate ``cfg.pod.timezone`` with a sensible default from the host
environment without forcing the user through a prompt.

Detection precedence (timezone):
  1. ``timedatectl show --property=Timezone --value`` (systemd hosts;
     authoritative).
  2. ``readlink /etc/localtime`` -> trailing zone path
     (e.g. ``/usr/share/zoneinfo/Asia/Seoul`` -> ``Asia/Seoul``). Works
     on systems without systemd or with broken systemd.
  3. First non-blank line of ``/etc/timezone`` (Debian-family fallback).
  4. ``"UTC"`` (last-resort safe default).

IANA-to-Windows mapping is loaded from ``data/locale/windows_zones.toml``
(shipped alongside the package). The table is the "001" wildcard entries
from the CLDR ``windowsZones.xml`` -- canonical mapping without per-
country variants.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from winpodx.utils.paths import bundle_dir

log = logging.getLogger(__name__)

_FALLBACK_TZ = "UTC"
_WINDOWS_ZONES_REL_PATH = ("data", "locale", "windows_zones.toml")

# Cache the loaded mapping after first read. The TOML file is tiny
# (~5 KB) but compose generation can run repeatedly inside a single
# process (tests, GUI re-saves), and re-parsing on each call would be
# wasteful. ``None`` = not yet loaded; ``{}`` = loaded but empty.
_MAPPING_CACHE: dict[str, str] | None = None


def detect_timezone() -> str:
    """Return the host's IANA timezone, or ``"UTC"`` on every failure.

    Never raises -- callers can treat the return value as the source of
    truth for what to send to the Windows guest. An "UTC" return is
    indistinguishable from a host genuinely on UTC; callers that need to
    know "did detection actually fire" should call the underlying
    helpers directly.
    """
    for helper in (_tz_from_timedatectl, _tz_from_localtime_symlink, _tz_from_etc_timezone):
        try:
            value = helper()
        except Exception as e:  # noqa: BLE001 -- defensive: any helper failure
            log.debug("timezone helper %s raised: %s", helper.__name__, e)
            continue
        if value:
            return value
    return _FALLBACK_TZ


def _tz_from_timedatectl() -> str | None:
    """systemd: ``timedatectl show -p Timezone --value`` -> ``Asia/Seoul``."""
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=Timezone", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _tz_from_localtime_symlink() -> str | None:
    """``/etc/localtime`` -> ``/usr/share/zoneinfo/Asia/Seoul`` -> ``Asia/Seoul``."""
    localtime = Path("/etc/localtime")
    try:
        target = os.readlink(localtime)
    except OSError:
        return None
    # Split on the zoneinfo prefix so we tolerate both
    # ``/usr/share/zoneinfo/...`` and the rarer
    # ``../usr/share/zoneinfo/...`` symlink form.
    marker = "/zoneinfo/"
    idx = target.find(marker)
    if idx < 0:
        return None
    suffix = target[idx + len(marker) :]
    suffix = suffix.strip().strip("/")
    return suffix or None


def _tz_from_etc_timezone() -> str | None:
    """Debian-family fallback: first non-blank line of ``/etc/timezone``."""
    path = Path("/etc/timezone")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return None


def _load_mapping() -> dict[str, str]:
    """Read and cache the IANA -> Windows TZ ID table."""
    global _MAPPING_CACHE
    if _MAPPING_CACHE is not None:
        return _MAPPING_CACHE

    path = bundle_dir().joinpath(*_WINDOWS_ZONES_REL_PATH)
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.warning("could not load %s: %s; timezone translation disabled", path, e)
        _MAPPING_CACHE = {}
        return _MAPPING_CACHE

    mapping = data.get("mapping", {})
    if not isinstance(mapping, dict):
        log.warning("%s: [mapping] is not a table; ignoring", path)
        _MAPPING_CACHE = {}
        return _MAPPING_CACHE

    _MAPPING_CACHE = {str(k): str(v) for k, v in mapping.items()}
    return _MAPPING_CACHE


def iana_to_windows(iana: str) -> str:
    """Translate an IANA zone (e.g. ``Asia/Seoul``) to a Windows TZ ID.

    Falls back to ``"UTC"`` when the input doesn't appear in the mapping
    table -- safer than emitting an invalid string that ``tzutil /s``
    would reject and stall the OEM stage on.
    """
    if not iana:
        return "UTC"
    mapping = _load_mapping()
    return mapping.get(iana, "UTC")


def resolve_timezone_for_oem(configured: str) -> str:
    """Resolve ``cfg.pod.timezone`` to a Windows TZ ID for OEM consumption.

    Resolution order:
      * Empty string -> detect host IANA, translate to Windows ID.
      * Explicit IANA name (contains ``/``) -> translate via the mapping
        table; falls back to ``"UTC"`` if unknown.
      * Explicit Windows ID (no ``/``) -> pass through verbatim. We
        deliberately do NOT validate against a Windows-side list because
        the CLDR table only covers the "001" wildcard subset and users
        on niche territories (e.g. ``Russia Time Zone 11``) need to be
        able to set it without us shipping every variant.
      * ``"UTC"`` / ``"utc"`` -> ``"UTC"``.

    Never raises.
    """
    raw = (configured or "").strip()
    if not raw:
        iana = detect_timezone()
        return iana_to_windows(iana)
    if raw.upper() == "UTC":
        return "UTC"
    if "/" in raw:
        # IANA-shaped -- translate.
        return iana_to_windows(raw)
    # Already a Windows TZ ID (no slash, e.g. "Korea Standard Time").
    return raw
