# SPDX-License-Identifier: MIT
"""Tests for the UI i18n layer (winpodx.core.i18n)."""

from __future__ import annotations

from collections import Counter
from string import Formatter

import pytest

from winpodx.core import devices as D
from winpodx.core import i18n
from winpodx.core.config import Config


@pytest.fixture(autouse=True)
def _reset_lang():
    # Each test starts from English; restore after.
    i18n.set_language("en")
    yield
    i18n.set_language("en")


def test_resolve_explicit_and_unknown() -> None:
    assert i18n.resolve_language("ko") == "ko"
    assert i18n.resolve_language("it") == "it"
    assert i18n.resolve_language("xx") == "en"  # unsupported -> English
    assert i18n.resolve_language("") == i18n.resolve_language("auto")


def test_resolve_auto_from_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    assert i18n.resolve_language("auto") == "ko"
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert i18n.resolve_language("auto") == "fr"
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")  # unsupported locale
    assert i18n.resolve_language("auto") == "en"


def test_tr_english_is_identity() -> None:
    i18n.set_language("en")
    assert i18n.tr("Pod stopped.") == "Pod stopped."


def test_tr_translates_and_falls_back() -> None:
    i18n.set_language("ko")
    # A real catalog key translates to non-English (don't hardcode the exact
    # wording -- just assert it changed). "Settings" is a wrapped tr() key.
    assert i18n.tr("Settings") != "Settings"
    # Unseeded string -> English source (graceful fallback, never blank).
    assert i18n.tr("totally-unseeded-string-xyz") == "totally-unseeded-string-xyz"


def test_all_supported_catalogs_load_and_are_flat_str_maps() -> None:
    for lang in i18n.SUPPORTED:
        i18n.set_language(lang)
        # tr must always return a str (no crash, no None) for any input.
        assert isinstance(i18n.tr("High"), str)


def _format_fields(text: str) -> Counter[str]:
    return Counter(field for _, field, _, _ in Formatter().parse(text) if field is not None)


@pytest.mark.parametrize("lang", [lang for lang in i18n.SUPPORTED if lang != "en"])
def test_pr819_ui_keys_are_translated_with_matching_placeholders(lang: str) -> None:
    catalog = i18n._load_catalog(lang)
    keys = {
        "Applications",
        "Launch an app or pin one from Applications to see it here.",
        "Applications are hidden",
        "Bus {bus}",
        "IOMMU {group}",
        "WARNING",
        *D._PCI_CLASS_NAMES.values(),
        "PCI device",
    }

    for key in keys:
        assert key in catalog, f"{lang} is missing {key!r}"
        assert catalog[key].strip(), f"{lang} has a blank translation for {key!r}"
        assert _format_fields(catalog[key]) == _format_fields(key)


def test_config_ui_language_default_and_coerce() -> None:
    cfg = Config()
    assert cfg.ui.language == "auto"
    cfg.ui.language = "KO"
    cfg.ui.__post_init__()
    assert cfg.ui.language == "ko"  # normalized
    cfg.ui.language = "bogus"
    cfg.ui.__post_init__()
    assert cfg.ui.language == "auto"  # invalid -> auto
