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
    """x86_64 hosts emit ``-cpu host,arch_capabilities=off`` plus the #287
    proc.sh marker (``_cfg()`` uses tuning_profile=off, which produces no
    extras, so the marker token is appended).
    """
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    content = _build_compose_content(_cfg())
    assert "-cpu host,arch_capabilities=off" in content
    assert "-msg timestamp=on" in content  # #287 workaround


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
    assert "-cpu host,arch_capabilities=off" in content
    assert "-msg timestamp=on" in content  # #287 workaround


def test_compose_arguments_invtsc_off_profile_does_not_append(monkeypatch):
    """``cfg.pod.tuning_profile = "off"`` must produce the baseline x86
    args (no `+invtsc`, no hv-*) even on an invtsc-capable host (#215).

    Also asserts the #287 proc.sh workaround: when no extra QEMU args
    would be emitted (off profile), `-msg timestamp=on` is appended so
    dockur's proc.sh:137 bash slice doesn't blow up on an empty post-
    strip ARGUMENTS string.
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
            cpu_vendor="intel",
        ),
    )
    cfg = _cfg()
    cfg.pod.tuning_profile = "off"
    content = _build_compose_content(cfg)
    # Baseline -cpu sub-options still emitted.
    assert "-cpu host,arch_capabilities=off" in content
    assert "+invtsc" not in content
    # #287 workaround: marker token appended when no other extra args.
    assert "-msg timestamp=on" in content


def test_compose_arguments_287_workaround_marker_only_when_no_extras(monkeypatch):
    """#287: when tuning produces no extra QEMU args, append a marker
    token so dockur's proc.sh:137 strip doesn't leave ARGUMENTS empty
    (the bash slice ``${args::-1}`` fails on an empty string).

    When tuning produces real extras (auto / safe with hv-* + virtio-rng),
    the marker should NOT be appended -- the real extras already serve
    the same purpose.
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
            cpu_vendor="intel",
        ),
    )
    cfg = _cfg()

    cfg.pod.tuning_profile = "auto"
    content_auto = _build_compose_content(cfg)
    # Auto profile adds hv-* + virtio-rng -- marker not needed.
    assert "-msg timestamp=on" not in content_auto
    assert "-no-hpet" in content_auto  # actual extra from auto

    cfg.pod.tuning_profile = "off"
    content_off = _build_compose_content(cfg)
    # Off profile produces no extras -- marker added.
    assert "-msg timestamp=on" in content_off


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
