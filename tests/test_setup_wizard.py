# SPDX-License-Identifier: MIT
"""Smoke tests for the host-setup wizard module (#227 fat AppImage)."""

from __future__ import annotations

from winpodx.setup_wizard import HostState, detect_host_state
from winpodx.setup_wizard.pkexec import _build_apply_script


def test_detect_host_state_returns_dataclass() -> None:
    """detect_host_state is read-only and must always return a HostState
    even on hosts with no /dev/kvm / no kvm group / etc. The wizard
    relies on this never raising so the GUI can call it on startup."""
    state = detect_host_state()
    assert isinstance(state, HostState)
    assert isinstance(state.in_kvm_group, bool)
    assert isinstance(state.kvm_group_exists, bool)
    assert isinstance(state.dev_kvm_present, bool)
    assert isinstance(state.dev_kvm_readable, bool)
    assert isinstance(state.subuid_configured, bool)
    assert isinstance(state.subgid_configured, bool)
    assert isinstance(state.kvm_module_persistent, bool)


def test_host_state_missing_fixable_excludes_non_fixable() -> None:
    """`/dev/kvm` not being present cannot be fixed by the wizard (it's
    a host kernel concern) -- only fixable items should appear in
    `missing_fixable`."""
    state = HostState(
        in_kvm_group=False,
        kvm_group_exists=True,
        dev_kvm_present=False,
        dev_kvm_readable=False,
        subuid_configured=False,
        subgid_configured=False,
        kvm_module_persistent=False,
    )
    items = state.missing_fixable
    assert "kvm-group-membership" in items
    assert "subuid-entry" in items
    assert "subgid-entry" in items
    # kvm-module-persistence requires /dev/kvm present to be meaningful.
    assert "kvm-module-persistence" not in items
    # No item for /dev/kvm itself (BIOS / modprobe concern, not pkexec).
    assert all("dev-kvm" not in i for i in items)


def test_host_state_is_complete_requires_all_fields() -> None:
    base = dict(
        in_kvm_group=True,
        kvm_group_exists=True,
        dev_kvm_present=True,
        dev_kvm_readable=True,
        subuid_configured=True,
        subgid_configured=True,
        kvm_module_persistent=True,
    )
    assert HostState(**base).is_complete
    for field in (
        "in_kvm_group",
        "dev_kvm_present",
        "dev_kvm_readable",
        "subuid_configured",
        "subgid_configured",
    ):
        bad = dict(base)
        bad[field] = False
        assert not HostState(**bad).is_complete, f"{field}=False should fail completeness"


def test_apply_script_only_includes_selected_items() -> None:
    """The shell script payload must not include sections for items the
    caller didn't select. Wizard re-runs with already-fixed items would
    otherwise re-apply (harmless given idempotency, but noisy in logs)."""
    script = _build_apply_script({"kvm-group-membership"}, "alice")
    assert "usermod -aG kvm" in script
    assert "alice" in script
    # No subuid / subgid / modules-load sections when not selected.
    assert "/etc/subuid" not in script
    assert "/etc/subgid" not in script
    assert "modules-load.d" not in script


def test_apply_script_handles_empty_selection() -> None:
    """Empty selection produces only the header + footer, no item blocks."""
    script = _build_apply_script(set(), "alice")
    assert "Running pkexec-elevated host setup" in script
    assert "usermod" not in script
    assert "/etc/subuid" not in script


def test_apply_script_full_selection() -> None:
    """All items selected -- script covers every wizard-owned fix."""
    script = _build_apply_script(
        {
            "kvm-group-membership",
            "subuid-entry",
            "subgid-entry",
            "kvm-module-persistence",
        },
        "bob",
    )
    assert "usermod -aG kvm bob" in script
    assert "/etc/subuid" in script
    assert "/etc/subgid" in script
    assert "/etc/modules-load.d/kvm-winpodx.conf" in script
    assert "kvm_intel" in script and "kvm_amd" in script
