# SPDX-License-Identifier: MIT
"""Tests for host<->guest device passthrough core (#286, core/devices.py)."""

from __future__ import annotations

import types

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


# --- live USB via dockur's QEMU monitor (HMP over `<backend> exec`) ------


def _fake_run(reply: str = "", *, rc: int = 0, stderr: str = "", captured: list | None = None):
    """Return a fake subprocess.run that records argv + yields a scripted reply."""

    def run(cmd, **kwargs):
        if captured is not None:
            captured.append(cmd)
        return types.SimpleNamespace(returncode=rc, stdout=reply, stderr=stderr)

    return run


def test_live_attach_delegates_to_usbredir(monkeypatch):
    # live_attach no longer uses `device_add usb-host` (QEMU's in-container
    # libusb is frozen — see core/usbredir). It delegates to the usbredir path.
    from winpodx.core import usbredir

    calls: list = []
    monkeypatch.setattr(usbredir, "attach", lambda be, c, dev: calls.append((be, c, dev.did)))
    D.live_attach("podman", "winpodx-windows", D.DeviceConfig("usb", "1234:5678", "Dongle"))
    assert calls == [("podman", "winpodx-windows", "1234:5678")]


def test_live_attach_propagates_usbredir_error(monkeypatch):
    from winpodx.core import usbredir

    def boom(be, c, dev):
        raise D.HmpError("usbredirect not found")

    monkeypatch.setattr(usbredir, "attach", boom)
    with pytest.raises(D.HmpError, match="usbredirect"):
        D.live_attach("podman", "winpodx-windows", D.DeviceConfig("usb", "1234:5678"))


def test_live_detach_delegates_to_usbredir(monkeypatch):
    from winpodx.core import usbredir

    calls: list = []
    monkeypatch.setattr(usbredir, "detach", lambda be, c, dev: calls.append((be, c, dev.did)))
    D.live_detach("podman", "winpodx-windows", D.DeviceConfig("usb", "1234:5678"))
    assert calls == [("podman", "winpodx-windows", "1234:5678")]


def test_live_attach_rejects_pci():
    with pytest.raises(D.HmpError, match="only supports USB"):
        D.live_attach("podman", "winpodx-windows", D.DeviceConfig("pci", "01:00.0"))


def test_hmp_command_unreachable_raises(monkeypatch):
    monkeypatch.setattr(D.subprocess, "run", _fake_run("", rc=7, stderr="connection refused"))
    with pytest.raises(D.HmpError, match="unreachable"):
        D.hmp_command("podman", "winpodx-windows", "info version")


# --- compose integration -------------------------------------------------


def test_compose_default_wires_usb_bus():
    # usb_live defaults ON: the default compose binds /dev/bus/usb (validated
    # to boot rootless) but uses NO custom -qmp socket / device_cgroup_rules
    # (both crash-looped boot). Live attach reuses dockur's own monitor.
    from winpodx.core.config import Config
    from winpodx.core.pod.compose import _build_compose_content

    out = _build_compose_content(Config())
    assert "- /dev/kvm" in out and "- /dev/net/tun" in out
    assert "- /dev/bus/usb:/dev/bus/usb" in out
    # SELinux hosts (openSUSE Tumbleweed / Fedora / RHEL) deny container_t
    # read on the usbfs nodes even with matching uid/ACL — label=disable
    # lifts that confinement (scoped to this one container). See #286.
    assert "security_opt:" in out and "- label=disable" in out
    assert "-qmp" not in out
    assert "device_cgroup_rules" not in out
    assert "usb-host" not in out  # never boot-added


def test_compose_usb_live_off_is_clean():
    from winpodx.core.config import Config
    from winpodx.core.pod.compose import _build_compose_content

    c = Config()
    c.pod.usb_live = False
    c.pod.__post_init__()
    out = _build_compose_content(c)
    assert "/dev/bus/usb" not in out
    assert "-qmp" not in out
    # No device exposure -> keep full SELinux confinement on the container.
    assert "label=disable" not in out


def test_compose_usb_live_opt_in_wires_usb_bus_only():
    # Explicit opt-in wires ONLY the /dev/bus/usb bind. Live attach reuses
    # dockur's own `-monitor`, so NO custom -qmp socket/bind-mount (that
    # crash-looped boot), NO device_cgroup_rules (rootless-incompatible), and
    # USB is never boot-added.
    from winpodx.core.config import Config
    from winpodx.core.pod.compose import _build_compose_content

    c = Config()
    c.pod.usb_live = True
    c.pod.__post_init__()
    out = _build_compose_content(c)
    assert "- /dev/bus/usb:/dev/bus/usb" in out
    assert "- label=disable" in out  # SELinux confinement lifted for usbfs access
    assert "-qmp" not in out  # reuse dockur's monitor, no custom socket
    assert "qmp.sock" not in out  # no bind-mounted socket
    assert "device_cgroup_rules" not in out
    assert "usb-host" not in out  # USB never boot-added


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
    # USB stays live-only even when assigned — never boot-added; the USB bus
    # bind is present (live attach reuses dockur's monitor, no custom -qmp).
    assert "usb-host" not in out
    assert "- /dev/bus/usb:/dev/bus/usb" in out
    assert "-qmp" not in out
    # PCI (vfio) also needs the SELinux lift to open /dev/vfio.
    assert "- label=disable" in out


def test_compose_pci_only_still_lifts_selinux():
    # Even with usb_live off, an assigned PCI device exposes /dev/vfio/vfio,
    # which container_t can't open on SELinux hosts -> label=disable applies.
    from winpodx.core.config import Config
    from winpodx.core.pod.compose import _build_compose_content

    c = Config()
    c.pod.usb_live = False
    c.pod.devices = ["pci|0000:01:00.0|Card"]
    c.pod.__post_init__()
    out = _build_compose_content(c)
    assert "- /dev/bus/usb" not in out  # usb off
    assert "- label=disable" in out  # but PCI still needs the lift


def test_assign_device_persists_and_dedups(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core.config import Config

    cfg = Config()
    dc = D.DeviceConfig("usb", "1234:5678", "ACME Dongle")
    assert D.assign_device(cfg, dc) is True
    assert cfg.pod.devices == ["usb|1234:5678|ACME Dongle"]
    # idempotent: same key -> no-op even with a different label
    assert D.assign_device(cfg, D.DeviceConfig("usb", "1234:5678", "other")) is False
    assert cfg.pod.devices == ["usb|1234:5678|ACME Dongle"]
    # persisted to disk
    assert "usb|1234:5678" in Config.path().read_text()


def test_unassign_device_removes_by_key(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.core.config import Config

    cfg = Config()
    D.assign_device(cfg, D.DeviceConfig("usb", "1234:5678", "ACME Dongle"))
    # matches by key, ignores label
    assert D.unassign_device(cfg, D.DeviceConfig("usb", "1234:5678", "different")) is True
    assert cfg.pod.devices == []
    # second call is a no-op
    assert D.unassign_device(cfg, D.DeviceConfig("usb", "1234:5678")) is False
