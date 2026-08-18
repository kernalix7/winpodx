# SPDX-License-Identifier: MIT
"""Smoke tests for the host-setup wizard module (#227 fat AppImage)."""

from __future__ import annotations

import runpy
import sys
from unittest.mock import patch

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
    # Username is bound to $wpu once (shlex-quoted) then referenced --
    # the raw name appears only in the `wpu=` assignment.
    assert "wpu=bob" in script
    assert 'usermod -aG kvm "$wpu"' in script
    assert "/etc/subuid" in script
    assert "/etc/subgid" in script
    assert "/etc/modules-load.d/kvm-winpodx.conf" in script
    assert "kvm_intel" in script and "kvm_amd" in script


def test_host_state_detects_group_membership_by_name(monkeypatch) -> None:
    import grp

    from winpodx.setup_wizard import host_state

    monkeypatch.setattr(host_state, "_current_username", lambda: "alice")
    monkeypatch.setattr(host_state.os, "getuid", lambda: 1000)
    monkeypatch.setattr(
        host_state.grp,
        "getgrall",
        lambda: [grp.struct_group(("kvm", "x", 36, ["alice"]))],
    )
    assert host_state._user_in_group("kvm") is True


def test_host_state_group_helpers_handle_missing_data(monkeypatch) -> None:
    from winpodx.setup_wizard import host_state

    monkeypatch.setattr(host_state.grp, "getgrall", lambda: (_ for _ in ()).throw(OSError()))
    assert host_state._user_in_group("kvm") is False
    monkeypatch.setattr(host_state.grp, "getgrnam", lambda _name: (_ for _ in ()).throw(KeyError()))
    assert host_state._group_exists("kvm") is False


def test_subid_entry_requires_matching_username_prefix(monkeypatch) -> None:
    from winpodx.setup_wizard import host_state

    monkeypatch.setattr(
        host_state.Path,
        "read_text",
        lambda _path: "malice:100000:65536\nalice:165536:65536\n",
    )
    assert host_state._subid_has_entry("/etc/subuid", "alice") is True
    assert host_state._subid_has_entry("/etc/subuid", "bob") is False


def test_subid_entry_unreadable_returns_false(monkeypatch) -> None:
    from winpodx.setup_wizard import host_state

    monkeypatch.setattr(
        host_state.Path,
        "read_text",
        lambda _path: (_ for _ in ()).throw(PermissionError()),
    )
    assert host_state._subid_has_entry("/etc/subuid", "alice") is False


def test_kvm_module_persistence_reads_configured_module(monkeypatch, tmp_path) -> None:
    from winpodx.setup_wizard import host_state

    config = tmp_path / "kvm.conf"
    config.write_text("kvm_amd\n")
    monkeypatch.setattr(host_state.Path, "iterdir", lambda _path: iter((config,)))
    assert host_state._kvm_module_persistent() is True


def test_detect_host_state_uses_isolated_probe_results(monkeypatch) -> None:
    from winpodx.setup_wizard import host_state

    monkeypatch.setattr(host_state, "_current_username", lambda: "alice")
    monkeypatch.setattr(host_state, "_user_in_group", lambda group: group == "kvm")
    monkeypatch.setattr(host_state, "_group_exists", lambda group: group == "kvm")
    monkeypatch.setattr(
        host_state,
        "_subid_has_entry",
        lambda path, username: path == "/etc/subuid" and username == "alice",
    )
    monkeypatch.setattr(host_state, "_kvm_module_persistent", lambda: True)
    monkeypatch.setattr(host_state.Path, "exists", lambda path: str(path) == "/dev/kvm")
    monkeypatch.setattr(
        host_state.os, "access", lambda path, mode: path == "/dev/kvm" and mode == 6
    )

    assert host_state.detect_host_state() == HostState(
        in_kvm_group=True,
        kvm_group_exists=True,
        dev_kvm_present=True,
        dev_kvm_readable=True,
        subuid_configured=True,
        subgid_configured=False,
        kvm_module_persistent=True,
    )


def test_apply_via_pkexec_noop_for_complete_state(monkeypatch) -> None:
    from winpodx.setup_wizard import pkexec

    state = HostState(True, True, True, True, True, True, True)
    monkeypatch.setattr(
        pkexec.shutil,
        "which",
        lambda _name: (_ for _ in ()).throw(AssertionError("pkexec must not be queried")),
    )
    pkexec.apply_via_pkexec(state)


def test_apply_via_pkexec_runs_single_exact_privileged_argv(monkeypatch) -> None:
    import subprocess

    from winpodx.setup_wizard import pkexec

    state = HostState(False, True, True, False, False, True, True)
    calls = []
    monkeypatch.setattr(pkexec.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pkexec, "_current_username", lambda: "alice")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="done", stderr="")

    monkeypatch.setattr(pkexec.subprocess, "run", fake_run)
    pkexec.apply_via_pkexec(state)

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[:3] == ["pkexec", "bash", "-c"]
    assert 'usermod -aG kvm "$wpu"' in argv[3]
    assert 'echo "$wpu:100000:65536" >> /etc/subuid' in argv[3]
    assert kwargs == {"capture_output": True, "text": True, "timeout": 120, "check": False}


def test_apply_via_pkexec_maps_return_codes_to_typed_errors(monkeypatch) -> None:
    import subprocess

    import pytest

    from winpodx.setup_wizard import pkexec

    state = HostState(False, True, False, False, True, True, True)
    monkeypatch.setattr(pkexec.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pkexec, "_current_username", lambda: "alice")
    cases = (
        (126, pkexec.PkexecAuthDenied),
        (127, pkexec.PkexecUnavailable),
        (5, pkexec.PkexecScriptFailed),
    )
    for returncode, error_type in cases:
        monkeypatch.setattr(
            pkexec.subprocess,
            "run",
            lambda argv, returncode=returncode, **_kwargs: subprocess.CompletedProcess(
                argv, returncode, stdout="out", stderr="err"
            ),
        )
        with pytest.raises(error_type):
            pkexec.apply_via_pkexec(state)


def test_python_m_winpodx_delegates_to_cli_entrypoint(monkeypatch) -> None:
    calls = []
    monkeypatch.delitem(sys.modules, "winpodx.__main__", raising=False)

    with patch("winpodx.cli.main.cli", side_effect=lambda: calls.append("cli")):
        runpy.run_module("winpodx", run_name="__main__")

    assert calls == ["cli"]
