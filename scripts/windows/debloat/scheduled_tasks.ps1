# SPDX-License-Identifier: MIT
# winpodx debloat: disable noisy/unused scheduled tasks (sysguides list).
#
# Tasks targeted:
#   * Application Experience\Microsoft Compatibility Appraiser
#   * Autochk\Proxy
#   * Customer Experience Improvement Program\Consolidator + KernelCeipTask + UsbCeip
#   * DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector
#   * Maps\MapsToastTask + MapsUpdateTask
#   * RetailDemo\CleanupOfflineContent (deals with rentable demo content)
#   * Windows Error Reporting\QueueReporting

$tasks = @(
    "\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser",
    "\Microsoft\Windows\Application Experience\ProgramDataUpdater",
    "\Microsoft\Windows\Autochk\Proxy",
    "\Microsoft\Windows\Customer Experience Improvement Program\Consolidator",
    "\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask",
    "\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip",
    "\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector",
    "\Microsoft\Windows\Maps\MapsToastTask",
    "\Microsoft\Windows\Maps\MapsUpdateTask",
    "\Microsoft\Windows\RetailDemo\CleanupOfflineContent",
    "\Microsoft\Windows\Windows Error Reporting\QueueReporting"
)

foreach ($task in $tasks) {
    Write-Host "[scheduled_tasks] Disabling $task"
    schtasks /Change /TN $task /Disable 2>$null | Out-Null
}
