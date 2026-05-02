@echo off
REM First-boot OEM setup for winpodx Windows guest. Runs once during dockur's unattended install. Every action must stay idempotent — there is no guest-side re-run channel in 0.1.6 (push/exec bridge planned for a later release).

set WINPODX_OEM_VERSION=17

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

REM Allow inbound on the winpodx agent port. QEMU's user-mode NAT forwards
REM container:8765 -> Windows VM 10.0.2.15:8765, which Windows Firewall
REM blocks by default — kernalix7 saw curl timeout from the host on
REM 2026-04-30 even with agent.ps1 bound to 0.0.0.0:8765 because the SYN
REM never made it past the firewall. RDP got auto-allowed via the
REM "Remote Desktop" group rule above; 8765 needs an explicit rule.
netsh advfirewall firewall delete rule name="winpodx-agent" >nul 2>&1
netsh advfirewall firewall add rule name="winpodx-agent" dir=in action=allow protocol=tcp localport=8765 2>nul

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

REM --- Diagnostic logs ------------------------------------------------------
REM Status marker (one-line classification, machine-readable):
REM   enabled / extract-failed / installer-failed / not-activated / missing-bundle
REM Detailed log (full timestamps + retry-by-retry stderr/stdout) so when
REM something fails users / `winpodx pod apply-fixes` have something to
REM root-cause from. The marker fits a single grep, the log is the deep dive.
set "RDPRRAP_STATUS=C:\winpodx\rdprrap\.activation_status"
set "RDPRRAP_LOG=C:\winpodx\rdprrap\install.log"
(echo === rdprrap install log === %DATE% %TIME%) > "%RDPRRAP_LOG%"
(echo version=%RDPRRAP_VERSION%)>>"%RDPRRAP_LOG%"
(echo bundle=%RDPRRAP_ZIP_SRC%)>>"%RDPRRAP_LOG%"

REM --- Extract with retries + per-attempt log ------------------------------
REM Expand-Archive occasionally fails on first-boot Sysprep with file-lock /
REM antivirus interference. Retry up to 3 times. Capture stderr each
REM attempt so a final extract-failed has actionable diagnostics.
echo [winpodx] Extracting rdprrap %RDPRRAP_VERSION%...
set "RDPRRAP_EXTRACTED="
for %%T in (1 2 3) do (
    if not defined RDPRRAP_EXTRACTED (
        (echo --- extract attempt %%T %DATE% %TIME%)>>"%RDPRRAP_LOG%"
        powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Expand-Archive -LiteralPath '%RDPRRAP_ZIP_SRC%' -DestinationPath '%RDPRRAP_DIR%' -Force; $inner = Get-ChildItem -LiteralPath '%RDPRRAP_DIR%' -Directory -Filter 'rdprrap-*' | Select-Object -First 1; if ($inner) { Get-ChildItem -LiteralPath $inner.FullName -Force | Move-Item -Destination '%RDPRRAP_DIR%' -Force; Remove-Item -LiteralPath $inner.FullName -Recurse -Force } exit 0 } catch { Write-Error $_; exit 1 }" >>"%RDPRRAP_LOG%" 2>&1
        if not errorlevel 1 set "RDPRRAP_EXTRACTED=1"
        if not defined RDPRRAP_EXTRACTED (
            (echo extract attempt %%T failed; sleeping 2s)>>"%RDPRRAP_LOG%"
            echo [winpodx]   extract attempt %%T failed, retrying after 2s...
            powershell -NoProfile -Command "Start-Sleep -Seconds 2"
        )
    )
)
if not defined RDPRRAP_EXTRACTED (
    (echo FINAL: extract-failed)>>"%RDPRRAP_LOG%"
    echo [winpodx] WARNING: rdprrap extraction failed after 3 attempts; staying single-session.
    echo [winpodx]   diagnostic log: %RDPRRAP_LOG%
    (echo extract-failed)>"%RDPRRAP_STATUS%"
    goto :rdprrap_done
)
(echo extract OK after retries^)>>"%RDPRRAP_LOG%"

set "RDPRRAP_EXE=%RDPRRAP_DIR%\rdprrap-installer.exe"
if not exist "%RDPRRAP_EXE%" (
    (echo FINAL: extract-failed - rdprrap-installer.exe missing after extract)>>"%RDPRRAP_LOG%"
    echo [winpodx] WARNING: rdprrap-installer.exe missing after extract; staying single-session.
    echo [winpodx]   diagnostic log: %RDPRRAP_LOG%
    (echo extract-failed)>"%RDPRRAP_STATUS%"
    goto :rdprrap_done
)

