"""Tests for utils.specs — host CPU/RAM detection + tier presets (v0.2.1)."""

from __future__ import annotations

from winpodx.utils.specs import (
    HostSpecs,
    all_tiers,
    detect_host_specs,
    recommend_tier,
)


class TestRecommendTier:
    def test_high_when_both_axes_clear(self):
        t = recommend_tier(HostSpecs(cpu_threads=16, ram_gb=64))
        assert t.name == "high"
        assert t.cpu_cores == 8
        assert t.ram_gb == 12

    def test_mid_when_both_axes_clear_mid(self):
        t = recommend_tier(HostSpecs(cpu_threads=8, ram_gb=24))
        assert t.name == "mid"
        assert t.cpu_cores == 4
        assert t.ram_gb == 6

    def test_low_for_small_machines(self):
        t = recommend_tier(HostSpecs(cpu_threads=2, ram_gb=8))
        assert t.name == "low"
        assert t.cpu_cores == 2
        assert t.ram_gb == 4

    def test_ram_rich_but_cpu_poor_falls_to_low(self):
        # Picking the higher tier requires BOTH axes to clear — a host
        # with 64 GB but only 4 cores still gets "low" (CPU is the
        # bottleneck for the VM workload).
        t = recommend_tier(HostSpecs(cpu_threads=4, ram_gb=64))
        assert t.name == "low"

    def test_cpu_rich_but_ram_poor_falls_to_low(self):
        # Symmetric case — lots of CPU but only 8 GB RAM.
        t = recommend_tier(HostSpecs(cpu_threads=24, ram_gb=8))
        assert t.name == "low"

    def test_exactly_at_mid_threshold(self):
        t = recommend_tier(HostSpecs(cpu_threads=6, ram_gb=16))
        assert t.name == "mid"

    def test_just_below_mid_threshold(self):
        t = recommend_tier(HostSpecs(cpu_threads=5, ram_gb=16))
        assert t.name == "low"  # cpu under 6 -> low
        t = recommend_tier(HostSpecs(cpu_threads=6, ram_gb=15))
        assert t.name == "low"  # ram under 16 -> low

    def test_exactly_at_high_threshold(self):
        t = recommend_tier(HostSpecs(cpu_threads=12, ram_gb=32))
        assert t.name == "high"


class TestDetectHostSpecs:
    def test_returns_usable_object(self):
        # Just verify the function runs and returns sane values on the
        # test host — exact numbers vary by CI runner.
        s = detect_host_specs()
        assert s.cpu_threads >= 1
        assert s.ram_gb >= 1


class TestAllTiers:
    def test_returns_three_in_order(self):
        tiers = all_tiers()
        assert [t.name for t in tiers] == ["low", "mid", "high"]
        # CPU and RAM must be monotonically non-decreasing.
        cpus = [t.cpu_cores for t in tiers]
        rams = [t.ram_gb for t in tiers]
        assert cpus == sorted(cpus)
        assert rams == sorted(rams)
