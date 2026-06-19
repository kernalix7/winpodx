# SPDX-License-Identifier: MIT
"""Lock the host-side agent port single source of truth.

`core/agent.AGENT_PORT` is the only Python literal for the guest agent
listener port; everything else (the compose template, the urlacl strings
we push into the guest, the AgentClient URL) must derive from it. These
tests fail loudly the moment someone re-introduces a literal copy."""

from __future__ import annotations

import re

from winpodx.core import guest_sync
from winpodx.core.agent import AGENT_PORT, AgentClient
from winpodx.core.guest_disk import GUEST_SMB_PORT, SMB_HOST_PORT
from winpodx.core.pod import compose


def test_agent_port_default_value() -> None:
    # Pinning the actual value so a typo-rebind elsewhere can't silently
    # change the port the guest binds vs. what the host probes.
    assert AGENT_PORT == 8765


def test_agent_client_default_base_url_uses_constant() -> None:
    assert AgentClient.DEFAULT_BASE_URL == f"http://127.0.0.1:{AGENT_PORT}"


def test_compose_template_port_mapping_uses_constant() -> None:
    # USER_PORTS (dockur slirp hostfwd) + the loopback port mapping must
    # both reference AGENT_PORT, not a separate literal. We assert the
    # rendered template, not just the source, because the .format() call
    # is where drift would actually break the container.
    from winpodx.core.config import Config

    cfg = Config()
    rendered = compose._build_compose_content(cfg)
    # USER_PORTS forwards both the agent port and the guest SMB port (#616).
    assert f'USER_PORTS: "{AGENT_PORT} {GUEST_SMB_PORT}"' in rendered
    assert f'"127.0.0.1:{AGENT_PORT}:{AGENT_PORT}/tcp"' in rendered
    # Guest SMB share (reverse-open guest-disk) published on loopback only.
    assert f'"127.0.0.1:{SMB_HOST_PORT}:{GUEST_SMB_PORT}/tcp"' in rendered
    # Sanity: only the AGENT_PORT-derived occurrences appear (3 total:
    # USER_PORTS env + the two halves of the loopback host:container map).
    occurrences = re.findall(r"\b8765\b", rendered)
    assert len(occurrences) == 3, f"unexpected literal 8765 occurrences: {occurrences}"


def test_urlacl_string_uses_constant() -> None:
    # The urlacl block pushed into the guest gates which SID can bind the
    # listener; if its port drifts from AGENT_PORT we get "address already
    # in use" or a refused bind on the guest side.
    src = guest_sync._URLACL_PS
    assert f"127.0.0.1:{AGENT_PORT}/" in src
    assert f"*:{AGENT_PORT}/" in src
    assert f"+:{AGENT_PORT}/" in src
