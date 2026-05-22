# SPDX-License-Identifier: MIT
"""Architecture-aware compose template generation tests.

Issue #141 Phase 2: the QEMU ``ARGUMENTS:`` value must differ between
x86_64 and aarch64 hosts. On x86_64 we pass
``-cpu host,arch_capabilities=off``; on aarch64 the ``arch_capabilities``
sub-option doesn't exist and crashes QEMU at boot (issue #140), so we
emit only ``-cpu host``.
"""

from __future__ import annotations

import winpodx.core.config as _config_module
import winpodx.core.pod.compose as _compose_module
from winpodx.core.config import Config
from winpodx.core.pod.compose import _build_compose_content


def _cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.rdp.user = "User"
    cfg.rdp.password = "TestPassword1!"
    cfg.rdp.port = 3390
    cfg.pod.vnc_port = 8007
    cfg.pod.container_name = "winpodx-windows"
    # Baseline architecture tests must stay independent of host capability
    # detection (#215). Turn off the auto tuner so the QEMU args reflect
    # only the architecture branch under test.
    cfg.pod.tuning_profile = "off"
    return cfg


def test_compose_arguments_x86_64(monkeypatch):
    """x86_64 hosts emit ``-cpu host,arch_capabilities=off``."""
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    content = _build_compose_content(_cfg())
    assert 'ARGUMENTS: "-cpu host,arch_capabilities=off"' in content


def test_compose_arguments_aarch64(monkeypatch):
    """aarch64 hosts emit ``-cpu host`` only (no arch_capabilities).

    Regression guard for issue #140: passing ``arch_capabilities=off`` on
    aarch64 crashes QEMU with ``Property 'host-arm-cpu.arch_capabilities'
    not found``.
    """
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "aarch64")
    content = _build_compose_content(_cfg())
    assert 'ARGUMENTS: "-cpu host"' in content
    assert "arch_capabilities" not in content


def test_compose_arguments_unknown_arch_falls_through_to_x86(monkeypatch):
    """Unknown / unexpected machine() value falls through to the x86_64
    behaviour. This is intentional: an unsupported platform should get the
    "wrong" arguments and surface a clear QEMU error at pod start rather
    than silently using a partially-correct ARM config.
    """
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "riscv64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "riscv64")
    content = _build_compose_content(_cfg())
    assert 'ARGUMENTS: "-cpu host,arch_capabilities=off"' in content


def test_compose_arguments_invtsc_off_profile_does_not_append(monkeypatch):
    """``cfg.pod.tuning_profile = "off"`` must produce the baseline x86
    args even on an invtsc-capable host (#215)."""
    import winpodx.utils.specs as specs

    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        specs,
        "detect_tuning_capability",
        lambda *, vm_cpu_cores, vm_ram_gb: specs.TuningCapability(
            invtsc=True,
            io_uring=True,
            hugepages_enabled=False,
            dedicated_host=False,
            kernel_version=(6, 18),
            cpu_vendor="intel",
        ),
    )
    cfg = _cfg()
    cfg.pod.tuning_profile = "off"
    content = _build_compose_content(cfg)
    assert 'ARGUMENTS: "-cpu host,arch_capabilities=off"' in content
    assert "+invtsc" not in content


def test_compose_arguments_invtsc_auto_profile_appends_when_supported(monkeypatch):
    """``tuning_profile = "auto"`` + invtsc-capable host appends ``+invtsc``
    so the Windows guest sees an invariant TSC clocksource (#215).

    #245: auto also now appends hv-* enlightenments, virtio-rng, and
    -no-hpet. Assert each piece is present rather than pinning the full
    string — the assertion stays meaningful even when the list grows.
    """
    import winpodx.utils.specs as specs

    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        specs,
        "detect_tuning_capability",
        lambda *, vm_cpu_cores, vm_ram_gb: specs.TuningCapability(
            invtsc=True,
            io_uring=True,
            hugepages_enabled=False,
            dedicated_host=False,
            kernel_version=(6, 18),
            cpu_vendor="amd",
            nested_kvm=False,
        ),
    )
    cfg = _cfg()
    cfg.pod.tuning_profile = "auto"
    content = _build_compose_content(cfg)
    # invtsc still on the -cpu sub-option line.
    assert "+invtsc" in content
    # #245: hv-* enlightenments + virtio-rng + -no-hpet always on under
    # auto for x86 hosts. nested_kvm=False so +svm / hv-evmcs stay off.
    assert "hv-relaxed" in content
    assert "hv-spinlocks=0x1fff" in content
    assert "hv-stimer-direct" in content
    assert "-no-hpet" in content
    assert "virtio-rng-pci,rng=rng0" in content
    assert "rng-random,id=rng0,filename=/dev/urandom" in content
    # AMD + nested_kvm=False => no +svm, no hv-evmcs.
    assert "+svm" not in content
    assert "hv-evmcs" not in content


def test_compose_arguments_invtsc_skipped_when_host_lacks_flag(monkeypatch):
    """Even with ``tuning_profile = "auto"``, a host without
    ``constant_tsc + nonstop_tsc`` must not get ``+invtsc`` — QEMU
    would either silently drop it or refuse to start."""
    import winpodx.utils.specs as specs

    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        specs,
        "detect_tuning_capability",
        lambda *, vm_cpu_cores, vm_ram_gb: specs.TuningCapability(
            invtsc=False,
            io_uring=True,
            hugepages_enabled=False,
            dedicated_host=False,
            kernel_version=(6, 18),
            cpu_vendor="intel",
            nested_kvm=False,
        ),
    )
    cfg = _cfg()
    cfg.pod.tuning_profile = "auto"
    content = _build_compose_content(cfg)
    assert "+invtsc" not in content


def test_compose_arguments_aarch64_ignores_tuning_profile(monkeypatch):
    """aarch64 returns ``-cpu host`` regardless of profile — invtsc is
    x86-only."""
    import winpodx.utils.specs as specs

    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(
        specs,
        "detect_tuning_capability",
        lambda *, vm_cpu_cores, vm_ram_gb: specs.TuningCapability(
            invtsc=True,
            io_uring=True,
            hugepages_enabled=False,
            dedicated_host=True,
            kernel_version=(6, 18),
            cpu_vendor="arm",
            nested_kvm=False,
        ),
    )
    cfg = _cfg()
    cfg.pod.tuning_profile = "auto"
    content = _build_compose_content(cfg)
    assert 'ARGUMENTS: "-cpu host"' in content
    assert "+invtsc" not in content
    assert "arch_capabilities" not in content
