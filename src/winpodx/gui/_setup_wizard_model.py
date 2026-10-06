# SPDX-License-Identifier: MIT
"""Answers, defaults, and the handle_setup Namespace the wizard builds."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from winpodx.core.config import Config
from winpodx.core.i18n import tr


@dataclass(frozen=True, slots=True)
class SetupAnswers:
    """Values the wizard collects and hands to ``handle_setup``."""

    win_version: str
    language: str
    region: str
    keyboard: str
    timezone: str
    cpu_cores: int
    ram_gb: int
    disk_size: str
    rdp_user: str
    tuning_profile: str
    backend: str
    storage_path: str
    win_iso: str


@dataclass(frozen=True, slots=True)
class PrereqSpec:
    """One HostState field as shown on the Prerequisites page."""

    field: str
    title: str
    note: str
    required: bool
    unfixable_hint: str


def prereq_specs() -> tuple[PrereqSpec, ...]:
    """Host setup plus minimum Windows boot requirements."""
    return (
        PrereqSpec(
            "cpu_virtualization", tr("CPU virtualization"), tr("VT-x / AMD-V enabled"), True, ""
        ),
        PrereqSpec(
            "dev_kvm_present",
            tr("/dev/kvm present"),
            tr("host kernel exposes KVM"),
            True,
            tr("Enable VT-x / AMD-V in firmware, then load the kvm module."),
        ),
        PrereqSpec(
            "dev_kvm_readable",
            tr("/dev/kvm readable by you"),
            tr("rootless QEMU can open it"),
            True,
            tr("Cannot open /dev/kvm. Check group membership and udev rules."),
        ),
        PrereqSpec(
            "kvm_group_exists",
            tr("kvm group exists"),
            "",
            True,
            tr("Create the kvm group on this host."),
        ),
        PrereqSpec(
            "in_kvm_group",
            tr("you are in kvm group"),
            tr("log out + back in after fix"),
            True,
            "",
        ),
        PrereqSpec(
            "subuid_configured",
            tr("subuid entry for you"),
            tr("rootless podman uid mapping"),
            True,
            "",
        ),
        PrereqSpec(
            "subgid_configured",
            tr("subgid entry for you"),
            tr("rootless podman gid mapping"),
            True,
            "",
        ),
        PrereqSpec(
            "kvm_module_persistent",
            tr("kvm module loads at boot"),
            tr("persistence across reboots"),
            False,
            "",
        ),
        PrereqSpec("ram", tr("Host RAM"), tr("at least 8 GiB"), True, ""),
        PrereqSpec("disk", tr("Windows storage space"), tr("disk and ISO space"), True, ""),
        PrereqSpec("iso", tr("Local Windows ISO"), tr("readable when selected"), True, ""),
        PrereqSpec("freerdp", tr("FreeRDP"), tr("version 3 or newer"), True, ""),
        PrereqSpec("backend", tr("Container backend"), tr("Podman or Docker usable"), True, ""),
        PrereqSpec("compose", tr("Compose provider"), tr("required to start Windows"), True, ""),
    )


def collect_answers(cfg: Config | None = None) -> SetupAnswers:
    """Prefill from CLI sources, or from an existing config on reinstall."""
    from winpodx.backend.select import choose_backend
    from winpodx.core.storage_migration import default_target_path
    from winpodx.utils.locale import detect_install_locale, detect_timezone
    from winpodx.utils.specs import detect_host_specs, recommend_tier

    host = detect_host_specs()
    tier = recommend_tier(host)
    language, region, keyboard = detect_install_locale()
    timezone = detect_timezone()
    if cfg is None:
        return SetupAnswers(
            win_version="11",
            language=language,
            region=region,
            keyboard=keyboard,
            timezone=timezone,
            cpu_cores=tier.cpu_cores,
            ram_gb=tier.ram_gb,
            disk_size="64G",
            rdp_user="Docker",
            tuning_profile="auto",
            backend=choose_backend(),
            storage_path=str(default_target_path()),
            win_iso="",
        )
    return SetupAnswers(
        win_version=cfg.pod.win_version or "11",
        language=cfg.pod.language or language,
        region=cfg.pod.region or region,
        keyboard=cfg.pod.keyboard or keyboard,
        timezone=cfg.pod.timezone or timezone,
        cpu_cores=cfg.pod.cpu_cores or tier.cpu_cores,
        ram_gb=cfg.pod.ram_gb or tier.ram_gb,
        disk_size=cfg.pod.disk_size or "64G",
        rdp_user=cfg.rdp.user or "Docker",
        tuning_profile=cfg.pod.tuning_profile or "auto",
        backend=cfg.pod.backend,
        storage_path=cfg.pod.storage_path,
        win_iso="",
    )


def host_spec_summary() -> str:
    """Host CPU/RAM plus the recommended VM tier, same sources as the CLI."""
    from winpodx.utils.specs import detect_host_specs, recommend_tier

    host = detect_host_specs()
    tier = recommend_tier(host)
    return tr(
        "Host: {cpu} threads, {ram} GB RAM. Recommended: {label} ({cores} cores, {vm_ram} GB)."
    ).format(
        cpu=host.cpu_threads,
        ram=host.ram_gb,
        label=tier.label,
        cores=tier.cpu_cores,
        vm_ram=tier.ram_gb,
    )


def to_namespace(answers: SetupAnswers, *, require_agent: bool = True) -> argparse.Namespace:
    """Build the Namespace ``handle_setup`` / ``apply_setup_presets`` consume."""
    return argparse.Namespace(
        backend=answers.backend,
        storage_path=answers.storage_path or None,
        win_iso=answers.win_iso or None,
        win_version=answers.win_version,
        update_image=False,
        migrate_storage=False,
        migrate_storage_target=None,
        non_interactive=True,
        require_agent=require_agent,
        customize=False,
        cpu_cores=answers.cpu_cores,
        ram_gb=answers.ram_gb,
        language=answers.language,
        region=answers.region,
        keyboard=answers.keyboard,
        timezone=answers.timezone,
        tuning_profile=answers.tuning_profile,
        disk_size=answers.disk_size,
        rdp_user=answers.rdp_user,
    )
