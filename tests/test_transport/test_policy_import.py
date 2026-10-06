# SPDX-License-Identifier: MIT
from __future__ import annotations

import importlib
import json
import subprocess
import sys

import pytest

from winpodx.core.config import Config


def test_windows_exec_imports_first_in_clean_interpreter() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import winpodx.core.windows_exec"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_protected_dispatch_rejects_invalid_preference_before_health_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from winpodx.core.transport import agent_only

    module = importlib.import_module("winpodx.core.transport.dispatch")
    health_calls: list[Config] = []
    monkeypatch.setattr(module, "AgentTransport", lambda cfg: health_calls.append(cfg))
    invalid = json.loads('"invalid"')
    with agent_only(), pytest.raises(ValueError, match="unknown prefer kind"):
        module.dispatch(Config(), prefer=invalid)
    assert health_calls == []
