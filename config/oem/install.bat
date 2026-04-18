@echo off
REM winpodx OEM post-install script
REM Runs automatically after Windows first boot (via dockur OEM mechanism)
REM AND re-runs on subsequent logins whenever WINPODX_OEM_VERSION below is
REM higher than C:\winpodx\oem_version.txt — see scripts/windows/oem_updater.ps1
REM
REM Bump WINPODX_OEM_VERSION whenever this script needs to re-apply on
REM existing VMs (new reg keys, new shortcuts, new firewall rules, etc.).
REM Every action below MUST be idempotent.

set WINPODX_OEM_VERSION=3

echo [winpodx] Starting post-install configuration (version %WINPODX_OEM_VERSION%)...

REM === Set DNS (Cloudflare) - slirp network has no DNS by default ===
echo [winpodx] Setting DNS...
netsh interface ip set dns "Ethernet" static 1.1.1.1
netsh interface ip add dns "Ethernet" 1.0.0.1 index=2

REM === Enable Remote Desktop ===
echo [winpodx] Enabling Remote Desktop...
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fSingleSessionPerUser /t REG_DWORD /d 0 /f

REM === NLA off for automated FreeRDP connections (RDP bound to 127.0.0.1 only) ===
REM SecurityLayer=2 enforces TLS encryption on the RDP channel
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v UserAuthentication /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v SecurityLayer /t REG_DWORD /d 2 /f

REM === Enable RemoteApp (allow any app to run as RemoteApp) ===
echo [winpodx] Enabling RemoteApp...
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList" /v fDisabledAllowList /t REG_DWORD /d 1 /f

REM === Allow client-specified initial program (alternate shell / RemoteApp) ===
REM Without this, Windows ignores /shell: and /app: parameters from the RDP client
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v fInheritInitialProgram /t REG_DWORD /d 1 /f
REM MaxInstanceCount > 1 allows concurrent sessions to use different initial programs
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxInstanceCount /t REG_DWORD /d 10 /f

REM === Firewall: allow RDP ===
REM Delete-then-add keeps the rule idempotent — plain add creates duplicates on re-run.
echo [winpodx] Configuring firewall...
netsh advfirewall firewall set rule group="Remote Desktop" new enable=yes 2>nul
netsh advfirewall firewall delete rule name="RDP TCP" >nul 2>&1
netsh advfirewall firewall delete rule name="RDP UDP" >nul 2>&1
netsh advfirewall firewall add rule name="RDP TCP" dir=in action=allow protocol=tcp localport=3389 2>nul
netsh advfirewall firewall add rule name="RDP UDP" dir=in action=allow protocol=udp localport=3389 2>nul

REM === Performance: disable animations for RDP ===
echo [winpodx] Optimizing for RDP...
reg add "HKCU\Control Panel\Desktop" /v DragFullWindows /t REG_SZ /d 0 /f
reg add "HKCU\Control Panel\Desktop" /v MenuShowDelay /t REG_SZ /d 0 /f
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f

REM === Pin Windows build (security updates OK, feature/build upgrades blocked) ===
REM Keeps termsrv.dll stable - build upgrades come via winpodx releases only
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v NoAutoRebootWithLoggedOnUsers /t REG_DWORD /d 1 /f
REM Block feature updates and build upgrades (keeps current build number)
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v TargetReleaseVersion /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v ProductVersion /t REG_SZ /d "Windows 11" /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v TargetReleaseVersionInfo /t REG_SZ /d "25H2" /f
REM Defer feature updates 365 days (max), security updates install normally
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v DeferFeatureUpdates /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v DeferFeatureUpdatesPeriodInDays /t REG_DWORD /d 365 /f

REM === Set timezone to UTC ===
tzutil /s "UTC"

REM === Disable Cortana ===
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search" /v AllowCortana /t REG_DWORD /d 0 /f

REM === Disable telemetry ===
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection" /v AllowTelemetry /t REG_DWORD /d 0 /f

REM === Disable search indexing (saves CPU in VM) ===
sc config WSearch start= disabled
net stop WSearch 2>nul

REM === Disable SysMain/Superfetch (saves disk I/O) ===
sc config SysMain start= disabled
net stop SysMain 2>nul

REM === Disable hibernation ===
powercfg /h off