REM --- Run installer with retries + per-attempt log ------------------------
REM rdprrap-installer can transiently fail on first-boot due to Windows
REM Update background tasks, antivirus init, or service startup races.
REM Retry up to 3 times with backoff. Pipe stdout/stderr into the log so
REM rc!=0 surfaces with the actual installer error message.
echo [winpodx] Running rdprrap-installer...
set "RDPRRAP_INSTALLED_OK="
for %%T in (1 2 3) do (
    if not defined RDPRRAP_INSTALLED_OK (
        (echo --- installer attempt %%T %DATE% %TIME%)>>"%RDPRRAP_LOG%"
        "%RDPRRAP_EXE%" install --skip-restart >>"%RDPRRAP_LOG%" 2>&1
        if not errorlevel 1 set "RDPRRAP_INSTALLED_OK=1"
        if not defined RDPRRAP_INSTALLED_OK (
            (echo installer attempt %%T failed; sleeping 3s)>>"%RDPRRAP_LOG%"
            echo [winpodx]   installer attempt %%T failed, retrying after 3s...
            powershell -NoProfile -Command "Start-Sleep -Seconds 3"
        )
    )
)
if not defined RDPRRAP_INSTALLED_OK (
    (echo FINAL: installer-failed)>>"%RDPRRAP_LOG%"
    echo [winpodx] WARNING: rdprrap-installer failed after 3 attempts; staying single-session.
    echo [winpodx]   diagnostic log: %RDPRRAP_LOG%
    (echo installer-failed)>"%RDPRRAP_STATUS%"
    goto :rdprrap_done
)
(echo installer OK after retries^)>>"%RDPRRAP_LOG%"

REM rdprrap patches the registry ServiceDll to termwrap.dll, but the already-running TermService still has the
REM original termsrv.dll loaded in memory. dockur's unattended flow does not restart svchost before handing off
REM to logon, so without an explicit cycle here the first RDP connection hits Windows' default single-session
REM limit and our multi-session feature looks broken. /y auto-confirms stopping dependent services.
echo [winpodx] Restarting TermService to activate rdprrap...
(echo --- restarting TermService %DATE% %TIME%)>>"%RDPRRAP_LOG%"
net stop TermService /y >>"%RDPRRAP_LOG%" 2>&1
net start TermService >>"%RDPRRAP_LOG%" 2>&1

REM --- Verify activation + log full diagnostics on failure -----------------
REM rdprrap-installer reporting rc=0 doesn't guarantee the patch landed —
REM observed cases where the binary completed but ServiceDll was never
REM flipped (transient Defender hold, Sysprep-time registry race). Check
REM HKLM\SYSTEM\CurrentControlSet\Services\TermService\Parameters\ServiceDll
REM — if it ends in termwrap.dll the patch is live; if termsrv.dll, it isn't.
REM On the not-activated path also dump termsrv state, ServiceDll value,
REM and the installer's exit context so failures are root-cause-able from
REM the log alone.
echo [winpodx] Verifying rdprrap activation...
(echo --- activation verify %DATE% %TIME%)>>"%RDPRRAP_LOG%"
set "RDPRRAP_SERVICEDLL="
for /f "usebackq tokens=2,*" %%A in (`reg query "HKLM\SYSTEM\CurrentControlSet\Services\TermService\Parameters" /v ServiceDll 2^>nul ^| findstr /R "REG_EXPAND_SZ"`) do (
    set "RDPRRAP_SERVICEDLL=%%B"
)
(echo ServiceDll=%RDPRRAP_SERVICEDLL%)>>"%RDPRRAP_LOG%"
echo %RDPRRAP_SERVICEDLL% | findstr /I "termwrap" >nul 2>&1
if errorlevel 1 (
    (echo FINAL: not-activated)>>"%RDPRRAP_LOG%"
    (echo TermService state:)>>"%RDPRRAP_LOG%"
    sc query TermService >>"%RDPRRAP_LOG%" 2>&1
    (echo Parameters key dump:)>>"%RDPRRAP_LOG%"
    reg query "HKLM\SYSTEM\CurrentControlSet\Services\TermService\Parameters" >>"%RDPRRAP_LOG%" 2>&1
    echo [winpodx] WARNING: rdprrap installer ran but ServiceDll didn't flip to termwrap.dll.
    echo [winpodx]   ServiceDll = %RDPRRAP_SERVICEDLL%
    echo [winpodx]   diagnostic log: %RDPRRAP_LOG%
    echo [winpodx]   Multi-session activation incomplete; staying single-session.
    (echo not-activated)>"%RDPRRAP_STATUS%"
    goto :rdprrap_done
)

(echo FINAL: enabled)>>"%RDPRRAP_LOG%"
(echo enabled)>"%RDPRRAP_STATUS%"
(echo %RDPRRAP_VERSION%)>"%RDPRRAP_INSTALLED%"
echo [winpodx] rdprrap %RDPRRAP_VERSION% installed and activated (ServiceDll = %RDPRRAP_SERVICEDLL%).
goto :rdprrap_done

