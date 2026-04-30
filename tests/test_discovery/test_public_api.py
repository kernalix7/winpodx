"""Tests for the redesigned ``core.discovery`` public API (Step 3).

Pins the contract that:
- ``scan(cfg)`` and ``persist(apps)`` are the explicit entry points.
- They wrap ``discover_apps`` / ``persist_discovered`` without behaviour drift.
- ``run_if_first_boot(cfg)`` is callable explicitly but is NOT auto-fired
  from ``provisioner.ensure_ready`` anymore — discovery is explicit-only.
"""

from __future__ import annotations

from unittest.mock import patch

from winpodx.core import discovery
from winpodx.core.config import Config


def test_scan_delegates_to_discover_apps() -> None:
    cfg = Config()
    sentinel: list = ["sentinel-list"]
    with patch.object(discovery, "discover_apps", return_value=sentinel) as m:
        result = discovery.scan(cfg, timeout=42)
    assert result is sentinel
    m.assert_called_once_with(cfg, timeout=42, progress_callback=None)


def test_persist_delegates_to_persist_discovered() -> None:
    apps: list = []
    with patch.object(discovery, "persist_discovered", return_value=[]) as m:
        discovery.persist(apps, replace=False)
    m.assert_called_once_with(apps, target_dir=None, replace=False)


def test_ensure_ready_does_not_fire_discovery() -> None:
    """Step 3 behaviour change: ``ensure_ready`` no longer auto-fires discovery.

    The user-facing first-boot UX is owned by install.sh's
    ``winpodx app refresh`` post-install hook — never by a side effect of
    ``ensure_ready``. This test pins that contract so a future regression
    that re-introduces an auto-fire path is caught immediately.
    """
    from winpodx.core import provisioner
    from winpodx.core.pod import PodState, PodStatus

    cfg = Config()

    # Whatever ensure_ready ends up doing internally, it must not import-
    # and-call any of the discovery entry points. Patch them all to
    # raise — if ensure_ready touches discovery at all, the test fails.
    with (
        patch.object(discovery, "scan", side_effect=AssertionError("scan called")),
        patch.object(
            discovery, "discover_apps", side_effect=AssertionError("discover_apps called")
        ),
        patch.object(
            discovery, "run_if_first_boot", side_effect=AssertionError("run_if_first_boot called")
        ),
        # Stub the rest of ensure_ready's dependencies so it can run to
        # completion without a real pod / RDP service.
        patch.object(provisioner, "_auto_rotate_password", side_effect=lambda c: c),
        # Sprint 3 (post-rollback) deleted _self_heal_apply — ensure_ready
        # no longer auto-applies. Nothing to stub for the apply path.
        patch.object(provisioner, "_ensure_desktop_entries", return_value=None),
        patch.object(provisioner, "pod_status", return_value=PodStatus(state=PodState.RUNNING)),
        # The early-return branch (RDP port already up) is the cheapest
        # path to exercise — no need to start a pod.
        patch.object(provisioner, "check_rdp_port", return_value=True),
    ):
        provisioner.ensure_ready(cfg)
