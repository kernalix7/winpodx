# SPDX-License-Identifier: MIT
"""Tests for the ``python -m winpodx`` entry point."""

from __future__ import annotations

import runpy

import pytest


def test_python_dash_m_delegates_to_cli(monkeypatch) -> None:
    called: list = []
    monkeypatch.setattr("winpodx.cli.main.cli", lambda: called.append(True))

    runpy.run_module("winpodx.__main__", run_name="__main__")

    assert called == [True]


def test_python_dash_m_propagates_cli_exit(monkeypatch) -> None:
    monkeypatch.setattr("winpodx.cli.main.cli", lambda: (_ for _ in ()).throw(SystemExit(3)))

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("winpodx.__main__", run_name="__main__")

    assert exc.value.code == 3
