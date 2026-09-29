# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: re-enable the scheduled tasks disabled by apply.

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
    Write-Host "[scheduled_tasks] Re-enabling $task"
    $detail = schtasks /Change /TN $task /Enable 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) {
        throw "[scheduled_tasks] Failed to enable $task (rc=$LASTEXITCODE): $($detail.Trim())"
    }
}
