# SPDX-License-Identifier: MIT
"""Parse-and-grep guard for install.sh's post-create provisioning (0.6.0 item B).

After the provision-unify cleanup, install.sh's post-`winpodx setup`
provisioning is the single ``winpodx provision`` command — NOT a bash copy
of the wait-ready → /health poll → 6× discovery retry → host-open chain.
These tests fail loudly if a regression reintroduces an inline bash copy,
which is exactly the duplication the unification killed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"


def _strip_comments(text: str) -> str:
    """Drop comment lines so prose explaining the unification doesn't trip the
    grep guards. A comment line is one whose first non-space char is ``#``
    (the shebang counts as a comment too). Inline trailing comments are rare
    in install.sh and not load-bearing for these assertions, so a line-level
    strip is sufficient and avoids mangling quoted ``#`` inside strings."""
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


@pytest.fixture(scope="module")
def script() -> str:
    assert _INSTALL_SH.is_file(), f"install.sh not found at {_INSTALL_SH}"
    return _strip_comments(_INSTALL_SH.read_text(encoding="utf-8"))


def test_invokes_winpodx_provision(script: str) -> None:
    # The unified call is the post-create provisioning step.
    assert "provision" in script
    # It runs through the installed symlink (same pattern as the old chain).
    assert '"$SYMLINK" provision' in script


def test_no_inline_health_curl_poll(script: str) -> None:
    # The 30× `curl .../health` settle poll moved into finish_provisioning.
    # (install.sh still uses curl elsewhere — e.g. the GitHub release check —
    #  so we assert the absence of the agent /health endpoint specifically,
    #  not curl in general.)
    assert "/health" not in script
    assert "127.0.0.1:8765" not in script
    assert "8765/health" not in script


def test_no_inline_six_times_discovery_retry(script: str) -> None:
    # The 6× `app refresh` retry loop is gone from bash.
    assert "for attempt in 1 2 3 4 5 6" not in script
    assert '"$SYMLINK" app refresh' not in script


def test_no_inline_host_open_listener_start(script: str) -> None:
    # Reverse-open listener start + refresh moved into finish_provisioning.
    assert "host-open start-listener" not in script
    assert "host-open refresh" not in script


def test_no_inline_migrate_in_provision_chain(script: str) -> None:
    # The post-create `winpodx migrate --non-interactive` call (the old
    # apply-fixes driver) is folded into finish_provisioning's apply stage.
    assert "migrate --non-interactive" not in script


def test_create_only_flag_is_gone(script: str) -> None:
    # setup --create-only was removed; install.sh must not pass it.
    assert "--create-only" not in script


def test_setup_skips_its_own_provision_tail(script: str) -> None:
    # install.sh runs the chain once via the explicit `winpodx provision`,
    # so it tells `winpodx setup` to skip its own full-provision tail.
    assert "WINPODX_NO_PROVISION=1" in script


def test_provision_is_the_only_post_create_winpodx_step(script: str) -> None:
    """After `winpodx setup`, the only chain-driving winpodx command is
    `winpodx provision` (plus its non-provisioning siblings: the GUI
    desktop-entry install isn't a winpodx subprocess). Assert none of the
    individual chain commands the old bash copy invoked survive."""
    forbidden = (
        '"$SYMLINK" pod wait-ready',
        '"$SYMLINK" migrate',
        '"$SYMLINK" app refresh',
        '"$SYMLINK" host-open',
    )
    for cmd in forbidden:
        assert cmd not in script, f"inline bash chain step still present: {cmd!r}"
