# SPDX-License-Identifier: MIT
"""Task-local agent-only policy for host-to-guest command execution."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_agent_only: ContextVar[bool] = ContextVar("winpodx_agent_only", default=False)


@contextmanager
def agent_only() -> Iterator[None]:
    """Require agent transport within this task; restore prior policy on exit."""
    token = _agent_only.set(True)
    try:
        yield
    finally:
        _agent_only.reset(token)


def agent_required() -> bool:
    """Return scoped policy or the legacy user-supplied environment preference."""
    return _agent_only.get() or os.environ.get("WINPODX_REQUIRE_AGENT") == "1"
