# SPDX-License-Identifier: MIT
"""Unit tests for disk auto-grow / manual grow helpers (#318).

Pure host-side logic only -- the guest ``/exec`` calls (usage probe,
partition extend) are mocked. The actual diskpart / Resize-Partition
behaviour is covered by the real-Windows smoke gate, not here."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from winpodx.core import disk as D
from winpodx.core.config import Config
from winpodx.core.disk import (
    DiskError,
    DiskUsage,
    compute_autogrow_target,
    compute_grow_target,
    effective_max_bytes,
    format_size,
    get_guest_resources,
    maybe_autogrow,
    parse_size,
)

GIB = 1024**3


class _AgentResult:
    def __init__(self, rc: int, stdout: str) -> None:
        self.rc = rc
        self.stdout = stdout


class _AgentTransport:
    name = "agent"

    def __init__(self, result: _AgentResult) -> None:
        self._result = result

    def exec(self, _payload, *, timeout=None, description=None):
        return self._result


def test_get_guest_resources_parses_disk_and_ram(monkeypatch) -> None:
    import json

    payload = json.dumps(
        {
            "total": 64 * GIB,
            "free": 16 * GIB,
            "ramTotal": 16 * GIB,
            "ramFree": 4 * GIB,
        }
    )
    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        lambda _cfg: _AgentTransport(_AgentResult(0, payload)),
    )

    gr = get_guest_resources(Config())
    assert gr is not None
    assert gr.disk is not None
    assert gr.disk.total_bytes == 64 * GIB
    assert gr.disk.used_bytes == 48 * GIB
    assert gr.ram_total_bytes == 16 * GIB
    assert gr.ram_used_bytes == 12 * GIB  # 16 GiB total - 4 GiB free


def test_get_guest_resources_none_when_transport_not_agent(monkeypatch) -> None:
    class _FreeRDP:
        name = "freerdp"

    monkeypatch.setattr("winpodx.core.transport.dispatch", lambda _cfg: _FreeRDP())
    assert get_guest_resources(Config()) is None


def test_get_guest_resources_none_on_bad_rc(monkeypatch) -> None:
    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        lambda _cfg: _AgentTransport(_AgentResult(1, "")),
    )
    assert get_guest_resources(Config()) is None


def test_parse_size_units() -> None:
    assert parse_size("64G") == 64 * GIB
    assert parse_size("1T") == 1024 * GIB
    assert parse_size("512M") == 512 * 1024**2
    assert parse_size(" 128G ") == 128 * GIB  # whitespace tolerated


@pytest.mark.parametrize("bad", ["", "0G", "abc", "-5G", "64X", "G"])
def test_parse_size_rejects_garbage(bad: str) -> None:
    with pytest.raises(DiskError):
        parse_size(bad)


def test_format_size_roundtrips_whole_units() -> None:
    assert format_size(64 * GIB) == "64G"
    assert format_size(1024 * GIB) == "1T"
    assert format_size(96 * GIB) == "96G"


def test_format_size_rounds_up_partial_to_gib() -> None:
    # Non-whole-unit byte counts round up to the next whole GiB so a grow
    # never lands below the requested size.
    assert format_size(64 * GIB + 1) == "65G"


def test_compute_grow_target_increment() -> None:
    cfg = Config()
    cfg.pod.disk_size = "64G"
    cfg.pod.disk_autogrow_increment = "32G"
    assert compute_grow_target(cfg) == "96G"


def test_compute_grow_target_explicit_size() -> None:
    cfg = Config()
    cfg.pod.disk_size = "64G"
    assert compute_grow_target(cfg, target_size="200G") == "200G"


def test_compute_grow_target_custom_increment() -> None:
    cfg = Config()
    cfg.pod.disk_size = "64G"
    assert compute_grow_target(cfg, increment="64G") == "128G"


def test_compute_grow_target_refuses_shrink_or_noop() -> None:
    cfg = Config()
    cfg.pod.disk_size = "128G"
    with pytest.raises(DiskError):
        compute_grow_target(cfg, target_size="64G")
    with pytest.raises(DiskError):
        compute_grow_target(cfg, target_size="128G")


def test_compute_grow_target_enforces_explicit_cap() -> None:
    cfg = Config()
    cfg.pod.storage_path = ""  # no host probe -> cap is the explicit one
    cfg.pod.disk_size = "480G"
    cfg.pod.disk_max_size = "512G"
    cfg.pod.disk_autogrow_increment = "64G"  # 480 + 64 = 544 > 512
    with pytest.raises(DiskError):
        compute_grow_target(cfg)


def test_compute_grow_target_unbounded_without_cap_or_host() -> None:
    # No explicit cap and no resolvable host path -> no ceiling.
    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_max_size = ""
    cfg.pod.disk_size = "64G"
    assert compute_grow_target(cfg, target_size="2000G") == "2000G"


def test_effective_max_bytes_none_when_unbounded() -> None:
    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_max_size = ""
    assert effective_max_bytes(cfg, 64 * GIB) is None


def test_effective_max_bytes_uses_explicit_cap() -> None:
    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_max_size = "256G"
    assert effective_max_bytes(cfg, 64 * GIB) == 256 * GIB


def test_compute_autogrow_target_restores_headroom() -> None:
    # 64G disk, 58G used (~91%), target 30% free -> need total >= 58/0.7
    # ~= 82.9G -> round up to whole 32G increments from 64 -> 96G.
    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_max_size = ""
    cfg.pod.disk_size = "64G"
    cfg.pod.disk_autogrow_increment = "32G"
    cfg.pod.disk_autogrow_target_free_pct = 30
    usage = DiskUsage(total_bytes=64 * GIB, free_bytes=6 * GIB)
    assert compute_autogrow_target(cfg, usage) == "96G"


def test_compute_autogrow_target_clamped_to_cap() -> None:
    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_max_size = "80G"  # only one 32G step fits from 64G? no -> 64 only
    cfg.pod.disk_size = "64G"
    cfg.pod.disk_autogrow_increment = "32G"
    usage = DiskUsage(total_bytes=64 * GIB, free_bytes=2 * GIB)
    # 64 + 32 = 96 > 80 cap, and no whole increment fits under 80 -> None.
    assert compute_autogrow_target(cfg, usage) is None


def test_disk_usage_pct() -> None:
    u = DiskUsage(total_bytes=100 * GIB, free_bytes=10 * GIB)
    assert u.used_bytes == 90 * GIB
    assert u.used_pct == pytest.approx(90.0)


def test_disk_usage_zero_total_is_safe() -> None:
    u = DiskUsage(total_bytes=0, free_bytes=0)
    assert u.used_pct == 0.0


def _cfg_autogrow() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.disk_size = "64G"
    cfg.pod.disk_max_size = "512G"
    cfg.pod.disk_autogrow = True
    cfg.pod.disk_autogrow_threshold_pct = 80
    cfg.pod.disk_autogrow_increment = "32G"
    return cfg


def test_maybe_autogrow_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_autogrow()
    cfg.pod.disk_autogrow = False
    # Should never even probe when disabled.
    monkeypatch.setattr(
        "winpodx.core.disk.get_guest_disk_usage",
        lambda *a, **k: pytest.fail("probe should not run when autogrow off"),
    )
    assert maybe_autogrow(cfg) is False


def test_maybe_autogrow_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_autogrow()
    monkeypatch.setattr(
        "winpodx.core.disk.get_guest_disk_usage",
        lambda *a, **k: DiskUsage(total_bytes=100 * GIB, free_bytes=50 * GIB),
    )
    grew = {"called": False}
    monkeypatch.setattr(
        "winpodx.core.disk.grow_disk",
        lambda *a, **k: grew.__setitem__("called", True),
    )
    assert maybe_autogrow(cfg) is False
    assert grew["called"] is False


def test_maybe_autogrow_triggers_above_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_autogrow()  # disk_size 64G, max 512G
    monkeypatch.setattr(
        "winpodx.core.disk.get_guest_disk_usage",
        lambda *a, **k: DiskUsage(total_bytes=64 * GIB, free_bytes=3 * GIB),
    )
    calls = {"n": 0}
    monkeypatch.setattr(
        "winpodx.core.disk.grow_disk",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1),
    )
    assert maybe_autogrow(cfg) is True
    assert calls["n"] == 1


def test_maybe_autogrow_skips_at_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_autogrow()
    cfg.pod.disk_size = "512G"  # already at the explicit cap
    cfg.pod.disk_max_size = "512G"
    monkeypatch.setattr(
        "winpodx.core.disk.get_guest_disk_usage",
        lambda *a, **k: DiskUsage(total_bytes=512 * GIB, free_bytes=5 * GIB),
    )
    grew = {"called": False}
    monkeypatch.setattr(
        "winpodx.core.disk.grow_disk",
        lambda *a, **k: grew.__setitem__("called", True),
    )
    # Over threshold but can't grow past the cap -> no grow.
    assert maybe_autogrow(cfg) is False
    assert grew["called"] is False


def test_maybe_autogrow_unreachable_guest(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg_autogrow()
    monkeypatch.setattr("winpodx.core.disk.get_guest_disk_usage", lambda *a, **k: None)
    assert maybe_autogrow(cfg) is False


def test_config_validation_clamps_threshold() -> None:
    cfg = Config()
    cfg.pod.disk_autogrow_threshold_pct = 200
    cfg.pod.__post_init__()
    assert cfg.pod.disk_autogrow_threshold_pct == 99
    cfg.pod.disk_autogrow_threshold_pct = 5
    cfg.pod.__post_init__()
    assert cfg.pod.disk_autogrow_threshold_pct == 50


def test_config_validation_coerces_bad_sizes() -> None:
    cfg = Config()
    cfg.pod.disk_autogrow_increment = "garbage"
    cfg.pod.disk_max_size = "0G"  # invalid -> empty (no cap)
    cfg.pod.disk_autogrow_target_free_pct = 99  # out of range -> clamp to 50
    cfg.pod.__post_init__()
    assert cfg.pod.disk_autogrow_increment == "32G"
    assert cfg.pod.disk_max_size == ""
    assert cfg.pod.disk_autogrow_target_free_pct == 50


def test_config_max_size_empty_default() -> None:
    cfg = Config()
    assert cfg.pod.disk_max_size == ""


def test_parse_size_accepts_bytes_and_lowercase() -> None:
    assert parse_size("512") == 512
    assert parse_size("2g") == 2 * GIB


def test_format_size_rejects_non_positive() -> None:
    with pytest.raises(DiskError, match="non-positive"):
        format_size(0)


def test_format_size_uses_smaller_whole_units() -> None:
    assert format_size(2 * 1024**2) == "2M"
    assert format_size(3 * 1024) == "3K"


def test_host_free_and_total_walks_to_existing_parent(tmp_path, monkeypatch) -> None:
    cfg = Config()
    cfg.pod.storage_path = str(tmp_path / "not" / "created")
    seen = []
    monkeypatch.setattr(
        D.shutil,
        "disk_usage",
        lambda path: seen.append(path) or SimpleNamespace(free=25 * GIB, total=100 * GIB),
    )
    assert D._host_free_and_total(cfg) == (25 * GIB, 100 * GIB)
    assert seen == [tmp_path]


def test_host_free_and_total_returns_none_on_probe_error(tmp_path, monkeypatch) -> None:
    cfg = Config()
    cfg.pod.storage_path = str(tmp_path)
    monkeypatch.setattr(D.shutil, "disk_usage", lambda _path: (_ for _ in ()).throw(OSError()))
    assert D._host_free_and_total(cfg) is None


def test_effective_max_preserves_ten_gib_floor(monkeypatch) -> None:
    cfg = Config()
    cfg.pod.disk_max_size = ""
    monkeypatch.setattr(D, "_host_free_and_total", lambda _cfg: (20 * GIB, 50 * GIB))
    assert effective_max_bytes(cfg, 64 * GIB) == 74 * GIB


def test_effective_max_preserves_ten_percent_for_large_host(monkeypatch) -> None:
    cfg = Config()
    cfg.pod.disk_max_size = ""
    monkeypatch.setattr(D, "_host_free_and_total", lambda _cfg: (80 * GIB, 500 * GIB))
    assert effective_max_bytes(cfg, 64 * GIB) == 94 * GIB


def test_effective_max_never_shrinks_when_reserve_exceeds_free(monkeypatch) -> None:
    cfg = Config()
    cfg.pod.disk_max_size = ""
    monkeypatch.setattr(D, "_host_free_and_total", lambda _cfg: (5 * GIB, 100 * GIB))
    assert effective_max_bytes(cfg, 64 * GIB) == 64 * GIB


def test_effective_max_uses_stricter_explicit_cap(monkeypatch) -> None:
    cfg = Config()
    cfg.pod.disk_max_size = "80G"
    monkeypatch.setattr(D, "_host_free_and_total", lambda _cfg: (100 * GIB, 200 * GIB))
    assert effective_max_bytes(cfg, 64 * GIB) == 80 * GIB


def test_effective_max_ignores_invalid_explicit_cap(monkeypatch) -> None:
    cfg = Config()
    cfg.pod.disk_max_size = "invalid"
    monkeypatch.setattr(D, "_host_free_and_total", lambda _cfg: None)
    assert effective_max_bytes(cfg, 64 * GIB) is None


def test_get_guest_disk_usage_agent_only_success(monkeypatch) -> None:
    payload = json.dumps({"total": 80 * GIB, "free": 20 * GIB})
    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        lambda _cfg: _AgentTransport(_AgentResult(0, payload)),
    )
    usage = D.get_guest_disk_usage(Config(), agent_only=True)
    assert usage == DiskUsage(total_bytes=80 * GIB, free_bytes=20 * GIB)


def test_get_guest_disk_usage_agent_only_handles_dispatch_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert D.get_guest_disk_usage(Config(), agent_only=True) is None


def test_get_guest_disk_usage_agent_only_handles_exec_error(monkeypatch) -> None:
    class _BrokenAgent:
        name = "agent"

        def exec(self, *_args, **_kwargs):
            raise RuntimeError("offline")

    monkeypatch.setattr("winpodx.core.transport.dispatch", lambda _cfg: _BrokenAgent())
    assert D.get_guest_disk_usage(Config(), agent_only=True) is None


@pytest.mark.parametrize(
    "result",
    [
        _AgentResult(1, ""),
        _AgentResult(0, "not-json"),
        _AgentResult(0, '{"total": 0, "free": 0}'),
    ],
)
def test_get_guest_disk_usage_agent_only_rejects_bad_results(monkeypatch, result) -> None:
    monkeypatch.setattr("winpodx.core.transport.dispatch", lambda _cfg: _AgentTransport(result))
    assert D.get_guest_disk_usage(Config(), agent_only=True) is None


def test_get_guest_disk_usage_transport_fallback_success(monkeypatch) -> None:
    payload = json.dumps({"total": 96 * GIB, "free": 32 * GIB})
    result = SimpleNamespace(rc=0, stdout=payload)
    monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", lambda *_a, **_k: result)
    assert D.get_guest_disk_usage(Config()) == DiskUsage(96 * GIB, 32 * GIB)


def test_get_guest_disk_usage_transport_error_returns_none(monkeypatch) -> None:
    from winpodx.core.windows_exec import WindowsExecError

    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_via_transport",
        lambda *_a, **_k: (_ for _ in ()).throw(WindowsExecError("offline")),
    )
    assert D.get_guest_disk_usage(Config()) is None


def test_get_guest_resources_handles_dispatch_and_exec_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    assert get_guest_resources(Config()) is None

    class _BrokenAgent:
        name = "agent"

        def exec(self, *_args, **_kwargs):
            raise RuntimeError("offline")

    monkeypatch.setattr("winpodx.core.transport.dispatch", lambda _cfg: _BrokenAgent())
    assert get_guest_resources(Config()) is None


def test_get_guest_resources_rejects_unparseable_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        lambda _cfg: _AgentTransport(_AgentResult(0, "{}")),
    )
    assert get_guest_resources(Config()) is None


def test_get_guest_resources_handles_zero_disk_and_ram(monkeypatch) -> None:
    payload = json.dumps({"total": 0, "free": 0, "ramTotal": 0, "ramFree": 0})
    monkeypatch.setattr(
        "winpodx.core.transport.dispatch",
        lambda _cfg: _AgentTransport(_AgentResult(0, payload)),
    )
    resources = get_guest_resources(Config())
    assert resources is not None
    assert resources.disk is None
    assert resources.ram_used_bytes is None
    assert resources.ram_total_bytes is None


def test_extend_guest_system_volume_success(monkeypatch) -> None:
    result = SimpleNamespace(ok=True, rc=0, stderr="", stdout="extended")
    calls = []
    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_in_windows",
        lambda cfg, script, **kwargs: calls.append((cfg, script, kwargs)) or result,
    )
    cfg = Config()
    assert D.extend_guest_system_volume(cfg, timeout=9) is True
    assert calls[0][2] == {"timeout": 9, "description": "extend-system-volume"}
    assert "Resize-Partition" in calls[0][1]


def test_extend_guest_system_volume_handles_failure(monkeypatch) -> None:
    result = SimpleNamespace(ok=False, rc=5, stderr="denied", stdout="")
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", lambda *_a, **_k: result)
    assert D.extend_guest_system_volume(Config()) is False


def test_extend_guest_system_volume_handles_exec_error(monkeypatch) -> None:
    from winpodx.core.windows_exec import WindowsExecError

    monkeypatch.setattr(
        "winpodx.core.windows_exec.run_in_windows",
        lambda *_a, **_k: (_ for _ in ()).throw(WindowsExecError("offline")),
    )
    assert D.extend_guest_system_volume(Config()) is False


def test_compute_grow_target_refuses_when_host_reserve_has_no_headroom(monkeypatch) -> None:
    cfg = Config()
    cfg.pod.disk_size = "64G"
    cfg.pod.disk_max_size = ""
    monkeypatch.setattr(D, "effective_max_bytes", lambda _cfg, current: current)
    with pytest.raises(DiskError, match="breach the host reserve"):
        compute_grow_target(cfg, target_size="65G")


def test_compute_grow_target_names_host_space_limit(monkeypatch) -> None:
    cfg = Config()
    cfg.pod.disk_size = "64G"
    cfg.pod.disk_max_size = ""
    monkeypatch.setattr(D, "effective_max_bytes", lambda _cfg, _current: 70 * GIB)
    with pytest.raises(DiskError, match="host free space minus reserve"):
        compute_grow_target(cfg, target_size="80G")


def test_compute_autogrow_target_clamps_to_largest_fitting_increment(monkeypatch) -> None:
    cfg = Config()
    cfg.pod.disk_size = "64G"
    cfg.pod.disk_autogrow_increment = "16G"
    cfg.pod.disk_autogrow_target_free_pct = 40
    monkeypatch.setattr(D, "effective_max_bytes", lambda _cfg, _current: 100 * GIB)
    usage = DiskUsage(total_bytes=64 * GIB, free_bytes=1 * GIB)
    assert compute_autogrow_target(cfg, usage) == "96G"


def test_grow_disk_rejects_manual_backend() -> None:
    cfg = Config()
    cfg.pod.backend = "manual"
    with pytest.raises(DiskError, match="only supports podman/docker"):
        D.grow_disk(cfg, target_size="96G")


def _patch_grow_lifecycle(monkeypatch, status_state):
    calls = []
    monkeypatch.setattr("winpodx.core.pod.stop_pod", lambda cfg: calls.append(("stop", cfg)))
    monkeypatch.setattr(
        "winpodx.core.pod.start_pod",
        lambda cfg: calls.append(("start", cfg)) or SimpleNamespace(state=status_state),
    )
    monkeypatch.setattr(
        "winpodx.core.pod.compose.generate_compose",
        lambda cfg: calls.append(("compose", cfg.pod.disk_size)),
    )
    return calls


def test_grow_disk_success_without_partition_extend(monkeypatch) -> None:
    from winpodx.core.pod import PodState

    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_size = "64G"
    cfg.save = lambda: None
    calls = _patch_grow_lifecycle(monkeypatch, PodState.RUNNING)
    result = D.grow_disk(cfg, target_size="96G", extend_partition=False)
    assert result == D.GrowResult("64G", "96G", False, "")
    assert [(name, value) for name, value in calls if name == "compose"] == [("compose", "96G")]


def test_grow_disk_rolls_back_on_compose_failure(monkeypatch) -> None:
    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_size = "64G"
    saved = []
    cfg.save = lambda: saved.append(cfg.pod.disk_size)
    monkeypatch.setattr("winpodx.core.pod.stop_pod", lambda _cfg: None)
    monkeypatch.setattr(
        "winpodx.core.pod.compose.generate_compose",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("bad compose")),
    )
    with pytest.raises(DiskError, match="failed to regenerate compose"):
        D.grow_disk(cfg, target_size="96G")
    assert cfg.pod.disk_size == "64G"
    assert saved == ["96G", "64G"]


def test_grow_disk_rolls_back_when_container_fails(monkeypatch) -> None:
    from winpodx.core.pod import PodState

    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_size = "64G"
    saved = []
    cfg.save = lambda: saved.append(cfg.pod.disk_size)
    calls = _patch_grow_lifecycle(monkeypatch, PodState.STOPPED)
    with pytest.raises(DiskError, match="rolled disk_size back"):
        D.grow_disk(cfg, target_size="96G")
    assert cfg.pod.disk_size == "64G"
    assert saved == ["96G", "64G"]
    assert ("compose", "64G") in calls


def test_grow_disk_extends_partition_after_guest_ready(monkeypatch) -> None:
    from winpodx.core.pod import PodState

    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_size = "64G"
    cfg.save = lambda: None
    _patch_grow_lifecycle(monkeypatch, PodState.STARTING)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive", lambda _cfg, timeout: timeout == 7
    )
    monkeypatch.setattr(D, "extend_guest_system_volume", lambda _cfg: True)
    result = D.grow_disk(cfg, target_size="96G", wait_timeout=7)
    assert result.partition_extended is True
    assert result.note == ""


def test_grow_disk_reports_partition_extension_failure(monkeypatch) -> None:
    from winpodx.core.pod import PodState

    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_size = "64G"
    cfg.save = lambda: None
    _patch_grow_lifecycle(monkeypatch, PodState.RUNNING)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive", lambda *_a, **_k: True
    )
    monkeypatch.setattr(D, "extend_guest_system_volume", lambda _cfg: False)
    result = D.grow_disk(cfg, target_size="96G")
    assert result.partition_extended is False
    assert "C: not extended" in result.note


def test_grow_disk_reports_unresponsive_guest(monkeypatch) -> None:
    from winpodx.core.pod import PodState

    cfg = Config()
    cfg.pod.storage_path = ""
    cfg.pod.disk_size = "64G"
    cfg.save = lambda: None
    _patch_grow_lifecycle(monkeypatch, PodState.RUNNING)
    monkeypatch.setattr(
        "winpodx.core.provisioner.wait_for_windows_responsive", lambda *_a, **_k: False
    )
    result = D.grow_disk(cfg, target_size="96G")
    assert result.partition_extended is False
    assert "didn't become responsive" in result.note


def test_maybe_autogrow_handles_grow_refusal(monkeypatch) -> None:
    cfg = _cfg_autogrow()
    monkeypatch.setattr(
        D, "get_guest_disk_usage", lambda _cfg: DiskUsage(total_bytes=64 * GIB, free_bytes=1)
    )
    monkeypatch.setattr(D, "compute_autogrow_target", lambda _cfg, _usage: "96G")
    monkeypatch.setattr(
        D, "grow_disk", lambda *_a, **_k: (_ for _ in ()).throw(DiskError("reserve"))
    )
    assert maybe_autogrow(cfg) is False
