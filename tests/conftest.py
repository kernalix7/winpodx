"""Shared pytest configuration and autouse fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_xdg_and_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
):
    """Redirect XDG_* and HOME to an isolated per-test directory."""
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
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
