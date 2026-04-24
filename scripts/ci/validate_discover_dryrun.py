#!/usr/bin/env python3
"""Smoke-test validator for ``scripts/windows/discover_apps.ps1 -DryRun``.

Run from CI: ``python3 scripts/ci/validate_discover_dryrun.py <json_file>``.

Asserts that the PS script's canned output parses as a JSON array with at
least one entry whose keys match the schema ``core.discovery`` expects.
Factored out of ``.github/workflows/ci.yml`` so the assertions don't need
to survive a round-trip through YAML + bash + Python quoting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED_FIELDS = frozenset(
    {"name", "path", "args", "source", "wm_class_hint", "launch_uri", "icon_b64"}
)
VALID_SOURCES = frozenset({"win32", "uwp", "steam"})


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: validate_discover_dryrun.py <json_file>", file=sys.stderr)
        return 2

    raw = Path(argv[1]).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"FAIL: not valid JSON: {exc}", file=sys.stderr)
        print(f"head: {raw[:200]!r}", file=sys.stderr)
        return 1

    if not isinstance(data, list):
        print(f"FAIL: expected array, got {type(data).__name__}", file=sys.stderr)
        return 1
    if not data:
        print("FAIL: expected at least one canned entry", file=sys.stderr)
        return 1

    first = data[0]
    if not isinstance(first, dict):
        print(f"FAIL: first element must be object, got {type(first).__name__}", file=sys.stderr)
        return 1

    missing = REQUIRED_FIELDS - set(first.keys())
    if missing:
        print(f"FAIL: canned entry missing fields: {sorted(missing)}", file=sys.stderr)
        return 1

    source = first.get("source")
    if source not in VALID_SOURCES:
        print(
            f"FAIL: invalid source {source!r}; must be one of {sorted(VALID_SOURCES)}",
            file=sys.stderr,
        )
        return 1

    print("OK: discover_apps.ps1 -DryRun JSON shape valid")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
