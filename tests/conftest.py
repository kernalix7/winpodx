"""Shared pytest configuration and autouse fixtures.

The fixtures in this module redirect every XDG base directory and ``HOME``
to a per-test temporary directory *before* each test runs, so winpodx code
paths that touch ``~/.config``, ``~/.local/share``, ``~/.cache``, or resolve
``Path.home()`` never leak into or out of the developer's real environment.

Tests that need to pin one of these variables to a specific value (for
example ``tests/test_paths.py`` which asserts literal paths like
``/tmp/test-config/winpodx``) can still call ``monkeypatch.setenv(...)``
inside the test — pytest's monkeypatch stacks on top of the autouse setup
and reverts at teardown, so the per-test override wins for the duration
of that test and the autouse defaults resume for the next one.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_xdg_and_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
):
    """Redirect XDG_* and HOME to an isolated per-test directory.

    The isolation root is provisioned via ``tmp_path_factory.mktemp`` so it
    lives *outside* the test's own ``tmp_path``. That matters for tests
    such as ``test_unregister_mime_atomic_write`` which call
    ``tmp_path.iterdir()`` and assert on the exact set of children —
    if this fixture dropped directories directly into ``tmp_path``, those
    assertions would break.

    Each XDG variable gets its own subdirectory so that, for example, a
    test writing ``XDG_CONFIG_HOME/winpodx/winpodx.toml`` cannot collide
    with a test writing ``XDG_DATA_HOME/winpodx/...``. Tests that need a
    specific value (e.g. ``tests/test_paths.py`` asserting literal
    ``/tmp/test-config/winpodx``) still call ``monkeypatch.setenv`` and
    win over the autouse default for that test.
    """
    root = tmp_path_factory.mktemp("winpodx_xdg")
    home = root / "home"
    config = root / "xdg_config"
    data = root / "xdg_data"
    cache = root / "xdg_cache"
    runtime = root / "xdg_runtime"
    for directory in (home, config, data, cache, runtime):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    # Runtime dir is used by .cproc files; isolate it too so process-tracking
    # tests cannot observe stray pidfiles from a prior run.
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
