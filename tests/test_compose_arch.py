# SPDX-License-Identifier: MIT
"""Architecture-aware compose template generation tests.

After the #287 refactor, CPU sub-flags live in the dedicated
``CPU_FLAGS:`` env (consumed by dockur's ``proc.sh``) rather than being
injected through ``ARGUMENTS:``. ``ARGUMENTS:`` now carries only the
non-``-cpu`` extras (virtio-rng device pair). dockur owns the hv-*
enlightenments via its ``HV=Y`` default and the nested-virt sub-flags
via the ``VMX=Y`` env.

The remaining x86 vs aarch64 split is in ``CPU_FLAGS``:

- x86_64: ``arch_capabilities=off`` (#141 / #140 -- Intel-only bits
  crash Windows when leaked through) plus ``+invtsc`` when supported
  (#215).
- aarch64: empty CPU_FLAGS -- dockur picks the right CPU on ARM.
"""

from __future__ import annotations

import pytest

import winpodx.core.config as _config_module
import winpodx.core.pod.compose as _compose_module
from winpodx.core.config import Config
from winpodx.core.pod.compose import _build_compose_content


@pytest.fixture(autouse=True)
def _stub_smbios_blob_write(monkeypatch):
    """Keep these tests hermetic: don't write a real SMBIOS blob to the OEM dir.

    The disguise (#246, T1.5) writes a synthetic SMBIOS blob during compose
    generation; stub the writer so the `-smbios file=` arg is still added but no
    file I/O happens. tests/test_smbios.py covers the real encode + write path.
    """
    monkeypatch.setattr(
        _compose_module,
        "_write_disguise_smbios_blob",
        lambda oem_dir: "/oem/winpodx-smbios.bin",
    )


def _cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.rdp.user = "User"
    cfg.rdp.password = "TestPassword1!"
    cfg.rdp.port = 3390
    cfg.pod.vnc_port = 8007
    cfg.pod.container_name = "winpodx-windows"
    # Baseline architecture tests must stay independent of host capability
    # detection (#215). Turn off the auto tuner so the CPU_FLAGS reflects
    # only the architecture branch under test.
    cfg.pod.tuning_profile = "off"
    # Likewise isolate from the default-ON hypervisor disguise (#246) — the
    # arch/tuning tests assert an exact CPU_FLAGS string. The disguise tests
    # below set it explicitly.
    cfg.pod.disguise_hypervisor = False
    return cfg


def test_compose_cpu_flags_x86_64(monkeypatch):
    """x86_64 hosts emit ``arch_capabilities=off`` via CPU_FLAGS env."""
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    content = _build_compose_content(_cfg())
    assert 'CPU_FLAGS: "arch_capabilities=off"' in content


def test_compose_cpu_flags_aarch64(monkeypatch):
    """aarch64 hosts emit empty CPU_FLAGS -- dockur picks the host CPU.

    Regression guard for issue #140: passing ``arch_capabilities=off`` on
    aarch64 crashes QEMU with ``Property 'host-arm-cpu.arch_capabilities'
    not found``.
    """
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "aarch64")
    content = _build_compose_content(_cfg())
    assert 'CPU_FLAGS: ""' in content
    assert "arch_capabilities" not in content


def test_compose_cpu_flags_unknown_arch_falls_through_to_x86(monkeypatch):
    """Unknown / unexpected machine() value falls through to the x86_64
    behaviour. Intentional: an unsupported platform should surface a
    clear QEMU error at pod start rather than silently using a partly-
    correct ARM config.
    """
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "riscv64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "riscv64")
    content = _build_compose_content(_cfg())
    assert 'CPU_FLAGS: "arch_capabilities=off"' in content


