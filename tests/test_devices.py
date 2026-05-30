# SPDX-License-Identifier: MIT
"""Tests for host<->guest device passthrough core (#286, core/devices.py)."""

from __future__ import annotations

import json
import socket
import threading

import pytest

from winpodx.core import devices as D

# --- parse / format round-trip ------------------------------------------


def test_parse_entry_usb():
    dc = D.parse_entry("usb|1234:5678|Security Dongle")
    assert dc is not None
    assert dc.dtype == "usb" and dc.did == "1234:5678" and dc.label == "Security Dongle"
    assert dc.key == "usb:1234:5678"
    assert dc.to_entry() == "usb|1234:5678|Security Dongle"


def test_parse_entry_pci_with_and_without_domain():
    assert D.parse_entry("pci|0000:01:00.0|Card").did == "0000:01:00.0"
    assert D.parse_entry("pci|01:00.0|Card").did == "01:00.0"


def test_parse_entry_rejects_malformed():
    assert D.parse_entry("usb|zzzz:5678|x") is None  # bad hex
    assert D.parse_entry("usb|1234|x") is None  # missing pid
    assert D.parse_entry("bogus|1234:5678|x") is None  # bad type
    assert D.parse_entry("usb|1234:5678") is None  # missing label field
    assert D.parse_entry("pci|notaddr|x") is None


def test_parse_entries_drops_bad():
    out = D.parse_entries(["usb|1234:5678|A", "bad|x|y", "pci|01:00.0|B"])
    assert [d.key for d in out] == ["usb:1234:5678", "pci:01:00.0"]


# --- lsusb / lspci parsing ----------------------------------------------


def test_parse_lsusb_skips_root_hub_and_parses_fields():
    out = D.parse_lsusb(
        "Bus 003 Device 004: ID 1234:5678 ACME Security Dongle\n"
        "Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
    )
    assert len(out) == 1
    d = out[0]
    assert d.dtype == "usb" and d.did == "1234:5678"
    assert d.bus == "003" and d.addr == "004"
    assert "ACME" in d.label


def test_parse_lsusb_dedups_same_vidpid():
    out = D.parse_lsusb(
        "Bus 003 Device 004: ID 1234:5678 Dongle A\nBus 003 Device 007: ID 1234:5678 Dongle A\n"
    )
    assert len(out) == 1


def test_parse_lspci_class_and_iommu():
    out = D.parse_lspci(
        '0000:01:00.0 "0300" "NVIDIA" "GA104"\n0000:00:1f.2 "0106" "Intel" "SATA"\n',
        iommu_lookup=lambda a: {"0000:01:00.0": "15", "0000:00:1f.2": "7"}.get(a),
    )
    assert out[0].pci_class == "03" and out[0].iommu_group == "15"
    assert out[1].pci_class == "01" and out[1].iommu_group == "7"


# --- safety classifier ---------------------------------------------------


def test_classify_usb_is_safe():
    s = D.classify_safety(D.HostDevice(dtype="usb", did="1234:5678"))
    assert s.safe and s.reasons == []


@pytest.mark.parametrize(
    "cls,needle",
    [("03", "GPU"), ("01", "boot/root disk"), ("02", "uplink"), ("0c", "vfio-pci")],
)
def test_classify_pci_is_risky_with_reason(cls, needle):
    s = D.classify_safety(D.HostDevice(dtype="pci", did="01:00.0", pci_class=cls))
    assert not s.safe
    assert any(needle in r for r in s.reasons)


def test_classify_pci_notes_iommu_group():
    s = D.classify_safety(D.HostDevice(dtype="pci", did="01:00.0", pci_class="04", iommu_group="9"))
    assert any("IOMMU group 9" in r for r in s.reasons)


# --- qemu arg builders ---------------------------------------------------


def test_qemu_device_args():
    args = D.qemu_device_args(
        [D.DeviceConfig("usb", "1234:5678", "x"), D.DeviceConfig("pci", "0000:01:00.0", "y")]
    )
    assert "usb-host,vendorid=0x1234,productid=0x5678" in args
    assert "vfio-pci,host=0000:01:00.0" in args


def test_host_device_nodes():
    assert D.host_device_nodes([D.DeviceConfig("usb", "1234:5678")]) == ["/dev/bus/usb"]
    assert D.host_device_nodes([D.DeviceConfig("pci", "01:00.0")]) == ["/dev/vfio/vfio"]
    assert D.host_device_nodes([]) == []


def test_usb_qom_id_stable():
    dc = D.DeviceConfig("usb", "1234:5678")
    assert D.usb_qom_id(dc) == "winpodx-usb-12345678"


# --- QMP client (simulated server over a socketpair) --------------------


