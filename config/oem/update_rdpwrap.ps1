# winpodx — RDPWrap INI updater (manual trigger only)
# Re-runs RDPWrapOffsetFinder against current termsrv.dll to regenerate offsets.
# Contacts Microsoft symbol server only. No third-party downloads.

$ErrorActionPreference = "SilentlyContinue"

$RDPWrapDir = "C:\Program Files\RDP Wrapper"
$OEMDir = "C:\OEM\rdpwrap"

if (-not (Test-Path "$RDPWrapDir\rdpwrap.dll")) {
    Write-Host "[winpodx] RDPWrap not installed"
    exit 1
}

if (-not (Test-Path "$OEMDir\RDPWrapOffsetFinder.exe")) {
    Write-Host "[winpodx] RDPWrapOffsetFinder not found in $OEMDir"
    exit 1
}

Write-Host "[winpodx] Regenerating rdpwrap.ini from current termsrv.dll..."

$env:_NT_SYMBOL_PATH = "srv*C:\OEM\rdpwrap\symbols*https://msdl.microsoft.com/download/symbols"

$proc = Start-Process -FilePath "$OEMDir\RDPWrapOffsetFinder.exe" `
    -WorkingDirectory $OEMDir `
    -ArgumentList "C:\Windows\System32\termsrv.dll" `
    -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput "$OEMDir\offsets_output.txt" `
    -RedirectStandardError "$OEMDir\offsets_error.txt"

if (($proc.ExitCode -eq 0) -and (Test-Path "$OEMDir\offsets_output.txt")) {
    $offsets = Get-Content "$OEMDir\offsets_output.txt" -Raw
    if ($offsets.Length -gt 0) {
        Set-Content "$RDPWrapDir\rdpwrap.ini" $offsets
        Write-Host "[winpodx] rdpwrap.ini regenerated successfully"
    } else {
        Write-Host "[winpodx] ERROR: OffsetFinder produced empty output"
        exit 1
    }
} else {
    Write-Host "[winpodx] ERROR: OffsetFinder failed (exit $($proc.ExitCode))"
    if (Test-Path "$OEMDir\offsets_error.txt") {
        Get-Content "$OEMDir\offsets_error.txt" | Write-Host
    }
    exit 1
}

Remove-Item "$OEMDir\offsets_output.txt" -ErrorAction SilentlyContinue
Remove-Item "$OEMDir\offsets_error.txt" -ErrorAction SilentlyContinue

Stop-Service TermService -Force
Start-Sleep -Seconds 2
Start-Service TermService
Write-Host "[winpodx] Terminal Services restarted"
