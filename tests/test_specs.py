# SPDX-License-Identifier: MIT
"""Tests for utils.specs — host CPU/RAM detection + tier presets (v0.2.1)."""

from __future__ import annotations

from unittest.mock import patch

from winpodx.utils.specs import (
    HostSpecs,
    TuningCapability,
    all_tiers,
    detect_host_specs,
    format_tuning_summary,
    recommend_tier,
    recommend_tuning_profile,
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


def _cap(**overrides) -> TuningCapability:
    """Build a TuningCapability with sensible defaults; override per test."""
    base = {
        "invtsc": True,
        "io_uring": True,
        "hugepages_enabled": False,
        "dedicated_host": True,
        "kernel_version": (6, 18),
        "cpu_vendor": "intel",
        "nested_kvm": False,
    }
    base.update(overrides)
    return TuningCapability(**base)


class TestRecommendTuningProfile:
    """Profile-resolution policy for #215. The capability is a snapshot of
    host state; the user_pref dial decides how aggressive to be."""

    def test_off_disables_everything(self):
        p = recommend_tuning_profile(_cap(nested_kvm=True), user_pref="off")
        assert p.name == "off"
        assert not p.apply_invtsc
        assert not p.apply_platform_tick
        assert not p.apply_io_uring
        assert not p.apply_cpu_pinning
        # #245: new flags also gated off.
        assert not p.apply_hv_enlightenments
        assert not p.apply_virtio_rng
        assert not p.apply_evmcs
        assert not p.apply_nested_virt

    def test_safe_applies_only_t1_tunings(self):
        p = recommend_tuning_profile(_cap(), user_pref="safe")
        assert p.name == "safe"
        assert p.apply_invtsc
        assert p.apply_platform_tick
        # Host-setup tunings are off in safe mode regardless of capability.
        assert not p.apply_io_uring
        assert not p.apply_hugepages
        assert not p.apply_cpu_pinning
        assert not p.apply_no_balloon
        # #245: hv-* + virtio-rng safe -> on. evmcs + nested-virt need
        # explicit host-side opt-in -> off under "safe".
        assert p.apply_hv_enlightenments
        assert p.apply_virtio_rng
        assert not p.apply_evmcs
        assert not p.apply_nested_virt

    def test_auto_applies_everything_supported(self):
        p = recommend_tuning_profile(
            _cap(hugepages_enabled=True, nested_kvm=True), user_pref="auto"
        )
        assert p.name == "auto"
        assert p.apply_invtsc
        assert p.apply_io_uring
        assert p.apply_hugepages
        assert p.apply_cpu_pinning  # dedicated host
        assert p.apply_platform_tick
        assert p.apply_no_balloon
        # #245: hv-* + virtio-rng always on under auto for x86 hosts.
        assert p.apply_hv_enlightenments
        assert p.apply_virtio_rng
        # nested-virt + evmcs gated on nested_kvm; here it's True.
        assert p.apply_evmcs  # intel
        assert p.apply_nested_virt

    def test_auto_skips_unsupported_capabilities(self):
        cap = _cap(invtsc=False, io_uring=False, dedicated_host=False, nested_kvm=False)
        p = recommend_tuning_profile(cap, user_pref="auto")
        assert not p.apply_invtsc
        assert not p.apply_io_uring
        assert not p.apply_cpu_pinning
        assert not p.apply_no_balloon
        # platform_tick is guest-side + always safe.
        assert p.apply_platform_tick
        # #245: hv-* + virtio-rng still on (x86 + always-safe). evmcs +
        # nested-virt gated on nested_kvm -> off here.
        assert p.apply_hv_enlightenments
        assert p.apply_virtio_rng
        assert not p.apply_evmcs
        assert not p.apply_nested_virt

    def test_invtsc_requires_x86_vendor(self):
        # invtsc is x86-only; ARM TSC story is different. The recommender
        # must not flip it on even when /proc/cpuinfo happens to expose
        # the named flags (unlikely but defensive).
        p = recommend_tuning_profile(_cap(cpu_vendor="arm"), user_pref="auto")
        assert not p.apply_invtsc
        # #245: hv-* + nested-virt are also x86-only.
        assert not p.apply_hv_enlightenments
        assert not p.apply_nested_virt
        assert not p.apply_evmcs
        # virtio-rng is generic; on for any vendor.
        assert p.apply_virtio_rng

    def test_auto_nested_virt_intel_uses_vmx(self):
        # Intel + nested_kvm => +vmx + hv-evmcs.
        p = recommend_tuning_profile(_cap(cpu_vendor="intel", nested_kvm=True), user_pref="auto")
        assert p.apply_nested_virt
        assert p.apply_evmcs

    def test_auto_nested_virt_amd_uses_svm_no_evmcs(self):
        # AMD + nested_kvm => +svm. evmcs is Intel-only -> stays off.
        p = recommend_tuning_profile(_cap(cpu_vendor="amd", nested_kvm=True), user_pref="auto")
        assert p.apply_nested_virt
        assert not p.apply_evmcs

    def test_auto_evmcs_requires_nested_kvm(self):
        # Intel without nested_kvm exposed -> evmcs off (no-op without nesting).
        p = recommend_tuning_profile(_cap(cpu_vendor="intel", nested_kvm=False), user_pref="auto")
        assert not p.apply_evmcs
        assert not p.apply_nested_virt

    def test_performance_forces_pinning_and_no_balloon(self):
        # `performance` bypasses cap.dedicated_host so soft-gated knobs
        # (CPU pinning + no-balloon) come on even on a shared host.
        cap = _cap(dedicated_host=False)
        p = recommend_tuning_profile(cap, user_pref="performance")
        assert p.name == "performance"
        assert p.apply_cpu_pinning
        assert p.apply_no_balloon
        # Hard-gated knobs still respect capability detection.
        assert p.apply_invtsc  # invtsc=True in fixture
        assert p.apply_io_uring  # io_uring=True in fixture
        # hv-* + virtio-rng same as auto.
        assert p.apply_hv_enlightenments
        assert p.apply_virtio_rng

    def test_performance_still_skips_unsupported_hard_gates(self):
        # invtsc + io_uring are HARD-gated -- performance can't force a
        # CPU flag QEMU would reject or a kernel feature that crashes.
        cap = _cap(invtsc=False, io_uring=False, dedicated_host=False)
        p = recommend_tuning_profile(cap, user_pref="performance")
        assert not p.apply_invtsc
        assert not p.apply_io_uring
        # Soft-gated knobs forced on regardless.
        assert p.apply_cpu_pinning
        assert p.apply_no_balloon

    def test_performance_matches_auto_when_host_already_dedicated(self):
        # On a dedicated host, performance and auto resolve to the same
        # set of apply_* flags (only the .name differs).
        cap = _cap(dedicated_host=True, nested_kvm=True)
        perf = recommend_tuning_profile(cap, user_pref="performance")
        auto = recommend_tuning_profile(cap, user_pref="auto")
        for field in (
            "apply_invtsc",
            "apply_io_uring",
            "apply_hugepages",
            "apply_cpu_pinning",
            "apply_platform_tick",
            "apply_no_balloon",
            "apply_hv_enlightenments",
            "apply_virtio_rng",
            "apply_evmcs",
            "apply_nested_virt",
        ):
            assert getattr(perf, field) == getattr(auto, field), (
                f"{field} differs between performance and auto on dedicated host"
            )

    def test_unknown_pref_falls_back_to_auto(self):
        # Defensive: a hand-edited TOML slipping past Config validation
        # with a typo like "automatic" should be treated as auto, not
        # silently kill all tunings.
        p = recommend_tuning_profile(_cap(), user_pref="automatic-typo")
        assert p.name == "auto"


class TestFormatTuningSummary:
    def test_renders_yes_no_for_every_capability(self):
        cap = _cap()
        profile = recommend_tuning_profile(cap, user_pref="auto")
        out = format_tuning_summary(cap, profile)
        assert "invtsc" in out
        assert "io_uring" in out
        assert "hugepages" in out
        assert "Profile: auto" in out
        assert "+invtsc:" in out
        # #245: new rows.
        assert "nested_kvm" in out
        assert "hv-* + no-hpet" in out
        assert "virtio-rng" in out
        assert "nested virt" in out
        assert "hv-evmcs" in out


class TestDetectTuningCapabilityIntegration:
    """`detect_tuning_capability` reads /proc + os.uname; smoke-test that it
    runs to completion on the actual test host without raising."""

    def test_runs_without_error(self):
        from winpodx.utils.specs import detect_tuning_capability

        cap = detect_tuning_capability(vm_cpu_cores=4, vm_ram_gb=6)
        assert isinstance(cap.invtsc, bool)
        assert isinstance(cap.io_uring, bool)
        assert isinstance(cap.hugepages_enabled, bool)
        assert isinstance(cap.dedicated_host, bool)
        assert cap.cpu_vendor in ("intel", "amd", "arm", "unknown")

    def test_kernel_version_parses(self):
        from winpodx.utils.specs import _read_kernel_version

        with patch("os.uname") as mock_uname:
            mock_uname.return_value.release = "6.18.29-1-longterm"
            assert _read_kernel_version() == (6, 18)

    def test_kernel_version_unparseable_returns_none(self):
        from winpodx.utils.specs import _read_kernel_version

        with patch("os.uname") as mock_uname:
            mock_uname.return_value.release = "weird-no-numbers"
            assert _read_kernel_version() is None
