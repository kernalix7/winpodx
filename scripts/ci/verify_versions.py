#!/usr/bin/env python3
"""Verify that the project version string matches across all version-stamped files.

Runs during the lint stage of CI so a release-prep commit that forgets to bump
one of the version-stamped files (the original sin behind v0.5.2's mis-named
`winpodx_0.5.1_*.deb` assets, which shipped because `debian/changelog` stayed
at 0.5.1 while `pyproject.toml` moved to 0.5.2) fails CI before the tag is
pushed, not after.

Single source of truth: ``[project] version`` in ``pyproject.toml``.

Files that have to agree with it:
  - ``debian/changelog`` (first entry: ``winpodx (X.Y.Z) ...``)
  - ``packaging/rpm/winpodx.spec`` (``Version: X.Y.Z`` -- the local-build
    cosmetic literal; OBS rewrites this from the tarball name at publish
    time, but a stale literal still misleads anyone running a manual rpmbuild
    and the v0.5.2 incident proved a missed bump on ONE packaging file ships)

Plus a round-trip sanity check: ``importlib.metadata.version("winpodx")``
matches when the package is actually installed (catches a broken install
that would otherwise silently report the wrong version at runtime, since
0.6.0 ``src/winpodx/__init__.py`` derives ``__version__`` from the package
metadata rather than hand-syncing a literal).

Exits 0 if everything agrees, 1 with a stamped diff otherwise. Read-only --
makes no changes to the tree.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# stdlib on 3.11+; tomli backfill on 3.9 / 3.10 (matches winpodx's own pattern).
try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]

ROOT = Path(__file__).resolve().parents[2]


def pyproject_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def debian_version() -> str:
    text = (ROOT / "debian" / "changelog").read_text()
    m = re.match(r"winpodx \(([^)]+)\)", text)
    if not m:
        raise SystemExit("first debian/changelog entry doesn't match 'winpodx (X.Y.Z) ...'")
    return m.group(1)


def spec_version() -> str:
    text = (ROOT / "packaging" / "rpm" / "winpodx.spec").read_text()
    m = re.search(r"^Version:\s+(\S+)\s*$", text, re.MULTILINE)
    if not m:
        raise SystemExit("Version: line not found in packaging/rpm/winpodx.spec")
    return m.group(1)


def installed_metadata_version() -> str | None:
    """Return ``importlib.metadata.version('winpodx')`` if winpodx is installed.

    ``None`` when winpodx isn't on ``sys.path`` as an installed package -- a
    fresh source checkout running this script before ``pip install -e .`` is a
    legitimate state; we don't want CI to flap on a tooling-only run.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("winpodx")
        except PackageNotFoundError:
            return None
    except ImportError:
        return None


def _sync_spec(version: str) -> bool:
    """Stamp ``Version: <version>`` into the RPM spec. True if it changed."""
    path = ROOT / "packaging" / "rpm" / "winpodx.spec"
    text = path.read_text()
    new = re.sub(r"^(Version:\s+)\S+\s*$", rf"\g<1>{version}", text, count=1, flags=re.MULTILINE)
    if new != text:
        path.write_text(new)
        return True
    return False


def _sync_debian(version: str) -> bool:
    """Prepend a debian/changelog entry for ``version`` if the top entry differs.

    Reuses the maintainer from the current top entry and stamps an RFC-2822
    timestamp; the bullet is a generic pointer to CHANGELOG.md that the release
    author can refine. True if an entry was added.
    """
    if debian_version() == version:
        return False
    path = ROOT / "debian" / "changelog"
    text = path.read_text()
    m = re.search(r"^ -- (.+?)  ", text, re.MULTILINE)
    maintainer = m.group(1) if m else "Kim DaeHyun <kernalix7@kodenet.io>"
    from datetime import datetime, timezone
    from email.utils import format_datetime

    stamp = format_datetime(datetime.now(timezone.utc))
    entry = (
        f"winpodx ({version}) unstable; urgency=medium\n\n"
        f"  * Release {version}. See CHANGELOG.md for details.\n\n"
        f" -- {maintainer}  {stamp}\n\n"
    )
    path.write_text(entry + text)
    return True


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Verify (or --write) version stamps.")
    ap.add_argument(
        "--write",
        action="store_true",
        help="stamp pyproject's version into debian/changelog + the RPM spec, "
        "then verify -- run this during release prep so the stamps can't drift",
    )
    args = ap.parse_args()

    pv = pyproject_version()
    if args.write:
        changed = [
            name
            for name, did in (
                ("packaging/rpm/winpodx.spec", _sync_spec(pv)),
                ("debian/changelog", _sync_debian(pv)),
            )
            if did
        ]
        print(
            f"Stamped {pv} into: {', '.join(changed)} (refine the debian/changelog bullet)"
            if changed
            else f"Already at {pv}; nothing to stamp."
        )

    versions = {
        "pyproject.toml [project] version": pv,
        "debian/changelog (first entry)": debian_version(),
        "packaging/rpm/winpodx.spec Version:": spec_version(),
    }
    metadata = installed_metadata_version()
    if metadata is not None:
        versions["importlib.metadata installed version"] = metadata

    unique = set(versions.values())
    if len(unique) == 1:
        print(f"Version stamps consistent: {unique.pop()}")
        return 0

    print("Version stamp mismatch -- release prep incomplete:")
    for path, v in versions.items():
        print(f"  {path:46s}  {v}")
    print(
        "\nBump the lagging file(s) and re-run before tagging.\n"
        "  pyproject.toml is the single source of truth; everything else must follow.\n"
        "  See `chore(release): vX.Y.Z` commits on main for the convention."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
