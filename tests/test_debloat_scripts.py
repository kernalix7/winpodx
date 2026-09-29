# SPDX-License-Identifier: MIT

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Final

import pytest

RegistryValue = tuple[str, str]

DEBLOAT_DIR: Final = Path(__file__).resolve().parents[1] / "scripts" / "windows" / "debloat"
SAFE_SCHEDULED_TASKS: Final = (
    r"\Microsoft\Office\OfficeTelemetryAgentFallBack2016",
    r"\Microsoft\Office\OfficeTelemetryAgentLogOn2016",
    r"\Microsoft\Windows\Application Experience\AitAgent",
    r"\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser",
    r"\Microsoft\Windows\Application Experience\ProgramDataUpdater",
    r"\Microsoft\Windows\Application Experience\ProgramInventoryUpdater",
    r"\Microsoft\Windows\Autochk\Proxy",
    r"\Microsoft\Windows\Customer Experience Improvement Program\BthSQM",
    r"\Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
    r"\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask",
    r"\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip",
    r"\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector",
    r"\Microsoft\Windows\Feedback\Siuf\DmClient",
    r"\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload",
    r"\Microsoft\Windows\Maps\MapsToastTask",
    r"\Microsoft\Windows\Maps\MapsUpdateTask",
    r"\Microsoft\Windows\PI\Sqm-Tasks",
    r"\Microsoft\Windows\RetailDemo\CleanupOfflineContent",
    r"\Microsoft\Windows\Windows Error Reporting\QueueReporting",
    r"\Microsoft\Windows\WindowsAI\Copilot\CopilotDataCollectionTask",
    r"\Microsoft\Windows\WindowsAI\Insights\InsightsDataCollectionTask",
)
PR_ONLY_AD_VALUES: Final = frozenset(
    {
        (
            r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Start",
            "ShowRecentList",
        ),
        (
            r"HKCU:\Software\Microsoft\Windows\CurrentVersion\SystemSettings"
            r"\AccountNotifications",
            "EnableAccountNotifications",
        ),
        (
            r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
            "Start_RecoPersonalizedSites",
        ),
        (
            r"HKCU:\Software\Microsoft\Windows\CurrentVersion\CPSS\Store"
            r"\TailoredExperiencesWithDiagnosticDataEnabled",
            "Value",
        ),
        (
            r"HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager",
            "SlideshowEnabled",
        ),
    }
)
TASKBAR_WIDGET_VALUE: Final = (
    r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced",
    "TaskbarDa",
)
PR_ONLY_WIDGET_VALUES: Final = frozenset(
    {
        (
            r"HKLM:\Software\Policies\Microsoft\Windows\Windows Feeds",
            "EnableFeeds",
        ),
        (
            r"HKLM:\Software\Microsoft\PolicyManager\default\NewsAndInterests"
            r"\AllowNewsAndInterests",
            "value",
        ),
    }
)


def _read_script(relative_path: str) -> str:
    return (DEBLOAT_DIR / relative_path).read_text(encoding="utf-8")


def _scheduled_tasks(script: str) -> tuple[str, ...]:
    return tuple(re.findall(r'^\s*"([^"]+)"[,]?\s*$', script, flags=re.MULTILINE))


def _registry_values(script: str) -> tuple[RegistryValue, ...]:
    return tuple(re.findall(r'@\{Path="([^"]+)"; Name="([^"]+)"(?:; Value=[^}]+)?\}', script))


def _casefold_values(values: Iterable[RegistryValue]) -> frozenset[RegistryValue]:
    return frozenset((path.casefold(), name.casefold()) for path, name in values)


def test_scheduled_tasks_apply_uses_safe_allowlist() -> None:
    # Given
    script = _read_script("scheduled_tasks.ps1")

    # When
    tasks = _scheduled_tasks(script)

    # Then
    assert tasks == SAFE_SCHEDULED_TASKS


def test_scheduled_tasks_undo_matches_apply() -> None:
    # Given
    apply_script = _read_script("scheduled_tasks.ps1")
    undo_script = _read_script("undo/scheduled_tasks.ps1")

    # When
    apply_tasks = _scheduled_tasks(apply_script)
    undo_tasks = _scheduled_tasks(undo_script)

    # Then
    assert undo_tasks == apply_tasks


def test_scheduled_tasks_apply_does_not_terminate_running_tasks() -> None:
    # Given
    script = _read_script("scheduled_tasks.ps1")

    # When
    normalized_script = script.casefold()

    # Then
    assert "schtasks /end" not in normalized_script


@pytest.mark.parametrize(
    ("script_path", "action"),
    [("scheduled_tasks.ps1", "Disable"), ("undo/scheduled_tasks.ps1", "Enable")],
)
def test_scheduled_tasks_skip_absent_but_surface_change_failures(
    script_path: str, action: str
) -> None:
    # Given: an apply or undo script with optional scheduled tasks.
    script = _read_script(script_path)

    # When: the guest's task query and native change status are handled.
    # Then: only confirmed absence is skipped; failed changes stay visible.
    assert "Get-ScheduledTask -ErrorAction Stop" in script
    assert "ContainsKey($task)" in script
    assert f"schtasks /Change /TN $task /{action} 2>&1" in script
    assert "if ($LASTEXITCODE -ne 0)" in script
    assert "throw" in script
    assert "2>$null" not in script


def test_ads_scripts_exclude_pr_only_registry_values() -> None:
    # Given
    apply_script = _read_script("ads.ps1")
    undo_script = _read_script("undo/ads.ps1")

    # When
    apply_values = _casefold_values(_registry_values(apply_script))
    undo_values = _casefold_values(_registry_values(undo_script))
    forbidden_values = _casefold_values(PR_ONLY_AD_VALUES)

    # Then
    assert apply_values
    assert undo_values
    assert apply_values.isdisjoint(forbidden_values)
    assert undo_values.isdisjoint(forbidden_values)


def test_ads_undo_matches_apply() -> None:
    # Given
    apply_script = _read_script("ads.ps1")
    undo_script = _read_script("undo/ads.ps1")

    # When
    apply_values = _registry_values(apply_script)
    undo_values = _registry_values(undo_script)

    # Then
    assert undo_values == apply_values


def test_widgets_scripts_exclude_pr_only_registry_values() -> None:
    # Given
    apply_script = _read_script("widgets.ps1")
    undo_script = _read_script("undo/widgets.ps1")

    # When
    apply_values = _casefold_values(_registry_values(apply_script))
    undo_values = _casefold_values(_registry_values(undo_script))
    forbidden_values = _casefold_values(PR_ONLY_WIDGET_VALUES)

    # Then
    assert apply_values
    assert undo_values
    assert apply_values.isdisjoint(forbidden_values)
    assert undo_values.isdisjoint(forbidden_values)


def test_widgets_undo_matches_apply_except_taskbar_icon() -> None:
    # Given
    apply_script = _read_script("widgets.ps1")
    undo_script = _read_script("undo/widgets.ps1")

    # When
    apply_values = set(_registry_values(apply_script))
    undo_values = set(_registry_values(undo_script))

    # Then
    assert undo_values == apply_values - {TASKBAR_WIDGET_VALUE}


def test_widgets_do_not_write_internal_policy_manager_defaults() -> None:
    # Given
    apply_script = _read_script("widgets.ps1")
    undo_script = _read_script("undo/widgets.ps1")

    # When
    scripts = (apply_script + undo_script).casefold()

    # Then
    assert "\\policymanager\\default\\" not in scripts
