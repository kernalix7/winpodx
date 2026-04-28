@echo off
REM First-boot OEM setup for winpodx Windows guest. Runs once during dockur's unattended install. Every action must stay idempotent — there is no guest-side re-run channel in 0.1.6 (push/exec bridge planned for a later release).

set WINPODX_OEM_VERSION=8

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
REM v0.2.1: cap bumped 10 -> 50. cfg.pod.max_sessions still controls the
REM actual desired count via _apply_max_sessions; this is just the
REM ceiling so the cfg value isn't silently clamped at OEM time.
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxInstanceCount /t REG_DWORD /d 50 /f

REM Bug B (v0.1.9 / OEM v7): host suspend / long idle commonly leaves Windows
REM with TermService stalled or the virtual NIC in power-save, breaking RDP
REM while VNC keeps working. Two preventive measures so the host-side
REM recover_rdp_if_needed() helper has less to do.
echo [winpodx] Disabling NIC power-save...
powershell -NoProfile -Command "Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {$_.Status -ne 'Disabled'} | Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false -ErrorAction SilentlyContinue" >nul 2>&1

echo [winpodx] Configuring TermService recovery actions...
REM 3 attempts at 5s spacing, 24h reset window — Windows itself recovers
REM TermService crashes without needing host intervention.
sc.exe failure TermService reset= 86400 actions= restart/5000/restart/5000/restart/5000 >nul 2>&1

REM v0.1.9.1: RDP session timeout + keep-alive. v0.2.1 adjusts the
REM disconnection-time semantics:
REM   * MaxIdleTime          0 = no idle timeout (active sessions never auto-disconnect)
REM   * MaxConnectionTime    0 = no max session duration
REM   * MaxDisconnectionTime 30000 ms = 30 sec — disconnected sessions
REM     auto-LOGOFF after 30 s. Previously this was 0 ("never logoff"),
REM     which left zombie disconnected sessions accumulating every time
REM     the user closed a FreeRDP window. The next launch then triggered
REM     "Select a session to reconnect to" dialog because Windows saw
REM     the user had N old disconnected sessions. rdprrap allows
REM     concurrent sessions but doesn't suppress that prompt — only
REM     auto-logoff does.
REM Both the machine policy and the RDP-Tcp WinStation keys are set;
REM whichever Windows consults first finds the saner default.
echo [winpodx] Disabling RDP session timeouts + enabling keep-alive...
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v MaxIdleTime /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v MaxDisconnectionTime /t REG_DWORD /d 30000 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v MaxConnectionTime /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v KeepAliveEnable /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v KeepAliveInterval /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxIdleTime /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxDisconnectionTime /t REG_DWORD /d 30000 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxConnectionTime /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v KeepAliveTimeout /t REG_DWORD /d 1 /f >nul 2>&1

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

REM Clean up any legacy OEM updater task / file from pre-0.1.6 installs.
schtasks /delete /tn "WinpodxOEMUpdate" /f >nul 2>&1
if exist C:\winpodx\oem_updater.ps1 del /F /Q C:\winpodx\oem_updater.ps1 >nul 2>&1

REM Parenthesized echo strips the trailing space that `echo X > file` leaves behind.
(echo %WINPODX_OEM_VERSION%)>C:\winpodx\oem_version.txt

echo [winpodx] Installing multi-session RDP (rdprrap) — offline bundle...
REM Bundle ships under config/oem/ and is staged into C:\OEM\ by dockur's unattended install.
REM The pin file (version / filename / sha256) lives next to the zip. No network
REM access is required — everything is copied straight from the staged folder.
set "RDPRRAP_PIN="
if exist "C:\OEM\rdprrap_version.txt" set "RDPRRAP_PIN=C:\OEM\rdprrap_version.txt"

set "RDPRRAP_VERSION="
set "RDPRRAP_FILENAME="
set "RDPRRAP_SHA256="
if defined RDPRRAP_PIN (
    for /f "usebackq tokens=1,* delims==" %%A in ("%RDPRRAP_PIN%") do (
        if /I "%%A"=="version"  set "RDPRRAP_VERSION=%%B"
        if /I "%%A"=="filename" set "RDPRRAP_FILENAME=%%B"
        if /I "%%A"=="sha256"   set "RDPRRAP_SHA256=%%B"
    )
)

if not defined RDPRRAP_VERSION  goto :rdprrap_skip
if not defined RDPRRAP_FILENAME goto :rdprrap_skip
if not defined RDPRRAP_SHA256   goto :rdprrap_skip

set "RDPRRAP_DIR=C:\winpodx\rdprrap"
set "RDPRRAP_INSTALLED=%RDPRRAP_DIR%\.installed_version"
set "RDPRRAP_CUR="
if exist "%RDPRRAP_INSTALLED%" (
    for /f "usebackq delims=" %%V in ("%RDPRRAP_INSTALLED%") do set "RDPRRAP_CUR=%%V"
)
REM Check sits on its own line so %RDPRRAP_CUR% expands AFTER the for-loop above (no delayed expansion needed).
if defined RDPRRAP_CUR if /I "%RDPRRAP_CUR%"=="%RDPRRAP_VERSION%" (
    echo [winpodx] rdprrap %RDPRRAP_VERSION% already installed, skipping.
    goto :rdprrap_done
)

