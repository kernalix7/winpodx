@echo off
REM First-boot OEM setup for winpodx Windows guest. Runs once during dockur's unattended install. Every action must stay idempotent — there is no guest-side re-run channel in 0.1.6 (push/exec bridge planned for a later release).

set WINPODX_OEM_VERSION=20

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
REM WinpodxMedia HKCU\Run registration moved later in install.bat -- the
REM same PowerShell block that registers WinpodxAgent now writes both
REM entries with shared launcher-existence gating + setup.log diagnostics.
REM See "Registering HKCU\Run entries" below.

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

REM --- Delegate install / TermService cycle / verify to rdprrap-activate.ps1
REM Single source of truth — same script `winpodx pod multi-session on`
REM uses for runtime activation. install.bat used to inline ~80 lines of
REM installer-retry / TermService-cycle / ServiceDll-verify / marker logic;
REM that code now lives in rdprrap-activate.ps1 so a fix to the activation
REM flow benefits both OEM-time and runtime paths without drift.
REM
REM Safety at OEM time: install.bat runs from FirstLogonCommands in the
REM local console session — TermService manages RDP sessions only, so the
REM cycle inside the script doesn't tear down our cmd.exe parent.
REM
REM The script writes the same .activation_status / install.log markers
REM install.bat used to write directly. Idempotency marker
REM (.installed_version) stays install.bat's responsibility — only OEM
REM time has the SHA-pinned bundle context to safely stamp it.
echo [winpodx] Activating rdprrap (delegating to rdprrap-activate.ps1)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rdprrap-activate.ps1" >>"%RDPRRAP_LOG%" 2>&1
set "RDPRRAP_RC=%ERRORLEVEL%"
(echo rdprrap-activate.ps1 exit=%RDPRRAP_RC%)>>"%RDPRRAP_LOG%"
if not "%RDPRRAP_RC%"=="0" (
    echo [winpodx] WARNING: rdprrap-activate.ps1 exited with code %RDPRRAP_RC%; staying single-session.
    echo [winpodx]   diagnostic log: %RDPRRAP_LOG%
    goto :rdprrap_done
)

(echo %RDPRRAP_VERSION%)>"%RDPRRAP_INSTALLED%"
echo [winpodx] rdprrap %RDPRRAP_VERSION% installed and activated.
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
REM
REM Diagnostic log (`C:\winpodx\setup.log`) records each copy + verify
REM step. Pre-OEM-v20 the copies were silent (`2>nul`), so when a
REM Sysprep-time failure (network share blip, AV interference) skipped a
REM file, the missing-file symptom only surfaced later as a "Cannot find
REM script file" wscript dialog at HKCU\Run firing time -- with no
REM breadcrumbs. With this log + per-file existence check below, a
REM staging failure now writes a single line we can grep from the host
REM via the agent.
mkdir C:\winpodx 2>nul
set "SETUP_LOG=C:\winpodx\setup.log"
(echo === setup log === %DATE% %TIME%) > "%SETUP_LOG%"
(echo OEM_VERSION=%WINPODX_OEM_VERSION%)>>"%SETUP_LOG%"
(echo install.bat dir=%~dp0)>>"%SETUP_LOG%"

mkdir "C:\Users\Public\winpodx" 2>nul
mkdir "C:\Users\Public\winpodx\launchers" 2>nul

REM Each launcher: copy, verify the file exists at its destination, log
REM the outcome. Continuing on failure (so a single bad copy doesn't
REM abort the rest of install.bat) but recording it for diagnostics.
REM `LAUNCHERS_OK=1` after the loop means hidden-launcher.vbs (the
REM critical wrapper) made it to its destination -- gates the wscript
REM reg add below.
set "LAUNCHERS_OK="
for %%F in (
    "hidden-launcher.vbs"
    "launch_uwp.vbs"
    "launch_uwp.ps1"
    "agent-respawn.ps1"
    "rdprrap-activate.ps1"
) do (
    copy /Y "%~dp0%%~F" "C:\Users\Public\winpodx\launchers\%%~F" >nul 2>>"%SETUP_LOG%"
    if exist "C:\Users\Public\winpodx\launchers\%%~F" (
        (echo launcher OK: %%~F)>>"%SETUP_LOG%"
    ) else (
        (echo launcher FAILED: %%~F src=%~dp0%%~F not staged or copy denied)>>"%SETUP_LOG%"
    )
)
if exist "C:\Users\Public\winpodx\launchers\hidden-launcher.vbs" set "LAUNCHERS_OK=1"
(echo launchers gate: LAUNCHERS_OK=%LAUNCHERS_OK%)>>"%SETUP_LOG%"

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

