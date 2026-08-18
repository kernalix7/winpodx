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


def test_snapshot_parses_cpu_ram_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    # CPU is host-side; disk + guest RAM share one agent round-trip.
    _patch_state(monkeypatch, "running")
    monkeypatch.setattr(stats, "_host_cpu_pct", lambda _cfg: 37.5)
    monkeypatch.setattr(
        stats,
        "_guest_resources",
        lambda _cfg: ((64.0, 48.0, 75.0), (4.2, 26.25)),
    )

    snap = stats.pod_resource_snapshot(_running_cfg())

    assert snap.pod_state == "running"
    assert snap.cpu_cores == 4
    assert snap.ram_gb == 16
    assert snap.cpu_pct == pytest.approx(37.5)
    assert snap.ram_pct == pytest.approx(26.25)
    assert snap.ram_used_gb == pytest.approx(4.2)
    assert snap.disk_total_gb == pytest.approx(64.0)
    assert snap.disk_used_gb == pytest.approx(48.0)
    assert snap.disk_pct == pytest.approx(75.0)


def test_podman_stats_parses_docker_single_object(monkeypatch: pytest.MonkeyPatch) -> None:
    # The host stats fallback still parses a docker single-object response.
    cfg = _running_cfg()
    cfg.pod.backend = "docker"

    stats_json = json.dumps({"CPUPerc": "5%", "MemUsage": "512MiB / 8GiB", "MemPerc": "6.25%"})

    def fake_run(cmd, **_kwargs):
        assert cmd[0] == "docker"
        if "ps" in cmd:
            return _FakeProc(returncode=0, stdout="winpodx-windows\n")
        return _FakeProc(returncode=0, stdout=stats_json)

    monkeypatch.setattr(stats.subprocess, "run", fake_run)

    cpu, ram_used_gb, ram_pct = stats._podman_stats_cpu_ram(cfg)
    assert cpu == pytest.approx(5.0)
    assert ram_pct == pytest.approx(6.25)
    assert ram_used_gb == pytest.approx(0.5, abs=0.01)


def test_snapshot_all_none_when_probes_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_state(monkeypatch, "running")
    monkeypatch.setattr(stats, "_host_cpu_pct", lambda _cfg: None)
    monkeypatch.setattr(stats, "_guest_resources", lambda _cfg: ((None, None, None), (None, None)))

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


def test_guest_resources_uses_generous_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # #634: the Dashboard RAM + Disk gauges read from the guest agent; a tight
    # 4s budget left them stuck on "n/a" on a slow / freshly-relaunched guest.
    # The probe now passes a generous timeout (off-thread, guarded — no freeze).
    seen: dict[str, int] = {}

    def fake_get_guest_resources(_cfg, *, timeout):
        seen["timeout"] = timeout
        return None

    monkeypatch.setattr("winpodx.core.disk.get_guest_resources", fake_get_guest_resources)
    stats._guest_resources(_running_cfg())
    assert seen["timeout"] >= 12


def test_snapshot_skips_guest_probe_when_with_disk_false(monkeypatch: pytest.MonkeyPatch) -> None:
    # CPU updates every tick; the guest agent round-trip is skipped off-cadence.
    _patch_state(monkeypatch, "running")
    monkeypatch.setattr(stats, "_host_cpu_pct", lambda _cfg: 12.0)

    def fail_if_called(*_a, **_k):
        raise AssertionError("guest probe must be skipped when with_disk=False")

    monkeypatch.setattr(stats, "_guest_resources", fail_if_called)

    snap = stats.pod_resource_snapshot(_running_cfg(), with_disk=False)

    assert snap.cpu_pct == pytest.approx(12.0)
    assert snap.ram_pct is None  # caller caches the last guest value
    assert snap.disk_pct is None


