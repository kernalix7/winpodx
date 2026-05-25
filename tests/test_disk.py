# SPDX-License-Identifier: MIT
"""Unit tests for disk auto-grow / manual grow helpers (#318).

Pure host-side logic only -- the guest ``/exec`` calls (usage probe,
partition extend) are mocked. The actual diskpart / Resize-Partition
behaviour is covered by the real-Windows smoke gate, not here."""

from __future__ import annotations

import pytest

from winpodx.core.config import Config
from winpodx.core.disk import (
    DiskError,
    DiskUsage,
    compute_autogrow_target,
    compute_grow_target,
    effective_max_bytes,
    format_size,
    maybe_autogrow,
    parse_size,
)

GIB = 1024**3


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
