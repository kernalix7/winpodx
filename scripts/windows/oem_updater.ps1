# Re-runs install.bat when its WINPODX_OEM_VERSION exceeds the applied
# value in C:\winpodx\oem_version.txt. Guest-local paths only: triggers
# (Scheduled Task + `podman exec`) run outside any RDP session, so
# \\tsclient\* is unreachable. install.bat must stay idempotent.

$ErrorActionPreference = 'Stop'

$state = 'C:\winpodx'
$versionFile = Join-Path $state 'oem_version.txt'
$logFile = Join-Path $state 'oem_updater.log'
$localBat = Join-Path $state 'install_current.bat'
if (-not (Test-Path -LiteralPath $state)) {
    New-Item -ItemType Directory -Path $state | Out-Null
}

function Write-Log([string]$msg) {
    $ts = Get-Date -Format 'yyyy-MM-ddTHH:mm:ssK'
    Add-Content -LiteralPath $logFile -Value "$ts $msg" -Encoding UTF8
}

function Get-BatVersion([string]$path) {
    try {
        $line = Select-String -LiteralPath $path -Pattern '^\s*set\s+WINPODX_OEM_VERSION=(\d+)\s*$' -CaseSensitive:$false |
            Select-Object -First 1
        if ($line -and $line.Matches[0].Groups[1].Value) {
            return [int]$line.Matches[0].Groups[1].Value
        }
    } catch {}
    return $null
}

try {
    $candidates = @(
        'C:\winpodx\install_shipped.bat',
        'C:\winpodx-scripts\oem\install.bat',
        'C:\OEM\install.bat'
    )
    $shipped = $null
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { $shipped = $c; break }
    }
    if (-not $shipped) {
        exit 0
    }

    $shippedVer = Get-BatVersion $shipped
    if ($null -eq $shippedVer) {
        Write-Log "shipped install.bat has no WINPODX_OEM_VERSION marker: $shipped"
        exit 0
    }

    $applied = 0
    if (Test-Path -LiteralPath $versionFile) {
        $raw = (Get-Content -LiteralPath $versionFile -Raw).Trim()
        if ($raw -match '^\d+$') { $applied = [int]$raw }
    }

    if ($shippedVer -le $applied) {
        exit 0
    }

    Write-Log "updating $applied -> $shippedVer from $shipped"
    # Copy aside so a concurrent winpodx refresh can't swap the file mid-run.
    Copy-Item -LiteralPath $shipped -Destination $localBat -Force

    $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$localBat`"" `
        -WorkingDirectory $state -Wait -PassThru -WindowStyle Hidden
    if ($proc.ExitCode -ne 0) {
        Write-Log "install.bat exited $($proc.ExitCode); not bumping version"
        exit 1
    }

    Set-Content -LiteralPath $versionFile -Value $shippedVer -Encoding ASCII
    Write-Log "applied version $shippedVer"
} catch {
    Write-Log "updater error: $_"
    exit 1
}
