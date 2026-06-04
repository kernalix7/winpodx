# SPDX-License-Identifier: MIT
"""Unit tests for the GUI resource snapshot (core.stats).

The pod-state query, ``podman/docker stats`` subprocess, and the guest
disk probe are all monkeypatched to canned output so the parsing logic
(cpu_pct / ram_pct / disk_pct) and the all-None degraded path are
exercised without a real backend or a running pod."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from winpodx.core import stats
from winpodx.core.config import Config
from winpodx.core.disk import DiskUsage

GIB = 1024**3


def _running_cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.container_name = "winpodx-windows"
    cfg.pod.cpu_cores = 4
    cfg.pod.ram_gb = 16
    return cfg


@dataclass
class _FakeProc:
    returncode: int
    stdout: str
    stderr: str = ""


def _patch_state(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    monkeypatch.setattr(stats, "_pod_state", lambda _cfg: state)


def test_snapshot_parses_live_cpu_ram_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_state(monkeypatch, "running")

    stats_json = json.dumps(
        [
            {
                "CPUPerc": "37.50%",
                "MemUsage": "4.2GiB / 16GiB",
                "MemPerc": "26.25%",
            }
        ]
    )

    def fake_run(cmd, **_kwargs):
        assert cmd[0] == "podman"
        if "ps" in cmd:
            # Container-name resolution (compose may prefix the name).
            return _FakeProc(returncode=0, stdout="winpodx-windows\n")
        assert "stats" in cmd
        assert "winpodx-windows" in cmd
        return _FakeProc(returncode=0, stdout=stats_json)

    monkeypatch.setattr(stats.subprocess, "run", fake_run)
    # Disk is read through the late import inside _guest_disk; patch that path.
    import winpodx.core.disk as disk_mod

    monkeypatch.setattr(
        disk_mod,
        "get_guest_disk_usage",
        lambda _cfg, **_kw: DiskUsage(total_bytes=64 * GIB, free_bytes=16 * GIB),
    )

    snap = stats.pod_resource_snapshot(_running_cfg())

    assert snap.pod_state == "running"
    assert snap.cpu_cores == 4
    assert snap.ram_gb == 16
    assert snap.cpu_pct == pytest.approx(37.5)
    assert snap.ram_pct == pytest.approx(26.25)
    assert snap.ram_used_gb == pytest.approx(4.2, abs=0.05)
    assert snap.disk_total_gb == pytest.approx(64.0)
    assert snap.disk_used_gb == pytest.approx(48.0)
    assert snap.disk_pct == pytest.approx(75.0)


def test_snapshot_handles_docker_single_object(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_state(monkeypatch, "running")
    cfg = _running_cfg()
    cfg.pod.backend = "docker"

    stats_json = json.dumps({"CPUPerc": "5%", "MemUsage": "512MiB / 8GiB", "MemPerc": "6.25%"})

    def fake_run(cmd, **_kwargs):
        assert cmd[0] == "docker"
        if "ps" in cmd:
            return _FakeProc(returncode=0, stdout="winpodx-windows\n")
        return _FakeProc(returncode=0, stdout=stats_json)

    monkeypatch.setattr(stats.subprocess, "run", fake_run)
    import winpodx.core.disk as disk_mod

    monkeypatch.setattr(disk_mod, "get_guest_disk_usage", lambda _cfg, **_kw: None)

    snap = stats.pod_resource_snapshot(cfg)

    assert snap.cpu_pct == pytest.approx(5.0)
    assert snap.ram_pct == pytest.approx(6.25)
    assert snap.ram_used_gb == pytest.approx(0.5, abs=0.01)
    # Disk probe returned None -> all disk fields None.
    assert snap.disk_total_gb is None
    assert snap.disk_used_gb is None
    assert snap.disk_pct is None


def test_snapshot_all_none_when_commands_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_state(monkeypatch, "running")

    def boom_run(cmd, **_kwargs):
        raise FileNotFoundError("podman: command not found")

    def boom_disk(_cfg, **_kw):
        raise RuntimeError("guest unreachable")

    monkeypatch.setattr(stats.subprocess, "run", boom_run)
    import winpodx.core.disk as disk_mod

    monkeypatch.setattr(disk_mod, "get_guest_disk_usage", boom_disk)

    snap = stats.pod_resource_snapshot(_running_cfg())

    # Caps still populated from cfg; everything live degrades to None.
    assert snap.pod_state == "running"
    assert snap.cpu_cores == 4
    assert snap.ram_gb == 16
    assert snap.cpu_pct is None
    assert snap.ram_used_gb is None
    assert snap.ram_pct is None
    assert snap.disk_total_gb is None
    assert snap.disk_used_gb is None
    assert snap.disk_pct is None


def test_snapshot_nonzero_rc_yields_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_state(monkeypatch, "running")

    monkeypatch.setattr(
        stats.subprocess,
        "run",
        lambda cmd, **_kw: _FakeProc(returncode=125, stdout="", stderr="no such container"),
    )
    import winpodx.core.disk as disk_mod

    monkeypatch.setattr(disk_mod, "get_guest_disk_usage", lambda _cfg, **_kw: None)

    snap = stats.pod_resource_snapshot(_running_cfg())

    assert snap.cpu_pct is None
    assert snap.ram_used_gb is None
    assert snap.ram_pct is None


def test_snapshot_skips_live_when_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_state(monkeypatch, "stopped")

    def fail_if_called(*_a, **_k):
        raise AssertionError("stats must not be probed when pod is not running")

    monkeypatch.setattr(stats.subprocess, "run", fail_if_called)
    import winpodx.core.disk as disk_mod

    monkeypatch.setattr(disk_mod, "get_guest_disk_usage", fail_if_called)

    snap = stats.pod_resource_snapshot(_running_cfg())

    assert snap.pod_state == "stopped"
    assert snap.cpu_cores == 4
    assert snap.ram_gb == 16
    assert snap.cpu_pct is None
    assert snap.ram_pct is None
    assert snap.disk_pct is None


def test_manual_backend_has_no_live_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_state(monkeypatch, "running")
    cfg = _running_cfg()
    cfg.pod.backend = "manual"

    def fail_if_called(*_a, **_k):
        raise AssertionError("manual backend has no container to probe")

    monkeypatch.setattr(stats.subprocess, "run", fail_if_called)
    import winpodx.core.disk as disk_mod

    monkeypatch.setattr(disk_mod, "get_guest_disk_usage", lambda _cfg, **_kw: None)

    snap = stats.pod_resource_snapshot(cfg)

    assert snap.cpu_pct is None
    assert snap.ram_used_gb is None
    assert snap.ram_pct is None


def test_parse_mem_bytes_units() -> None:
    assert stats._parse_mem_bytes("1GiB") == pytest.approx(GIB)
    assert stats._parse_mem_bytes("512MiB") == pytest.approx(512 * 1024**2)
    assert stats._parse_mem_bytes("2.5GiB") == pytest.approx(2.5 * GIB)
    assert stats._parse_mem_bytes("garbage") is None
    assert stats._parse_mem_bytes("") is None


def test_parse_cpu_pct_variants() -> None:
    assert stats._parse_cpu_pct("12.34%") == pytest.approx(12.34)
    assert stats._parse_cpu_pct("0%") == pytest.approx(0.0)
    assert stats._parse_cpu_pct(7.5) == pytest.approx(7.5)
    assert stats._parse_cpu_pct(None) is None
    assert stats._parse_cpu_pct("") is None
    assert stats._parse_cpu_pct("nope") is None


def _write_cgroup(tmp_path, *, mem_bytes: int, usage_usec: int) -> str:
    (tmp_path / "memory.current").write_text(f"{mem_bytes}\n")
    (tmp_path / "cpu.stat").write_text(f"usage_usec {usage_usec}\nuser_usec 0\nsystem_usec 0\n")
    return str(tmp_path)


def test_cgroup_cpu_ram_reads_memory_and_cpu_delta(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # Two reads of cumulative usage_usec, dt apart, give CPU%. RAM is one read.
    cfg = _running_cfg()  # cpu_cores=4, ram_gb=16
    cgdir = _write_cgroup(tmp_path, mem_bytes=4 * GIB, usage_usec=1_000_000)

    monkeypatch.setattr(stats, "_CPU_SAMPLE", {"mono": None, "usage_usec": None})
    monkeypatch.setattr(stats, "_stats_cli", lambda _cfg: "podman")
    monkeypatch.setattr(stats, "_container_pid", lambda *_a, **_k: 4321)
    monkeypatch.setattr(stats, "_cgroup_dir_for_pid", lambda _pid: cgdir)
    monos = iter([100.0, 102.0])
    monkeypatch.setattr(stats.time, "monotonic", lambda: next(monos))

    # First call: RAM resolves, CPU has no prior sample to diff -> None.
    cpu0, used0, pct0 = stats._cgroup_cpu_ram(cfg)
    assert cpu0 is None
    assert used0 == pytest.approx(4.0)
    assert pct0 == pytest.approx(25.0)  # 4 / 16 GiB

    # Second call: usage jumps 4.0 CPU-seconds over a 2.0s window across 4 cores
    # -> 4 / 2 / 4 * 100 = 50%.
    _write_cgroup(tmp_path, mem_bytes=4 * GIB, usage_usec=5_000_000)
    cpu1, _used1, _pct1 = stats._cgroup_cpu_ram(cfg)
    assert cpu1 == pytest.approx(50.0)


def test_live_cpu_ram_falls_back_to_cgroup_when_stats_blank(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # Rootless podman often emits CPUPerc/MemPerc as "--"; the cgroup fills them.
    cfg = _running_cfg()
    cgdir = _write_cgroup(tmp_path, mem_bytes=8 * GIB, usage_usec=2_000_000)

    def fake_run(cmd, **_kwargs):
        if "ps" in cmd:
            return _FakeProc(returncode=0, stdout="winpodx-windows\n")
        return _FakeProc(
            returncode=0,
            stdout=json.dumps([{"CPUPerc": "--", "MemUsage": "-- / --", "MemPerc": "--"}]),
        )

    monkeypatch.setattr(stats.subprocess, "run", fake_run)
    monkeypatch.setattr(stats, "_CPU_SAMPLE", {"mono": 100.0, "usage_usec": 0})
    monkeypatch.setattr(stats, "_container_pid", lambda *_a, **_k: 4321)
    monkeypatch.setattr(stats, "_cgroup_dir_for_pid", lambda _pid: cgdir)
    monkeypatch.setattr(stats.time, "monotonic", lambda: 101.0)

    cpu, used, pct = stats._live_cpu_ram(cfg)
    # usage 2.0 CPU-s over 1.0s / 4 cores = 50%; RAM 8/16 GiB = 50%.
    assert cpu == pytest.approx(50.0)
    assert pct == pytest.approx(50.0)
    assert used == pytest.approx(8.0)


def test_cgroup_dir_for_pid_v1_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # A cgroup v1 host has no unified "0::" line -> the reader bows out (None).
    proc_cgroup = tmp_path / "cgroup"
    proc_cgroup.write_text("12:devices:/foo\n11:memory:/foo\n")
    real_open = open

    def fake_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/"):
            return real_open(proc_cgroup, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert stats._cgroup_dir_for_pid(4321) is None
