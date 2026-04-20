@echo off
REM First-boot OEM setup for winpodx Windows guest. Bump WINPODX_OEM_VERSION to force re-run on existing VMs; every action must stay idempotent.

set WINPODX_OEM_VERSION=3

echo [winpodx] Starting post-install configuration (version %WINPODX_OEM_VERSION%)...

echo [winpodx] Setting DNS...
netsh interface ip set dns "Ethernet" static 1.1.1.1
netsh interface ip add dns "Ethernet" 1.0.0.1 index=2

echo [winpodx] Enabling Remote Desktop...
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fSingleSessionPerUser /t REG_DWORD /d 0 /f

REM NLA off for automated FreeRDP; SecurityLayer=2 keeps TLS on the RDP channel.
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v UserAuthentication /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v SecurityLayer /t REG_DWORD /d 2 /f

echo [winpodx] Enabling RemoteApp...
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList" /v fDisabledAllowList /t REG_DWORD /d 1 /f

REM Without fInheritInitialProgram, Windows ignores /shell: and /app: from the RDP client.
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v fInheritInitialProgram /t REG_DWORD /d 1 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxInstanceCount /t REG_DWORD /d 10 /f

echo [winpodx] Configuring firewall...
REM Delete-then-add keeps the rule idempotent; plain add creates duplicates on re-run.
netsh advfirewall firewall set rule group="Remote Desktop" new enable=yes 2>nul
netsh advfirewall firewall delete rule name="RDP TCP" >nul 2>&1
netsh advfirewall firewall delete rule name="RDP UDP" >nul 2>&1
netsh advfirewall firewall add rule name="RDP TCP" dir=in action=allow protocol=tcp localport=3389 2>nul
netsh advfirewall firewall add rule name="RDP UDP" dir=in action=allow protocol=udp localport=3389 2>nul

echo [winpodx] Optimizing for RDP...
reg add "HKCU\Control Panel\Desktop" /v DragFullWindows /t REG_SZ /d 0 /f
reg add "HKCU\Control Panel\Desktop" /v MenuShowDelay /t REG_SZ /d 0 /f
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f

REM Pin Windows build so termsrv.dll stays stable; feature/build upgrades come via winpodx releases only.
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v NoAutoRebootWithLoggedOnUsers /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v TargetReleaseVersion /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v ProductVersion /t REG_SZ /d "Windows 11" /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v TargetReleaseVersionInfo /t REG_SZ /d "25H2" /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v DeferFeatureUpdates /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v DeferFeatureUpdatesPeriodInDays /t REG_DWORD /d 365 /f

tzutil /s "UTC"

reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search" /v AllowCortana /t REG_DWORD /d 0 /f

reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection" /v AllowTelemetry /t REG_DWORD /d 0 /f

sc config WSearch start= disabled
net stop WSearch 2>nul

sc config SysMain start= disabled
net stop SysMain 2>nul

powercfg /h off

REM Print Spooler stays enabled for RDP printer redirection.

sc config WerSvc start= disabled
net stop WerSvc 2>nul

sc config DiagTrack start= disabled
net stop DiagTrack 2>nul

powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize" /v StartupDelayInMSec /t REG_DWORD /d 0 /f

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications" /v GlobalUserDisabled /t REG_DWORD /d 1 /f

echo [winpodx] Home folder is available at \\tsclient\home via RDP drive redirection

REM dockur's base image ships a "Shared" desktop item pointing to \\host.lan\Data (SMB) that we don't use; replace with Home/USB shortcuts to the RDP redirections.
echo [winpodx] Creating desktop shortcuts to tsclient shares...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); foreach($n in 'Shared','Shared.lnk'){ $p=Join-Path $d $n; if(Test-Path -LiteralPath $p){ Remove-Item -Force -Recurse -LiteralPath $p -ErrorAction SilentlyContinue } }; $s=New-Object -ComObject WScript.Shell; foreach($x in @(@('Home','\\tsclient\home'), @('USB','\\tsclient\media'))){ $l=$s.CreateShortcut((Join-Path $d ($x[0]+'.lnk'))); $l.TargetPath=$x[1]; $l.Save() }"

