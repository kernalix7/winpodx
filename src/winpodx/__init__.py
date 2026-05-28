# SPDX-License-Identifier: MIT
"""winpodx: Windows app integration for Linux desktop."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Single source of truth for the version string is pyproject.toml; expose it
# here via importlib.metadata so an installed copy reports the same value the
# build artefact carries. A source checkout that hasn't been pip-installed
# (e.g. `python -m winpodx` directly from the repo) falls back to a clearly
# non-release placeholder so callers don't paper over an undeclared dev run.
# See docs/design/ROADMAP-0.6.0.md item F.
try:
    __version__ = _pkg_version("winpodx")
except PackageNotFoundError:
    __version__ = "0.0.0+source"
