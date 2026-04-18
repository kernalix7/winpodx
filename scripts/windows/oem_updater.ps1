# winpodx OEM updater
#
# Re-runs install.bat when a newer version is shipped.  Triggered on user
# login via a Scheduled Task registered by install.bat itself.  Designed as
# a fast no-op when nothing new is pending.
#
# Version contract: install.bat contains a line of the form
#   set WINPODX_OEM_VERSION=<N>
# where <N> is a monotonically increasing integer.  We grep the line out of
# the shipped install.bat, compare to the integer stored locally in
#   C:\winpodx\oem_version.txt
# and if the shipped version is higher, re-run install.bat end-to-end.
# install.bat must stay idempotent for this to be safe.

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
        'C:\winpodx-scripts\oem\install.bat',
        '\\tsclient\home\.local\share\winpodx\config\oem\install.bat',
        '\\tsclient\home\.local\pipx\venvs\winpodx\share\winpodx\config\oem\install.bat',
        '\\tsclient\home\winpodx\config\oem\install.bat',
        '\\tsclient\home\.local\bin\winpodx-app\config\oem\install.bat'
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