echo [winpodx] Setting up USB media auto-mapping...
mkdir C:\winpodx 2>nul

REM Prefer compose-mounted C:\winpodx-scripts; fall back to well-known install locations over \\tsclient\home. See config/oem/README.md.
set "WINPODX_SRC_OK="
if exist "C:\winpodx-scripts\media_monitor.ps1" (
    copy /Y "C:\winpodx-scripts\media_monitor.ps1" C:\winpodx\media_monitor.ps1 >nul 2>&1
    set "WINPODX_SRC_OK=1"
)
if not defined WINPODX_SRC_OK (
    for %%P in (
        "\\tsclient\home\.local\share\winpodx\scripts\windows\media_monitor.ps1"
        "\\tsclient\home\.local\pipx\venvs\winpodx\share\winpodx\scripts\windows\media_monitor.ps1"
        "\\tsclient\home\winpodx\scripts\windows\media_monitor.ps1"
        "\\tsclient\home\.local\bin\winpodx-app\scripts\windows\media_monitor.ps1"
    ) do (
        if not defined WINPODX_SRC_OK if exist %%P (
            copy /Y %%P C:\winpodx\media_monitor.ps1 >nul 2>&1
            if not errorlevel 1 set "WINPODX_SRC_OK=1"
        )
    )
)
if not defined WINPODX_SRC_OK (
    echo [winpodx] WARNING: media_monitor.ps1 not found in any known location.
    echo [winpodx] Mount the scripts dir at C:\winpodx-scripts via compose, or
    echo [winpodx] place media_monitor.ps1 under ~/.local/share/winpodx/scripts/windows/.
)
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WinpodxMedia /t REG_SZ /d "powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\winpodx\media_monitor.ps1" /f

echo [winpodx] Installing OEM updater...
set "WINPODX_UPD_OK="
if exist "C:\winpodx-scripts\oem_updater.ps1" (
    copy /Y "C:\winpodx-scripts\oem_updater.ps1" C:\winpodx\oem_updater.ps1 >nul 2>&1
    set "WINPODX_UPD_OK=1"
)
if not defined WINPODX_UPD_OK (
    for %%P in (
        "\\tsclient\home\.local\share\winpodx\scripts\windows\oem_updater.ps1"
        "\\tsclient\home\.local\pipx\venvs\winpodx\share\winpodx\scripts\windows\oem_updater.ps1"
        "\\tsclient\home\winpodx\scripts\windows\oem_updater.ps1"
        "\\tsclient\home\.local\bin\winpodx-app\scripts\windows\oem_updater.ps1"
    ) do (
        if not defined WINPODX_UPD_OK if exist %%P (
            copy /Y %%P C:\winpodx\oem_updater.ps1 >nul 2>&1
            if not errorlevel 1 set "WINPODX_UPD_OK=1"
        )
    )
)
if not defined WINPODX_UPD_OK (
    echo [winpodx] WARNING: oem_updater.ps1 not found in any known location.
)
REM Register updater with AtLogOn + AtStartup triggers as SYSTEM/Highest so HKLM writes succeed on re-run. Delete any legacy task first.
schtasks /delete /tn "WinpodxOEMUpdate" /f >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\winpodx\oem_updater.ps1'; $t=@((New-ScheduledTaskTrigger -AtLogOn),(New-ScheduledTaskTrigger -AtStartup)); $p=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest; Register-ScheduledTask -TaskName 'WinpodxOEMUpdate' -Action $a -Trigger $t -Principal $p -Force | Out-Null" >nul 2>&1

REM Parenthesized echo strips the trailing space that `echo X > file` leaves behind.
(echo %WINPODX_OEM_VERSION%)>C:\winpodx\oem_version.txt

REM Multi-session RDP (RDPWrap or equivalent) is tracked as a separate project.

REM Sentinel lives under C:\winpodx so it survives past the one-shot C:\OEM stage.
(echo done)>C:\winpodx\setup_done.txt

echo [winpodx] Post-install configuration complete (version %WINPODX_OEM_VERSION%)!
echo [winpodx] RDP is now enabled. You can connect with FreeRDP.
