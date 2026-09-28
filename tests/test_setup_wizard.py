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
        "dev_kvm_present",
        "dev_kvm_readable",
        "subuid_configured",
        "subgid_configured",
    ):
        bad = dict(base)
        bad[field] = False
        assert not HostState(**bad).is_complete, f"{field}=False should fail completeness"


class TestKvmGroupIsAMechanismNotARequirement:
    """A world-accessible /dev/kvm needs no group membership.

    Distros shipping a 0666 udev rule leave everyone outside the `kvm` group
    while KVM works fine. Treating membership as its own requirement failed a
    check no `usermod` could clear -- the setup wizard refused to advance on a
    host that had been running Windows all along.
    """

    def _state(self, **over) -> HostState:
        base = dict(
            in_kvm_group=False,
            kvm_group_exists=True,
            dev_kvm_present=True,
            dev_kvm_readable=True,
            subuid_configured=True,
            subgid_configured=True,
            kvm_module_persistent=True,
        )
        base.update(over)
        return HostState(**base)

    def test_accessible_dev_kvm_without_group_membership_is_complete(self):
        state = self._state()

        assert state.kvm_access_ok
        assert state.blocking_failures == []
        assert state.is_complete

    def test_it_is_not_offered_as_a_fix_when_access_already_works(self):
        assert "kvm-group-membership" not in self._state().missing_fixable

    def test_it_does_block_when_the_device_is_not_accessible(self):
        state = self._state(dev_kvm_readable=False)

        assert state.blocking_failures == ["dev_kvm_readable", "in_kvm_group"]
        assert "kvm-group-membership" in state.missing_fixable

    def test_a_pending_relogin_blocks_on_access_not_on_membership(self):
        # usermod succeeded but the session predates it: membership reads True
        # while the device is still unreachable until the user logs back in.
        state = self._state(in_kvm_group=True, dev_kvm_readable=False)

        assert state.blocking_failures == ["dev_kvm_readable"]
        assert "kvm-group-membership" not in state.missing_fixable

    def test_a_missing_device_reports_only_that(self):
        state = self._state(dev_kvm_present=False, dev_kvm_readable=False)

        assert state.blocking_failures == ["dev_kvm_present"]


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


def test_preflight_reports_every_blocker_and_docker_ignores_rootless_ids() -> None:
    from winpodx.setup_wizard.host_state import PreflightFacts, assess_preflight
    from winpodx.utils.deps import DepCheck

    host = HostState(False, True, False, False, False, False, False)
    deps = {
        "freerdp": DepCheck("freerdp", False),
        "docker": DepCheck("docker", False),
    }
    facts = PreflightFacts(host, False, 4, 10, 64, 8, "docker", deps, False, False)

    report = assess_preflight(facts)

    assert {item.key for item in report.failures} == {
        "cpu_virtualization",
        "dev_kvm_present",
        "ram",
        "disk",
        "freerdp",
        "backend",
        "compose",
    }
    assert not report.ready
    assert all(not item.fixable for item in report.failures)


def test_preflight_readable_kvm_and_docker_need_no_kvm_group_or_subids() -> None:
    from winpodx.setup_wizard.host_state import PreflightFacts, assess_preflight
    from winpodx.utils.deps import DepCheck

    host = HostState(False, False, True, True, False, False, False)
    deps = {
        "freerdp": DepCheck("freerdp", True),
        "docker": DepCheck("docker", True, daemon_reachable=True),
    }

    report = assess_preflight(
        PreflightFacts(host, True, 16, 100, 64, 8, "docker", deps, True, False)
    )

    assert report.ready
    assert "in_kvm_group" not in {item.key for item in report.failures}


def test_preflight_podman_reports_both_fixable_subids_and_missing_compose() -> None:
    from winpodx.setup_wizard.host_state import PreflightFacts, assess_preflight
    from winpodx.utils.deps import DepCheck

    host = HostState(True, True, True, True, False, False, True)
    deps = {
        "freerdp": DepCheck("freerdp", True),
        "podman": DepCheck("podman", True, daemon_reachable=True),
    }
    report = assess_preflight(
        PreflightFacts(host, True, 16, 100, 64, 8, "podman", deps, False, True)
    )

    assert {item.key for item in report.failures} == {
        "subuid_configured",
        "subgid_configured",
        "compose",
    }
    assert {item.key for item in report.failures if item.fixable} == {
        "subuid_configured",
        "subgid_configured",
    }


def test_preflight_rootful_podman_does_not_require_subids() -> None:
    from winpodx.setup_wizard.host_state import PreflightFacts, assess_preflight
    from winpodx.utils.deps import DepCheck

    host = HostState(True, True, True, True, False, False, True)
    deps = {
        "freerdp": DepCheck("freerdp", True),
        "podman": DepCheck("podman", True, daemon_reachable=True),
    }

    report = assess_preflight(
        PreflightFacts(host, True, 16, 100, 64, 8, "podman", deps, True, False)
    )

    assert report.ready


def test_preflight_checks_chosen_vm_ram_against_host_capacity() -> None:
    from winpodx.setup_wizard.host_state import PreflightFacts, assess_preflight
    from winpodx.utils.deps import DepCheck

    host = HostState(True, True, True, True, True, True, True)
    deps = {
        "freerdp": DepCheck("freerdp", True),
        "docker": DepCheck("docker", True, daemon_reachable=True),
    }

    report = assess_preflight(
        PreflightFacts(host, True, 16, 100, 64, 8, "docker", deps, True, False, vm_ram_gb=16)
    )

    assert {issue.key for issue in report.failures} == {"ram"}


