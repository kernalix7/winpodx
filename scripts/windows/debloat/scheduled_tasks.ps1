# SPDX-License-Identifier: MIT
# winpodx debloat: disable noisy/unnecessary scheduled tasks.
#
# Scope is deliberately limited to pure telemetry / CEIP / feedback / ad tasks.
# We do NOT touch security (Defender), licensing/activation, certificate
# services, Windows Update repair (WaaSMedic/UpdateOrchestrator), language
# packs, Windows Hello, or general health/maintenance tasks -- disabling those
# risks breaking activation, updates, the IME, or hiding disk-failure warnings.
# PR #590 proposed a ~170-task blanket list; only the telemetry-safe subset
# below was adopted. #669 kept the SQM (CEIP) task and dropped the compat /
# maintenance / disk-health / network-diagnostic tasks it also proposed.
#
# Tasks targeted:
#   * Application Experience: Microsoft Compatibility Appraiser, ProgramDataUpdater,
#     ProgramInventoryUpdater, AitAgent (application-impact telemetry)
#   * Autochk\Proxy
#   * Customer Experience Improvement Program: Consolidator, KernelCeipTask, UsbCeip, BthSQM
#   * DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector (telemetry only;
#     the DiskDiagnosticResolver that surfaces SMART failure warnings is NOT touched)
#   * Feedback\Siuf: DmClient + DmClientOnScenarioDownload (Windows feedback telemetry)
#   * PI\Sqm-Tasks (Software Quality Metrics / CEIP telemetry)
#   * WindowsAI: Copilot + Insights data-collection telemetry
#   * Office telemetry agents (no-op when Office is absent)
#   * Maps\MapsToastTask + MapsUpdateTask
#   * RetailDemo\CleanupOfflineContent (deals with rentable demo content)
#   * Windows Error Reporting\QueueReporting
#
$tasks = @(
    "\Microsoft\Office\OfficeTelemetryAgentFallBack2016",
    "\Microsoft\Office\OfficeTelemetryAgentLogOn2016",
    "\Microsoft\Windows\Application Experience\AitAgent",
    "\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser",
    "\Microsoft\Windows\Application Experience\ProgramDataUpdater",
    "\Microsoft\Windows\Application Experience\ProgramInventoryUpdater",
    "\Microsoft\Windows\Autochk\Proxy",
    "\Microsoft\Windows\Customer Experience Improvement Program\BthSQM",
    "\Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
    "\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask",
    "\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip",
    "\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector",
    "\Microsoft\Windows\Feedback\Siuf\DmClient",
    "\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload",
    "\Microsoft\Windows\Maps\MapsToastTask",
    "\Microsoft\Windows\Maps\MapsUpdateTask",
    "\Microsoft\Windows\PI\Sqm-Tasks",
    "\Microsoft\Windows\RetailDemo\CleanupOfflineContent",
    "\Microsoft\Windows\Windows Error Reporting\QueueReporting",
    "\Microsoft\Windows\WindowsAI\Copilot\CopilotDataCollectionTask",
    "\Microsoft\Windows\WindowsAI\Insights\InsightsDataCollectionTask"
)

$existingTasks = @{}
foreach ($scheduledTask in Get-ScheduledTask -ErrorAction Stop) {
    $existingTasks["$($scheduledTask.TaskPath)$($scheduledTask.TaskName)"] = $true
}

foreach ($task in $tasks) {
    if (-not $existingTasks.ContainsKey($task)) {
        Write-Host "[scheduled_tasks] Skipping absent $task"
        continue
    }
    Write-Host "[scheduled_tasks] Disabling $task"
    $detail = schtasks /Change /TN $task /Disable 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "[scheduled_tasks] Failed to disable $task (rc=$LASTEXITCODE): $($detail.Trim())"
    }
}
