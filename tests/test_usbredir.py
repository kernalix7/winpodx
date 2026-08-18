# SPDX-License-Identifier: MIT
"""Tests for the usbredir live-USB passthrough path (core/usbredir.py)."""

from __future__ import annotations

import json
from types import SimpleNamespace

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
    monkeypatch.setattr(U, "_wait_relay_ready", lambda log, timeout: None)
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
    monkeypatch.setattr(U, "_wait_relay_ready", lambda log, timeout: None)
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


def test_proc_alive_checks_pid(monkeypatch):
    calls = []
    monkeypatch.setattr(U.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    assert U._proc_alive(None) is False
    assert U._proc_alive(42) is True
    assert calls == [(42, 0)]


def test_proc_alive_false_when_process_missing(monkeypatch):
    def missing(_pid, _sig):
        raise ProcessLookupError

    monkeypatch.setattr(U.os, "kill", missing)
    assert U._proc_alive(42) is False


@pytest.mark.parametrize(
    ("display", "pkexec", "sudo", "expected"),
    [
        (True, "/bin/pkexec", "/bin/sudo", ["/bin/pkexec"]),
        (False, "/bin/pkexec", "/bin/sudo", ["/bin/sudo"]),
        (False, "/bin/pkexec", None, ["/bin/pkexec"]),
        (False, None, None, None),
    ],
)
def test_privilege_wrapper_selection(monkeypatch, display, pkexec, sudo, expected):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    if display:
        monkeypatch.setenv("DISPLAY", ":1")
    found = {"pkexec": pkexec, "sudo": sudo}
    monkeypatch.setattr(U.shutil, "which", lambda name: found[name])
    assert U._privilege_wrapper() == expected


def test_active_slots_ignores_invalid_state(statedir):
    statedir.mkdir(parents=True)
    (statedir / "good.json").write_text('{"slot": 3}')
    (statedir / "missing.json").write_text("{}")
    (statedir / "bad.json").write_text("not-json")
    assert U._active_slots() == {3}


def test_alloc_slot_refuses_when_all_slots_used(statedir):
    statedir.mkdir(parents=True)
    for slot in range(U._MAX_SLOTS):
        (statedir / f"{slot}.json").write_text(json.dumps({"slot": slot}))
    with pytest.raises(HmpError, match="too many USB devices"):
        U._alloc_slot()


def test_is_attached_reads_live_state(statedir, monkeypatch):
    dev = DeviceConfig("usb", "1058:2626")
    U._state_path(dev).write_text('{"relay_pid": 77}')
    monkeypatch.setattr(U, "_proc_alive", lambda pid: pid == 77)
    assert U.is_attached(dev) is True


def test_is_attached_rejects_malformed_state(statedir):
    dev = DeviceConfig("usb", "1058:2626")
    U._state_path(dev).write_text("{")
    assert U.is_attached(dev) is False


def test_attach_is_idempotent_when_already_attached(statedir, monkeypatch):
    dev = DeviceConfig("usb", "1058:2626")
    monkeypatch.setattr(U, "is_attached", lambda _dev: True)
    monkeypatch.setattr(U, "usbredirect_path", lambda: pytest.fail("must not probe binary"))
    assert U.attach("podman", "c", dev) is None


def test_attach_reports_chardev_error(statedir, monkeypatch):
    dev = DeviceConfig("usb", "1058:2626")
    monkeypatch.setattr(U, "usbredirect_path", lambda: "/usr/bin/usbredirect")
    monkeypatch.setattr(U, "_privilege_wrapper", lambda: ["sudo"])
    commands = []

    def hmp(_backend, _container, command):
        commands.append(command)
        return "Error: socket unavailable"

    monkeypatch.setattr(U, "hmp_command", hmp)
    with pytest.raises(HmpError, match="chardev-add.*socket unavailable"):
        U.attach("podman", "c", dev)
    assert commands == [
        "chardev-add socket,id=wpxur-10582626,host=127.0.0.1,port=7310,server=on,wait=off"
    ]


def test_attach_cleans_chardev_after_device_error(statedir, monkeypatch):
    dev = DeviceConfig("usb", "1058:2626")
    monkeypatch.setattr(U, "usbredirect_path", lambda: "/usr/bin/usbredirect")
    monkeypatch.setattr(U, "_privilege_wrapper", lambda: ["sudo"])
    replies = iter(["(qemu)", "failed to add device"])
    monkeypatch.setattr(U, "hmp_command", lambda *_args: next(replies))
    cleaned = []
    monkeypatch.setattr(U, "_hmp_cleanup", lambda *args, **kwargs: cleaned.append((args, kwargs)))
    with pytest.raises(HmpError, match="device_add usb-redir"):
        U.attach("podman", "c", dev)
    assert cleaned[0][1] == {"drop_device": False}


def test_attach_assembles_relay_and_usbredirect_argv(statedir, monkeypatch):
    dev = DeviceConfig("usb", "1234:abcd")
    monkeypatch.setattr(U, "usbredirect_path", lambda: "/bin/usbredirect")
    monkeypatch.setattr(U, "_privilege_wrapper", lambda: ["pkexec"])
    monkeypatch.setattr(U, "hmp_command", lambda *_args: "(qemu)")
    monkeypatch.setattr(U, "_wait_relay_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(U, "_wait_chardev_connected", lambda *_args, **_kwargs: True)
    argv = []

    def popen(command, **_kwargs):
        argv.append(command)
        return SimpleNamespace(pid=100 + len(argv))

    monkeypatch.setattr(U.subprocess, "Popen", popen)
    U.attach("docker", "box", dev)
    assert argv[0][2:7] == ["winpodx.core.usbredir", "relay", "docker", "box", "7310"]
    assert argv[1] == [
        "pkexec",
        "/bin/usbredirect",
        "--device",
        "1234:abcd",
        "--to",
        "127.0.0.1:7410",
    ]


def test_detach_rejects_pci(statedir):
    with pytest.raises(HmpError, match="only supports USB"):
        U.detach("podman", "c", DeviceConfig("pci", "01:00.0"))


def test_detach_kills_relay_and_privileged_forwarder(statedir, monkeypatch):
    dev = DeviceConfig("usb", "1058:2626")
    path = U._state_path(dev)
    path.write_text('{"relay_pid": 11, "usbredirect_pid": 22}')
    killed = []
    monkeypatch.setattr(U, "_kill_pid", lambda pid, **kw: killed.append((pid, kw)))
    monkeypatch.setattr(U, "_proc_alive", lambda pid: pid == 22)
    monkeypatch.setattr(U.time, "sleep", lambda _seconds: None)
    cleaned = []
    monkeypatch.setattr(U, "_hmp_cleanup", lambda *args, **kw: cleaned.append((args, kw)))
    U.detach("docker", "box", dev)
    assert killed == [(11, {}), (22, {"privileged": True})]
    assert cleaned[0][1] == {"drop_device": True}
    assert not path.exists()


def test_wait_relay_ready_observes_marker(tmp_path, monkeypatch):
    log = tmp_path / "relay.log"
    log.write_text("relay up 127.0.0.1:7410")
    monkeypatch.setattr(U.time, "monotonic", lambda: 0.0)
    assert U._wait_relay_ready(log, timeout=1.0) is None


def test_wait_relay_ready_times_out_without_sleep(tmp_path, monkeypatch):
    ticks = iter([0.0, 0.1, 1.1])
    monkeypatch.setattr(U.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(U.time, "sleep", lambda _seconds: None)
    with pytest.raises(HmpError, match="did not start listening"):
        U._wait_relay_ready(tmp_path / "missing.log", timeout=1.0)


def test_wait_chardev_connected_parses_matching_channel(monkeypatch):
    monkeypatch.setattr(U.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        U, "hmp_command", lambda *_args: "other: disconnected\r\nwpxur-1234 <-> tcp:peer"
    )
    assert U._wait_chardev_connected("podman", "c", "wpxur-1234", 1.0) is True


def test_wait_chardev_connected_handles_hmp_error(monkeypatch):
    ticks = iter([0.0, 0.1, 1.1])
    monkeypatch.setattr(U.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(U.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(U, "hmp_command", lambda *_args: (_ for _ in ()).throw(HmpError("no")))
    assert U._wait_chardev_connected("podman", "c", "qom", 1.0) is False


def test_hmp_cleanup_sends_exact_commands(monkeypatch):
    commands = []
    monkeypatch.setattr(U, "hmp_command", lambda _be, _c, cmd: commands.append(cmd))
    monkeypatch.setattr(U.time, "sleep", lambda _seconds: None)
    U._hmp_cleanup("podman", "c", "qom", drop_device=True)
    assert commands == ["device_del qom", "chardev-remove qom"]


def test_kill_falls_back_to_process_terminate(monkeypatch):
    proc = SimpleNamespace(pid=44, terminate=lambda: setattr(proc, "terminated", True))
    monkeypatch.setattr(U.os, "getpgid", lambda _pid: 55)
    monkeypatch.setattr(U.os, "killpg", lambda *_args: (_ for _ in ()).throw(OSError()))
    U._kill(proc)
    assert proc.terminated is True


def test_kill_pid_privileged_uses_wrapper(monkeypatch):
    calls = []
    monkeypatch.setattr(U, "_privilege_wrapper", lambda: ["sudo"])
    monkeypatch.setattr(U.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    U._kill_pid(88, privileged=True)
    assert calls == [(["sudo", "kill", "88"], {"check": False})]


def test_main_dispatches_relay_and_rejects_bad_argv(monkeypatch):
    monkeypatch.setattr(U, "_run_relay", lambda *args: 7 if args == ("docker", "c", 1, 2) else 9)
    assert U._main(["relay", "docker", "c", "1", "2"]) == 7
    assert U._main([]) == 2


def test_run_relay_returns_when_accept_fails(monkeypatch):
    class _Socket:
        def setsockopt(self, *_args):
            pass

        def bind(self, address):
            self.address = address

        def listen(self, backlog):
            self.backlog = backlog

        def accept(self):
            raise OSError

    sock = _Socket()
    monkeypatch.setattr(U.socket, "socket", lambda *_args: sock)
    assert U._run_relay("podman", "box", 7310, 7410) == 0
    assert sock.address == ("127.0.0.1", 7410)


def test_detach_tolerates_malformed_state(statedir, monkeypatch):
    dev = DeviceConfig("usb", "1058:2626")
    path = U._state_path(dev)
    path.write_text("{")
    killed = []
    monkeypatch.setattr(U, "_kill_pid", lambda pid, **_kw: killed.append(pid))
    monkeypatch.setattr(U, "_proc_alive", lambda _pid: False)
    monkeypatch.setattr(U.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(U, "_hmp_cleanup", lambda *_args, **_kwargs: None)
    U.detach("podman", "c", dev)
    assert killed == [None]
    assert not path.exists()


def test_hmp_cleanup_swallows_hmp_error(monkeypatch):
    monkeypatch.setattr(U, "hmp_command", lambda *_args: (_ for _ in ()).throw(HmpError("missing")))
    assert U._hmp_cleanup("podman", "c", "qom", drop_device=False) is None


def test_kill_none_is_noop(monkeypatch):
    monkeypatch.setattr(U.os, "killpg", lambda *_args: pytest.fail("must not signal"))
    assert U._kill(None) is None


def test_kill_pid_non_privileged_signals_exact_pid(monkeypatch):
    calls = []
    monkeypatch.setattr(U.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    U._kill_pid(91)
    assert calls == [(91, U.signal.SIGTERM)]