def test_read_only_preflight_counts_selected_storage_and_local_iso(monkeypatch, tmp_path) -> None:
    from winpodx.core.config import Config
    from winpodx.setup_wizard import host_state
    from winpodx.utils.deps import DepCheck
    from winpodx.utils.specs import HostSpecs

    cfg = Config()
    cfg.pod.backend = "docker"
    cfg.pod.disk_size = "64G"
    cfg.pod.storage_path = str(tmp_path / "new-storage")
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"ISO")
    monkeypatch.setattr(
        host_state,
        "detect_host_state",
        lambda: HostState(False, False, True, True, False, False, True),
    )
    monkeypatch.setattr(host_state, "_cpu_virtualization_available", lambda: True)
    monkeypatch.setattr("winpodx.utils.specs.detect_host_specs", lambda: HostSpecs(8, 16))
    monkeypatch.setattr("winpodx.utils.deps.check_compose_provider", lambda backend: True)
    monkeypatch.setattr(host_state, "_storage_free_gb", lambda path: 60)
    deps = {
        "freerdp": DepCheck("freerdp", True),
        "docker": DepCheck("docker", True, daemon_reachable=True),
    }

    report = host_state.inspect_preflight(cfg, iso_path=str(iso), deps=deps)

    assert {issue.key for issue in report.failures} == {"disk"}
    assert not (tmp_path / "new-storage").exists()


def test_preflight_reports_missing_local_iso_with_other_failures(monkeypatch, tmp_path) -> None:
    from winpodx.core.config import Config
    from winpodx.setup_wizard import host_state
    from winpodx.utils.deps import DepCheck
    from winpodx.utils.specs import HostSpecs

    cfg = Config()
    cfg.pod.backend = "docker"
    cfg.pod.storage_path = str(tmp_path)
    monkeypatch.setattr(
        host_state, "detect_host_state", lambda: HostState(True, True, True, True, True, True, True)
    )
    monkeypatch.setattr(host_state, "_cpu_virtualization_available", lambda: True)
    monkeypatch.setattr("winpodx.utils.specs.detect_host_specs", lambda: HostSpecs(8, 4))
    monkeypatch.setattr("winpodx.utils.deps.check_compose_provider", lambda backend: True)
    monkeypatch.setattr(host_state, "_storage_free_gb", lambda path: 100)
    deps = {
        "freerdp": DepCheck("freerdp", True),
        "docker": DepCheck("docker", True, daemon_reachable=True),
    }

    report = host_state.inspect_preflight(cfg, iso_path=str(tmp_path / "missing.iso"), deps=deps)

    assert {issue.key for issue in report.failures} == {"ram", "iso"}


def test_preflight_fails_when_named_volume_mount_cannot_be_inspected(monkeypatch, tmp_path) -> None:
    from winpodx.core.config import Config
    from winpodx.setup_wizard import host_state
    from winpodx.utils.deps import DepCheck
    from winpodx.utils.specs import HostSpecs

    cfg = Config()
    cfg.pod.backend = "docker"
    cfg.pod.initialized = True
    monkeypatch.setattr(
        "winpodx.core.storage_migration.resolve_named_volume", lambda backend: "winpodx-data"
    )
    monkeypatch.setattr(
        "winpodx.core.storage_migration.get_volume_mountpoint", lambda backend, volume: None
    )
    monkeypatch.setattr(
        "winpodx.core.storage_migration.default_target_path", lambda: tmp_path / "wrong-disk"
    )
    monkeypatch.setattr(
        host_state, "detect_host_state", lambda: HostState(True, True, True, True, True, True, True)
    )
    monkeypatch.setattr(host_state, "_cpu_virtualization_available", lambda: True)
    monkeypatch.setattr("winpodx.utils.specs.detect_host_specs", lambda: HostSpecs(8, 16))
    monkeypatch.setattr("winpodx.utils.deps.check_compose_provider", lambda backend: True)
    deps = {
        "freerdp": DepCheck("freerdp", True),
        "docker": DepCheck("docker", True, daemon_reachable=True),
    }

    report = host_state.inspect_preflight(cfg, deps=deps)

    assert "disk" in {issue.key for issue in report.failures}
    assert not (tmp_path / "wrong-disk").exists()


def test_preflight_uses_podman_runtime_mode_for_subid_requirements(monkeypatch, tmp_path) -> None:
    from winpodx.core.config import Config
    from winpodx.setup_wizard import host_state
    from winpodx.utils.deps import DepCheck
    from winpodx.utils.specs import HostSpecs

    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.storage_path = str(tmp_path)
    monkeypatch.setattr(
        host_state,
        "detect_host_state",
        lambda: HostState(True, True, True, True, False, False, True),
    )
    monkeypatch.setattr(host_state, "_cpu_virtualization_available", lambda: True)
    monkeypatch.setattr("winpodx.utils.specs.detect_host_specs", lambda: HostSpecs(8, 16))
    monkeypatch.setattr("winpodx.utils.deps.check_compose_provider", lambda backend: True)
    monkeypatch.setattr(host_state, "_storage_free_gb", lambda path: 100)
    deps = {
        "freerdp": DepCheck("freerdp", True),
        "podman": DepCheck("podman", True, daemon_reachable=True),
    }
    monkeypatch.setattr("winpodx.backend.podman.is_rootless_podman", lambda: False)
    assert host_state.inspect_preflight(cfg, deps=deps).ready

    monkeypatch.setattr("winpodx.backend.podman.is_rootless_podman", lambda: True)
    report = host_state.inspect_preflight(cfg, deps=deps)
    assert {item.key for item in report.failures} == {"subuid_configured", "subgid_configured"}