def test_compose_cpu_flags_invtsc_off_profile_does_not_append(monkeypatch):
    """``cfg.pod.tuning_profile = "off"`` keeps CPU_FLAGS at the baseline
    (``arch_capabilities=off`` only) even on an invtsc-capable host."""
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
            nested_kvm=False,
        ),
    )
    cfg = _cfg()
    cfg.pod.tuning_profile = "off"
    content = _build_compose_content(cfg)
    assert 'CPU_FLAGS: "arch_capabilities=off"' in content


# --- Bare-metal compatibility / hypervisor disguise (#246, default ON) ---

_DISGUISE_FLAGS = (
    "-hypervisor",
    "kvm=off",
    "-kvm-pv-eoi",
    "-kvm-pv-unhalt",
    "-kvm-pv-tlb-flush",
    "-kvm-asyncpf",
)


def test_compose_disguise_on_by_default(monkeypatch):
    """disguise_hypervisor=None (absent) defaults ON — emits the disguise flags."""
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    cfg = _cfg()
    cfg.pod.disguise_hypervisor = None  # the real default (absent key)
    assert cfg.pod.disguise_active is True
    content = _build_compose_content(cfg)
    for flag in _DISGUISE_FLAGS:
        assert flag in content, f"disguise flag {flag!r} missing (default ON)"
    # hv-vendor-id was deliberately dropped (collides with dockur hv flags).
    assert "hv-vendor-id" not in content


def test_compose_disguise_explicit_true(monkeypatch):
    """disguise_hypervisor=True emits the same flags as the default."""
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    cfg = _cfg()
    cfg.pod.disguise_hypervisor = True
    content = _build_compose_content(cfg)
    for flag in _DISGUISE_FLAGS:
        assert flag in content, f"disguise flag {flag!r} missing from compose"
    assert "hv-vendor-id" not in content


def test_compose_disguise_explicit_false_opts_out(monkeypatch):
    """disguise_hypervisor=False (explicit opt-out) emits no disguise flags."""
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    cfg = _cfg()
    cfg.pod.disguise_hypervisor = False
    assert cfg.pod.disguise_active is False
    content = _build_compose_content(cfg)
    assert "-hypervisor" not in content
    assert "kvm=off" not in content


def test_compose_disguise_smbios_mirrors_host_dmi(monkeypatch):
    """T1 (#246): the host's real, shell-safe DMI fields land in `-smbios`."""
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    # Synthetic fixture values (not any real machine's DMI).
    dmi = {
        "sys_vendor": "ACME",
        "product_name": "MDL-9000",
        "board_vendor": "ACME",
        "board_name": "BRD-42",
        "bios_vendor": "ACME",
        "bios_date": "01/01/2020",
        "chassis_vendor": "ACME",
    }
    monkeypatch.setattr(_compose_module, "_host_dmi_field", lambda n: dmi.get(n))
    cfg = _cfg()
    cfg.pod.disguise_hypervisor = None  # default ON
    content = _build_compose_content(cfg)
    assert "type=1,manufacturer=ACME,product=MDL-9000" in content
    assert "type=0,vendor=ACME,date=01/01/2020" in content
    assert "type=3,manufacturer=ACME" in content


def test_compose_disguise_smbios_skips_unsafe_dmi(monkeypatch):
    """Fields with spaces / unsafe chars are dropped, not mangled into ARGUMENTS."""
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    # Only product_name is unsafe (space); the vendor is safe. Emulate the
    # real _host_dmi_field safety filter (drop values with unsafe chars).
    dmi = {"sys_vendor": "ACME", "product_name": "Generic Model 9000"}

    def fake(n):
        v = dmi.get(n)
        return v if (v and all(c.isalnum() or c in "._/+-" for c in v)) else None

    monkeypatch.setattr(_compose_module, "_host_dmi_field", fake)
    cfg = _cfg()
    cfg.pod.disguise_hypervisor = True
    content = _build_compose_content(cfg)
    assert "manufacturer=ACME" in content  # safe vendor kept
    assert "Generic Model 9000" not in content  # spaced product dropped


