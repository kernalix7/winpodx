# SPDX-License-Identifier: MIT
"""Tests for the `winpodx device` CLI (#286, cli/device.py)."""

from __future__ import annotations

import argparse
import json

import pytest

from winpodx.cli import device as DC
from winpodx.core import devices as D
from winpodx.core.config import Config


@pytest.fixture()
def cfg(monkeypatch):
    """A throwaway Config that device handlers load/save against, plus a stub
    host enumeration and a 'guest not running' default."""
    c = Config()
    monkeypatch.setattr(Config, "load", classmethod(lambda cls: c))
    monkeypatch.setattr(Config, "save", lambda self: None)
    monkeypatch.setattr(DC, "_guest_running", lambda _c: False)
    monkeypatch.setattr(
        DC,
        "_enumerate_host",
        lambda: [
            D.HostDevice(dtype="usb", did="1234:5678", label="ACME Dongle"),
            D.HostDevice(
                dtype="pci",
                did="0000:01:00.0",
                label="NVIDIA GPU",
                pci_class="03",
                iommu_group="15",
            ),
        ],
    )
    return c


def _ns(**kw):
    return argparse.Namespace(**kw)


def test_attach_usb_persists(cfg, capsys):
    DC._attach(_ns(device_command="attach", id="1234:5678", type=None, label=None, force=False))
    assert cfg.pod.devices == ["usb|1234:5678|ACME Dongle"]
    out = capsys.readouterr().out
    assert "Assigned usb 1234:5678" in out
    assert "next `pod start`" in out  # not running -> deferred


def test_attach_usb_explicit_label(cfg):
    DC._attach(_ns(device_command="attach", id="1234:5678", type=None, label="My Key", force=False))
    assert cfg.pod.devices == ["usb|1234:5678|My Key"]


def test_attach_duplicate_is_noop(cfg, capsys):
    DC._attach(_ns(device_command="attach", id="1234:5678", type=None, label=None, force=False))
    DC._attach(_ns(device_command="attach", id="1234:5678", type=None, label=None, force=False))
    assert cfg.pod.devices == ["usb|1234:5678|ACME Dongle"]
    assert "already assigned" in capsys.readouterr().out


def test_attach_pci_without_force_refuses(cfg, capsys):
    with pytest.raises(SystemExit) as ei:
        DC._attach(
            _ns(device_command="attach", id="0000:01:00.0", type="pci", label=None, force=False)
        )
    assert ei.value.code == 1
    assert cfg.pod.devices == []  # not persisted
    err = capsys.readouterr().err
    assert "without --force" in err and "GPU" in err


def test_attach_pci_with_force_persists(cfg, capsys):
    DC._attach(_ns(device_command="attach", id="0000:01:00.0", type="pci", label=None, force=True))
    assert cfg.pod.devices == ["pci|0000:01:00.0|NVIDIA GPU"]
    # Guest not running -> deferred to next start (not an immediate recreate).
    assert "pod start" in capsys.readouterr().out


def test_attach_pci_with_force_running_says_recreate(monkeypatch, cfg, capsys):
    monkeypatch.setattr(DC, "_guest_running", lambda _c: True)
    DC._attach(_ns(device_command="attach", id="0000:01:00.0", type="pci", label=None, force=True))
    assert cfg.pod.devices == ["pci|0000:01:00.0|NVIDIA GPU"]
    assert "recreate" in capsys.readouterr().out.lower()


def test_attach_invalid_id_exits(cfg):
    with pytest.raises(SystemExit) as ei:
        DC._attach(_ns(device_command="attach", id="nonsense", type=None, label=None, force=False))
    assert ei.value.code == 2


def test_detach_removes(cfg, capsys):
    cfg.pod.devices = ["usb|1234:5678|ACME Dongle"]
    DC._detach(_ns(device_command="detach", id="1234:5678", type=None))
    assert cfg.pod.devices == []
    assert "Released usb 1234:5678" in capsys.readouterr().out


def test_detach_not_assigned_is_noop(cfg, capsys):
    DC._detach(_ns(device_command="detach", id="1234:5678", type=None))
    assert "not assigned" in capsys.readouterr().out


def test_list_json(cfg, capsys):
    DC._list(_ns(device_command="list", json=True))
    rows = json.loads(capsys.readouterr().out)
    by_id = {r["id"]: r for r in rows}
    assert by_id["1234:5678"]["safe"] is True
    assert by_id["0000:01:00.0"]["safe"] is False
    assert by_id["0000:01:00.0"]["iommu_group"] == "15"


def test_list_marks_assigned(cfg, capsys):
    cfg.pod.devices = ["usb|1234:5678|ACME Dongle"]
    DC._list(_ns(device_command="list", json=False))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "1234:5678" in ln]
    assert lines and lines[0].lstrip().startswith("*")  # assigned marker


def test_status_json(cfg, capsys):
    cfg.pod.devices = ["pci|0000:01:00.0|GPU"]
    DC._status(_ns(device_command="status", json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["guest_running"] is False
    assert data["devices"] == [{"type": "pci", "id": "0000:01:00.0", "label": "GPU"}]


def test_attach_usb_live_when_running(monkeypatch, cfg, capsys):
    calls = []
    monkeypatch.setattr(DC, "_guest_running", lambda _c: True)
    monkeypatch.setattr(DC.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(D, "live_attach", lambda sock, dc: calls.append(dc.key))
    DC._attach(_ns(device_command="attach", id="1234:5678", type=None, label=None, force=False))
    assert calls == ["usb:1234:5678"]
    assert "live, no restart" in capsys.readouterr().out
