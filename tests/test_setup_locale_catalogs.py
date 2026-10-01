# SPDX-License-Identifier: MIT
"""Guard the machine-consumed ``tr()`` keys for CLI storage/ISO text (#849).

The lookup keys are derived from the current source with :mod:`ast` instead
of being pinned as prose, so this guard follows the real ``tr()`` calls: every
literal key the storage/ISO helpers hand to :func:`winpodx.core.i18n.tr` must
exist in each shipped catalog, carry a non-empty translation, and keep the
same ``str.format`` placeholders as the English source key.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path
from string import Formatter

import pytest

from winpodx.core import i18n

_SOURCE = Path(__file__).resolve().parents[1] / "src" / "winpodx" / "cli" / "setup_cmd.py"
_FUNCTIONS = ("_prompt_storage_and_iso", "_decide_storage_mode", "_stage_win_iso")
_CATALOG_LANGS = tuple(lang for lang in i18n.SUPPORTED if lang != "en")


def _format_fields(text: str) -> Counter[str]:
    """Counter of ``str.format`` field names referenced by ``text``."""
    return Counter(field for _, field, _, _ in Formatter().parse(text) if field is not None)


def _literal_tr_keys(node: ast.AST) -> set[str]:
    """Literal first-argument strings of every ``tr(...)`` call under ``node``."""
    keys: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.Call):
            continue
        func = inner.func
        if not isinstance(func, ast.Name) or func.id != "tr" or not inner.args:
            continue
        first = inner.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            keys.add(first.value)
    return keys


def _source_storage_iso_keys() -> set[str]:
    """AST-extract the literal ``tr()`` lookup keys used by the storage/ISO helpers."""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    wanted = set(_FUNCTIONS)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            keys |= _literal_tr_keys(node)
    return keys


def _has_value(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def test_storage_iso_keys_are_collected_from_source() -> None:
    keys = _source_storage_iso_keys()
    assert keys, (
        "AST derivation found no literal tr() keys in "
        f"{', '.join(_FUNCTIONS)}; source layout changed"
    )


@pytest.mark.parametrize("lang", _CATALOG_LANGS)
def test_storage_iso_keys_are_translated(lang: str) -> None:
    keys = _source_storage_iso_keys()
    catalog = i18n._load_catalog(lang)

    missing = sorted(key for key in keys if not _has_value(catalog.get(key)))
    if missing:
        body = "\n".join(f"  - {key!r}" for key in missing)
        pytest.fail(f"{lang} catalog is missing {len(missing)} storage/ISO key(s):\n{body}")

    mismatched = sorted(key for key in keys if _format_fields(catalog[key]) != _format_fields(key))
    if mismatched:
        body = "\n".join(
            f"  - {key!r}: expected {dict(_format_fields(key))}, "
            f"got {dict(_format_fields(catalog[key]))}"
            for key in mismatched
        )
        pytest.fail(f"{lang} translation(s) have mismatched format placeholders:\n{body}")