def _fake_qmp_server(sock: socket.socket, scripted_replies: list[dict]):
    """Serve a QMP greeting, then for each received command line send the next
    run of scripted objects up to and including the next ``return``/``error``
    (so a command's reply can be preceded by async ``event`` objects)."""
    conn, _ = sock.accept()
    conn.sendall(b'{"QMP": {"version": {}}}\n')
    buf = b""
    idx = 0
    while idx < len(scripted_replies):
        while b"\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                conn.close()
                return
            buf += chunk
        _line, _, buf = buf.partition(b"\n")
        while idx < len(scripted_replies):
            obj = scripted_replies[idx]
            idx += 1
            conn.sendall((json.dumps(obj) + "\n").encode())
            if "return" in obj or "error" in obj:
                break
    conn.close()


def _run_with_fake_server(tmp_path, scripted_replies, body):
    sock_path = str(tmp_path / "qmp.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    t = threading.Thread(target=_fake_qmp_server, args=(srv, scripted_replies), daemon=True)
    t.start()
    try:
        return body(sock_path)
    finally:
        srv.close()
        t.join(timeout=2)


def test_qmp_live_attach_sends_device_add(tmp_path):
    # capabilities reply, then device_add reply.
    def body(sock_path):
        D.live_attach(sock_path, D.DeviceConfig("usb", "1234:5678", "Dongle"))
        return True

    assert _run_with_fake_server(tmp_path, [{"return": {}}, {"return": {}}], body) is True


def test_qmp_error_raises(tmp_path):
    def body(sock_path):
        with pytest.raises(D.QmpError):
            D.live_attach(sock_path, D.DeviceConfig("usb", "1234:5678"))
        return True

    # capabilities OK, then device_add returns an error object.
    replies = [{"return": {}}, {"error": {"class": "GenericError", "desc": "no such device"}}]
    assert _run_with_fake_server(tmp_path, replies, body) is True


def test_qmp_skips_events_before_reply(tmp_path):
    def body(sock_path):
        D.live_detach(sock_path, D.DeviceConfig("usb", "1234:5678"))
        return True

    # capabilities, then an async event, then the device_del return.
    replies = [
        {"return": {}},
        {"event": "DEVICE_DELETED", "data": {}},
        {"return": {}},
    ]
    assert _run_with_fake_server(tmp_path, replies, body) is True


def test_live_attach_rejects_pci(tmp_path):
    with pytest.raises(D.QmpError, match="only supports USB"):
        D.live_attach(str(tmp_path / "nope.sock"), D.DeviceConfig("pci", "01:00.0"))


def test_qmp_connect_error_on_missing_socket(tmp_path):
    with pytest.raises(D.QmpError, match="cannot connect"):
        with D.QmpClient(str(tmp_path / "absent.sock")):
            pass


# --- compose integration -------------------------------------------------


def test_compose_default_has_no_usb_live_infra():
    # usb_live defaults OFF (EXPERIMENTAL — the QMP-socket bind crash-loops
    # Windows boot on rootless dockur), so a default compose is clean: no QMP
    # socket, no /dev/bus/usb bind. The pod boots normally.
    from winpodx.core.config import Config
    from winpodx.core.pod.compose import _build_compose_content

    out = _build_compose_content(Config())
    assert "- /dev/kvm" in out and "- /dev/net/tun" in out
    assert "-qmp" not in out
    assert "/dev/bus/usb" not in out
    assert "device_cgroup_rules" not in out
    assert "usb-host" not in out


def test_compose_usb_live_opt_in_wires_infra():
    # Explicit opt-in: QMP socket (off /run) + /dev/bus/usb bind, but NO
    # device_cgroup_rules (rootless-incompatible) and USB never boot-added.
    from winpodx.core.config import Config
    from winpodx.core.pod.compose import _build_compose_content

    c = Config()
    c.pod.usb_live = True
    c.pod.__post_init__()
    out = _build_compose_content(c)
    assert "-qmp unix:/winpodx/qmp.sock" in out
    assert "/run/winpodx" not in out  # off /run
    assert "- /dev/bus/usb:/dev/bus/usb" in out
    assert "device_cgroup_rules" not in out
    assert "usb-host" not in out


def test_compose_pci_boot_adds_vfio_usb_stays_live():
    from winpodx.core.config import Config
    from winpodx.core.pod.compose import _build_compose_content

    c = Config()
    c.pod.usb_live = True  # opt-in so the QMP socket is wired alongside PCI
    c.pod.devices = ["usb|1234:5678|Dongle", "pci|0000:01:00.0|Card"]
    c.pod.__post_init__()
    out = _build_compose_content(c)
    # PCI is boot-added (can't hot-plug into a container QEMU).
    assert "- /dev/vfio/vfio" in out
    assert "vfio-pci,host=0000:01:00.0" in out
    # USB stays live-only even when assigned — never boot-added.
    assert "usb-host" not in out
    assert "-qmp unix:/winpodx/qmp.sock" in out
