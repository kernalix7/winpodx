# SPDX-License-Identifier: MIT
# winpodx debloat UNDO: re-enable the scheduled tasks disabled by apply.

$tasks = @(
    "\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser",
    "\Microsoft\Windows\Application Experience\ProgramDataUpdater",
    "\Microsoft\Windows\Application Experience\ProgramInventoryUpdater",
    "\Microsoft\Windows\Application Experience\AitAgent",
    "\Microsoft\Windows\Autochk\Proxy",
    "\Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
    "\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask",
    "\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip",
    "\Microsoft\Windows\Customer Experience Improvement Program\BthSQM",
    "\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector",
    "\Microsoft\Windows\Feedback\Siuf\DmClient",
    "\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload",
    "\Microsoft\Windows\WindowsAI\Copilot\CopilotDataCollectionTask",
    "\Microsoft\Windows\WindowsAI\Insights\InsightsDataCollectionTask",
    "\Microsoft\Office\OfficeTelemetryAgentLogOn2016",
    "\Microsoft\Office\OfficeTelemetryAgentFallBack2016",
    "\Microsoft\Windows\Maps\MapsToastTask",
    "\Microsoft\Windows\Maps\MapsUpdateTask",
    "\Microsoft\Windows\RetailDemo\CleanupOfflineContent",
    "\Microsoft\Windows\Windows Error Reporting\QueueReporting"
)

foreach ($task in $tasks) {
    Write-Host "[scheduled_tasks] Re-enabling $task"
    schtasks /Change /TN $task /Enable 2>$null | Out-Null
}
