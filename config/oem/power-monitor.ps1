# winpodx guest-side power monitor.
#
# Subscribes to Win32_PowerManagementEvent and restarts TermService when
# the system resumes from sleep. Linux host suspend pauses the QEMU
# vCPUs but leaves Windows wall-clock running -- the guest sees a
# multi-minute time jump on host wake. TCP keepalive on the RDP socket
# fails, NAT mappings expire, and TermService stalls in a way that
# pre-this-script left the GUI / tray stuck on "starting" forever
# (kernalix7's recurring symptom).
#
# Power event types (Win32_PowerManagementEvent.EventType):
#   4  -- Suspend (entry)
#   7  -- Resume from Suspend
#   18 -- Resume from low-power state (modern standby)
#
# We restart TermService on 7 + 18 so the listening RDP socket is
# fresh on the now-stale TCP/keepalive state. The 5s pre-restart
# sleep gives the virtio NIC time to renegotiate its link before
# TermService rebinds.

$ErrorActionPreference = 'SilentlyContinue'
$logPath = 'C:\winpodx\power-monitor.log'

function Write-PowerLog {
    param([string]$line)
    try {
        $stamp = (Get-Date).ToUniversalTime().ToString('o')
        Add-Content -Path $logPath -Value "$stamp $line"
    } catch { }
}

Write-PowerLog "power-monitor: starting (pid $PID)"

Register-WmiEvent `
    -Query "SELECT * FROM Win32_PowerManagementEvent" `
    -SourceIdentifier 'WinpodxPowerEvent' `
    -Action {
        $evt = $Event.SourceEventArgs.NewEvent.EventType
        $stamp = (Get-Date).ToUniversalTime().ToString('o')
        $logPath = 'C:\winpodx\power-monitor.log'
        try { Add-Content -Path $logPath -Value "$stamp event=$evt" } catch { }
        if ($evt -eq 7 -or $evt -eq 18) {
            # Resume. Wait for NIC to stabilise then cycle TermService.
            Start-Sleep -Seconds 5
            try {
                Restart-Service -Force TermService -ErrorAction Stop
                try { Add-Content -Path $logPath -Value "$stamp termservice=restarted" } catch { }
            } catch {
                try { Add-Content -Path $logPath -Value "$stamp termservice=restart_failed $($_.Exception.Message)" } catch { }
            }
        }
    } | Out-Null

# Keep the host PowerShell process alive so the WMI subscription stays
# attached. Without this the script returns immediately, the runspace
# tears down, and the registered event never fires.
while ($true) {
    Start-Sleep -Seconds 3600
}
