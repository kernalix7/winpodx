# Synchronize Windows time with host
# Run on Windows startup to prevent clock drift after sleep/wake

$ntpServers = @("time.windows.com", "pool.ntp.org")
$maxAttempts = 3
$retryDelaySeconds = 5

$synced = $false
foreach ($server in $ntpServers) {
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            # w32tm writes errors to the success stream and returns a non-zero
            # exit code on failure — it does NOT throw. Catching exceptions is
            # kept only for truly absent-binary / access-denied cases. Success
            # must be detected via $LASTEXITCODE after each invocation.
            w32tm /config /manualpeerlist:$server /syncfromflags:manual /reliable:YES /update | Out-Null
            $configExit = $LASTEXITCODE
            w32tm /resync /force | Out-Null
            $resyncExit = $LASTEXITCODE

            if ($configExit -eq 0 -and $resyncExit -eq 0) {
                Write-Host "Time synced with $server"
                $synced = $true
                break
            }

            Write-Host ("Sync with {0} failed (config={1}, resync={2}), attempt {3}/{4}" -f `
                $server, $configExit, $resyncExit, $attempt, $maxAttempts)
        } catch {
            Write-Host "Sync with $server threw: $($_.Exception.Message) (attempt $attempt/$maxAttempts)"
        }

        if ($attempt -lt $maxAttempts) {
            Start-Sleep -Seconds $retryDelaySeconds
        }
    }

    if ($synced) { break }
    Write-Host "Giving up on $server, trying next server..."
}

if (-not $synced) {
    Write-Host "Time sync failed for all configured NTP servers."
    exit 1
}
