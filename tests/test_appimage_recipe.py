# SPDX-License-Identifier: MIT
"""Regression test: the Thin AppImage recipe must not re-introduce the
podman stack (0.6.0 item A, #357 / #363 root-cause fix).

Bundling rootless podman + helpers (conmon / crun / netavark /
aardvark-dns / pasta / passt / slirp4netns / fuse-overlayfs) is what
caused #357 (Ubuntu 26.04 -- bundled podman-compose shadowed the host's
working stack) and #363 (Fedora Bluefin -- bundled libcrypto poisoned
host systemd-run / aardvark-dns via LD_LIBRARY_PATH). The Thin redesign
drops the entire container stack from the AppImage; this test guards
against a future PR silently re-adding it.

The AppImage build is driven by:

* ``.github/workflows/appimage-publish.yml`` -- the CI workflow whose
  ``dnf install -y ...`` line is the package include list.
* ``packaging/appimage/bundle-system-bins.sh`` -- the BINARIES array
  that names the binaries copied out of the Fedora image into the
  AppDir.

Both files are scanned. If a forbidden token appears in either, fail
with a pointer back to this docstring so the author understands the
constraint before adding an opt-out.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "appimage-publish.yml"
BUNDLE_SCRIPT = REPO_ROOT / "packaging" / "appimage" / "bundle-system-bins.sh"

# Packages / binaries that must NOT be bundled into the Thin AppImage.
# Order: stack core, then helpers. Each is matched as a whole word so
# substrings (e.g. ``podman-compose`` containing ``podman``) don't
# false-positive each other.
FORBIDDEN_PACKAGES = (
    "podman",
    "podman-compose",
    "conmon",
    "crun",
    "netavark",
    "aardvark-dns",
    "passt",
    "pasta",
    "slirp4netns",
    "fuse-overlayfs",
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file at {path}"
    return path.read_text(encoding="utf-8")


def _scan_dnf_install_block(workflow_text: str) -> str:
    """Return the ``dnf install -y ...`` argument block from the workflow.

    The workflow embeds the install line as part of a shell ``bash -c '...'``
    payload, with each package on its own line. We slice out everything
    between ``dnf install -y`` and the next ``echo`` line so a future
    rewording of the surrounding shell doesn't break the scan.
    """
    match = re.search(
        r"dnf\s+install\s+-y(?P<args>.*?)(?:^\s*echo\b|^\s*rpm\b|^\s*bash\b)",
        workflow_text,
        re.DOTALL | re.MULTILINE,
    )
    if match is None:
        # Workflow doesn't dnf install anything (CI re-architected) --
        # nothing to scan, the test passes vacuously on this file.
        return ""
    return match.group("args")


def _scan_bundle_binaries_array(script_text: str) -> str:
    """Return the BINARIES=( ... ) block contents from bundle-system-bins.sh."""
    match = re.search(
        r"BINARIES=\((?P<body>.*?)\)",
        script_text,
        re.DOTALL,
    )
    if match is None:
        return ""
    return match.group("body")


def _tokens(block: str) -> set[str]:
    """Split a whitespace-separated argument block into bare tokens.

    Ignores shell line-continuations, comments, and empty lines.
    """
    out: set[str] = set()
    for raw in block.splitlines():
        # Strip in-line comments after ``#``
        line = raw.split("#", 1)[0]
        # Drop the line-continuation backslash
        line = line.replace("\\", " ")
        for tok in line.split():
            out.add(tok)
    return out


def test_workflow_dnf_install_has_no_podman_stack_packages():
    text = _read(WORKFLOW)
    block = _scan_dnf_install_block(text)
    tokens = _tokens(block)
    found = sorted(tokens.intersection(FORBIDDEN_PACKAGES))
    assert not found, (
        "Thin AppImage regression: "
        f".github/workflows/appimage-publish.yml `dnf install -y` block reintroduced {found!r}. "
        "See tests/test_appimage_recipe.py docstring -- bundling rootless podman "
        "broke #357 and #363; 0.6.0 item A removed the stack."
    )


def test_bundle_script_binaries_array_has_no_podman_stack():
    text = _read(BUNDLE_SCRIPT)
    block = _scan_bundle_binaries_array(text)
    tokens = _tokens(block)
    found = sorted(tokens.intersection(FORBIDDEN_PACKAGES))
    assert not found, (
        "Thin AppImage regression: "
        f"packaging/appimage/bundle-system-bins.sh BINARIES=() reintroduced {found!r}. "
        "See tests/test_appimage_recipe.py docstring -- bundling rootless podman "
        "broke #357 and #363; 0.6.0 item A removed the stack."
    )


def test_workflow_does_not_pip_install_podman_compose():
    """podman-compose was pip-installed into the bundled interpreter
    (#322 workaround for ldd-bundling a pure-Python script). Thin drops
    that too -- the host's podman-compose is used directly."""
    text = _read(WORKFLOW)
    assert "pip install --no-cache-dir podman-compose" not in text, (
        "Thin AppImage regression: appimage-publish.yml re-introduced a "
        "`pip install podman-compose` into the bundled interpreter. The Thin "
        "AppImage requires host podman-compose; see tests/test_appimage_recipe.py."
    )
    assert "-m podman_compose" not in text, (
        "Thin AppImage regression: appimage-publish.yml re-introduced a "
        "podman-compose wrapper that runs ``python3 -m podman_compose``."
    )


def test_forbidden_tokens_are_known():
    """Lock the forbidden list so a future expansion is deliberate."""
    assert set(FORBIDDEN_PACKAGES) == {
        "podman",
        "podman-compose",
        "conmon",
        "crun",
        "netavark",
        "aardvark-dns",
        "passt",
        "pasta",
        "slirp4netns",
        "fuse-overlayfs",
    }
