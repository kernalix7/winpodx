# winpodx — RDPWrap setup script
# Installs RDPWrap from local OEM directory (built from source via CI).
# Generates rdpwrap.ini using RDPWrapOffsetFinder (symbol-based, from MS symbol server).
# No third-party downloads — only Microsoft official symbol server is contacted.
# Runs inside the Windows container only.

$ErrorActionPreference = "Stop"

$RDPWrapDir = "C:\Program Files\RDP Wrapper"
$OEMDir = "C:\OEM\rdpwrap"

Write-Host "[winpodx] Setting up RDPWrap for multi-session RDP..."

# Check bundled rdpwrap.dll exists
if (-not (Test-Path "$OEMDir\rdpwrap.dll")) {
    Write-Host "[winpodx] rdpwrap.dll not found in $OEMDir"
    Write-Host "[winpodx] Run 'Build RDPWrap' CI workflow first"
    exit 1
}

# Skip if already installed and INI exists
if ((Test-Path "$RDPWrapDir\rdpwrap.dll") -and (Test-Path "$RDPWrapDir\rdpwrap.ini")) {
    Write-Host "[winpodx] RDPWrap already installed with INI"
    exit 0
}

# Install RDPWrap manually (replaces RDPWInst.exe -i)
# 1. Create target directory
# 2. Copy rdpwrap.dll
# 3. Redirect TermService ServiceDll to load rdpwrap.dll instead of termsrv.dll
if (-not (Test-Path "$RDPWrapDir\rdpwrap.dll")) {
    Write-Host "[winpodx] Installing RDPWrap from local bundle..."

    # Stop TermService before modifying
    Stop-Service TermService -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    # Create target directory
    New-Item -ItemType Directory -Force -Path $RDPWrapDir | Out-Null

    # Copy rdpwrap.dll
    Copy-Item "$OEMDir\rdpwrap.dll" "$RDPWrapDir\rdpwrap.dll" -Force
    Write-Host "[winpodx] Copied rdpwrap.dll to $RDPWrapDir"

    # Backup original ServiceDll path and redirect to rdpwrap.dll
    $regPath = "HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters"
    $origDll = (Get-ItemProperty -Path $regPath -Name ServiceDll).ServiceDll
    Write-Host "[winpodx] Original ServiceDll: $origDll"

    # Save original path so rdpwrap.dll can find the real termsrv.dll
    Set-ItemProperty -Path $regPath -Name "ServiceDll" -Value "$RDPWrapDir\rdpwrap.dll" -Type ExpandString
    Write-Host "[winpodx] ServiceDll redirected to rdpwrap.dll"
}

# Generate INI using RDPWrapOffsetFinder (symbol-based)
if (Test-Path "$OEMDir\RDPWrapOffsetFinder.exe") {
    Write-Host "[winpodx] Running OffsetFinder to generate INI for current build..."

    # OffsetFinder needs these files in its working directory:
    # - dbghelp.dll, symsrv.dll (from Windows SDK, bundled via CI)
    # - symsrv.yes (auto-accept symbol server EULA)
    # - Zydis.dll (disassembler, bundled via CI)
    $env:_NT_SYMBOL_PATH = "srv*C:\OEM\rdpwrap\symbols*https://msdl.microsoft.com/download/symbols"

    $finderProc = Start-Process -FilePath "$OEMDir\RDPWrapOffsetFinder.exe" `
        -WorkingDirectory $OEMDir `
        -ArgumentList "C:\Windows\System32\termsrv.dll" `
        -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput "$OEMDir\offsets_output.txt" `
        -RedirectStandardError "$OEMDir\offsets_error.txt"

    if (($finderProc.ExitCode -eq 0) -and (Test-Path "$OEMDir\offsets_output.txt")) {
        $offsets = Get-Content "$OEMDir\offsets_output.txt" -Raw
        if ($offsets.Length -gt 0) {
            # Apply generated offsets to INI
            if (Test-Path "$RDPWrapDir\rdpwrap.ini") {
                Add-Content "$RDPWrapDir\rdpwrap.ini" "`n$offsets"
            } else {
                Set-Content "$RDPWrapDir\rdpwrap.ini" $offsets
            }
            Write-Host "[winpodx] INI generated from OffsetFinder (symbol-based)"
        } else {
            Write-Host "[winpodx] WARNING: OffsetFinder produced empty output"
        }
    } else {
        Write-Host "[winpodx] WARNING: OffsetFinder failed (exit $($finderProc.ExitCode))"
        if (Test-Path "$OEMDir\offsets_error.txt") {
            Get-Content "$OEMDir\offsets_error.txt" | Write-Host
        }
    }

    # Cleanup temp files
    Remove-Item "$OEMDir\offsets_output.txt" -ErrorAction SilentlyContinue
    Remove-Item "$OEMDir\offsets_error.txt" -ErrorAction SilentlyContinue
} else {
    Write-Host "[winpodx] WARNING: RDPWrapOffsetFinder not found, skipping INI generation"
    # Fallback: use bundled INI if available
    if (Test-Path "$OEMDir\rdpwrap.ini") {
        Copy-Item "$OEMDir\rdpwrap.ini" "$RDPWrapDir\rdpwrap.ini" -Force
        Write-Host "[winpodx] Fallback: bundled INI applied"
    }
}

# Restart Terminal Services to apply
Write-Host "[winpodx] Restarting Terminal Services..."
Stop-Service TermService -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Service TermService -ErrorAction SilentlyContinue

Write-Host "[winpodx] RDPWrap setup complete — multi-session RDP enabled"
