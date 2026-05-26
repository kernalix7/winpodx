# SPDX-License-Identifier: MIT
"""winpodx UI internationalisation (the Linux-side tray / GUI / CLI text).

English is the **source** language: the literal string in the code IS the
key, and any language without a translation for it falls back to that
English text. So English is always 100% complete, and a partially
translated language degrades gracefully (English for the missing strings)
rather than showing blanks.

Usage:

    from winpodx.core.i18n import tr
    print(tr("Pod stopped."))

Call :func:`init_from_config` once at process startup (CLI / GUI / tray
entry points) to set the active language from ``cfg.ui.language``.

Catalogs live at ``winpodx/locale/<lang>.json`` as flat
``{english_source: translation}`` maps -- translator-friendly, packaged
with the wheel, loaded via importlib.resources (works from a zip too).
``auto`` resolves the language from the host locale ($LC_ALL / $LC_MESSAGES
/ $LANG); unknown locales fall back to English.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

# Languages with a (possibly partial) catalog. "en" needs none -- it's the
# source. Keep in sync with config._UI_LANGUAGES (minus "auto").
SUPPORTED = ("en", "ko", "zh", "ja", "de", "fr", "it")

_lang: str = "en"
_catalog: dict[str, str] = {}


def _detect_locale_language() -> str:
    """Map the host locale to a SUPPORTED language code, else 'en'."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var, "")
        if not val:
            continue
        # e.g. "ko_KR.UTF-8" -> "ko", "zh_CN" -> "zh"
        code = val.split(".")[0].split("_")[0].strip().lower()
        if code in SUPPORTED:
            return code
        return "en"
    return "en"


def resolve_language(configured: str) -> str:
    """Resolve a config value ('auto' | code) to a concrete SUPPORTED code."""
    c = (configured or "auto").strip().lower()
    if c == "auto":
        return _detect_locale_language()
    return c if c in SUPPORTED else "en"


def _load_catalog(lang: str) -> dict[str, str]:
    """Load ``winpodx/locale/<lang>.json``; empty dict for 'en' or on miss."""
    if lang == "en":
        return {}
    try:
        from importlib.resources import files

        data = (files("winpodx") / "locale" / f"{lang}.json").read_text(encoding="utf-8")
        parsed = json.loads(data)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except (FileNotFoundError, ModuleNotFoundError, OSError, ValueError) as e:
        log.debug("no/invalid i18n catalog for %r: %s", lang, e)
    return {}


def set_language(lang: str) -> None:
    """Set the active UI language (a config value: 'auto' or a code)."""
    global _lang, _catalog
    _lang = resolve_language(lang)
    _catalog = _load_catalog(_lang)
    log.debug("UI language set to %s (%d catalog entries)", _lang, len(_catalog))


def init_from_config(cfg: object | None = None) -> None:
    """Set the language from ``cfg.ui.language`` (loads config if omitted)."""
    try:
        if cfg is None:
            from winpodx.core.config import Config

            cfg = Config.load()
        set_language(cfg.ui.language)  # type: ignore[attr-defined]
    except Exception as e:  # noqa: BLE001 -- i18n must never break startup
        log.debug("i18n init fell back to English: %s", e)
        set_language("en")


def current_language() -> str:
    """The active resolved language code (e.g. 'en', 'ko')."""
    return _lang


def tr(text: str) -> str:
    """Translate ``text`` to the active language; English source on miss."""
    if not _catalog:
        return text
    return _catalog.get(text, text)