REM Locate the bundled zip inside the OEM staging folder.
set "RDPRRAP_ZIP_SRC="
if exist "C:\OEM\%RDPRRAP_FILENAME%" set "RDPRRAP_ZIP_SRC=C:\OEM\%RDPRRAP_FILENAME%"
if not defined RDPRRAP_ZIP_SRC (
    echo [winpodx] WARNING: bundled %RDPRRAP_FILENAME% not found at C:\winpodx or C:\OEM; staying single-session.
    goto :rdprrap_done
)

REM certutil prints 3 lines; line 2 has the hex digest with spaces between bytes.
set "RDPRRAP_GOT="
for /f "usebackq skip=1 delims=" %%H in (`certutil -hashfile "%RDPRRAP_ZIP_SRC%" SHA256 ^| findstr /R "^[0-9a-fA-F ]*$"`) do (
    if not defined RDPRRAP_GOT set "RDPRRAP_GOT=%%H"
)
set "RDPRRAP_GOT=%RDPRRAP_GOT: =%"
if /I not "%RDPRRAP_GOT%"=="%RDPRRAP_SHA256%" (
    echo [winpodx] WARNING: rdprrap sha256 mismatch on bundle; staying single-session.
    echo [winpodx]   expected %RDPRRAP_SHA256%
    echo [winpodx]   got      %RDPRRAP_GOT%
    goto :rdprrap_done
)

mkdir "%RDPRRAP_DIR%" 2>nul
echo [winpodx] Extracting rdprrap %RDPRRAP_VERSION%...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Expand-Archive -LiteralPath '%RDPRRAP_ZIP_SRC%' -DestinationPath '%RDPRRAP_DIR%' -Force; $inner = Get-ChildItem -LiteralPath '%RDPRRAP_DIR%' -Directory -Filter 'rdprrap-*' | Select-Object -First 1; if ($inner) { Get-ChildItem -LiteralPath $inner.FullName -Force | Move-Item -Destination '%RDPRRAP_DIR%' -Force; Remove-Item -LiteralPath $inner.FullName -Recurse -Force } exit 0 } catch { Write-Error $_; exit 1 }"
if errorlevel 1 (
    echo [winpodx] WARNING: rdprrap extraction failed; staying single-session.
    goto :rdprrap_done
)

set "RDPRRAP_EXE=%RDPRRAP_DIR%\rdprrap-installer.exe"
if not exist "%RDPRRAP_EXE%" (
    echo [winpodx] WARNING: rdprrap-installer.exe missing after extract; staying single-session.
    goto :rdprrap_done
)

echo [winpodx] Running rdprrap-installer...
"%RDPRRAP_EXE%" install --skip-restart
if errorlevel 1 (
    echo [winpodx] WARNING: rdprrap-installer failed; staying single-session.
    goto :rdprrap_done
)

REM rdprrap patches the registry ServiceDll to termwrap.dll, but the already-running TermService still has the
REM original termsrv.dll loaded in memory. dockur's unattended flow does not restart svchost before handing off
REM to logon, so without an explicit cycle here the first RDP connection hits Windows' default single-session
REM limit and our multi-session feature looks broken. /y auto-confirms stopping dependent services.
echo [winpodx] Restarting TermService to activate rdprrap...
net stop TermService /y >nul 2>&1
net start TermService >nul 2>&1

(echo %RDPRRAP_VERSION%)>"%RDPRRAP_INSTALLED%"
echo [winpodx] rdprrap %RDPRRAP_VERSION% installed (offline bundle).
goto :rdprrap_done

:rdprrap_skip
echo [winpodx] rdprrap_version.txt not found or incomplete; staying single-session.
:rdprrap_done

REM -----------------------------------------------------------------------
REM v0.2.2: winpodx guest HTTP agent
REM -----------------------------------------------------------------------
echo [winpodx] Installing winpodx guest agent...
mkdir C:\OEM 2>nul
REM agent.ps1 is staged next to install.bat inside the OEM bundle.
copy /Y "%~dp0agent.ps1" "C:\OEM\agent.ps1" 2>nul
mkdir C:\OEM\agent-runs 2>nul

REM Pull the shared token from the host home share.
REM \\tsclient\home is mounted via FreeRDP +home-drive at install time.
REM The token is written by `winpodx setup` on the Linux host side.
REM If absent (e.g. the share isn't up yet), the copy is skipped silently;
REM the agent will pick up the token later once the share is available.
copy /Y "\\tsclient\home\.config\winpodx\agent_token.txt" "C:\OEM\agent_token.txt" 2>nul

REM Register a logon Task Scheduler entry.  /F overwrites if already present
REM so re-runs are idempotent.
schtasks /Create /SC ONLOGON /TN winpodx-agent /RU User /RL HIGHEST ^
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\OEM\agent.ps1" /F >nul 2>&1

echo [winpodx] Guest agent installed (C:\OEM\agent.ps1, task: winpodx-agent).

REM Sentinel lives under C:\winpodx so it survives past the one-shot C:\OEM stage.
(echo done)>C:\winpodx\setup_done.txt

echo [winpodx] Post-install configuration complete (version %WINPODX_OEM_VERSION%)!
echo [winpodx] RDP is now enabled. You can connect with FreeRDP.