REM Register HKCU\Run via PowerShell instead of `reg add`. Three reasons
REM the cmd-quoting path bit users pre-OEM-v20:
REM   1. `reg add /d "...\\\"path\\\"..."` survives cmd parsing but
REM      reg.exe stores literal backslash-quote pairs in the registry,
REM      and CommandLineToArgvW at logon evaluates them as escaped
REM      quotes -- not always argv-equivalent to the intended quoted
REM      argument. PS's Set-ItemProperty takes a real .NET string and
REM      stores it byte-exact.
REM   2. PS can verify hidden-launcher.vbs exists *before* registering
REM      the wscript-wrapped value -- if the launcher staging copy
REM      failed (logged via setup.log above, LAUNCHERS_OK unset), we
REM      fall back to direct `powershell.exe -WindowStyle Hidden` for
REM      WinpodxAgent. That fallback flashes briefly but at least
REM      starts the agent; without it, HKCU\Run fired wscript on a
REM      missing file -> "Cannot find script file" dialog blocking
REM      the user session indefinitely (kernalix7 saw this on a fresh
REM      install 2026-05-02 18:48).
REM   3. Single PS round-trip writes both WinpodxAgent and WinpodxMedia,
REM      replacing the two separate reg-add lines + the WinpodxMedia
REM      special case below.
echo [winpodx] Registering HKCU\Run entries...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$wrap = 'C:\Users\Public\winpodx\launchers\hidden-launcher.vbs';" ^
  "$haveWrap = Test-Path -LiteralPath $wrap;" ^
  "Add-Content -LiteralPath '%SETUP_LOG%' -Value (\"reg-add: haveWrap=$haveWrap\");" ^
  "$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run';" ^
  "if ($haveWrap) {" ^
  "  $agent = 'wscript.exe \"' + $wrap + '\" \"powershell.exe\" \"-NoProfile\" \"-ExecutionPolicy\" \"Bypass\" \"-File\" \"C:\OEM\agent.ps1\"';" ^
  "  $media = 'wscript.exe \"' + $wrap + '\" \"powershell.exe\" \"-NoProfile\" \"-ExecutionPolicy\" \"Bypass\" \"-File\" \"C:\winpodx\media_monitor.ps1\"';" ^
  "} else {" ^
  "  Add-Content -LiteralPath '%SETUP_LOG%' -Value 'reg-add: hidden-launcher.vbs missing -> fallback to direct powershell (brief flash)';" ^
  "  $agent = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\OEM\agent.ps1';" ^
  "  $media = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\winpodx\media_monitor.ps1';" ^
  "}" ^
  "Set-ItemProperty -Path $key -Name 'WinpodxAgent' -Value $agent -Force;" ^
  "Set-ItemProperty -Path $key -Name 'WinpodxMedia' -Value $media -Force;" ^
  "Add-Content -LiteralPath '%SETUP_LOG%' -Value ('reg-add: WinpodxAgent=' + $agent);" ^
  "Add-Content -LiteralPath '%SETUP_LOG%' -Value ('reg-add: WinpodxMedia=' + $media);" 2>>"%SETUP_LOG%"

REM Token is delivered via the OEM bind mount — no \\tsclient\home copy
REM needed. Setup stages it to {oem_dir}/agent_token.txt before container
REM creation; dockur lays the OEM directory contents into C:\OEM\.
echo [winpodx] Guest agent installed.

REM Sentinel lives under C:\winpodx so it survives past the one-shot C:\OEM stage.
(echo done)>C:\winpodx\setup_done.txt

echo [winpodx] Post-install configuration complete (version %WINPODX_OEM_VERSION%)!
echo [winpodx] RDP is now enabled. You can connect with FreeRDP.
