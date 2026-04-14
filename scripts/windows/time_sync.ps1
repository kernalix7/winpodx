# Synchronize Windows time with host
# Run on Windows startup to prevent clock drift after sleep/wake

$ntpServers = @("time.windows.com", "pool.ntp.org")

foreach ($server in $ntpServers) {
    try {
        w32tm /config /manualpeerlist:$server /syncfromflags:manual /reliable:YES /update
        w32tm /resync /force
        Write-Host "Time synced with $server"
        break
    } catch {
        Write-Host "Failed to sync with $server, trying next..."
    }
}
