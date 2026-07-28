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


# Windows installation locale, detected from the host (#791, #790).
#
# dockur takes three separate values and bakes them into the Sysprep answer
# file at FIRST BOOT: LANGUAGE (the UI language pack), REGION (the BCP 47 tag
# behind date/number/currency formatting) and KEYBOARD (the input layout).
# They are not applied again afterwards, so the defaults matter more than most
# — a guest installed as English/en-001 stays that way until it is reinstalled.
#
# The names on the left of _DOCKUR_LANGUAGE_BY_TAG are what dockur's own
# language list accepts; they are not BCP 47 and not interchangeable with the
# region tags. Kept in sync with the picker in gui/_main_window_settings.py.
_DOCKUR_LANGUAGE_BY_TAG: dict[str, str] = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hr": "Croatian",
    "hu": "Hungarian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "nb": "Norwegian",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sr": "Serbian",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "zh": "Chinese",
}

# Territory changes which language pack dockur installs, not just the region:
# Traditional and Simplified Chinese are different packs, and so are the two
# Portuguese and two Spanish variants.
_DOCKUR_LANGUAGE_BY_LOCALE: dict[str, str] = {
    "zh-TW": "Traditional Chinese",
    "zh-HK": "Traditional Chinese",
    "zh-MO": "Traditional Chinese",
    "pt-BR": "Brazilian Portuguese",
    "es-MX": "Mexican Spanish",
}

# Region / keyboard tags dockur accepts. A host locale outside this set keeps
# the English default rather than handing dockur a tag it will reject at
# install time, which would be a much worse failure than the wrong language.
_DOCKUR_REGION_TAGS: frozenset[str] = frozenset(
    {
        "ar-SA",
        "cs-CZ",
        "da-DK",
        "de-DE",
        "el-GR",
        "en-001",
        "en-GB",
        "en-US",
        "es-ES",
        "es-MX",
        "fi-FI",
        "fr-FR",
        "he-IL",
        "hu-HU",
        "it-IT",
        "ja-JP",
        "ko-KR",
        "nb-NO",
        "nl-NL",
        "pl-PL",
        "pt-BR",
        "pt-PT",
        "ru-RU",
        "sv-SE",
        "th-TH",
        "tr-TR",
        "uk-UA",
        "zh-CN",
        "zh-TW",
    }
)

DEFAULT_INSTALL_LOCALE = ("English", "en-001", "en-US")


def _posix_locale_from_env() -> str | None:
    """Return the host's ``ll_CC`` locale from the environment, or None.

    Reads the POSIX precedence order. ``C`` and ``POSIX`` mean "no locale
    configured" and give None, so a stripped-down shell environment falls back
    to the English default rather than being read as a preference.
    """
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        # Strip the codeset and any @modifier: "sr_RS.UTF-8@latin" -> "sr_RS".
        value = raw.split(".", 1)[0].split("@", 1)[0]
        if value.upper() in ("C", "POSIX", ""):
            continue
        return value
    return None


def detect_install_locale() -> tuple[str, str, str]:
    """Detect ``(language, region, keyboard)`` for the Windows install.

    Falls back to :data:`DEFAULT_INSTALL_LOCALE` whenever the host locale is
    absent, malformed, or outside the set dockur accepts — a wrong-language
    guest is recoverable, an install that aborts on a rejected tag is not.

    Never raises.
    """
    posix = _posix_locale_from_env()
    if not posix or "_" not in posix:
        return DEFAULT_INSTALL_LOCALE

    lang, _, territory = posix.partition("_")
    lang = lang.lower()
    tag = f"{lang}-{territory.upper()}"

    if tag not in _DOCKUR_REGION_TAGS:
        return DEFAULT_INSTALL_LOCALE

    language = _DOCKUR_LANGUAGE_BY_LOCALE.get(tag) or _DOCKUR_LANGUAGE_BY_TAG.get(lang)
    if language is None:
        return DEFAULT_INSTALL_LOCALE

    # Keyboard follows the region: dockur's keyboard list is the same set of
    # tags, and a layout that does not match the language is rarely what
    # someone wants from an autodetected default.
    return language, tag, tag