def test_compose_disguise_off_emits_no_smbios(monkeypatch):
    """disguise off (explicit) → no host-DMI `-smbios` override."""
    monkeypatch.setattr(_compose_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_config_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(_compose_module, "_host_dmi_field", lambda n: "ACME")
    cfg = _cfg()  # _cfg() sets disguise_hypervisor = False
    content = _build_compose_content(cfg)
    assert "-smbios" not in content


def test_compose_cpu_flags_invtsc_auto_profile_appends_when_supported(monkeypatch):
    """``tuning_profile = "auto"`` + invtsc-capable host appends
    ``+invtsc`` to CPU_FLAGS (#215). hv-* enlightenments are NOT
    emitted by us -- dockur's HV=Y handles those.
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
    # +invtsc lands in CPU_FLAGS env now, not ARGUMENTS.
    assert 'CPU_FLAGS: "arch_capabilities=off,+invtsc"' in content
    # virtio-rng -device pair stays in ARGUMENTS (dockur doesn't add it).
    assert "virtio-rng-pci,rng=rng0" in content
    assert "rng-random,id=rng0,filename=/dev/urandom" in content
    # We no longer emit hv-* explicitly -- dockur owns those via HV=Y.
    assert "hv-relaxed" not in content
    assert "hv-evmcs" not in content
    # nested-virt CPU sub-flags also delegated -- we set VMX env instead.
    assert "+svm" not in content
    assert "+vmx" not in content
    # QEMU 10 dropped -no-hpet -- regression guard.
    assert "-no-hpet" not in content
    # nested_kvm=False so VMX env reads N.
    assert 'VMX: "N"' in content


def test_compose_cpu_flags_invtsc_skipped_when_host_lacks_flag(monkeypatch):
    """Even with ``tuning_profile = "auto"``, a host without
    ``constant_tsc + nonstop_tsc`` must not get ``+invtsc`` in CPU_FLAGS
    -- QEMU would either silently drop it or refuse to start.
    """
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


def test_compose_cpu_flags_aarch64_ignores_tuning_profile(monkeypatch):
    """aarch64 returns empty CPU_FLAGS regardless of profile -- invtsc
    and arch_capabilities are x86-only."""
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
    assert 'CPU_FLAGS: ""' in content
    assert "+invtsc" not in content
    assert "arch_capabilities" not in content


def test_compose_vmx_env_reflects_nested_virt(monkeypatch):
    """VMX env reads ``Y`` when tuning profile resolves nested virt on,
    ``N`` otherwise. dockur's proc.sh handles the actual +vmx/+svm
    sub-flag selection per CPU vendor.
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
            nested_kvm=True,
        ),
    )
    cfg = _cfg()
    cfg.pod.tuning_profile = "auto"
    content = _build_compose_content(cfg)
    # nested_kvm=True + auto profile -> VMX=Y.
    assert 'VMX: "Y"' in content


def test_compose_287_workaround_no_longer_needed(monkeypatch):
    """After the refactor, no ``-cpu host,...`` token ever lands in
    ARGUMENTS, so dockur's proc.sh:137 strip code path doesn't trigger
    and the ``-msg timestamp=on`` workaround (#287) is no longer
    appended. Regression guard.
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
            nested_kvm=False,
        ),
    )
    cfg = _cfg()
    # Every profile shape -- marker should never appear.
    for profile in ("off", "safe", "auto", "performance", "manual"):
        cfg.pod.tuning_profile = profile
        content = _build_compose_content(cfg)
        assert "-msg timestamp=on" not in content, f"marker leaked back in under profile={profile}"
        # And -cpu host, never appears in ARGUMENTS (the whole point).
        # We only need to check that ARGUMENTS lines don't contain it.
        for line in content.splitlines():
            if "ARGUMENTS:" in line:
                assert "-cpu" not in line, (
                    f"-cpu leaked into ARGUMENTS under profile={profile}: {line}"
                )