:rdprrap_skip
echo [winpodx] rdprrap_version.txt not found or incomplete; staying single-session.
:rdprrap_done

REM ---------------------------------------------------------------------
REM v0.2.2-rev1: winpodx guest HTTP agent
REM ---------------------------------------------------------------------
echo [winpodx] Installing winpodx guest agent...
copy /Y "%~dp0agent\agent.ps1" "C:\OEM\agent.ps1" 2>nul
mkdir C:\OEM\agent-runs 2>nul

REM Hidden launchers go under C:\Users\Public\winpodx\launchers\ rather than
REM C:\OEM\. Public is universally writable for Authenticated Users, so the
REM agent (which runs as User, non-admin) can later overwrite these files
REM during a `winpodx pod apply-fixes` migration without needing UAC. C:\OEM\
REM is SYSTEM-owned and rejects User writes by default.
mkdir "C:\Users\Public\winpodx" 2>nul
mkdir "C:\Users\Public\winpodx\launchers" 2>nul
copy /Y "%~dp0hidden-launcher.vbs" "C:\Users\Public\winpodx\launchers\hidden-launcher.vbs" 2>nul
copy /Y "%~dp0launch_uwp.vbs" "C:\Users\Public\winpodx\launchers\launch_uwp.vbs" 2>nul
copy /Y "%~dp0launch_uwp.ps1" "C:\Users\Public\winpodx\launchers\launch_uwp.ps1" 2>nul
copy /Y "%~dp0agent-respawn.ps1" "C:\Users\Public\winpodx\launchers\agent-respawn.ps1" 2>nul
REM rdprrap-activate.ps1 is the runtime activation script `winpodx pod
REM multi-session on` invokes (detached via wscript). Staged here so
REM fresh OEM installs can re-activate post-Sysprep without container
REM recreate; the host-side _apply_vbs_launchers also pushes it during
REM migration for older guests.
copy /Y "%~dp0rdprrap-activate.ps1" "C:\Users\Public\winpodx\launchers\rdprrap-activate.ps1" 2>nul

REM Pre-register the URL ACL for agent.ps1's HttpListener prefix.
REM
REM agent.ps1 binds ``http://+:8765/`` (all interfaces inside the
REM Windows VM) — NOT 127.0.0.1. dockur's user-mode QEMU NAT forwards
REM from container:8765 to the VM's slirp interface (10.0.2.15:8765,
REM NOT 127.0.0.1:8765); a 127.0.0.1-only listener would mean slirp's
REM forwarded packets hit a closed port (kernalix7 saw "Connection
REM reset by peer" on 2026-04-30 from exactly this). Binding to + is
REM safe because the agent stays externally unreachable: compose's
REM 127.0.0.1:8765:8765/tcp mapping is loopback on the host, and the
REM QEMU slirp net is private to the container.
REM
REM HttpListener.Start() needs a urlacl entry to bind ``+``; without
REM it the bind fails with "conflicts with an existing registration".
REM listen=yes + user=Everyone gives the autologon User permission to
REM listen on this prefix. Delete-then-add keeps it idempotent across
REM reinstalls (clears any stale registration from a previous SDDL).
REM Also clean up the old loopback-only ACL that pre-2026-04-30 builds
REM may have left.
netsh http delete urlacl url=http://127.0.0.1:8765/ >nul 2>&1
netsh http delete urlacl url=http://+:8765/ >nul 2>&1
netsh http add urlacl url=http://+:8765/ user=Everyone listen=yes >nul 2>&1

REM Register agent at user logon via the hidden VBS launcher rather than
REM `powershell.exe -WindowStyle Hidden`. The Hidden flag is honored AFTER
REM PowerShell allocates its conhost, so a brief PS console flashes for
REM ~50ms on every user logon. wscript.exe is a GUI-subsystem process
REM (no console of its own) and WshShell.Run with intWindowStyle=0
REM propagates SW_HIDE to CreateProcess — the spawned powershell starts
REM windowless, never flashing.
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WinpodxAgent /t REG_SZ /d "wscript.exe \"C:\Users\Public\winpodx\launchers\hidden-launcher.vbs\" \"powershell.exe\" \"-NoProfile\" \"-ExecutionPolicy\" \"Bypass\" \"-File\" \"C:\OEM\agent.ps1\"" /f >nul 2>&1

REM Token is delivered via the OEM bind mount — no \\tsclient\home copy
REM needed. Setup stages it to {oem_dir}/agent_token.txt before container
REM creation; dockur lays the OEM directory contents into C:\OEM\.
echo [winpodx] Guest agent installed.

REM Sentinel lives under C:\winpodx so it survives past the one-shot C:\OEM stage.
(echo done)>C:\winpodx\setup_done.txt

echo [winpodx] Post-install configuration complete (version %WINPODX_OEM_VERSION%)!
echo [winpodx] RDP is now enabled. You can connect with FreeRDP.
