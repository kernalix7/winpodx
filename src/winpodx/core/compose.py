"""Compatibility shim — compose generation moved to ``winpodx.core.pod.compose``.

Step S1A-1 of the feat/redesign refactor (see .priv-storage/WINPODX_REDESIGN.md)
extracts ``core/pod/`` as its own subpackage and moves the compose-template
helpers there. This module re-exports the public surface so existing imports
(`from winpodx.core.compose import generate_compose`) keep working.

Step 6 (Sprint 4) deletes this shim once all callers are updated. Do not
add new code here.
"""

from __future__ import annotations

from winpodx.core.pod.compose import (
    _COMPOSE_PODMAN_EXTRAS,
    _COMPOSE_TEMPLATE_BASE,
    _COMPOSE_TEMPLATE_FOOTER,
    _build_compose_content,
    _build_compose_template,
    _find_oem_dir,
    _yaml_escape,
    generate_compose,
    generate_compose_to,
    generate_password,
)

__all__ = [
    "_COMPOSE_PODMAN_EXTRAS",
    "_COMPOSE_TEMPLATE_BASE",
    "_COMPOSE_TEMPLATE_FOOTER",
    "_build_compose_content",
    "_build_compose_template",
    "_find_oem_dir",
    "_yaml_escape",
    "generate_compose",
    "generate_compose_to",
    "generate_password",
]
