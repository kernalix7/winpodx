# SPDX-License-Identifier: MIT
"""Tests for the usbredir live-USB passthrough path (core/usbredir.py)."""

from __future__ import annotations

import json

import pytest

from winpodx.core import usbredir as U
from winpodx.core.devices import DeviceConfig, HmpError


@pytest.fixture()
def statedir(tmp_path, monkeypatch):
    """Point usbredir's state dir at a throwaway tmp path."""
    from winpodx.utils import paths

    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path)
    return tmp_path / "usbredir"


def test_qom_id_stable():
    assert U._qom_id(DeviceConfig("usb", "1058:2626")) == "wpxur-10582626"


def test_alloc_slot_empty(statedir):
    assert U._alloc_slot() == 0


def test_alloc_slot_skips_used(statedir):
    statedir.mkdir(parents=True, exist_ok=True)
    (statedir / "a.json").write_text(json.dumps({"slot": 0}))
    (statedir / "b.json").write_text(json.dumps({"slot": 1}))
    assert U._alloc_slot() == 2


def test_attach_rejects_pci(statedir):
    with pytest.raises(HmpError, match="only supports USB"):
        U.attach("podman", "c", DeviceConfig("pci", "01:00.0"))


def test_attach_requires_usbredirect(statedir, monkeypatch):
    monkeypatch.setattr(U, "usbredirect_path", lambda: None)
    with pytest.raises(HmpError, match="usbredirect not found"):
        U.attach("podman", "c", DeviceConfig("usb", "1058:2626"))


def test_attach_requires_privilege(statedir, monkeypatch):
    monkeypatch.setattr(U, "usbredirect_path", lambda: "/usr/bin/usbredirect")
    monkeypatch.setattr(U, "_privilege_wrapper", lambda: None)
    with pytest.raises(HmpError, match="pkexec nor sudo"):
        U.attach("podman", "c", DeviceConfig("usb", "1058:2626"))


def test_is_attached_false_without_state(statedir):
    assert U.is_attached(DeviceConfig("usb", "1058:2626")) is False


def test_detach_idempotent_without_state(statedir, monkeypatch):
    cleaned: list = []
    monkeypatch.setattr(U, "_hmp_cleanup", lambda be, c, qom, drop_device: cleaned.append(qom))
    # No state file -> must not crash, and still attempt HMP cleanup.
    U.detach("podman", "c", DeviceConfig("usb", "1058:2626"))
    assert cleaned == ["wpxur-10582626"]


def test_looks_like_error():
    assert U._looks_like_error("Error: no such device")
    assert U._looks_like_error("(qemu) could not open")
    assert not U._looks_like_error("(qemu) ")


def test_attach_rolls_back_when_channel_never_connects(statedir, monkeypatch):
    # chardev-add/device_add "succeed" (empty reply), processes are stubbed,
    # but the channel never connects -> attach raises and leaves no state +
    # runs HMP cleanup (no orphan device/chardev).
    dev = DeviceConfig("usb", "1058:2626")
    monkeypatch.setattr(U, "usbredirect_path", lambda: "/usr/bin/usbredirect")
    monkeypatch.setattr(U, "_privilege_wrapper", lambda: ["sudo"])
    monkeypatch.setattr(U, "hmp_command", lambda be, c, cmd, **kw: "(qemu) ")
    monkeypatch.setattr(U, "_wait_port", lambda port, timeout: None)
    monkeypatch.setattr(U, "_wait_chardev_connected", lambda be, c, qom, timeout: False)
    monkeypatch.setattr(U, "_kill", lambda p: None)

    class _FakeProc:
        pid = 1234567

    monkeypatch.setattr(U.subprocess, "Popen", lambda *a, **k: _FakeProc())
    cleaned: list = []
    monkeypatch.setattr(
        U, "_hmp_cleanup", lambda be, c, qom, drop_device: cleaned.append((qom, drop_device))
    )
    with pytest.raises(HmpError, match="never connected"):
        U.attach("podman", "c", dev)
    assert not U._state_path(dev).exists()
    assert cleaned and cleaned[0][0] == "wpxur-10582626"


def test_attach_writes_state_on_success(statedir, monkeypatch):
    dev = DeviceConfig("usb", "1058:2626")
    monkeypatch.setattr(U, "usbredirect_path", lambda: "/usr/bin/usbredirect")
    monkeypatch.setattr(U, "_privilege_wrapper", lambda: ["sudo"])
    monkeypatch.setattr(U, "hmp_command", lambda be, c, cmd, **kw: "(qemu) ")
    monkeypatch.setattr(U, "_wait_port", lambda port, timeout: None)
    monkeypatch.setattr(U, "_wait_chardev_connected", lambda be, c, qom, timeout: True)

    class _FakeProc:
        pid = 4242

    monkeypatch.setattr(U.subprocess, "Popen", lambda *a, **k: _FakeProc())
    U.attach("podman", "c", dev)
    state = json.loads(U._state_path(dev).read_text())
    assert state["did"] == "1058:2626"
    assert state["qom"] == "wpxur-10582626"
    assert state["relay_pid"] == 4242 and state["usbredirect_pid"] == 4242
    assert state["qemu_port"] == U._QEMU_PORT_BASE and state["host_port"] == U._HOST_PORT_BASE
