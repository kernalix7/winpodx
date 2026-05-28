# SPDX-License-Identifier: MIT
"""Lock the version + edition single-source-of-truth contracts.

F (version): ``pyproject.toml`` is canonical; ``src/winpodx/__init__.py`` derives
``__version__`` via ``importlib.metadata`` so the literal can't drift.

F (edition): ``WIN_VERSION_LABELS`` in ``core/config.py`` is the single source
for the curated Windows-edition list; the CLI help, the setup prompt, and the
GUI dropdown all derive from it.

N (packaging): ``scripts/ci/verify_versions.py`` agrees ``pyproject.toml``,
``debian/changelog``, and ``packaging/rpm/winpodx.spec`` share one version
string. The script itself is exercised by CI; this test pins the contract that
``packaging/rpm/winpodx.spec`` matches ``pyproject.toml``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read_pyproject_version() -> str:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.9 / 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


# ---- F1: __version__ derives from package metadata (no literal drift) ----


def test_winpodx_version_matches_pyproject_when_installed() -> None:
    # When winpodx is pip-installed (the CI test env always is, via the lint
    # job's editable install), importlib.metadata must return the pyproject
    # version. A drift here means the package isn't installed, which is itself
    # the failure mode we want to catch.
    import winpodx

    assert winpodx.__version__ == _read_pyproject_version()


def test_init_derives_version_from_metadata() -> None:
    # F1 makes pyproject.toml the SoT and derives __version__ via
    # importlib.metadata. Lock that: a future edit that re-adds the
    # hand-synced literal alongside the derivation must fail.
    text = (ROOT / "src" / "winpodx" / "__init__.py").read_text()
    assert "importlib.metadata" in text, (
        "__init__.py must derive __version__ from importlib.metadata; do not hand-sync a literal"
    )
    pyproject_v = _read_pyproject_version()
    assert pyproject_v not in text, (
        f"__init__.py contains the pyproject literal {pyproject_v!r}; "
        "derive from importlib.metadata instead so the version has one source"
    )


# ---- F2 / F3: edition list comes from WIN_VERSION_LABELS ----


def test_known_versions_equal_labels_keys() -> None:
    from winpodx.core.config import _KNOWN_WIN_VERSIONS, WIN_VERSION_LABELS

    assert set(WIN_VERSION_LABELS.keys()) == set(_KNOWN_WIN_VERSIONS)


def test_known_win_version_codes_preserves_label_order() -> None:
    from winpodx.core.config import WIN_VERSION_LABELS, known_win_version_codes

    assert known_win_version_codes() == tuple(WIN_VERSION_LABELS.keys())


def test_labels_are_non_empty_strings() -> None:
    from winpodx.core.config import WIN_VERSION_LABELS

    for code, label in WIN_VERSION_LABELS.items():
        assert isinstance(code, str) and code, f"empty code in WIN_VERSION_LABELS: {code!r}"
        assert isinstance(label, str) and label, f"empty label for code {code!r}"


def test_cli_help_text_lists_all_curated_editions() -> None:
    # End-to-end: invoking `winpodx setup --help` must surface every code
    # from WIN_VERSION_LABELS. The help string is built in cli/main.py from
    # known_win_version_codes(); this round-trip pins that wiring.
    import subprocess
    import sys

    from winpodx.core.config import known_win_version_codes

    result = subprocess.run(
        [sys.executable, "-m", "winpodx", "setup", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    help_text = result.stdout
    for code in known_win_version_codes():
        assert code in help_text, (
            f"curated edition {code!r} missing from `winpodx setup --help` output; "
            "cli/main.py likely lost its known_win_version_codes() derivation"
        )


# ---- N: packaging version stamps agree ----


def test_rpm_spec_version_matches_pyproject() -> None:
    spec = (ROOT / "packaging" / "rpm" / "winpodx.spec").read_text()
    m = re.search(r"^Version:\s+(\S+)\s*$", spec, re.MULTILINE)
    assert m, "Version: line missing from packaging/rpm/winpodx.spec"
    assert m.group(1) == _read_pyproject_version(), (
        "packaging/rpm/winpodx.spec Version: must match pyproject.toml [project] version "
        "(the OBS build rewrites this from the tarball name, but the literal still "
        "ships in source rpmbuild output and a stale value misleads contributors)"
    )


def test_debian_changelog_first_entry_matches_pyproject() -> None:
    text = (ROOT / "debian" / "changelog").read_text()
    m = re.match(r"winpodx \(([^)]+)\)", text)
    assert m, "first debian/changelog entry doesn't match 'winpodx (X.Y.Z) ...'"
    assert m.group(1) == _read_pyproject_version()