def test_snapshot_skips_live_when_not_running(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_state(monkeypatch, "stopped")

    def fail_if_called(*_a, **_k):
        raise AssertionError("probes must not run when the pod is not running")

    monkeypatch.setattr(stats, "_host_cpu_pct", fail_if_called)
    monkeypatch.setattr(stats, "_guest_resources", fail_if_called)

    snap = stats.pod_resource_snapshot(_running_cfg())

    assert snap.pod_state == "stopped"
    assert snap.cpu_cores == 4
    assert snap.ram_gb == 16
    assert snap.cpu_pct is None
    assert snap.ram_pct is None
    assert snap.disk_pct is None


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


def test_host_cpu_pct_prefers_cgroup(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # _host_cpu_pct reads the cgroup cpu.stat delta directly (instant), so it
    # never needs the slow podman stats call when the cgroup CPU is available.
    cfg = _running_cfg()
    cgdir = _write_cgroup(tmp_path, mem_bytes=8 * GIB, usage_usec=2_000_000)

    def boom_run(*_a, **_k):
        raise AssertionError("podman stats must not run when the cgroup has CPU")

    monkeypatch.setattr(stats.subprocess, "run", boom_run)
    monkeypatch.setattr(stats, "_CPU_SAMPLE", {"mono": 100.0, "usage_usec": 0})
    monkeypatch.setattr(stats, "_container_pid", lambda *_a, **_k: 4321)
    monkeypatch.setattr(stats, "_cgroup_dir_for_pid", lambda _pid: cgdir)
    monkeypatch.setattr(stats.time, "monotonic", lambda: 101.0)

    # usage 2.0 CPU-s over 1.0s / 4 cores = 50%.
    assert stats._host_cpu_pct(cfg) == pytest.approx(50.0)


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


def test_cgroup_memory_bytes_prefers_memory_current(tmp_path) -> None:
    (tmp_path / "memory.current").write_text("123456\n")
    assert stats._cgroup_memory_bytes(str(tmp_path)) == 123456


def test_cgroup_memory_bytes_falls_back_to_rss_sum(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # No memory.current (memory controller not delegated in rootless) -> sum the
    # VmRSS of every PID in cgroup.procs (kernel-accounted, controller-agnostic).
    import io

    (tmp_path / "cgroup.procs").write_text("111\n222\n")
    status = {
        111: "Name:\tqemu\nVmRSS:\t  2097152 kB\n",  # 2 GiB (the dockur QEMU)
        222: "Name:\tconmon\nVmRSS:\t     1024 kB\n",  # 1 MiB
    }
    real_open = open

    def fake_open(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith("/proc/") and path.endswith("/status"):
            return io.StringIO(status[int(path.split("/")[2])])
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert stats._cgroup_memory_bytes(str(tmp_path)) == (2097152 + 1024) * 1024


def test_cgroup_memory_bytes_none_when_nothing_readable(tmp_path) -> None:
    # No memory.current and no cgroup.procs -> give up (None).
    assert stats._cgroup_memory_bytes(str(tmp_path)) is None


@pytest.mark.parametrize(
    ("backend", "expected"),
    [("podman", "podman"), ("docker", "docker"), ("manual", None)],
)
def test_stats_cli_selects_supported_backend(backend: str, expected: str | None) -> None:
    cfg = _running_cfg()
    cfg.pod.backend = backend

    assert stats._stats_cli(cfg) == expected


def test_resolve_container_name_uses_first_match_and_falls_back(monkeypatch) -> None:
    outputs = iter(
        [
            _FakeProc(0, "winpodx_windows_1\nwinpodx-windows\n"),
            _FakeProc(0, ""),
            _FakeProc(1, "", "denied"),
        ]
    )
    monkeypatch.setattr(stats.subprocess, "run", lambda *_a, **_k: next(outputs))

    assert stats._resolve_container_name("podman", "winpodx-windows", {}) == "winpodx_windows_1"
    assert stats._resolve_container_name("podman", "winpodx-windows", {}) is None
    assert stats._resolve_container_name("podman", "winpodx-windows", {}) is None


@pytest.mark.parametrize("stdout", ["", "not-json", "[]", "null", '"string"'])
def test_podman_stats_malformed_or_empty_output_returns_na(monkeypatch, stdout: str) -> None:
    cfg = _running_cfg()
    monkeypatch.setattr(stats, "_resolve_container_name", lambda *_a, **_k: None)
    monkeypatch.setattr(
        stats.subprocess,
        "run",
        lambda *_a, **_k: _FakeProc(returncode=0, stdout=stdout),
    )

    assert stats._podman_stats_cpu_ram(cfg) == (None, None, None)


def test_podman_stats_parses_podman_array_and_decimal_units(monkeypatch) -> None:
    cfg = _running_cfg()
    payload = json.dumps([{"CPU": "2.5%", "MemoryUsage": "1.5GB / 16GB", "Mem": "9.4%"}])
    monkeypatch.setattr(stats, "_resolve_container_name", lambda *_a, **_k: "actual-container")
    monkeypatch.setattr(
        stats.subprocess,
        "run",
        lambda cmd, **_kwargs: (
            _FakeProc(0, payload)
            if cmd[-1] == "actual-container"
            else pytest.fail("resolved name not used")
        ),
    )

    cpu, used, pct = stats._podman_stats_cpu_ram(cfg)

    assert cpu == pytest.approx(2.5)
    assert used == pytest.approx(1.5 * 1000**3 / GIB)
    assert pct == pytest.approx(9.4)


def test_podman_stats_runtime_error_and_nonzero_return_na(monkeypatch) -> None:
    cfg = _running_cfg()
    monkeypatch.setattr(stats, "_resolve_container_name", lambda *_a, **_k: None)
    monkeypatch.setattr(stats.subprocess, "run", lambda *_a, **_k: (_ for _ in ()).throw(OSError()))
    assert stats._podman_stats_cpu_ram(cfg) == (None, None, None)

    monkeypatch.setattr(stats.subprocess, "run", lambda *_a, **_k: _FakeProc(1, "", "failed"))
    assert stats._podman_stats_cpu_ram(cfg) == (None, None, None)


@pytest.mark.parametrize(("stdout", "expected"), [("4321\n", 4321), ("0\n", None), ("bad", None)])
def test_container_pid_parsing(monkeypatch, stdout: str, expected: int | None) -> None:
    monkeypatch.setattr(stats, "_resolve_container_name", lambda *_a, **_k: None)
    monkeypatch.setattr(stats.subprocess, "run", lambda *_a, **_k: _FakeProc(0, stdout))

    assert stats._container_pid("podman", _running_cfg(), {}) == expected


def test_guest_resources_converts_disk_ram_and_clamps_percent(monkeypatch) -> None:
    disk = type("Disk", (), {"total_bytes": 64 * GIB, "used_bytes": 48 * GIB, "used_pct": 75.0})()
    guest = type(
        "Guest",
        (),
        {"disk": disk, "ram_used_bytes": 20 * GIB, "ram_total_bytes": 16 * GIB},
    )()
    monkeypatch.setattr("winpodx.core.disk.get_guest_resources", lambda *_a, **_k: guest)

    disk_result, ram_result = stats._guest_resources(_running_cfg())

    assert disk_result == pytest.approx((64.0, 48.0, 75.0))
    assert ram_result == pytest.approx((20.0, 100.0))


def test_guest_resources_degrades_on_probe_exception(monkeypatch) -> None:
    monkeypatch.setattr(
        "winpodx.core.disk.get_guest_resources",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("guest unavailable")),
    )

    assert stats._guest_resources(_running_cfg()) == ((None, None, None), (None, None))


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("RUNNING", "running"),
        ("PAUSED", "paused"),
        ("STOPPED", "stopped"),
        ("STARTING", "checking"),
        ("UNRESPONSIVE", "checking"),
        ("ERROR", "unknown"),
    ],
)
def test_pod_state_maps_backend_states(monkeypatch, state: str, expected: str) -> None:
    from winpodx.core.pod import PodState, PodStatus

    monkeypatch.setattr(
        "winpodx.core.pod.backend.pod_status",
        lambda _cfg: PodStatus(state=getattr(PodState, state)),
    )

    assert stats._pod_state(_running_cfg()) == expected


def test_snapshot_uses_supplied_state_and_degrades_probe_exceptions(monkeypatch) -> None:
    monkeypatch.setattr(stats, "_pod_state", lambda _cfg: pytest.fail("state was supplied"))
    monkeypatch.setattr(
        stats,
        "_host_cpu_pct",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("cpu unavailable")),
    )
    monkeypatch.setattr(
        stats,
        "_guest_resources",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("guest unavailable")),
    )

    snap = stats.pod_resource_snapshot(_running_cfg(), pod_state="running")

    assert snap.pod_state == "running"
    assert snap.cpu_pct is None
    assert snap.ram_used_gb is None
    assert snap.disk_total_gb is None
