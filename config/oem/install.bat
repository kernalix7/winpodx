@echo off
REM winpodx OEM post-install script
REM Runs automatically after Windows first boot (via dockur OEM mechanism)
REM Configures RDP, RemoteApp, firewall, and performance settings

echo [winpodx] Starting post-install configuration...

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

REM === Firewall: allow RDP ===
echo [winpodx] Configuring firewall...
netsh advfirewall firewall set rule group="Remote Desktop" new enable=yes 2>nul
netsh advfirewall firewall add rule name="RDP TCP" dir=in action=allow protocol=tcp localport=3389 2>nul
netsh advfirewall firewall add rule name="RDP UDP" dir=in action=allow protocol=udp localport=3389 2>nul

REM === Performance: disable animations for RDP ===
echo [winpodx] Optimizing for RDP...
reg add "HKCU\Control Panel\Desktop" /v DragFullWindows /t REG_SZ /d 0 /f
reg add "HKCU\Control Panel\Desktop" /v MenuShowDelay /t REG_SZ /d 0 /f
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f

REM === Pin Windows build (security updates OK, feature/build upgrades blocked) ===
REM Keeps termsrv.dll stable for RDPWrap — build upgrades come via winpodx releases only
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

REM === Disable Print Spooler (not needed for RemoteApp) ===
sc config Spooler start= disabled
net stop Spooler 2>nul

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

REM === RDPWrap: enable multi-session RDP ===
echo [winpodx] Installing RDPWrap for multi-session support...
powershell -ExecutionPolicy Bypass -File "C:\OEM\setup_rdpwrap.ps1"

REM === Mark setup complete ===
echo done > C:\OEM\winpodx_setup_done.txt

echo [winpodx] Post-install configuration complete!
echo [winpodx] RDP is now enabled. You can connect with FreeRDP.