REM === Print Spooler: keep enabled for RDP printer redirection ===
REM sc config Spooler start= disabled

REM === Disable Windows Error Reporting ===
sc config WerSvc start= disabled
net stop WerSvc 2>nul

REM === Disable Diagnostic services ===
sc config DiagTrack start= disabled
net stop DiagTrack 2>nul

REM === Set High Performance power plan ===
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c

REM === Disable startup delay ===
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize" /v StartupDelayInMSec /t REG_DWORD /d 0 /f

REM === Disable background apps ===
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications" /v GlobalUserDisabled /t REG_DWORD /d 1 /f

REM === Map home folder ===
echo [winpodx] Home folder is available at \\tsclient\home via RDP drive redirection

REM === Replace broken dockur "Shared" desktop link with \\tsclient\* shortcuts ===
REM dockur's base image ships a "Shared" desktop item pointing to \\host.lan\Data (SMB),
REM which we don't use. Remove it and create Home/USB shortcuts to the RDP redirections.
echo [winpodx] Creating desktop shortcuts to tsclient shares...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); foreach($n in 'Shared','Shared.lnk'){ $p=Join-Path $d $n; if(Test-Path -LiteralPath $p){ Remove-Item -Force -Recurse -LiteralPath $p -ErrorAction SilentlyContinue } }; $s=New-Object -ComObject WScript.Shell; foreach($x in @(@('Home','\\tsclient\home'), @('USB','\\tsclient\media'))){ $l=$s.CreateShortcut((Join-Path $d ($x[0]+'.lnk'))); $l.TargetPath=$x[1]; $l.Save() }"

REM === USB media auto-mapping (FileSystemWatcher, event-driven) ===
REM Watches \\tsclient\media for USB mount/unmount and maps drive letters automatically
REM No polling — reacts only when OS sends a file change event
echo [winpodx] Setting up USB media auto-mapping...
mkdir C:\winpodx 2>nul

REM Preferred: compose mounts the scripts dir at C:\winpodx-scripts (read-only)
REM Fallback paths search well-known install locations over \\tsclient\home.
REM Search order covers: compose-mounted dir, pip wheel (sys.prefix/share),
REM editable/source checkout, user-local install, and legacy path.
REM See config/oem/README.md for the compose mount recipe.
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

REM === OEM updater (re-runs install.bat on winpodx version bump) ===
REM Deploys oem_updater.ps1 and registers a logon scheduled task at HIGHEST
REM privileges so HKLM writes succeed on re-run. The updater greps
REM WINPODX_OEM_VERSION out of the shipped install.bat and replays this
REM script end-to-end when the number increases.
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
REM Register the updater with TWO triggers (AtLogOn + AtStartup) so the check
REM fires on both fresh logon and cold boot. Runs as SYSTEM with highest
REM privileges — required for the HKLM writes inside install.bat. Pause/unpause
REM cycles don't fire either trigger; those are handled by winpodx itself
REM pushing a podman-exec call from the Linux side on version bump.
REM Delete any legacy /sc ONLOGON task from earlier OEM versions first.
schtasks /delete /tn "WinpodxOEMUpdate" /f >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File C:\winpodx\oem_updater.ps1'; $t=@((New-ScheduledTaskTrigger -AtLogOn),(New-ScheduledTaskTrigger -AtStartup)); $p=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest; Register-ScheduledTask -TaskName 'WinpodxOEMUpdate' -Action $a -Trigger $t -Principal $p -Force | Out-Null" >nul 2>&1

REM === Record applied OEM version ===
REM Parenthesized echo strips the trailing space that `echo X > file` leaves behind.
(echo %WINPODX_OEM_VERSION%)>C:\winpodx\oem_version.txt

REM === Multi-session RDP (TBD) ===
REM Multi-session support (RDPWrap or equivalent) is planned as a separate project.
REM Currently, only one RemoteApp/RDP session per user is supported.
REM See: https://github.com/kernalix7/winpodx

REM === Mark setup complete ===
REM Stored under C:\winpodx so the sentinel survives past the one-shot C:\OEM stage.
(echo done)>C:\winpodx\setup_done.txt

echo [winpodx] Post-install configuration complete (version %WINPODX_OEM_VERSION%)!
echo [winpodx] RDP is now enabled. You can connect with FreeRDP.
