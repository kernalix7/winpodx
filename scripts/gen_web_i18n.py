#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Regenerate ``web/lang/translations.js`` from the per-language ``web/lang/*.json``
catalogs.

The winpodx.org site loads its translations from a single bundled
``translations.js`` (``window.WPX_I18N``) via a ``<script>`` tag, not by
fetching the individual JSON files -- that keeps language switching working on
``file://`` as well as ``https``. That bundle is generated, so editing a
``web/lang/<lang>.json`` catalog has no effect on the live site until this
script rebuilds the bundle. Run it after any catalog edit:

    python3 scripts/gen_web_i18n.py

It is idempotent: with no catalog changes it rewrites an identical file.
"""

from __future__ import annotations

import json
from pathlib import Path

# English is the inline source text in the HTML (data-i18n elements), so the
# bundle carries only the non-English catalogs -- adding "en" would just
# duplicate the inline defaults and bloat the file. web/lang/en.json stays the
# reviewable English source of record that translators mirror.
_LANGS = ("de", "fr", "it", "ja", "ko", "zh")
_HEADER = (
    "// SPDX-License-Identifier: MIT — winpodx.org bundled translations "
    "(auto-generated from lang/*.json).\n"
    "// Loaded via <script> so language switching works on file:// and https "
    "alike (no fetch).\n"
)


def _lang_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "web" / "lang"


def build_bundle() -> str:
    lang_dir = _lang_dir()
    bundle: dict[str, dict[str, str]] = {}
    for lang in _LANGS:
        path = lang_dir / f"{lang}.json"
        with path.open(encoding="utf-8") as fh:
            bundle[lang] = json.load(fh)
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"{_HEADER}window.WPX_I18N = {payload};\n"


def main() -> int:
    out = _lang_dir() / "translations.js"
    out.write_text(build_bundle(), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
